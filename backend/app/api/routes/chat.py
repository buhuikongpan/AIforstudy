"""聊天接口 — 直连 DeepSeek/智谱（本地直连模式），SSE 流式输出
会话已持久化到 SQLite（chat_sessions + chat_messages），服务重启不丢，
支持按科目/模式管理会话（新建 / 删除 / 重命名 / 历史）。
"""
import json
import base64
import datetime
import os
import re
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Depends
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...services.llm_service import (
    chat_completion_stream, execute_tool_call, UPLOAD_DIR, upload_image_block,
    upload_public_url, READ_ARTIFACT_TOOL, LIST_ARTIFACTS_TOOL, GEN_CHAPTER_TOOL, LIST_FILES_TOOL,
)
from ...services.orchestrator import build_file_context
from ...core.paths import resolve, to_rel
from ...models import Video, Subtitle, ChatSession, ChatMessage, File as FileModel, Folder
from ..deps import get_db

router = APIRouter(prefix="/api/chat", tags=["聊天"])


def normalize_math(text: str) -> str:
    """公式形态硬约束：把模型偶尔输出的 \(...\) / \[...\] 统一归一化为 $...$ / $$...$$。

    提示词里对模型是软约束；这里在落库前做硬清洗，保证库里和历史
    恢复的消息都是前端能渲染的形态。
    """
    text = re.sub(r'\\\[([\s\S]*?)\\\]', r'$$\1$$', text)
    text = re.sub(r'\\\(([\s\S]*?)\\\)', r'$\1$', text)
    return text

SUBJECT_NAMES = {"math": "数学", "english": "英语", "politics": "政治", "zhuanye": "专业课"}
MODE_NAMES = {"A": "即时问答", "B": "引导输出", "C": "课后问答"}
DEFAULT_NAME = "新对话"
# ask 工具（照抄 dsh 的 ask_user_question）：模型可暂停并向前端提问
ASK_TOOL_NAME = "ask_user_question"
QUIZ_TOOL_NAME = "quiz_user"
# 这两个工具都会"暂停本轮等用户作答"，服务端处理路径相同（chat.py 同一段中断逻辑）
INTERACTIVE_TOOL_NAMES = (ASK_TOOL_NAME, QUIZ_TOOL_NAME)
MAX_ASK_QUESTIONS = 3    # 每轮最多回传 3 个问题（工具 schema 无硬上限，服务端防御）
MAX_TOOL_ROUNDS = 6      # 一次请求内最多几轮「工具调用 → 拿结果 → 继续作答」
MAX_HISTORY = 40           # 回填给模型的历史消息上限（条）
MAX_REASONING_ROUNDS = 2   # 思维链只回填最近 N 轮 assistant（防 token 膨胀）
# 模式B 字幕上限：模型上下文为 1M token，10 节长课全文（约 15 万字符 ≈ 20 万 token）
# 也只是总量的 2%，此上限纯属防御极端堆积（历史消息 + 思维链叠加），正常不触发
MAX_SUBTITLE_CHARS = 400000


def _parse_ask_questions(call: dict) -> list:
    """解析交互工具（ask_user_question / quiz_user）的参数，顺便对提问文本做公式形态硬清洗。

    模型生成的 question/header/options 文本同样受「公式铁律」约束，
    这里在发给前端前统一成 $...$ / $$...$$，避免前端渲染出 \(...\) 原文。
    """
    try:
        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except Exception:
        return []
    out = []
    for q in (args.get("questions") or [])[:MAX_ASK_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        options = []
        for o in (q.get("options") or [])[:6]:
            if isinstance(o, dict) and o.get("label"):
                options.append({
                    "label": normalize_math(str(o["label"])),
                    "description": normalize_math(str(o.get("description") or "")),
                })
        out.append({
            "id": str(q.get("id") or f"q{len(out) + 1}"),
            "question": normalize_math(str(q.get("question") or "")),
            "header": normalize_math(str(q.get("header") or "")),
            "options": options,
            "multi_select": bool(q.get("multi_select")),
        })
    return out


# ---------- DB 读写辅助（统一走调用方传入的 Session） ----------
def _load_messages(db: Session, conv_id: str, limit: int = MAX_HISTORY,
                   keep_dangling: bool = False) -> list:
    """按 conv_id 从 DB 载入历史消息（最近 limit 条）。

    keep_dangling: 仅当本轮就是来补 tool 结果时（用户提交了 ask/quiz 卡片）为 True，
    保留末尾那条「带 tool_calls、还没等到 tool 结果」的 assistant；
    其余情况（用户跳过卡片直接发新消息）必须摘掉，否则接口会以
    "insufficient tool messages following tool_calls message" 报 400。
    """
    rows = db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id)\
        .order_by(ChatMessage.seq.asc()).all()
    out = []
    reasoning_left = MAX_REASONING_ROUNDS  # 只给最近几轮 assistant 回填思维链
    for m in rows[-limit:]:
        try:
            content = json.loads(m.content) if m.content else None
        except Exception:
            content = m.content
        # 类型归一化后再回填：
        # 1) 数组 content 分两种：
        #    - user 消息（带图提问）：库里的 image_url.url 存的是**相对路径**，
        #      这里循环展开成 base64 图块再发给模型（图直接进上下文，模型当轮就能看图）
        #    - 其它角色的历史脏数据：拍平为纯文本（DeepSeek 聊天接口只接受字符串 content）
        # 2) json.loads 会把纯数字字符串("2")解析成 int —— 同样会 400，强制转回字符串
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            if m.role == "user":
                blocks = []
                for p in content:
                    if not (isinstance(p, dict) and p.get("type") == "image_url"):
                        continue
                    url = (p.get("image_url") or {}).get("url") or ""
                    if url.startswith("data:"):
                        blocks.append(p)          # 已是内联图块，原样用
                        continue
                    try:
                        blocks.append(upload_image_block(url))
                    except Exception:
                        continue                  # 图丢了/格式不对：丢图不丢文字
                content = ([{"type": "text", "text": "\n".join(t for t in texts if t)}] + blocks)
            else:
                content = "\n".join(t for t in texts if t)
        elif not isinstance(content, str):
            # None（空正文/JSON null）统一成空串：str(None) 会得到字面量 "None"，
            # 既污染上下文，也让下方“空正文就丢弃”的判断失效
            content = "" if content is None else str(content)
        # 工具结果消息：必须对应「最近一条带 tool_calls 的 assistant」，孤立则丢弃（防历史截断后 400）。
        # 注意：一轮里模型可能同时调多个工具（例如同时读两张图 → read ×2），
        # 它们的 tool 结果会连续出现，所以要先跳过前面连续的 tool 消息再找宿主 assistant；
        # 只拿 out[-1] 比对的话第 2 条结果会被误判为孤立而丢掉（丢图 + 后续 400）。
        if m.role == "tool":
            k = len(out) - 1
            while k >= 0 and out[k].get("role") == "tool":
                k -= 1
            prev = out[k] if k >= 0 else None
            tcid = m.tool_call_id or ""
            if not (prev and prev.get("role") == "assistant" and prev.get("tool_calls")
                    and any((tc.get("id") or "") == tcid for tc in prev["tool_calls"])):
                continue
            raw = m.content or ""
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            # 数组形式的工具结果（read 返回的图片内容块）要还原成数组；
            # 字符串/对象（如 ask 的 answers）保持原样回填
            out.append({"role": "tool", "tool_call_id": tcid,
                        "content": parsed if isinstance(parsed, list) else raw})
            continue
        item = {"role": m.role, "content": content}
        if m.role == "assistant" and m.tool_calls:
            try:
                item["tool_calls"] = json.loads(m.tool_calls)
            except Exception:
                pass
        if m.reasoning and m.role == "assistant" and reasoning_left > 0:
            # 思维链只回填最近少数几轮：历史 + 思维链都占上下文，全量回填
            # 会在长对话中顶爆 token（这也是节省账单的关键）
            item["reasoning_content"] = m.reasoning
            reasoning_left -= 1
        out.append(item)

    # 兜底：带 tool_calls 的 assistant 后面必须跟齐对应的 tool 结果，否则 API 报 400。
    #   1) 末尾一条且本轮就是来补结果的（用户提交了卡片）—— 保留，调用方马上 append tool 消息；
    #   2) 其余情况（后面跟了别的消息、或用户跳过提问直接发新消息）—— 摘掉 tool_calls。
    cleaned = []
    for i, item in enumerate(out):
        if item.get("role") == "assistant" and item.get("tool_calls"):
            wanted = {tc.get("id") for tc in item["tool_calls"]}
            got = set()
            j = i + 1
            while j < len(out) and out[j].get("role") == "tool":
                got.add(out[j].get("tool_call_id"))
                j += 1
            if not wanted.issubset(got):
                if keep_dangling and j >= len(out):
                    pass  # 等调用方补 tool 结果
                else:
                    item = {k: v for k, v in item.items() if k != "tool_calls"}
                    # 摘完正文为空（模型只调了工具没说话）：整条丢弃，
                    # 残留空 content 的 assistant 同样会被接口拒绝
                    if not (item.get("content") or "").strip():
                        continue
        cleaned.append(item)

    # 二次兜底：上面把某条 assistant 的 tool_calls 摘掉后，紧跟它的 tool 消息就成了
    # “孤儿”（前一条不再是带 tool_calls 的 assistant）——接口同样 400：
    # "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"。
    # 逐条校验：tool 的 tool_call_id 必须能在紧邻前一条 assistant 的 tool_calls 里找到，否则丢弃。
    valid = []
    for item in cleaned:
        if item.get("role") == "tool":
            # 同样要先跳过连续的 tool 消息，找到宿主 assistant（一轮多个工具结果）
            k = len(valid) - 1
            while k >= 0 and valid[k].get("role") == "tool":
                k -= 1
            prev = valid[k] if k >= 0 else None
            ids = ({tc.get("id") for tc in prev["tool_calls"]}
                   if prev and prev.get("role") == "assistant" and prev.get("tool_calls")
                   else set())
            if (item.get("tool_call_id") or "") not in ids:
                continue
        valid.append(item)
    cleaned = valid

    # 图片型工具结果（read 返回的图块）非常占 token：只保留最近一条原样，
    # 更早的换成占位文字，避免长对话里反复重发整张图的 base64。
    visual_left = 1
    for item in reversed(cleaned):
        if item.get("role") == "tool" and isinstance(item.get("content"), list):
            if visual_left > 0:
                visual_left -= 1
            else:
                item["content"] = "[该图片此前已查看过，内容见当轮对话；如需重看可再次调用工具]"
    return cleaned


def _ensure_session(db: Session, conv_id: str, subject: str, mode: str):
    """确保会话行存在（老流程/直接发送时自动建），返回会话行（新建则 commit）。

    调用方通常紧接着调 _append_message：把返回的行传给它作 _sess，
    复用查询，避免同一发送流程里 session 被查两次。
    """
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        sess = ChatSession(conv_id=conv_id, subject=subject, mode=mode, name=DEFAULT_NAME)
        db.add(sess)
        db.commit()
    return sess


def _append_message(
    db: Session,
    conv_id: str, subject: str, mode: str, role: str,
    content, reasoning: str = "", quote: str = "",
    tool_calls: str = "", tool_call_id: str = "",
    _sess=None,
) -> None:
    """写入一条消息，并更新会话 updated_at、首条提问自动命名。

    _sess: 可选，由 _ensure_session 返回的已加载会话行（避免重复查询）。
    """
    import datetime
    # seq 用 max(seq)+1，避免 count(*) 全表扫描行集（两者复杂度同级，但 max 对稀疏
    # seq 更精确，且避免 count 在超大表上的行级聚合）
    top = db.query(func.max(ChatMessage.seq)).filter(ChatMessage.conv_id == conv_id).scalar()
    seq = (top + 1) if top is not None else 0
    jsonable = content if isinstance(content, (list, dict)) else (content or "")
    stored = json.dumps(jsonable, ensure_ascii=False) if isinstance(jsonable, (list, dict)) else str(jsonable)
    db.add(ChatMessage(conv_id=conv_id, subject=subject, mode=mode,
                       role=role, content=stored, reasoning=reasoning or "",
                       quote=quote or "", tool_calls=tool_calls or "",
                       tool_call_id=tool_call_id or "", seq=seq))
    sess = _sess if _sess is not None else db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if sess:
        sess.updated_at = datetime.datetime.utcnow()
        if role == "user" and (not sess.name or sess.name == DEFAULT_NAME):
            text = content if isinstance(content, str) else ""
            sess.name = (text.strip()[:30] or DEFAULT_NAME)
    db.commit()


def _rejected_inline_image(exc: Exception) -> bool:
    """判断异常是否是「端点不接受 tool 消息里的图片内容块」。

    tool 消息携带图片是 DeepSeek 的扩展，OpenAI 标准里 tool 只能纯文本；
    换模型/换网关后可能变成 400，这里只做保守识别（响应体里提到 image）。
    """
    resp = getattr(exc, "response", None)
    body = getattr(resp, "text", "") or str(exc) or ""
    return "image" in body.lower()


def _truncate(text: str, limit: int = MAX_SUBTITLE_CHARS) -> str:
    """超长上下文裁剪：保留开头和结尾（正文精华通常在首尾），中间省略"""
    if len(text) <= limit:
        return text
    head = limit * 3 // 5
    tail = limit - head
    return text[:head] + "\n…（中间省略）…\n" + text[-tail:]


# ---------- 会话管理 API ----------
@router.get("/conversations")
def list_conversations(subject: str = Query("math"), db: Session = Depends(get_db)):
    """当前科目的所有会话，按 A/B/C 分组返回（一条 group-by 查计数，避免 N+1）"""
    rows = db.query(ChatSession).filter(ChatSession.subject == subject)\
        .order_by(ChatSession.updated_at.desc()).all()
    counts = dict(
        db.query(ChatMessage.conv_id, func.count(ChatMessage.id))
        .filter(ChatMessage.subject == subject)  # 只统计当前科目，缩小聚合范围
        .group_by(ChatMessage.conv_id).all()
    )
    groups = {"A": [], "B": [], "C": []}
    for s in rows:
        groups.setdefault(s.mode, []).append({
            "conversation_id": s.conv_id,
            "name": s.name or DEFAULT_NAME,
            "mode": s.mode,
            "subject": s.subject,
            "model": s.model or "",  # 会话级模型（空 = 全局）
            "effort": s.effort or "",  # 会话级思考强度（空 = 全局默认 high）
            "file_id": getattr(s, "file_id", 0) or 0,  # 绑定的文件（0 = 未绑定）
            "focus_folder_id": getattr(s, "focus_folder_id", 0) or 0,
            "focus_locked": getattr(s, "focus_locked", 0) or 0,
            "message_count": counts.get(s.conv_id, 0),
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })
    return groups


@router.post("/conversations")
def create_conversation(subject: str = Query("math"), mode: str = Query("A"),
                        file_id: int = Query(0), focus_folder_id: int = Query(0),
                        focus_locked: int = Query(0), db: Session = Depends(get_db)):
    """新建会话（可绑定当前查看的文件：上下文跟随文件，共识 D5）"""
    if mode not in MODE_NAMES:
        raise HTTPException(400, f"不支持的 Agent 模式: {mode}")
    # 动态科目（文件中心）：科目 = 托管库顶层文件夹名，不再限定固定四个
    subject = (subject or "").strip()[:50]
    if not subject:
        raise HTTPException(400, "科目不能为空")
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    sess = ChatSession(conv_id=conv_id, subject=subject, mode=mode, name=DEFAULT_NAME,
                       file_id=file_id or 0,
                       focus_folder_id=focus_folder_id or 0,
                       focus_locked=focus_locked or 0)
    db.add(sess)
    db.commit()
    return {"conversation_id": conv_id, "name": DEFAULT_NAME, "mode": mode,
            "subject": subject, "file_id": file_id or 0,
            "focus_folder_id": focus_folder_id or 0, "focus_locked": focus_locked or 0}


@router.patch("/conversations/{conv_id}")
def rename_conversation(conv_id: str, name: str = Query(...), db: Session = Depends(get_db)):
    """重命名会话"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.name = name[:255]
    db.commit()
    return {"conversation_id": conv_id, "name": sess.name}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, db: Session = Depends(get_db)):
    """删除会话及其全部消息"""
    db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id).delete()
    db.query(ChatSession).filter(ChatSession.conv_id == conv_id).delete()
    db.commit()
    return {"message": "已删除"}


@router.get("/{conv_id}/history")
def get_history(conv_id: str, db: Session = Depends(get_db)):
    """返回会话历史（用于前端重渲染）"""
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    msgs = db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id)\
        .order_by(ChatMessage.seq.asc()).all()
    items = []
    # 已被作答的 tool_call_id：前端据此区分历史卡片是「已作答」还是「当时未作答直接继续」
    answered_tcids = {m.tool_call_id for m in msgs
                      if m.role == "tool" and m.tool_call_id}
    # 「学生用图片作答」的轮次：tool 结果里带了 images（落盘路径列表），
    # 供历史卡片标注「含 N 张图片作答」
    answered_images = {}
    for m in msgs:
        if m.role == "tool" and m.tool_call_id:
            try:
                obj = json.loads(m.content) or {}
                if isinstance(obj, dict) and obj.get("images"):
                    answered_images[m.tool_call_id] = list(obj["images"])
            except Exception:
                pass
    for m in msgs:
        base = {"role": m.role, "reasoning": m.reasoning or "", "quote": m.quote or "",
                "tool_call_id": m.tool_call_id or ""}
        # ask 工具的回答轮：content 是 {"answers":[...],"images":[...]}，
        # 单独解析给前端渲染（图片作答的图也一并回显，走鉴权取图接口）
        if m.role == "tool":
            try:
                answers = (json.loads(m.content) or {}).get("answers") or []
            except Exception:
                answers = []
            ans_imgs = [u for u in (upload_public_url(p)
                                    for p in (answered_images.get(m.tool_call_id) or [])) if u]
            items.append({**base, "content": "", "answers": answers,
                          "has_image": bool(ans_imgs), "images": ans_imgs})
            continue
        content = m.content
        # 只有 user 消息可能是结构化的（带图消息存成数组）；
        # 其他内容一律当纯文本——json.loads 会把「7391」「2」这种纯数字正文解析成 int，
        # 前端拿到数字会在 (content||'').trim() 上直接报错（历史面板整个渲染不出来）
        if m.role == "user":
            try:
                parsed = json.loads(content) if content else None
                if isinstance(parsed, list):
                    content = parsed
            except Exception:
                pass
        if isinstance(content, list):  # 含图片的 user 消息（库里存的是相对路径）
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            imgs = [upload_public_url(p.get("image_url", {}).get("url", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "image_url"]
            imgs = [u for u in imgs if u]
            items.append({**base, "content": "\n".join(t for t in texts if t),
                          "has_image": bool(imgs), "images": imgs})
        else:
            items.append({**base, "content": content or "", "has_image": False, "images": []})
        # assistant 要求调用 ask 工具时，把问题一并返回（前端渲染历史卡片）
        if m.role == "assistant" and m.tool_calls and items:
            try:
                for c in json.loads(m.tool_calls):
                    tool_name = (c.get("function") or {}).get("name")
                    if tool_name in INTERACTIVE_TOOL_NAMES:
                        items[-1]["ask_questions"] = _parse_ask_questions(c)
                        # 前端据此选卡片文案（"需要你确认" vs "出题考你"）
                        items[-1]["ask_kind"] = "quiz" if tool_name == QUIZ_TOOL_NAME else "ask"
                        items[-1]["ask_answered"] = (c.get("id") or "") in answered_tcids
                        items[-1]["ask_images"] = len(answered_images.get(c.get("id") or "", []))
                        break
            except Exception:
                pass
    return {
        "conversation_id": conv_id,
        "name": sess.name if sess else DEFAULT_NAME,
        "mode": sess.mode if sess else "A",
        "model": sess.model if sess and sess.model else "",
        "effort": sess.effort if sess and sess.effort else "",
        "messages": items,
    }


@router.get("/context-preview")
def context_preview(file_id: int = Query(...), page: int = Query(0),
                    time: float = Query(0), db: Session = Depends(get_db)):
    """上下文实时预览（透明化层，共识 D5）：返回当前文件实际会注入的产物明细"""
    f = db.query(FileModel).filter(FileModel.id == file_id, FileModel.deleted == 0).first()
    if not f:
        raise HTTPException(404, "文件不存在")
    position = {"page": page} if page else ({"time": time} if time else None)
    ctx = build_file_context(f, position)
    return {
        "file_id": file_id, "file_name": f.name, "kind": f.kind, "status": f.status,
        "enabled": ctx["enabled"], "components": ctx["components"],
        "total_chars": len(ctx["text"]),
        "text_preview": ctx["text"][:2000],
    }


@router.get("/uploads/{name}")
def get_upload_image(name: str):
    """取用户上传的图片（历史消息回显用）。

    上传目录没有静态挂载（不把用户作业图暴露成免鉴权静态资源），
    统一走这里：/api/chat/** 在鉴权中间件覆盖范围内，前端按既有约定
    追加 ?token= 即可（与视频流同一套做法）。
    """
    safe = Path(str(name or "")).name            # 只取文件名，挡掉目录穿越
    path = (Path(UPLOAD_DIR) / safe)
    try:
        path = path.resolve()
    except Exception:
        raise HTTPException(404, "图片不存在")
    base = Path(UPLOAD_DIR).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(str(path))


@router.post("/conversations/{conv_id}/focus")
def set_conversation_focus(conv_id: str, folder_id: int = Query(0),
                           locked: int = Query(1), db: Session = Depends(get_db)):
    """设置/取消会话的聚焦文件夹（沙箱作用域）。
    folder_id=0 → 取消锁定，恢复「跟随当前文件」；locked=0 也等同解除锁定。
    """
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    if folder_id and not db.query(Folder).filter(
            Folder.id == folder_id, Folder.deleted == 0).first():
        raise HTTPException(404, "文件夹不存在")
    sess.focus_folder_id = folder_id or 0
    sess.focus_locked = 1 if (folder_id and locked) else 0
    db.commit()
    fld = db.query(Folder).filter(Folder.id == folder_id).first() if folder_id else None
    return {"conversation_id": conv_id, "focus_folder_id": sess.focus_folder_id,
            "focus_locked": sess.focus_locked,
            "focus_folder_name": fld.name if fld else ""}


@router.get("/conversations/{conv_id}/focus")
def get_conversation_focus(conv_id: str, db: Session = Depends(get_db)):
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    fid = getattr(sess, "focus_folder_id", 0) or 0
    fld = db.query(Folder).filter(Folder.id == fid).first() if fid else None
    return {"conversation_id": conv_id, "focus_folder_id": fid,
            "focus_locked": getattr(sess, "focus_locked", 0) or 0,
            "focus_folder_name": fld.name if fld else ""}


@router.post("/conversations/{conv_id}/model")
def set_conversation_model(conv_id: str, model: str = Query(...),
                           db: Session = Depends(get_db)):
    """会话级模型切换：只改当前会话用的模型（不碰全局设置），发送即生效"""
    model = (model or "").strip()[:120]
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.model = model
    db.commit()
    return {"conversation_id": conv_id, "model": sess.model}


@router.post("/conversations/{conv_id}/effort")
def set_conversation_effort(conv_id: str, effort: str = Query(...),
                            db: Session = Depends(get_db)):
    """会话级思考强度切换：只改当前会话（不碰全局设置），发送即生效"""
    e = (effort or "").strip()[:20]
    if e and e not in ("low", "medium", "high", "xhigh", "max"):
        raise HTTPException(400, f"不支持的思考强度: {effort}")
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.effort = e
    db.commit()
    return {"conversation_id": conv_id, "effort": sess.effort}


# ---------- 聊天（SSE 流式） ----------
def _sse(payload: dict) -> str:
    """序列化一条 SSE 事件：data: {json}\n\n"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/send-stream")
async def send_message_stream(
    query: str = Form(""),
    subject: str = Form("math"),
    mode: str = Form("A"),
    subtitle_context: str = Form(""),
    conversation_id: str = Form(""),
    quote: str = Form(""),  # 引用内容（JSON 字符串 {"text": "..."}，用户引用 AI 回答某段特别说明）
    image: List[UploadFile] = File(None),  # 支持多图（前端同名多字段 append，最多 4 张）
    watched_video_ids: str = Form(""),  # 模式B：勾选的视频ID（JSON 数组字符串）
    file_id: int = Form(0),   # 文件中心：当前查看的文件（上下文跟随文件，共识 D5）
    position: str = Form(""),  # 查看位置 JSON：{"page": 12} 或 {"time": 834.5}
    # 沙箱分级（共识 Q4b）：聚焦文件夹；focus_locked=1 表示用户手动锁定（播放器切换不改变范围）
    focus_folder_id: int = Form(0),
    focus_locked: int = Form(0),
    # ask 工具回答（JSON 字符串 {"tool_call_id": "...", "answers": [{"id","selected","custom"}]}）：
    # 非空表示这一轮是"回答 AI 的提问"，后端把它作为 tool 结果续上继续推理
    ask_answers: str = Form(""),
    db: Session = Depends(get_db),
):
    """发送消息（可选多张图片），SSE 流式返回 AI 回答。

    事件序列：
      data: {"type":"reasoning","text":"思维链片段"} （可多次，正文前或与正文交替）
      data: {"type":"delta","text":"正文片段"}       （可多次）
      data: {"type":"ask","tool_call_id":"...","kind":"ask|quiz","questions":[...],"text":"前言"}
      data: {"type":"done","conversation_id":...}    （正常结束）
      data: {"type":"error","detail":"..."}          （出错时替代 done）
    """
    # 动态科目（文件中心）：任意非空科目名（= 顶层文件夹名），AI 配置全局共享
    subject = (subject or "").strip()[:50]
    if not subject:
        raise HTTPException(400, "科目不能为空")
    if mode not in MODE_NAMES:
        raise HTTPException(400, f"不支持的 Agent 模式: {mode}")
    if not (query or "").strip() and not image and not (ask_answers or "").strip():
        raise HTTPException(400, "消息不能为空")

    # 引用解析：前端传 {"text": "原文"}，取 text 截断（防上下文膨胀）
    quote_text = ""
    if quote and quote.strip():
        try:
            qobj = json.loads(quote)
            quote_text = str(qobj.get("text") or "").strip()[:500]
        except Exception:
            quote_text = quote.strip()[:500]
    quote_prefix = f"[用户引用了你的回答：「{quote_text}」]\n" if quote_text else ""

    # 图片 → 落盘（先存文件，再把图直接内联进本轮消息：模型当轮就能看到图，
    # 不再靠「给模型一个路径、让它自己调工具取图」——那会把一轮问答拖成多轮工具调用）
    if len(image or []) > 4:
        raise HTTPException(400, "一次最多上传 4 张图片")
    image_paths: List[str] = []
    if image:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in image[:4]:  # 服务端防御上限
            data = await f.read()
            if not data:
                continue
            ext = (f.filename or "").split(".")[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
                ext = "jpeg"
            name = f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.{ext}"
            path = UPLOAD_DIR / name
            path.write_bytes(data)
            image_paths.append(to_rel(path))

    wids = []
    if watched_video_ids and watched_video_ids.strip():
        try:
            wids = [int(x) for x in watched_video_ids.strip().strip("[]").split(",") if x.strip()]
        except ValueError:
            wids = []

    conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    # 确保会话行存在并取其当前值（新建+旧行一次查询搞定，供后续复用）
    sess_row = _ensure_session(db, conv_id, subject, mode)

    # ---- 文件中心：会话绑定当前文件（上下文跟随查看器） ----
    bound_file = None
    file_position = None
    if (position or "").strip():
        try:
            file_position = json.loads(position)
        except Exception:
            file_position = None
    if file_id:
        bound_file = db.query(FileModel).filter(FileModel.id == file_id, FileModel.deleted == 0).first()
        if not bound_file:
            raise HTTPException(404, "绑定的文件不存在")
        if sess_row and getattr(sess_row, "file_id", 0) != file_id:
            sess_row.file_id = file_id
            db.commit()

    # ---- 沙箱分级：聚焦文件夹（共识 Q4b：跟随+可锁定） ----
    # 锁定时用前端给的 folder_id；未锁定跟随当前文件所属文件夹；若会话已锁定则保持
    focus_folder = 0
    if focus_locked:
        focus_folder = focus_folder_id or 0
        if sess_row:
            sess_row.focus_folder_id = focus_folder
            sess_row.focus_locked = 1
            db.commit()
    else:
        focus_folder = bound_file.folder_id if bound_file else 0
        if sess_row and getattr(sess_row, "focus_locked", 0):
            focus_folder = sess_row.focus_folder_id or 0   # 已被用户锁定，保持
        elif sess_row:
            sess_row.focus_folder_id = focus_folder or 0
            sess_row.focus_locked = 0
            db.commit()

    # 会话级模型：会话行有 model 则用它，否则用全局主模型（llm_service 内部兜底）
    conv_model = sess_row.model.strip() if sess_row and sess_row.model else ""
    conv_effort = sess_row.effort.strip() if sess_row and sess_row.effort else ""

    # ---- 先判定是不是 ask/quiz 作答轮（决定历史里末尾悬空 tool_calls 的取舍）----
    ask_round = bool(ask_answers and ask_answers.strip())
    ask_tcid = ""
    ask_list = None
    ask_tool_text = ""       # 作答轮的 tool 结果文本（落库用，含图片路径）
    ask_images_inline = False  # 本轮的图片块是否已内联进 history（供降级重试用）
    if ask_round:
        try:
            ask_obj = json.loads(ask_answers)
        except Exception:
            raise HTTPException(400, "ask_answers 不是合法 JSON")
        ask_tcid = str(ask_obj.get("tool_call_id") or "")
        ask_list = ask_obj.get("answers") or []
        if not ask_tcid or not isinstance(ask_list, list):
            raise HTTPException(400, "ask_answers 缺少 tool_call_id 或 answers")

    # 从 DB 载入历史（含 reasoning 回填限制），保证重启后上下文不丢。
    # keep_dangling：只有作答轮才保留末尾「等待补 tool 结果」的 assistant，
    # 否则用户跳过卡片直接发新消息时，悬空的 tool_calls 会触发接口 400。
    history = _load_messages(db, conv_id, keep_dangling=ask_round)

    # ---- ask/quiz 作答轮：把用户作答作为 tool 结果续上（不再追加 user 消息）----
    if ask_round:
        last = history[-1] if history else None
        matched = bool(last and last.get("role") == "assistant"
                       and any((tc.get("id") or "") == ask_tcid
                               for tc in (last.get("tool_calls") or [])))
        if not matched:
            raise HTTPException(400, "提问卡片已失效（历史已被截断），请重新提问")
        # 学生可以用图片作答（拍照交作业）。分两层落数据：
        #   - 落库：只存文本（含图片路径）。几 MB 的 base64 灌进 chat_messages 会让库迅速膨胀，
        #     且图片本来就在 storage/uploads 里；历史重载后模型仍可用 read 工具按路径重看图。
        #   - 本次请求：把图片块内联进 tool 结果，让模型这一轮就直接看到（省一轮往返）。
        # 注意：content 必须是「合法 JSON」——历史接口靠 json.loads 取 answers/images，
        # 额外说明写成 JSON 里的字段，不能拼在 JSON 后面（拼后面会让 json.loads 整段失败）
        tool_payload = {"answers": ask_list}
        if image_paths:
            tool_payload["images"] = image_paths
            tool_payload["note"] = (
                "学生用图片作答：以上问题的答案在随附的 %d 张图片里。请仔细读图后逐题批改；"
                "看不清、拍糊或没拍全的地方直接说不确定，不要猜测。" % len(image_paths))
        ask_tool_text = json.dumps(tool_payload, ensure_ascii=False)
        _append_message(db, conv_id, subject, mode, "tool", ask_tool_text,
                        tool_call_id=ask_tcid)
        history.append({"role": "tool", "tool_call_id": ask_tcid,
                        "content": ask_tool_text})
        if image_paths:
            blocks = []
            for rel in image_paths[:4]:
                try:
                    blocks.append(upload_image_block(rel))
                except Exception:
                    continue  # 单张不合格（超大/格式不对）就跳过，不让整个作答轮失败
            if blocks:
                history[-1]["content"] = [{"type": "text", "text": ask_tool_text}] + blocks
                ask_images_inline = True

    # ---- 构建用户消息：文本（+ 引用）+ 直接内联上传的图片 ----
    # 落库只写「文本 + 相对路径」（图块里的 base64 动辄几百 KB，写库会让 chat_messages 迅速膨胀）；
    # 真正发给模型的 history 里再循环把每张图展开成 image_url 图块。
    if not ask_round:
        user_text = quote_prefix + query
        stored_content = user_text
        if image_paths:
            stored_content = [{"type": "text", "text": user_text}] + [
                {"type": "image_url", "image_url": {"url": rel}} for rel in image_paths
            ]
        _append_message(db, conv_id, subject, mode, "user", stored_content,
                        quote=quote_text, _sess=sess_row)
        if image_paths:
            blocks = []
            for rel in image_paths[:4]:          # ← 循环：每张图都附上，不只第一张
                try:
                    blocks.append(upload_image_block(rel))
                except Exception:
                    continue                     # 单张不合格（超大/格式不对）就跳过
            # 文本段为空时（只发了图）仍保留 text 块，避免部分兼容端点不接受纯图消息
            content = [{"type": "text", "text": user_text}] + blocks
            history.append({"role": "user", "content": content})
        else:
            history.append({"role": "user", "content": user_text})

    # ---- 上下文编排（共识 D5）：预编排保下限 ----
    # file_id 优先：后端按文件类型模板拼装产物（总结/摘录/当前位置），
    # 前端不再传字幕全文；无文件时沿用旧的 subtitle_context（模式A 前端拼字幕）。
    extra_tools = []
    if bound_file is not None:
        ctx = build_file_context(bound_file, file_position)
        if ctx["enabled"] and ctx["text"]:
            header = '\n--- 当前学习文件：' + bound_file.name + '（' + bound_file.kind + '） ---'
            subtitle_context = _truncate(header + "\n" + ctx["text"])
        # 沙箱工具保上限：让模型自己翻当前文件的产物（作用域锁死 artifacts/{file_id}/）
        extra_tools = [READ_ARTIFACT_TOOL, LIST_ARTIFACTS_TOOL, GEN_CHAPTER_TOOL, LIST_FILES_TOOL]

    # 模式B：勾选视频的上下文——优先用「课程总结」（高密度、跨多节不爆上下文），
    # 没有总结的视频回退字幕全文；总量仍由 _truncate 兜底（1M 上下文下基本不触发）
    if mode == 'B' and wids:
        videos = db.query(Video).filter(
            Video.id.in_(wids), Video.subject == subject).all()
        if videos:
            parts = []
            for v in videos:
                header = f'\n--- 视频 {v.id}: {v.title or v.filename} ---'
                summary = ""
                if v.summary_status == "done" and v.summary_path:
                    sm = resolve(v.summary_path)
                    if sm:
                        try:
                            summary = sm.read_text(encoding="utf-8")
                        except Exception:
                            summary = ""
                if summary.strip():
                    parts.append(f"{header}（课程总结）\n{summary.strip()}")
                else:
                    subs = db.query(Subtitle).filter(Subtitle.video_id == v.id)\
                        .order_by(Subtitle.seq).all()
                    if subs:
                        parts.append(f"{header}（无总结，附字幕全文）\n"
                                     + "\n".join(s.text for s in subs if s.text))
            if parts:
                subtitle_context = _truncate('\n'.join(parts))

    async def event_gen():
        try:
            round_no = 0
            ask_fallback_done = False   # 内联图片被端点拒后，只降级重试一次
            while round_no < MAX_TOOL_ROUNDS:
                round_no += 1
                reasoning_acc = []
                content_acc = []
                tool_calls_json = ""
                try:
                    async for kind, text in chat_completion_stream(
                        messages=history,
                        subject=subject,
                        mode=mode,
                        subtitle_context=subtitle_context,
                        model_override=conv_model,
                        effort=conv_effort,
                    ):
                        if kind == "reasoning":
                            reasoning_acc.append(text)
                            # 思维链实时推给前端做"深度思考中"展示（独立于正文，不混入回答）
                            yield _sse({"type": "reasoning", "text": text})
                        elif kind == "tool_calls":
                            tool_calls_json = text  # 完整参数在流末尾一次性给出
                        else:
                            content_acc.append(text)
                            yield _sse({"type": "delta", "text": text})
                except Exception as e:
                    # 端点拒收 tool 消息里的图片块（DeepSeek 扩展，非 OpenAI 标准）时降级：
                    # 换回纯文本 tool 结果重试本轮，让模型改成自己调 read 取图，
                    # 而不是把整个「图片作答」轮直接报错。（已产出过半则不再重试，避免重复输出）
                    if (ask_images_inline and not ask_fallback_done
                            and not content_acc and not reasoning_acc
                            and _rejected_inline_image(e)):
                        ask_fallback_done = True
                        history[-1] = {"role": "tool", "tool_call_id": ask_tcid,
                                       "content": ask_tool_text}
                        round_no -= 1   # 降级重试不占用工具轮次
                        continue
                    raise

                answer = "".join(content_acc)
                reasoning = "".join(reasoning_acc)

                # ---- 没有工具调用：这就是最终回答 ----
                if not tool_calls_json:
                    if not answer:
                        yield _sse({"type": "error", "detail": "AI 返回为空"})
                        return
                    # 公式形态硬约束：落库前统一为 $ 格式（前端渲染的形态）
                    answer = normalize_math(answer)
                    # 只有在完整拿到回答后才写入历史（中途断流不落库，前端可重发）
                    _append_message(db, conv_id, subject, mode, "assistant", answer,
                                    reasoning=reasoning)
                    yield _sse({"type": "done", "conversation_id": conv_id})
                    return

                try:
                    calls = json.loads(tool_calls_json)
                except Exception:
                    calls = []
                if not calls:
                    yield _sse({"type": "error", "detail": "工具调用参数解析失败"})
                    return

                # ---- ask / quiz 工具：要等用户作答，本轮到此为止（前端提交后再续）----
                ask_calls = [c for c in calls
                             if (c.get("function") or {}).get("name") in INTERACTIVE_TOOL_NAMES]
                if ask_calls:
                    call = ask_calls[0]
                    fn_name = (call.get("function") or {}).get("name")
                    questions = _parse_ask_questions(call)
                    # 同轮的其他工具调用拿不到结果，落库时只保留这一个，
                    # 否则历史里 tool_calls 与 tool 结果对不上（API 400）
                    only_ask = json.dumps([call], ensure_ascii=False)
                    answer = normalize_math(answer)
                    _append_message(db, conv_id, subject, mode, "assistant", answer,
                                    reasoning=reasoning, tool_calls=only_ask)
                    yield _sse({"type": "ask", "tool_call_id": call.get("id") or "",
                                "kind": "quiz" if fn_name == QUIZ_TOOL_NAME else "ask",
                                "questions": questions, "text": answer})
                    yield _sse({"type": "done", "conversation_id": conv_id})
                    return

                # ---- 视觉工具：服务端自己就能执行，执行完带着结果继续让模型作答 ----
                if round_no >= MAX_TOOL_ROUNDS:
                    yield _sse({"type": "error", "detail": "工具调用层数过深，已中止"})
                    return
                _append_message(db, conv_id, subject, mode, "assistant",
                                normalize_math(answer), reasoning=reasoning,
                                tool_calls=tool_calls_json)
                assistant_msg = {"role": "assistant", "content": answer, "tool_calls": calls}
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                history.append(assistant_msg)
                for call in calls:
                    fn = call.get("function") or {}
                    tool_name = fn.get("name") or ""
                    try:
                        tool_args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        tool_args = {}
                    yield _sse({"type": "tool", "name": tool_name, "status": "running"})
                    try:
                        result = await execute_tool_call(tool_name, tool_args,
                                                         file_id=file_id if bound_file is not None else 0)
                        failed = False
                    except Exception as e:
                        result = f"[工具执行失败：{e}]"
                        failed = True
                    stored = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                    _append_message(db, conv_id, subject, mode, "tool", stored,
                                    tool_call_id=call.get("id") or "")
                    history.append({"role": "tool", "tool_call_id": call.get("id") or "",
                                    "content": result})
                    yield _sse({"type": "tool", "name": tool_name,
                                "status": "failed" if failed else "done"})
            yield _sse({"type": "error", "detail": "工具调用层数过深，已中止"})
        except HTTPException as e:
            yield _sse({"type": "error", "detail": e.detail})
        except Exception as e:
            import httpx
            detail = f"AI 调用失败: {str(e)}"
            resp_body = getattr(e, "response", None) or getattr(e, "message", None)
            if isinstance(e, httpx.HTTPStatusError):
                resp_body = e.response
            if resp_body is not None and hasattr(resp_body, "text"):
                try:
                    body = resp_body.text[:800]
                    if body:
                        detail += f" | 接口原始响应: {body}"
                except Exception:
                    pass
            yield _sse({"type": "error", "detail": detail})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
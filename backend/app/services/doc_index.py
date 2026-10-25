# -*- coding: utf-8 -*-
"""文档/视频索引服务 — 章节树、关键词检索、分层总结、LLM 调用闸门

设计共识（2026-02）：
- 索引层零成本：章节树来自 PDF 书签(get_toc) → 字号聚类启发式（两级：章/节），
  PPT 特判按「第X章 / 第X讲」编号归并；chunk 带页码；关键词检索用中文字符 bigram。
- 总结层花钱但克制：文档总览用「章节标题 + 每章抽样头尾」生成（1 次调用）；
  章节摘要懒生成（首次问到才生成并缓存）；科目地图由各文档总览合并（增量重建）。
- 闸门：单文档自动 LLM 调用 ≤ MAX_LLM_CALLS_PER_DOC，超出需手动点「继续生成」。
- 视频纳入同一套：视频=1 章，内部按字幕时间每 ~12 分钟切 1 节（零成本）。

产物布局（storage/artifacts/{file_id}/）：
    outline.json         章节树（可编辑）
    overview.md          文档总览（≈600-900 字）
    summary.md           同 overview（兼容旧字段/旧前端）
    chap_{cid}.md        章节摘要（懒生成）
    llm_calls.json       LLM 调用台账（闸门用）
    snap_p{N}.jpg        页码截图缓存
subject 地图：storage/subject_maps/{subject}.md
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.config import settings
from ..core.paths import to_abs

# ================= 常量 =================

MAX_LLM_CALLS_PER_DOC = 20      # 单文档自动 LLM 调用上限（超出需手动「继续生成」）
OVERVIEW_SAMPLE_CHARS = 200     # 总览抽样：每章头尾各取多少字
VIDEO_SECTION_SEC = 720         # 视频每节时长（12 分钟）

# 注入预算（字符）——见共识 Q16「精简预算 ~7000 字」
BUDGET = {
    "subject_map": 400,
    "overview": 800,
    "toc": 600,
    "chapter_path": 100,
    "chapter_summary": 1500,
    "page_text": 800,
    "neighbor_pages": 800,
    "hit_each": 600,
    "hits": 3,
}


def _art(file_id: int) -> Path:
    return Path(settings.artifacts_dir) / str(file_id)


def _read_json(file_id: int, name: str, default=None):
    p = _art(file_id) / name
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(file_id: int, name: str, obj):
    d = _art(file_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_text(file_id: int, name: str) -> str:
    p = _art(file_id) / name
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def read_artifact(file_id: int, name: str) -> str:
    return _read_text(file_id, name)


def write_text(file_id: int, name: str, content: str):
    _write_text(file_id, name, content)


def _write_text(file_id: int, name: str, content: str):
    d = _art(file_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(content, encoding="utf-8")


# ================= LLM 调用闸门 =================

def llm_calls(file_id: int) -> List[dict]:
    return _read_json(file_id, "llm_calls.json", []) or []


def llm_gate_ok(file_id: int, kind: str, manual: bool = False) -> bool:
    """是否允许再花一次 LLM。manual=True（用户点按钮）不受自动上限约束。"""
    if manual:
        return True
    return len([c for c in llm_calls(file_id) if c.get("auto")]) < MAX_LLM_CALLS_PER_DOC


def llm_record(file_id: int, kind: str, auto: bool = True):
    calls = llm_calls(file_id)
    calls.append({
        "kind": kind, "auto": bool(auto),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    })
    _write_json(file_id, "llm_calls.json", calls)


def llm_usage(file_id: int) -> dict:
    calls = llm_calls(file_id)
    auto = [c for c in calls if c.get("auto")]
    return {
        "total": len(calls), "auto": len(auto),
        "limit": MAX_LLM_CALLS_PER_DOC,
        "remaining": max(0, MAX_LLM_CALLS_PER_DOC - len(auto)),
        "by_kind": {k: len([c for c in calls if c.get("kind") == k])
                    for k in {c.get("kind") for c in calls}},
    }


# ================= 章节树：PDF 书签 → 字号启发式 =================

_CN_NUM = "一二三四五六七八九十百零〇0-9１-９"
# 「第X章」「第X讲」「第X节」「Chapter N」等编号标题
_NUM_HEAD = re.compile(
    rf"^(第\s*[{_CN_NUM}]+\s*[章讲部篇节单元]|Chapter\s*\d+|Lesson\s*\d+|\d+(\.\d+)+\s|\d+[、.．])",
    re.IGNORECASE)


def _clean_title(t: str, limit: int = 80) -> str:
    t = re.sub(r"\s+", " ", (t or "").strip())
    t = re.sub(r"^[·•\-—–_]+\s*", "", t)
    return t[:limit]


_GENERIC_TOC = re.compile(
    r"^(幻灯片|slide|page|第\s*\d+\s*页|Untitled|untitled)?\s*\d*$", re.IGNORECASE)


def _toc_from_bookmarks(pdf_path: Path) -> List[dict]:
    """PDF 书签目录 → 章节列表（免费且精确）。无书签返回 []。

    LibreOffice 转 PPT 会自动生成「幻灯片 1..N」这类无信息书签——这种不算有效目录，
    必须丢弃退化到字号启发式（实测 199 号文件就踩了这个坑：55 个"幻灯片 N"书签
    全部被当成真目录，章节碎成 55 个"幻灯片"）。
    """
    try:
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            toc = doc.get_toc(simple=True) or []
    except Exception:
        return []
    toc = [t for t in toc if not _GENERIC_TOC.match(_clean_title(t[1] or ""))]
    if len(toc) < 2:
        return []
    out = []
    for level, title, page in toc:
        title = _clean_title(title)
        if not title:
            continue
        out.append({
            "level": max(1, min(int(level or 1), 3)),
            "title": title,
            "start": max(1, int(page or 1)),
            "end": max(1, int(page or 1)),
        })
    # 用下一个同级/上级的起始页作为本节结束页
    for i, ch in enumerate(out):
        nxt = None
        for j in range(i + 1, len(out)):
            if out[j]["level"] <= ch["level"]:
                nxt = out[j]["start"]
                break
        ch["end"] = max(ch["start"], (nxt - 1) if nxt and nxt - 1 >= ch["start"] else ch["start"])
    return out


def _page_titles(pdf_path: Path) -> List[dict]:
    """逐页取「最大字号的一行」作为标题候选：返回 [{page, size, text, body_size}]"""
    import fitz
    per_page = []
    size_counter: Dict[float, int] = {}
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, 1):
            try:
                d = page.get_text("dict")
            except Exception:
                per_page.append({"page": pno, "size": 0.0, "text": ""})
                continue
            best = (0.0, "")
            for block in d.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                    if not text:
                        continue
                    size = max((s.get("size", 0) for s in line.get("spans", [{}])), default=0)
                    # 统计正文字号（字数多的行才计入，避免标题污染）
                    if len(text) >= 12:
                        key = round(float(size), 1)
                        size_counter[key] = size_counter.get(key, 0) + len(text)
                    if size > best[0] and len(text) >= 2:
                        best = (float(size), text)
            per_page.append({"page": pno, "size": best[0], "text": _clean_title(best[1])})
    body = max(size_counter.items(), key=lambda kv: kv[1])[0] if size_counter else 0.0
    for p in per_page:
        p["body_size"] = body
    return per_page


def build_outline(pages: List[dict], pdf_path: Optional[Path] = None,
                  kind: str = "pdf") -> dict:
    """构建章节树。优先书签，退化到字号启发式。

    返回 {"source": "toc|heuristic|whole", "unit": "page", "chapters": [...]}
    章节字段：id, parent_id, level, title, start, end
    """
    total = max(1, len(pages))

    # 1) 书签（过滤「幻灯片 N」这类无信息书签）
    if pdf_path and Path(pdf_path).is_file():
        toc = _toc_from_bookmarks(Path(pdf_path))
        if len(toc) >= 2:
            return _normalize(toc, "toc", default_end=total)

    # 2) 字号启发式：把【页面级标题候选】转成【区间级章节】
    if pdf_path and Path(pdf_path).is_file():
        try:
            cand = _page_titles(Path(pdf_path))
        except Exception:
            cand = []
        heads = _pick_headings(cand)
        if heads:
            # page → start；end = 同级下一个的 start-1（_normalize 里也会补一次）
            items = []
            for h in heads:
                items.append({
                    "level": h.get("_lvl", 1),
                    "title": h.get("text", ""),
                    "start": h.get("page", 1),
                    "end": h.get("page", 1),
                })
            if items:
                return _normalize(items, "heuristic", default_end=total)

    # 3) 全文单章
    return _normalize([{"level": 1, "title": "全文", "start": 1, "end": total}], "whole",
                      default_end=total)


def _pick_headings(cand: List[dict]) -> List[dict]:
    """从「每页最大字号行」里挑出标题，并按字号聚成两级（章/节）。

    关键难点（共识 Q13）：PPT 每页都有大标题。若把所有标题都当章，55 页会碎成 55 章。
    对策：① 只有「明显大于正文」（>body*1.25）的才算候选标题；
          ② 含「第X章/第X讲」等编号的强制为一级（章）；
          ③ 其余候选按字号最大簇是否够大分成两级；只有一级时全部归为二级（节），
            自动挂到前面最近的编号章下；没有编号章则按页数等分成若干「块」作为章。
    """
    if not cand:
        return []
    body = cand[0].get("body_size") or 0.0
    thresh = max(body * 1.25, 8.0)
    heads = [c for c in cand
             if c["text"] and c["size"] >= thresh and len(c["text"]) >= 2
             and c.get("page", 0) > 1]   # 第 1 页通常是封面，跳过
    if not heads:
        return []

    # 编号标题 → 一级
    numbered = [h for h in heads if _NUM_HEAD.match(h["text"])]
    for h in numbered:
        h["_lvl"] = 1

    rest = [h for h in heads if "_lvl" not in h]
    if rest:
        sizes = sorted({round(h["size"], 1) for h in rest}, reverse=True)
        # 最大字号显著大于次大 → 两簇；否则只有一簇
        if len(sizes) >= 2 and sizes[0] >= sizes[1] * 1.15:
            big = sizes[0]
            for h in rest:
                h["_lvl"] = 1 if round(h["size"], 1) >= big else 2
        else:
            for h in rest:
                h["_lvl"] = 2

    # 没有任何一级 → 把所有节等分成 3~6 个虚拟章
    if not any(h["_lvl"] == 1 for h in heads):
        total = len(cand) or 1
        n_ch = max(1, min(6, round(total / 12) or 1))
        step = max(1, total // n_ch)
        synth = []
        for i in range(n_ch):
            s = i * step + 1
            e = total if i == n_ch - 1 else (i + 1) * step
            synth.append({"level": 1, "title": f"第 {i + 1} 部分（自动划分）", "start": s, "end": e})
            # 该区间内的节挂到其下
            for h in heads:
                if s <= h["page"] <= e:
                    h["_parent_hint"] = f"第 {i + 1} 部分（自动划分）"
        return synth + heads  # _normalize 会按 start 排序并重建层级

    # 有序输出，节会在 _normalize 里挂到前面最近的章下
    heads.sort(key=lambda h: h["page"])
    return heads


def _normalize(items: List[dict], source: str, default_end: int = 0) -> dict:
    """把 [{level,title,start,end}] 归一成两级树，分配 cid/parent_id。"""
    items = sorted(items, key=lambda x: (x.get("start", 1), x.get("level", 1)))
    chapters: List[dict] = []
    last_h1 = None
    for it in items:
        lvl = int(it.get("_lvl") or it.get("level") or 1)
        if it.get("_parent_hint"):
            lvl = 2
        title = _clean_title(it.get("title", ""))
        cid = f"c{len(chapters) + 1}"
        node = {
            "id": cid,
            "parent_id": None,
            "level": 1 if lvl == 1 else 2,
            "title": title,
            "start": int(it.get("start") or 1),
            "end": int(it.get("end") or it.get("start") or 1),
        }
        if node["level"] == 1:
            last_h1 = node
        else:
            node["parent_id"] = last_h1["id"] if last_h1 else None
            if last_h1 is None:
                node["level"] = 1
                last_h1 = node
        # 空/过短标题（如 PPT 转换里「一、」「2.」这种纯序号行）：不单独成节，
        # 把序号并入所属章标题，同时把章的起始页提前到它（章节真正开始的地方）
        if node["level"] == 2 and len(node["title"]) <= 2:
            if last_h1 is not None and last_h1 is not node:
                if len(last_h1["title"]) <= 2 and node["title"]:
                    last_h1["title"] = (last_h1["title"] + node["title"])[:60]
                last_h1["start"] = min(last_h1["start"], node["start"])
            continue
        chapters.append(node)

    # 同页重复标题去重（PPT 常见：字号同簇时同一行被取两次）
    seen = set()
    dedup = []
    for ch in chapters:
        key = (ch["start"], ch["title"])
        if key in seen and ch["title"]:
            continue
        seen.add(key)
        dedup.append(ch)
    chapters = dedup

    # 结束位置：同级下一个节点的起点前；末节点用 default_end 或自身
    for i, ch in enumerate(chapters):
        nxt = None
        for j in range(i + 1, len(chapters)):
            if chapters[j]["level"] <= ch["level"]:
                nxt = chapters[j]["start"]
                break
        if nxt and nxt - 1 >= ch["start"]:
            ch["end"] = nxt - 1
        elif default_end:
            ch["end"] = max(ch["start"], default_end)

    return {"source": source, "unit": "page", "chapters": chapters,
            "built_at": datetime.datetime.now().isoformat(timespec="seconds")}


# ---------- 视频章节（按字幕时间切节，零成本） ----------

def build_video_outline(video_id: int, duration: float,
                        subtitles: List[Tuple[float, str]] = None) -> dict:
    """视频 = 1 章，内部按 ~12 分钟切节。subtitles=[(start_sec, text)] 用于命名小节。"""
    title = "全课"
    chapters = [{"id": "c1", "parent_id": None, "level": 1, "title": title,
                 "start": 0, "end": int(duration or 0), "unit": "sec"}]
    if duration and duration > VIDEO_SECTION_SEC * 1.2:
        n = int(duration // VIDEO_SECTION_SEC) + (1 if duration % VIDEO_SECTION_SEC else 0)
        for i in range(n):
            s = i * VIDEO_SECTION_SEC
            e = min(int(duration), (i + 1) * VIDEO_SECTION_SEC)
            # 用该段第一句字幕做名字提示
            label = ""
            if subtitles:
                for st, tx in subtitles:
                    if s <= st < e and tx.strip():
                        label = _clean_title(tx, 24)
                        break
            chapters.append({
                "id": f"c{i + 2}", "parent_id": "c1", "level": 2,
                "title": f"{_fmt_sec(s)}–{_fmt_sec(e)}" + (f" {label}" if label else ""),
                "start": s, "end": e, "unit": "sec",
            })
    return {"source": "video", "unit": "sec", "chapters": chapters,
            "built_at": datetime.datetime.now().isoformat(timespec="seconds")}


def _fmt_sec(sec: float) -> str:
    m, s = int(sec // 60), int(sec % 60)
    return f"{m:02d}:{s:02d}"


# ---------- 读写章节树 ----------

def get_outline(file_id: int) -> Optional[dict]:
    return _read_json(file_id, "outline.json")


def save_outline(file_id: int, outline: dict):
    outline["edited_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write_json(file_id, "outline.json", outline)


def chapter_at(outline: dict, unit_value: float) -> Tuple[Optional[dict], Optional[dict], str]:
    """给定页码/秒数 → (所属章, 所属节, 路径文本)"""
    if not outline:
        return None, None, ""
    chapters = outline.get("chapters") or []
    hit_section = None
    for ch in chapters:
        if ch["level"] == 2 and ch["start"] <= unit_value <= ch["end"]:
            hit_section = ch
            break
    hit_chapter = None
    for ch in chapters:
        if ch["level"] == 1 and ch["start"] <= unit_value <= ch["end"]:
            hit_chapter = ch
            break
    if hit_section:
        pid = hit_section.get("parent_id")
        hit_chapter = next((c for c in chapters if c["id"] == pid), hit_chapter)
    path = " > ".join(x["title"] for x in (hit_chapter, hit_section) if x)
    return hit_chapter, hit_section, path


def outline_toc_text(outline: dict, limit_chars: int = 600) -> str:
    """大纲的纯文本形式（注入用）"""
    if not outline:
        return ""
    lines = []
    unit = outline.get("unit", "page")
    for ch in outline.get("chapters") or []:
        if ch["level"] == 1:
            loc = f"p{ch['start']}" if unit == "page" else _fmt_sec(ch["start"])
            lines.append(f"[{loc}] {ch['title']}")
        else:
            loc = f"p{ch['start']}" if unit == "page" else _fmt_sec(ch['start'])
            lines.append(f"    - {ch['title']} ({loc})")
    text = "\n".join(lines)
    return text[:limit_chars]


# ================= 关键词检索（中文字符 bigram，零依赖） =================

def _bigrams(s: str) -> List[str]:
    s = re.sub(r"\s+", "", s or "")
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", s)
    if len(s) < 2:
        return [s] if s else []
    return [s[i:i + 2] for i in range(len(s) - 1)]


def search_chunks(file_id: int, query: str, top_n: int = 3) -> List[dict]:
    """在已建好的 chunks 上做 bigram 打分检索。

    打分：命中的 bigram 种类数（越大越相关）+ 命中密度的小权重。
    中文无需分词：bigram 对「导数极限」这类短语命中率高且零依赖。
    """
    if not (query or "").strip():
        return []
    raw = _read_text(file_id, "chunks.jsonl")
    if not raw.strip():
        return []
    chunks = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except Exception:
            continue
    qs = set(_bigrams(query))
    if not qs:
        return []
    scored = []
    for c in chunks:
        text = c.get("text", "")
        cb = set(_bigrams(text))
        hit = qs & cb
        if not hit:
            continue
        score = len(hit) * 10 + (len(hit) / max(len(qs), 1)) * 10
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, c in scored[:top_n]:
        out.append({
            "idx": c.get("idx"), "page_start": c.get("page_start"),
            "page_end": c.get("page_end"), "score": round(score, 1),
            "text": c.get("text", ""),
        })
    return out


# ================= 分层总结 =================

OVERVIEW_PROMPT = """下面是《{title}》的章节目录与部分抽样内容。请生成一份供 AI 助教使用的「文档总览」。

目录：
{toc}

抽样内容：
{samples}

要求：
1. 300-600 字，说明：这是什么资料、整体结构、覆盖哪些主题、适合什么样的问题。
2. 明确列出主要章节及其页码范围（如 第3章 数据结构 p45-78）。
3. 只写目录里出现的内容，不要编造。中文输出，直接给 markdown，不要客套。"""

CHAPTER_PROMPT = """下面是《{title}》中「{chapter}」的正文（页码已标注）。

{content}

任务：生成这一章节的详细学习摘要，供 AI 助教回答该部分问题：
1. 核心概念/知识点（每个带页码，如 [p45]）
2. 公式：完整 LaTeX（$...$ / $$...$$）
3. 例题/关键步骤（简短）
4. 易错点
5. 本章与前后文的衔接（一两句）
只输出 markdown，不要客套话。某类没有就省略。"""

SUBJECT_MAP_PROMPT = """下面是同一科目（{subject}）下若干学习资料的总览。

{docs}

请合并成一张「学科知识地图」，供 AI 助教把握整体：300-500 字，
说明这门科目包含哪些模块/资料、各自的主题与页码范围、以及它们之间的先后与依赖关系。
只依据上面给出的信息，不要编造。中文 markdown 输出。"""


def _sampled_chapter_text(pages: List[dict], ch: dict, unit: str = "page") -> str:
    """抽取某章节的头尾样本（成本控制关键：不送全文）"""
    s, e = ch["start"], ch["end"]
    picked = [p for p in pages if s <= p.get("page", 0) <= e and p.get("text", "").strip()]
    if not picked:
        return ""
    head = "\n".join(p["text"] for p in picked[:2])
    tail = "\n".join(p["text"] for p in picked[-2:]) if len(picked) > 2 else ""
    seg = head[:OVERVIEW_SAMPLE_CHARS]
    if tail:
        seg += "\n…\n" + tail[-OVERVIEW_SAMPLE_CHARS:]
    return seg


def _chapter_full_text(pages: List[dict], ch: dict) -> str:
    s, e = ch["start"], ch["end"]
    picked = [p for p in pages if s <= p.get("page", 0) <= e and p.get("text", "").strip()]
    return "\n".join(f"[p{p['page']}] {p['text']}" for p in picked)


def compose_overview(title: str, outline: dict, pages: List[dict]) -> str:
    """构造 overview 的输入（抽样文本），供上层调用 LLM"""
    chapters = [c for c in (outline.get("chapters") or []) if c["level"] == 1]
    if not chapters:
        chapters = [{"id": "all", "title": "全文", "start": 1,
                     "end": max((p.get("page", 1) for p in pages), default=1)}]
    samples = []
    for ch in chapters[:12]:          # 最多抽 12 章，控成本
        seg = _sampled_chapter_text(pages, ch)
        if seg:
            samples.append(f"### {ch['title']} (p{ch['start']}-{ch['end']})\n{seg}")
    return OVERVIEW_PROMPT.format(
        title=title,
        toc=outline_toc_text(outline, 1500),
        samples="\n\n".join(samples),
    )


def compose_chapter_prompt(title: str, outline: dict, pages: List[dict],
                           chapter: dict) -> str:
    path = chapter["title"]
    parent = next((c for c in (outline.get("chapters") or [])
                   if c["id"] == chapter.get("parent_id")), None)
    if parent:
        path = f"{parent['title']} > {chapter['title']}"
    content = _chapter_full_text(pages, chapter)
    # 单章超长时截断（保头保尾），避免一次调用爆预算
    if len(content) > 24000:
        content = content[:16000] + "\n…（中间省略）…\n" + content[-8000:]
    return CHAPTER_PROMPT.format(title=title, chapter=path, content=content)


def compose_subject_map(subject: str, docs: List[Tuple[str, str]]) -> str:
    body = "\n\n".join(f"### {name}\n{ov}" for name, ov in docs)
    return SUBJECT_MAP_PROMPT.format(subject=subject, docs=body)


# ---------- 科目地图读写 ----------

def _subject_map_path(subject: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*]', "_", (subject or "未分组").strip()) or "未分组"
    d = Path(settings.storage_dir) / "subject_maps"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.md"


def file_subject(file_id: int) -> str:
    """文件所属科目 = 其顶层文件夹名（沿 parent_id 走到根）"""
    from ..core.database import SessionLocal
    from ..models import Folder, File
    db = SessionLocal()
    try:
        f = db.query(File).filter(File.id == file_id).first()
        if not f:
            return ""
        top, cur, hops = None, f.folder_id, 0
        while cur and hops < 64:
            folder = db.query(Folder).filter(Folder.id == cur).first()
            if not folder:
                break
            top = folder
            cur = folder.parent_id
            hops += 1
        return (top.name if top else "")
    finally:
        db.close()


def get_subject_map(subject: str) -> str:
    p = _subject_map_path(subject)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def save_subject_map(subject: str, content: str):
    _subject_map_path(subject).write_text(content, encoding="utf-8")

# -*- coding: utf-8 -*-
"""文件中心服务层 — 托管库文件的上传/转换/提取/总结管线

职责：
- 托管库（storage/library/）的文件落盘与 sha256 去重
- kind 识别（video/pdf/office/image/text/markdown/audio/other）
- Office → PDF 转换（x2t / LibreOffice 外部依赖，均不 vendored 进仓库）
- PDF 文字层提取（pymupdf）、分块索引、文档总结（复用主模型）
- AI 产物目录 storage/artifacts/{file_id}/ 的读写

产物管线（上传后自动排队，见 process_file）：
    office : x2t → derived.pdf → 逐页提取 extracted.md → chunks.jsonl → summary.md
    pdf    : （原生）逐页提取 → chunks.jsonl → summary.md
    video  : 沿用 videos.py 的字幕/总结链路（产物由迁移脚本拷入 artifacts/）
    image  : 一期仅保存（OCR 二期接入）
"""
import asyncio
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from ..core.config import settings
from ..core.database import SessionLocal
from ..core.paths import to_rel
from ..models import File, FileTask

# ================= kind 识别 =================

KIND_EXTS = {
    "video": {"mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v", "ts"},
    "audio": {"mp3", "wav", "m4a", "aac", "flac", "ogg"},
    "pdf": {"pdf"},
    "office": {"pptx", "docx", "xlsx", "ppsx", "ppt", "doc", "xls", "odp", "odt", "ods"},
    "image": {"jpg", "jpeg", "png", "webp", "gif", "bmp", "svg"},
    "markdown": {"md", "markdown", "mdx"},
    "text": {"txt", "srt", "vtt", "json", "csv", "tsv", "log", "ini", "yaml", "yml",
             "py", "js", "ts", "html", "css", "bat", "sh", "sql"},
}

VIDEO_EXTS = KIND_EXTS["video"]

# 上传白名单：视频 + 文档 + 图片 + 音频 + 常见文本（拒绝可执行/脚本上传到学习库）
UPLOAD_DENY_EXTS = {"exe", "bat", "cmd", "ps1", "msi", "dll", "scr", "com", "jar",
                    "apk", "vbs", "sh"}


def detect_kind(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    for kind, exts in KIND_EXTS.items():
        if ext in exts:
            return kind
    return "other"


def sanitize_name(name: str) -> str:
    """清理文件名：去路径分隔符与非法字符（保留中文）"""
    name = (name or "").replace("/", "_").replace("\\", "_").replace("..", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip().strip(".")
    return name[:200] or "未命名"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ================= 路径 =================

def library_abs(*parts) -> Path:
    p = Path(settings.library_dir).joinpath(*parts)
    return p


def artifacts_dir(file_id: int) -> Path:
    return Path(settings.artifacts_dir) / str(file_id)


def artifact_path(file_id: int, name: str) -> Path:
    return artifacts_dir(file_id) / name


# 文件名 → 产物文件名白名单（/api/files/{id}/artifacts/{name} 只放行这些）
ARTIFACT_NAMES = {"derived.pdf", "extracted.md", "summary.md", "chunks.jsonl",
                  "pages.json", "thumb.png", "meta.json"}


def write_bytes(file_id: int, name: str, content: bytes) -> Path:
    """写二进制产物（页截图等）"""
    d = artifacts_dir(file_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


def write_artifact(file_id: int, name: str, content, binary: bool = False) -> Path:
    """写产物文件（自动建目录）。content: str/bytes"""
    d = artifacts_dir(file_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if binary:
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def read_artifact(file_id: int, name: str) -> Optional[str]:
    p = artifact_path(file_id, name)
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


# ================= Office → PDF 转换 =================

def _x2t_config() -> dict:
    """x2t 配置：platform_config 的 x2t 键优先，.env X2T_PATH 兜底"""
    from ..models import PlatformConfig
    from ..core.database import engine
    import sqlalchemy as sa
    cfg = {}
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT value FROM platform_config WHERE key='x2t'")).first()
            if row and row[0]:
                cfg = json.loads(row[0])
    except Exception:
        pass
    if not cfg.get("path") and settings.x2t_path:
        cfg["path"] = settings.x2t_path
    return cfg


def _find_soffice() -> Optional[str]:
    """定位 LibreOffice 可执行文件：PATH → 常见安装目录（Windows/Linux）"""
    hit = shutil.which("soffice") or shutil.which("soffice.exe")
    if hit:
        return hit
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice", "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # Windows 注册表式兜底：扫 Program Files 下的 LibreOffice*
    for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
        try:
            for d in os.listdir(base):
                if d.lower().startswith("libreoffice"):
                    p = os.path.join(base, d, "program", "soffice.exe")
                    if os.path.isfile(p):
                        return p
        except OSError:
            continue
    return None


def _office_to_pdf(src: Path, dst: Path) -> Path:
    """Office → PDF。依次尝试：x2t（OnlyOffice 引擎，保真度最高）→ LibreOffice。

    两个外部依赖都不随仓库分发；都没配置时抛出带部署提示的 RuntimeError。
    返回转换后的 PDF 路径。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    # --- x2t ---
    cfg = _x2t_config()
    x2t = cfg.get("path") or ""
    if x2t:
        xp = Path(x2t)
        if xp.is_file():
            extra = cfg.get("args") or []
            cmd = [str(xp), str(src), str(dst)] + [str(a) for a in extra]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=600)
                if dst.is_file() and dst.stat().st_size > 0:
                    return dst
                err = (r.stderr or b"").decode("utf-8", "replace")[-300:]
                out = (r.stdout or b"").decode("utf-8", "replace")[-300:]
                raise RuntimeError(f"x2t 退出码 {r.returncode}：{err or out}")
            except subprocess.TimeoutExpired:
                raise RuntimeError("x2t 转换超时（>600s）")
        else:
            raise RuntimeError(f"x2t 路径无效：{x2t}（设置里检查「文件转换」配置）")
    # --- LibreOffice 兜底 ---
    soffice = _find_soffice()
    if soffice:
        outdir = dst.parent
        outdir.mkdir(parents=True, exist_ok=True)
        # 独立 profile：并发/残留进程锁住默认 profile 时 LibreOffice 会静默失败（常见坑）
        profile_dir = Path(settings.data_dir) / "_lo_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_uri = profile_dir.resolve().as_uri()
        r = subprocess.run(
            [soffice, f"-env:UserInstallation={profile_uri}",
             "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(outdir), str(src)],
            capture_output=True, timeout=600,   # 不用 text=True：Windows 下按 GBK 解码会崩
        )
        produced = outdir / (src.stem + ".pdf")
        if produced.is_file() and produced.stat().st_size > 0:
            if produced != dst:
                shutil.move(str(produced), str(dst))
            return dst
        out = (r.stdout or b"").decode("utf-8", "replace")[-300:]
        err = (r.stderr or b"").decode("utf-8", "replace")[-300:]
        raise RuntimeError(f"LibreOffice 转换失败（rc={r.returncode}）：{err or out}")
    raise RuntimeError(
        "未找到 Office 转换引擎：请安装 LibreOffice，或在设置里配置 x2t（OnlyOffice 转换引擎）路径。"
        "原文件已保存，可下载查看。")


# ================= PDF 提取 / 分块 / 缩略图 =================

MIN_DOC_CHARS = 60  # 提取出的总字符低于此值 → 大概率扫描版


def _pdf_pages(pdf_path: Path) -> List[dict]:
    """pymupdf 逐页提取文本。返回 [{page: 1, text: "..."}]"""
    import fitz  # pymupdf
    pages = []
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc, 1):
            try:
                text = page.get_text("text").strip()
            except Exception:
                text = ""
            pages.append({"page": i, "text": text})
    return pages


def _render_pdf_thumb(pdf_path: Path, out: Path, width: int = 400) -> bool:
    """PDF 第一页渲染缩略图（树/查看器封面用）"""
    try:
        import fitz
        with fitz.open(str(pdf_path)) as doc:
            if not len(doc):
                return False
            page = doc[0]
            zoom = width / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(str(out))
            return True
    except Exception:
        return False


def split_into_segments(pages: List[dict], max_chars: int = 6000) -> List[str]:
    """逐页文本 → 按 max_chars 分段（供分段提取总结，策略同视频字幕的 20 分钟分段）"""
    segments, cur, cur_len = [], [], 0
    for p in pages:
        block = f"[第{p['page']}页]\n{p['text']}"
        if cur and cur_len + len(block) > max_chars:
            segments.append("\n".join(cur))
            cur, cur_len = [block], len(block)
        else:
            cur.append(block)
            cur_len += len(block)
    if cur:
        segments.append("\n".join(cur))
    return segments


def build_chunks(pages: List[dict], chunk_chars: int = 2000) -> List[dict]:
    """分块索引：每块约 chunk_chars 字符，带页码范围，供 agent 定位引用。

    返回 [{idx, page_start, page_end, text}]
    """
    chunks, cur, cur_len, page_start = [], [], 0, None
    last_page = 1
    for p in pages:
        t = p["text"]
        if not t:
            continue
        if not cur:
            page_start = p["page"]
        if cur and cur_len + len(t) > chunk_chars:
            chunks.append({"idx": len(chunks), "page_start": page_start,
                           "page_end": last_page, "text": "\n".join(cur)})
            cur, cur_len, page_start = [], 0, p["page"]
        cur.append(t)
        cur_len += len(t)
        last_page = p["page"]
    if cur:
        chunks.append({"idx": len(chunks), "page_start": page_start,
                       "page_end": last_page, "text": "\n".join(cur)})
    return chunks


# ================= 文档总结（复用主模型） =================

DOC_SEGMENT_EXTRACT_PROMPT = """你是考研资料内容提取器。下面是《{title}》第 {n}/{total} 段内容（[第N页] 标记页码）：

内容：
{content}

任务：逐句扫描，提取这一段里出现的全部有效信息，宁多勿漏：
1. 知识点/概念：名称 + 一句话解释 + 页码（如 [p12]）
2. 公式：完整 LaTeX 公式（$...$ / $$...$$），一个都别漏
3. 例题：题干 + 解题关键步骤（简短）+ 页码
4. 方法/技巧/口诀
5. 易错点/强调内容
只输出提取结果，不要客套话；某类内容不存在就省略该类。若本段没有任何有效内容，输出「（无有效内容）」。"""

DOC_MERGE_PROMPT = """下面是同一份考研资料《{title}》按页分段提取的 {k} 份要点。

请对照全部要点，合并去重，生成一份结构化的考研复习总结，要求：
1. 按五段组织：## 知识点 / ## 核心公式 / ## 例题与题型 / ## 方法技巧 / ## 易错点
2. 合并前逐份检查一遍：任何公式、例题、知识点都不得遗漏（宁可多列）
3. 同一概念出现在多段的合并为一条，页码取首次出现
4. 公式一律用 $...$ 或 $$...$$ LaTeX，中文讲解
5. 直接输出 markdown，不要额外说明。

{parts}"""


async def summarize_document(title: str, segments: List[str]) -> str:
    """文档总结：分段提取 → 合并防漏（与视频字幕总结同一策略）"""
    from .llm_service import chat_completion_simple
    if not segments:
        raise ValueError("没有可总结的内容")
    part_results = []
    for i, seg in enumerate(segments, 1):
        prompt = DOC_SEGMENT_EXTRACT_PROMPT.format(title=title, n=i, total=len(segments), content=seg)
        out = await chat_completion_simple([{"role": "user", "content": prompt}],
                                           max_tokens=2048, temperature=0.2)
        part_results.append(out)
    parts_body = "\n\n".join(f"=== 分段 {i} ===\n{t}" for i, t in enumerate(part_results, 1))
    merge_prompt = DOC_MERGE_PROMPT.format(title=title, k=len(part_results), parts=parts_body)
    return await chat_completion_simple([{"role": "user", "content": merge_prompt}],
                                        max_tokens=4096, temperature=0.3)


# ================= 产物管线（后台任务） =================

# 总结类 LLM 调用全局串行（与视频总结共用一把锁，防并发限流）
from .llm_service import SUMMARY_LOCK  # noqa: E402

# 转换串行：x2t/soffice 都是重进程，一次转一个。
# 用 threading.Lock 而非 asyncio.Lock —— pipeline 跑在各自独立的事件循环里，
# asyncio.Lock 跨 loop 不互斥。
import threading as _threading
_convert_lock = _threading.Lock()


def _create_task(db, file_id: int, task_type: str) -> FileTask:
    t = FileTask(file_id=file_id, task_type=task_type, status="pending")
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def enqueue_pipeline(file_id: int, kinds: Optional[List[str]] = None):
    """入库后触发产物管线（异步后台执行，不阻塞上传请求）。

    kinds: 限定要跑的任务类型（None = 按文件类型自动）。
    """
    db = SessionLocal()
    try:
        f = db.query(File).filter(File.id == file_id).first()
        if not f:
            return
        f.status = "processing"
        f.status_msg = "排队处理中"
        db.commit()
        todo = _plan_tasks(f, kinds)
        for task_type in todo:
            _create_task(db, file_id, task_type)
        db.close()
        # 用独立线程 + asyncio.run 调度：本函数既会被 async 路由调用（有 running loop），
        # 也会被 sync 路由/线程池调用（没有 loop，get_event_loop() 会抛错被吞掉，
        # 任务就永远卡在 pending——曾在重启后重新处理时踩到）。
        import threading
        threading.Thread(target=lambda: asyncio.run(_run_pipeline(file_id)),
                         daemon=True, name=f"pipeline-{file_id}").start()
    except Exception as e:
        print(f"  [files] 排队失败 #{file_id}: {e}")
        try:
            db.close()
        except Exception:
            pass


def _plan_tasks(f: File, kinds: Optional[List[str]]) -> List[str]:
    """按文件类型规划任务序列"""
    todo = []
    if f.kind == "office":
        todo.append("convert_pdf")
    if f.kind in ("office", "pdf"):
        todo += ["extract_text", "summarize"]
    if kinds:
        todo = [t for t in todo if t in kinds]
    return todo


async def _run_pipeline(file_id: int):
    """串行执行一个文件的全部待办任务；单个任务失败不阻断后续（除非依赖它）"""
    db = SessionLocal()
    try:
        f = db.query(File).filter(File.id == file_id).first()
        if not f:
            return
        tasks = db.query(FileTask).filter(
            FileTask.file_id == file_id,
            FileTask.status.in_(("pending", "running"))).order_by(FileTask.id.asc()).all()
        derived: Optional[Path] = None
        pages: List[dict] = []
        text_ok = False
        for t in tasks:
            t.status = "running"
            t.updated_at = datetime.datetime.utcnow()
            db.commit()
            try:
                if t.task_type == "convert_pdf":
                    src = to_abs_safe(f.storage_path)
                    if not src:
                        raise RuntimeError("源文件不存在")
                    derived = await _do_convert(file_id, src, f.name)
                    t.progress = 1.0
                elif t.task_type == "extract_text":
                    pdf = derived or artifact_path(file_id, "derived.pdf")
                    if not pdf.is_file():
                        pdf = to_abs_safe(f.storage_path)
                    if f.kind == "pdf":
                        pdf = to_abs_safe(f.storage_path)
                    if not pdf or not Path(pdf).is_file():
                        raise RuntimeError("没有可提取的 PDF（转换未完成）")
                    pages = await asyncio.to_thread(_pdf_pages, Path(pdf))
                    total_chars = sum(len(p["text"]) for p in pages)
                    meta = {"pages": len(pages), "chars": total_chars,
                            "extracted_at": datetime.datetime.now().isoformat(timespec="seconds")}
                    if total_chars < MIN_DOC_CHARS:
                        meta["hint"] = "提取文本过少，可能是扫描版/图片版，OCR 将在二期接入"
                    write_artifact(file_id, "pages.json",
                                   json.dumps(pages, ensure_ascii=False))
                    write_artifact(file_id, "extracted.md",
                                   "\n\n".join(f"<!-- p{p['page']} -->\n{p['text']}" for p in pages))
                    write_artifact(file_id, "chunks.jsonl",
                                   "\n".join(json.dumps(c, ensure_ascii=False)
                                             for c in build_chunks(pages)))
                    write_artifact(file_id, "meta.json", json.dumps(meta, ensure_ascii=False))
                    _render_pdf_thumb(Path(pdf), artifact_path(file_id, "thumb.png"))
                    # 章节树（书签→字号启发式，零 LLM 成本）
                    try:
                        from .doc_index import build_outline, _write_json as _di_write
                        ol = build_outline(pages, Path(pdf), kind="pdf")
                        _di_write(file_id, "outline.json", ol)
                        meta["chapters"] = len(ol.get("chapters") or [])
                    except Exception as _e:
                        print(f"  [files] outline 构建失败 #{file_id}: {_e}")
                    text_ok = True
                    t.progress = 1.0
                elif t.task_type == "summarize":
                    if not text_ok:
                        # 直接跑总结（如手动重试）：从 pages.json 恢复
                        raw = read_artifact(file_id, "pages.json")
                        if not raw:
                            raise RuntimeError("没有提取文本，先执行提取")
                        pages = json.loads(raw)
                    # 共识 Q6：抽样鸟瞰替代全量总结（省钱：300页 45K→3K 输出 token）
                    # 章节详细摘要改为「首次问到该章」时懒生成
                    from .doc_index import (compose_overview, llm_gate_ok,
                                            llm_record, get_outline)
                    _ol = get_outline(file_id) or {"chapters": [], "unit": "page"}
                    _prompt = compose_overview(f.name, _ol, pages)
                    summary = ""
                    async with SUMMARY_LOCK:
                        if llm_gate_ok(file_id, "overview"):
                            summary = await summarize_document(f.name, [_prompt])
                            llm_record(file_id, "overview")
                    if (summary or "").strip():
                        write_artifact(file_id, "summary.md", summary)
                        write_artifact(file_id, "overview.md", summary)
                t.status = "done"
            except Exception as e:
                t.status = "error"
                t.error = str(e)[:480]
            t.updated_at = datetime.datetime.utcnow()
            db.commit()
            if t.status == "error" and t.task_type in ("convert_pdf", "extract_text"):
                break  # 依赖链断了，后面不用跑
        # 汇总文件状态
        db.refresh(f)
        errs = [t for t in db.query(FileTask).filter(
            FileTask.file_id == file_id, FileTask.status == "error").all()]
        dones = [t for t in db.query(FileTask).filter(
            FileTask.file_id == file_id, FileTask.status == "done").all()]
        if errs:
            f.status = "error"
            f.status_msg = errs[-1].error[:480]
        elif dones:
            f.status = "ready"
            f.status_msg = ""
        db.commit()
    finally:
        db.close()


def to_abs_safe(rel: str) -> Optional[Path]:
    from ..core.paths import to_abs
    p = to_abs(rel)
    if p and Path(p).is_file():
        return Path(p)
    return None


def to_abs_local(rel: str) -> Optional[Path]:
    """存库值 → 绝对路径（不检查存在性，files 路由用）"""
    from ..core.paths import to_abs
    return to_abs(rel)


async def _do_convert(file_id: int, src: Path, name: str) -> Path:
    dst = artifact_path(file_id, "derived.pdf")

    def _locked_convert():
        with _convert_lock:
            return _office_to_pdf(src, dst)

    return await asyncio.to_thread(_locked_convert)

# -*- coding: utf-8 -*-
"""上下文编排器 — agent 上下文的「预编排」层

设计（共识 D5）：预编排保下限 + 沙箱 read 工具保上限 + 全程可预览非黑盒。

- 模板按文件类型（kind）配置，存 platform_config 的 orchestrator 键，设置页可改
- build_file_context() 产出注入 system prompt 的文本 + 逐组件明细（预览面板用）
- 组件：总结 summary.md / 提取文本摘录 extracted.md / 当前位置（页码或时间点对应内容）
"""
import json
from pathlib import Path
from typing import List, Optional

from ..core.config import settings

# 模板默认值（platform_config.orchestrator 缺省时用这个）
DEFAULT_TEMPLATE = {
    "include_summary": True,      # 注入 summary.md
    "summary_chars": 8000,        # 总结注入上限（字符）
    "include_extracted": True,    # 注入提取文本摘录
    "extracted_head": 3000,       # 开头字符数
    "extracted_tail": 1500,       # 结尾字符数
    "include_position": True,     # 注入当前查看位置对应的内容块
}
# text/markdown 类文件直接注入全文（有上限）
DEFAULT_TEXT_TEMPLATE = {
    "include_summary": False,
    "summary_chars": 0,
    "include_extracted": True,
    "extracted_head": 60000,      # 文本类注入量大得多（1M 上下文下不构成压力）
    "extracted_tail": 0,
    "include_position": False,
}
TEMPLATES_DEFAULT = {
    "office": dict(DEFAULT_TEMPLATE),
    "pdf": dict(DEFAULT_TEMPLATE),
    "video": dict(DEFAULT_TEMPLATE),
    "image": {"include_summary": False, "summary_chars": 0, "include_extracted": False,
              "extracted_head": 0, "extracted_tail": 0, "include_position": False},
    "audio": {"include_summary": False, "summary_chars": 0, "include_extracted": False,
              "extracted_head": 0, "extracted_tail": 0, "include_position": False},
    "text": dict(DEFAULT_TEXT_TEMPLATE),
    "markdown": dict(DEFAULT_TEXT_TEMPLATE),
    "other": dict(DEFAULT_TEXT_TEMPLATE),
}

ORCH_KEY = "orchestrator"


def load_orchestrator_config() -> dict:
    """读 platform_config.orchestrator，逐字段与默认值合并（向后兼容缺字段）"""
    import sqlalchemy as sa
    from ..core.database import engine
    cfg = {"enabled": True, "templates": {}}
    try:
        with engine.connect() as conn:
            row = conn.execute(sa.text(
                "SELECT value FROM platform_config WHERE key=:k"), {"k": ORCH_KEY}).first()
            if row and row[0]:
                saved = json.loads(row[0])
                cfg["enabled"] = bool(saved.get("enabled", True))
                for kind, tpl in (saved.get("templates") or {}).items():
                    base = dict(TEMPLATES_DEFAULT.get(kind, DEFAULT_TEMPLATE))
                    base.update({k: v for k, v in tpl.items() if k in base})
                    cfg["templates"][kind] = base
    except Exception:
        pass
    for kind, base in TEMPLATES_DEFAULT.items():
        cfg["templates"].setdefault(kind, dict(base))
    return cfg


def save_orchestrator_config(cfg: dict):
    from ..models import PlatformConfig
    from ..core.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(PlatformConfig).filter(PlatformConfig.key == ORCH_KEY).first()
        val = json.dumps(cfg, ensure_ascii=False)
        if row:
            row.value = val
        else:
            db.add(PlatformConfig(key=ORCH_KEY, value=val))
        db.commit()
    finally:
        db.close()


def _read_head_tail(text: str, head: int, tail: int) -> str:
    if len(text) <= head + tail:
        return text
    sep = "\n\n…（中间省略）…\n\n"
    return text[:head] + sep + (text[-tail:] if tail else "")


def _position_content(file, position: dict) -> str:
    """当前查看位置 → 对应内容块。position: {page: N} 或 {time: 秒}

    共识（PDF/视频位置上下文）：位置块 = 章节路径 + 当前页原文。
    章节路径来自 outline.json（书签/字号启发式），当前页原文取 pages.json。
    """
    page = None
    time_val = None
    if isinstance(position, dict):
        page = position.get("page")
        time_val = position.get("time")

    # ---- 视频：字幕时间点 ----
    if time_val is not None and file.kind == "video":
        try:
            t = float(time_val)
            from ..models import Subtitle
            from ..core.database import SessionLocal
            from .doc_index import get_outline, chapter_at, _fmt_sec
            db = SessionLocal()
            try:
                sub = db.query(Subtitle).filter(
                    Subtitle.video_id == file.id, Subtitle.start_time <= t)                     .order_by(Subtitle.start_time.desc()).first()
            finally:
                db.close()
            ol = get_outline(file.id)
            _, _, path = chapter_at(ol, t) if ol else (None, None, "")
            head = f"[播放位置 {_fmt_sec(t)}" + (f" · {path}]" if path else "]")
            text = sub.text if sub else ""
            return head + chr(10) + text
        except Exception:
            pass

    # ---- 文档：页码 → 章节路径 + 当前页原文 ----
    if page:
        try:
            raw = Path(settings.artifacts_dir) / str(file.id) / "pages.json"
            if not raw.is_file():
                return ""
            pages = json.loads(raw.read_text(encoding="utf-8"))
            page = int(page)
            cur = next((p for p in pages if p["page"] == page), None)
            if not cur:
                return ""
            from .doc_index import get_outline, chapter_at
            ol = get_outline(file.id)
            _, _, path = chapter_at(ol, page) if ol else (None, None, "")
            out = f"[当前查看第 {page} 页" + (f" · {path}]" if path else "]")
            out = f"[当前查看第 {page} 页" + (f" · {path}]" if path else "]")
            out += chr(10) + cur['text'][:2600]
        except Exception:
            pass
    return ""


def build_scope_manifest(folder_id: int, limit_chars: int = 1800) -> str:
    """范围清单：聚焦文件夹内的完整文件名 + 产物状态。

    为什么列文件名就够（用户洞察）：文件名本身是人工整理的「讲次-主题」元数据，
    多个文件名连起来读即可还原这一范围的结构，不需要额外生成「讲次总览」文档。
    """
    from ..models import File as FileModel, Folder
    from ..core.database import SessionLocal
    if not folder_id:
        return ""
    db = SessionLocal()
    try:
        fld = db.query(Folder).filter(Folder.id == folder_id).first()
        rows = db.query(FileModel).filter(
            FileModel.folder_id == folder_id, FileModel.deleted == 0
        ).order_by(FileModel.name).all()
        if not rows:
            return ""
        lines = []
        for r in rows:
            d = Path(settings.artifacts_dir) / str(r.id)
            marks = []
            if (d / "extracted.txt").is_file() or (d / "extracted.md").is_file() or (d / "pages.json").is_file():
                marks.append("正文可读")
            if (d / "summary.md").is_file() or (d / "overview.md").is_file():
                marks.append("有总结")
            if (d / "outline.json").is_file():
                marks.append("有章节")
            st = ("｜" + "·".join(marks)) if marks else "｜暂无提取"
            lines.append("%s [id=%d]%s" % (r.name, r.id, st))
        name = fld.name if fld else str(folder_id)
        _head = "### 当前学习范围：" + str(name)
        _body = "%s" % chr(10).join([
            "本区共 %d 个资料。文件名自带讲次与主题，按顺序读即可还原这一范围的结构；" % len(rows),
            "需要细节用 read(file_id=N)，找知识点用 grep。",
            chr(10).join(lines),
        ])
        txt = _head + chr(10) + _body
        return txt[:limit_chars]
    finally:
        db.close()


def build_subject_map_part(folder_id: int, limit_chars: int = 400) -> str:
    """科目知识地图片段（沿文件夹上溯到顶层科目）"""
    from ..models import Folder
    from ..core.database import SessionLocal
    from .doc_index import get_subject_map
    if not folder_id:
        return ""
    db = SessionLocal()
    try:
        cur, top, hops = folder_id, None, 0
        while cur and hops < 64:
            f = db.query(Folder).filter(Folder.id == cur).first()
            if not f:
                break
            top = f
            cur = f.parent_id
            hops += 1
        if not top:
            return ""
        m = get_subject_map(top.name)
        if not m.strip():
            return ""
        return "### 科目知识地图（" + str(top.name) + "）" + chr(10) + m[:limit_chars]
    finally:
        db.close()


def build_file_context(file, position: Optional[dict] = None) -> dict:
    """按模板为文件构建注入上下文。

    file: models.File 行。返回：
    {enabled, text(拼接好的注入文本), components: [{key,label,source,chars,note}]}
    """
    cfg = load_orchestrator_config()
    tpl = cfg["templates"].get(file.kind, DEFAULT_TEMPLATE)
    components: List[dict] = []
    parts: List[str] = []

    if not cfg["enabled"]:
        return {"enabled": False, "text": "", "components": []}

    art = Path(settings.artifacts_dir) / str(file.id)

    def _art(name: str) -> str:
        p = art / name
        if not p.is_file():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    # 1) 总结
    if tpl.get("include_summary"):
        s = _art("overview.md") or _art("summary.md")
        if s.strip():
            s2 = _read_head_tail(s, int(tpl.get("summary_chars") or 8000), 0)
            components.append({"key": "summary", "label": "文档总结", "source": "summary.md",
                               "chars": len(s2), "note": ""})
            parts.append(f"### 《{file.name}》AI 总结\n{s2}")
    # 2) 提取文本摘录
    if tpl.get("include_extracted"):
        e = _art("extracted.md")
        if e.strip():
            e2 = _read_head_tail(e, int(tpl.get("extracted_head") or 3000),
                                 int(tpl.get("extracted_tail") or 1500))
            components.append({"key": "extracted", "label": "全文摘录（头尾）",
                               "source": "extracted.md", "chars": len(e2), "note": ""})
            parts.append(f"### 《{file.name}》全文摘录\n{e2}")
    # 3) 当前位置（升级：章节路径 + 当前页原文 + 邻页）
    if tpl.get("include_position") and position:
        pc = _position_content(file, position)
        if pc:
            components.append({"key": "position", "label": "当前查看位置",
                               "source": "pages.json/字幕", "chars": len(pc), "note": ""})
            parts.append(pc)
        # 邻页原文（前后各 1 页，预算内）
        try:
            page = position.get("page") if isinstance(position, dict) else None
            if page and file.kind in ("office", "pdf"):
                import json as _json
                raw = Path(settings.artifacts_dir) / str(file.id) / "pages.json"
                if raw.is_file():
                    pages_map = {p["page"]: p["text"] for p in
                                 _json.loads(raw.read_text(encoding="utf-8"))}
                    neighbors = []
                    for pp in (int(page) - 1, int(page) + 1):
                        if pp in pages_map and pages_map[pp].strip():
                            neighbors.append("[p" + str(pp) + "] " + pages_map[pp][:500])
                    if neighbors:
                        nb = chr(10).join(neighbors)[:800]
                        components.append({"key": "neighbors", "label": "前后邻页",
                                           "source": "pages.json", "chars": len(nb),
                                           "note": ""})
                        parts.append("### 前后邻页" + chr(10) + nb)
        except Exception:
            pass

    # 4) 章节路径 + 完整目录 + 懒生成的章节摘要（新索引体系）
    if position and isinstance(position, dict):
        unit_val = position.get("page") if position.get("page") is not None else position.get("time")
        if unit_val is not None:
            try:
                from .doc_index import get_outline, chapter_at, outline_toc_text
                ol = get_outline(file.id)
                if ol:
                    ch, sec, path = chapter_at(ol, unit_val)
                    if path:
                        components.append({"key": "chapter_path", "label": "当前章节路径",
                                           "source": "outline.json", "chars": len(path),
                                           "note": ""})
                        parts.append("### 当前位置：" + path)
                    toc = outline_toc_text(ol, 600)
                    if toc:
                        components.append({"key": "toc", "label": "完整目录树",
                                           "source": "outline.json", "chars": len(toc),
                                           "note": ""})
                        parts.append("### 目录结构" + chr(10) + toc)
                    if sec:
                        txt = _art(f"chap_{sec['id']}.md")
                        if txt.strip():
                            txt = txt[:1500]
                            components.append({"key": "chapter_summary",
                                               "label": "章节摘要·" + sec["title"][:20],
                                               "source": "chap_" + sec["id"] + ".md",
                                               "chars": len(txt), "note": ""})
                            parts.append("### 当前章节摘要" + chr(10) + txt)
                        else:
                            parts.append("（提示：当前章节「" + sec["title"] + "」尚无详细摘要；"
                                         "若用户问题涉及本章，请先调用工具 gen_chapter_summary(cid=\"" + sec["id"] + "\") 生成后再回答）")
            except Exception:
                pass

    # 3.5) 科目知识地图（subject_maps/{subject}.md；共识 Q10，预算 400 字）
    try:
        from .doc_index import get_subject_map, file_subject
        _subj = file_subject(file.id)
        if _subj:
            _smap = get_subject_map(_subj)
            if _smap.strip():
                _smap = _smap[:400]
                components.append({"key": "subject_map", "label": "科目知识地图·" + _subj,
                                   "source": "subject_maps", "chars": len(_smap), "note": ""})
                parts.append("### 科目知识地图（" + _subj + "）" + chr(10) + _smap)
    except Exception:
        pass

    # 4.5) 范围清单（共识 Q3：注入「总览+清单状态」）
    # 关键洞察（用户）：文件名本身就是人工整理的「讲次-主题」元数据，多个文件名连起来读
    # 就能还原整体结构——所以这里列完整文件名 + 产物状态，不需要额外生成一份"讲次总览"文档。
    try:
        from ..models import File as FileModel, Folder
        from ..core.database import SessionLocal
        import os as _os
        _scope = None
        if position and isinstance(position, dict):
            _scope = position.get("scope_folder_id")
        if _scope:
            _db = SessionLocal()
            try:
                _fld = _db.query(Folder).filter(Folder.id == _scope).first()
                _rows = _db.query(FileModel).filter(
                    FileModel.folder_id == _scope, FileModel.deleted == 0
                ).order_by(FileModel.name).all()
                if _rows:
                    _lines = []
                    for _r in _rows:
                        _d = Path(settings.artifacts_dir) / str(_r.id)
                        _marks = []
                        if (_d / "extracted.txt").is_file() or (_d / "extracted.md").is_file() or (_d / "pages.json").is_file():
                            _marks.append("正文可读")
                        if (_d / "summary.md").is_file() or (_d / "overview.md").is_file():
                            _marks.append("有总结")
                        if (_d / "outline.json").is_file():
                            _marks.append("有章节")
                        _st = ("｜" + "·".join(_marks)) if _marks else "｜暂无提取"
                        _lines.append("%s [id=%d]%s" % (_r.name, _r.id, _st))
                    _txt = ("本区共 %d 个资料。文件名自带讲次与主题，按顺序读即可还原这一范围的结构；"
                            "需要细节用 read(file_id=N)，找知识点用 grep。\n%s"
                            % (len(_rows), "\n".join(_lines)))
                    _txt = _txt[:1800]
                    components.append({"key": "scope_manifest",
                                       "label": "学习范围清单·" + ((_fld.name if _fld else str(_scope))),
                                       "source": "files", "chars": len(_txt), "note": ""})
                    parts.append("### 当前学习范围：" + ((_fld.name if _fld else str(_scope))) + chr(10) + _txt)
            finally:
                _db.close()
    except Exception:
        pass

    # 5) 关键词检索：position.hit_query（前端传问题文本）在 chunks 里检索相关片段
    if position and isinstance(position, dict) and position.get("hit_query"):
        try:
            from .doc_index import search_chunks
            hits = search_chunks(file.id, str(position["hit_query"])[:120], top_n=3)
            if hits:
                seg = chr(10).join(
                    "[p" + str(h["page_start"]) + "-" + str(h["page_end"]) + "] "
                    + h["text"][:600] for h in hits)
                components.append({"key": "hits", "label": "相关片段（关键词检索）",
                                   "source": "chunks.jsonl", "chars": len(seg), "note": ""})
                parts.append("### 可能相关的其他片段" + chr(10) + seg)
        except Exception:
            pass

    text = "\n\n".join(parts)
    return {"enabled": True, "text": text, "components": components}

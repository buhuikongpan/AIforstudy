# -*- coding: utf-8 -*-
"""启动时的路径规范化：把库里的绝对路径统一改成相对项目根（幂等）。

历史包袱：videos / chat_messages 表早期存的是绝对路径，项目一搬家就集体失效。
本模块在每次启动时做一次巡检（几毫秒），保证：

- 绝对路径且文件在        → 转成相对路径 ``storage/videos/math/x.mp4``
- 路径失效（搬过家/换过盘）→ 按**文件名**在 storage/ 下重定位后再转相对
- 已经是相对路径          → 只做分隔符归一（反斜杠 → 正斜杠）
- 彻底找不到的            → **保留原值**，只计数告警（不破坏性清空，便于人工排查）

对外只暴露 ``normalize_paths(db)``，由 main.py 启动时调用。
"""
from __future__ import annotations

import re

from .paths import resolve, to_abs, to_rel

# videos 表里所有存文件的列
VIDEO_COLS = ("file_path", "subtitle_path", "summary_path", "compressed_path")

# 聊天消息里的上传图片路径（消息里的图块 / 模型调 read 工具拿到的路径）
_UPLOAD_RE = re.compile(
    r"(?:[A-Za-z]:)?[\\/][^\s\"'\[\],]*?storage[\\/]uploads[\\/][^\s\"'\[\],\\/]+"
)


def _fix(raw) -> tuple:
    """规范化单个路径值 → (新值, 状态)。

    状态：'' 无需改动 / 'normalized' 绝对转相对 / 'relocated' 失效后重定位 / 'missing' 找不到
    """
    if raw is None:
        return "", ""
    old = str(raw)
    if not old.strip():
        return "", ""

    direct = to_abs(old)
    direct_ok = False
    if direct is not None:
        try:
            direct_ok = direct.is_file()
        except OSError:
            direct_ok = False

    if direct_ok:
        new = to_rel(direct)
        return new, ("normalized" if new != old else "")

    hit = resolve(old)
    if hit:
        return to_rel(hit), "relocated"
    return old, "missing"


def _fix_chat_text(text: str) -> tuple:
    """把聊天消息里内嵌的上传图片绝对路径替换成相对路径 → (新文本, 改动数)"""
    if not text or "storage" not in text.replace("\\", "/"):
        return text, 0
    changed = 0

    def _sub(m):
        nonlocal changed
        raw = m.group(0)
        rel = to_rel(raw)
        if rel.startswith("storage/uploads/"):
            changed += 1
            return rel
        return raw

    return _UPLOAD_RE.sub(_sub, text), changed


def normalize_paths(db) -> dict:
    """巡检并规范化所有存路径的表。返回统计字典。"""
    from ..models import ChatMessage, Video

    stats = {"scanned": 0, "normalized": 0, "relocated": 0, "missing": 0, "chat": 0}

    for v in db.query(Video).all():
        stats["scanned"] += 1
        for col in VIDEO_COLS:
            raw = getattr(v, col)
            if not raw:
                continue
            new, state = _fix(raw)
            if state in ("normalized", "relocated"):
                setattr(v, col, new)
                stats[state] += 1
            elif state == "missing":
                stats["missing"] += 1

    for m in db.query(ChatMessage).all():
        for col in ("content", "tool_calls"):
            raw = getattr(m, col)
            if not raw:
                continue
            new, n = _fix_chat_text(raw)
            if n:
                setattr(m, col, new)
                stats["chat"] += n

    if any(stats[k] for k in ("normalized", "relocated", "chat")):
        db.commit()

    return stats

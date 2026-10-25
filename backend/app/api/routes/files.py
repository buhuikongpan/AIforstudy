# -*- coding: utf-8 -*-
"""文件中心 API — 托管资源管理器（左树）+ 全类型查看器的数据面

- 托管库 storage/library/ 是树的真实本体；数据库 folders/files 做索引
- 上传复制制：文件复制进托管库，不碰用户原始磁盘（共识 D2）
- 删除 = 软删除进 library_trash（回收站可恢复/彻底清除）
- AI 产物在 storage/artifacts/{file_id}/（共识 D4：集中按 ID，关联永不断）
"""
import datetime
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.paths import to_rel
from ...models import File as FileModel, Folder, FileTask, VideoMeta
from ..deps import get_db
from ...services import file_service as fs
from ...services import doc_index as di

router = APIRouter(tags=["文件中心"])

MIME = {
    ".pdf": "application/pdf", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".svg": "image/svg+xml",
    ".md": "text/markdown", ".txt": "text/plain",
}


# ================= 工具：folder 路径换算 =================

def _folder_chain(db: Session, folder_id: int) -> List[Folder]:
    """folder_id → 从根到该文件夹的链（防环：最多走 64 层）"""
    chain, cur, hops = [], folder_id, 0
    while cur and hops < 64:
        f = db.query(Folder).filter(Folder.id == cur).first()
        if not f:
            break
        chain.insert(0, f)
        cur = f.parent_id or 0
        hops += 1
    return chain


def folder_rel_path(db: Session, folder_id: int) -> str:
    """folder_id → 托管库内相对路径（'' = 根）"""
    return "/".join(f.name for f in _folder_chain(db, folder_id)) if folder_id else ""


def _folder_children(db: Session, folder_id: int) -> List[Folder]:
    return db.query(Folder).filter(Folder.parent_id == folder_id, Folder.deleted == 0)\
        .order_by(Folder.sort_order.asc(), Folder.id.asc()).all()


def _folder_descendant_ids(db: Session, folder_id: int) -> List[int]:
    """自身 + 全部后代文件夹 id（防环）"""
    out, frontier, hops = [], [folder_id], 0
    while frontier and hops < 64:
        nxt = []
        rows = db.query(Folder).filter(Folder.parent_id.in_(frontier)).all()
        for f in rows:
            if f.id not in out:
                out.append(f.id)
                nxt.append(f.id)
        frontier = nxt
        hops += 1
    return [folder_id] + out


def _unique_path(p: Path) -> Path:
    """同目录同名冲突 → name (1).ext"""
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    for i in range(1, 999):
        cand = p.with_name(f"{stem} ({i}){suffix}")
        if not cand.exists():
            return cand
    return p


def _ensure_dir(rel: str) -> Path:
    d = fs.library_abs(rel)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_file(db: Session, file_id: int) -> FileModel:
    f = db.query(FileModel).filter(FileModel.id == file_id, FileModel.deleted == 0).first()
    if not f:
        raise HTTPException(404, "文件不存在")
    return f


# ================= 树 =================

@router.get("/api/fs/tree")
def fs_tree(keyword: str = Query(""), db: Session = Depends(get_db)):
    """托管库树：folders + files（全部科目一树展示，科目只是顶层文件夹）"""
    folders = db.query(Folder).filter(Folder.deleted == 0).all()
    files = db.query(FileModel).filter(FileModel.deleted == 0).order_by(
        FileModel.sort_order.asc(), FileModel.name.asc()).all()

    fmap = {}
    for f in folders:
        fmap[f.id] = {"id": f.id, "name": f.name, "parent_id": f.parent_id,
                      "subject": f.subject, "children": [], "files": []}

    kw = (keyword or "").strip().lower()
    root_files = []
    # 总结状态：视频查 video_meta，office/pdf 查产物 summary.md
    vmeta = dict(db.query(VideoMeta.file_id, VideoMeta.summary_status).all())
    _art = Path(settings.artifacts_dir)

    def _summary_of(v):
        if v.kind == "video":
            return vmeta.get(v.id, "")
        if v.kind in ("office", "pdf"):
            return "done" if (_art / str(v.id) / "summary.md").is_file() else ""
        return ""

    def _outline_info(v):
        if v.kind not in ("office", "pdf"):
            return (False, 0)
        op = _art / str(v.id) / "outline.json"
        if not op.is_file():
            return (False, 0)
        try:
            ol = json.loads(op.read_text(encoding="utf-8"))
            return (True, len(ol.get("chapters") or []))
        except Exception:
            return (False, 0)

    for v in files:
        info = _file_info(v, _summary_of(v), _outline_info(v))
        if kw and kw not in info["name"].lower():
            continue
        if v.folder_id and v.folder_id in fmap:
            fmap[v.folder_id]["files"].append(info)
        else:
            root_files.append(info)

    roots = []
    for fid, fdata in fmap.items():
        if fdata["parent_id"] and fdata["parent_id"] in fmap:
            fmap[fdata["parent_id"]]["children"].append(fdata)
        else:
            roots.append(fdata)

    def _prune(nodes):
        kept = []
        for n in nodes:
            n["children"] = _prune(n["children"])
            if kw and not n["children"] and not n["files"] \
                    and kw not in n["name"].lower():
                continue
            kept.append(n)
        return kept

    roots = _prune(roots)
    return {"folders": roots, "rootFiles": root_files, "keyword": kw}


def _file_info(v, summary: str = "", outline_info=None) -> dict:
    info = {
        "id": v.id, "name": v.name, "ext": v.ext, "kind": v.kind,
        "size": v.size, "status": v.status, "status_msg": v.status_msg or "",
        "folder_id": v.folder_id,
        "summary": summary or "",   # done|processing|failed|''（''=未总结/无总结概念）
        "has_outline": False, "chapters": 0,
    }
    if outline_info:
        info["has_outline"], info["chapters"] = outline_info
    return info


# ================= 目录操作 =================

@router.post("/api/fs/mkdir")
def fs_mkdir(data: dict, db: Session = Depends(get_db)):
    name = fs.sanitize_name(data.get("name") or "")
    if not name:
        raise HTTPException(400, "名称不能为空")
    parent_id = int(data.get("parent_id") or 0)
    if parent_id:
        if not db.query(Folder).filter(Folder.id == parent_id, Folder.deleted == 0).first():
            raise HTTPException(400, "父目录不存在")
    dup = db.query(Folder).filter(Folder.parent_id == parent_id, Folder.name == name,
                                  Folder.deleted == 0).first()
    if dup:
        raise HTTPException(400, "同名文件夹已存在")
    folder = Folder(subject="all", name=name, parent_id=parent_id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    _ensure_dir(str(Path(folder_rel_path(db, folder.id))))
    return {"id": folder.id, "name": folder.name}


@router.patch("/api/fs/folder/{folder_id}/rename")
def fs_folder_rename(folder_id: int, data: dict, db: Session = Depends(get_db)):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.deleted == 0).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    name = fs.sanitize_name(data.get("name") or "")
    if not name:
        raise HTTPException(400, "名称不能为空")
    old_rel = folder_rel_path(db, folder_id)
    new_rel = old_rel.rsplit("/", 1)[0] + "/" + name if "/" in old_rel else name
    src = fs.library_abs(old_rel) if old_rel else None
    dst = _ensure_dir(new_rel.rsplit("/", 1)[0] if "/" in new_rel else "")
    if src and src.is_dir():
        target = dst / name
        if target.exists():
            raise HTTPException(400, "磁盘上已存在同名目录")
        src.rename(target)
    folder.name = name
    db.commit()
    _rewrite_file_paths(db, old_rel, new_rel)
    return {"message": "已重命名", "id": folder_id, "name": name}


@router.post("/api/fs/folder/{folder_id}/move")
def fs_folder_move(folder_id: int, data: dict, db: Session = Depends(get_db)):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.deleted == 0).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    target_id = int(data.get("target_folder_id") or 0)
    if target_id == folder_id:
        raise HTTPException(400, "不能移动到自身")
    if target_id and target_id in _folder_descendant_ids(db, folder_id):
        raise HTTPException(400, "不能移动到自己的子目录")
    old_rel = folder_rel_path(db, folder_id)
    folder.parent_id = target_id
    db.commit()
    new_rel = folder_rel_path(db, folder_id)
    if old_rel != new_rel:
        src = fs.library_abs(old_rel)
        dst_parent = _ensure_dir(new_rel.rsplit("/", 1)[0] if "/" in new_rel else "")
        if src.is_dir():
            target = dst_parent / folder.name
            if target.exists():
                folder.parent_id = int(data.get("target_folder_id") or 0)  # 回滚 db
                db.commit()
                raise HTTPException(400, "目标位置已存在同名目录")
            src.rename(target)
        _rewrite_file_paths(db, old_rel, new_rel)
    return {"message": "已移动"}


def _rewrite_file_paths(db: Session, old_rel: str, new_rel: str):
    """目录改名/移动后，批量改写后代文件的 storage_path 前缀"""
    prefix_old = f"storage/library/{old_rel}/"
    prefix_new = f"storage/library/{new_rel}/"
    rows = db.query(FileModel).filter(
        FileModel.storage_path.like(prefix_old + "%")).all()
    for r in rows:
        r.storage_path = prefix_new + r.storage_path[len(prefix_old):]
    db.commit()


# ================= 文件操作 =================

@router.patch("/api/fs/file/{file_id}/rename")
def fs_file_rename(file_id: int, data: dict, db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    name = fs.sanitize_name(data.get("name") or "")
    if not name:
        raise HTTPException(400, "名称不能为空")
    src = fs.to_abs_local(f.storage_path)
    new_path = src.parent / name if src else None
    if src and src.is_file():
        if new_path.exists():
            raise HTTPException(400, "同名文件已存在")
        src.rename(new_path)
        f.storage_path = to_rel(new_path)
    f.name = name
    f.ext = Path(name).suffix.lstrip(".").lower()
    f.kind = fs.detect_kind(f.ext)
    db.commit()
    return {"message": "已重命名"}


@router.post("/api/fs/file/{file_id}/move")
def fs_file_move(file_id: int, data: dict, db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    target_id = int(data.get("target_folder_id") or 0)
    rel_dir = folder_rel_path(db, target_id) if target_id else ""
    if target_id and not db.query(Folder).filter(Folder.id == target_id, Folder.deleted == 0).first():
        raise HTTPException(400, "目标文件夹不存在")
    src = fs.to_abs_local(f.storage_path)
    if src and src.is_file():
        dst_dir = _ensure_dir(rel_dir)
        dst = _unique_path(dst_dir / f.name)
        src.rename(dst)
        f.storage_path = to_rel(dst)
    f.folder_id = target_id
    db.commit()
    return {"message": "已移动"}


# ---------- 回收站 ----------

def _trash_dir() -> Path:
    d = Path(settings.library_trash_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.delete("/api/fs/file/{file_id}")
def fs_file_delete(file_id: int, db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    src = fs.to_abs_local(f.storage_path)
    if src and src.is_file():
        dst = _unique_path(_trash_dir() / f.name)
        try:
            src.rename(dst)
            f.storage_path = to_rel(dst)
        except OSError:
            pass
    f.deleted = 1
    db.commit()
    return {"message": "已移入回收站"}


@router.delete("/api/fs/folder/{folder_id}")
def fs_folder_delete(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.deleted == 0).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    rel = folder_rel_path(db, folder_id)
    src = fs.library_abs(rel) if rel else None
    if src and src.is_dir():
        dst = _unique_path(_trash_dir() / folder.name)
        try:
            src.rename(dst)
            meta = {"type": "folder", "folder_id": folder_id,
                    "orig_rel": rel, "deleted_at": datetime.datetime.now().isoformat()}
            (dst / ".trash_meta.json").write_text(json.dumps(meta, ensure_ascii=False),
                                                  encoding="utf-8")
            # 后代文件 storage_path 指到回收站
            prefix_old = f"storage/library/{rel}/"
            for r in db.query(FileModel).filter(
                    FileModel.deleted == 0, FileModel.storage_path.like(prefix_old + "%")).all():
                r.storage_path = (to_rel(dst) + "/" + r.storage_path[len(prefix_old):])
        except OSError:
            pass
    ids = _folder_descendant_ids(db, folder_id)
    db.query(FileModel).filter(FileModel.folder_id.in_(ids), FileModel.deleted == 0)\
        .update({"deleted": 1}, synchronize_session=False)
    db.query(Folder).filter(Folder.id.in_(ids)).update({"deleted": 1},
                                                       synchronize_session=False)
    db.commit()
    return {"message": "已移入回收站"}


@router.get("/api/fs/trash")
def fs_trash_list(db: Session = Depends(get_db)):
    files = db.query(FileModel).filter(FileModel.deleted == 1).order_by(FileModel.id.desc()).limit(500).all()
    folders = db.query(Folder).filter(Folder.deleted == 1).order_by(Folder.id.desc()).limit(200).all()
    return {"files": [_file_info(f) for f in files],
            "folders": [{"id": f.id, "name": f.name} for f in folders]}


@router.post("/api/fs/file/{file_id}/restore")
def fs_file_restore(file_id: int, db: Session = Depends(get_db)):
    f = db.query(FileModel).filter(FileModel.id == file_id, FileModel.deleted == 1).first()
    if not f:
        raise HTTPException(404, "回收站中不存在该文件")
    src = fs.to_abs_local(f.storage_path)
    rel_dir = folder_rel_path(db, f.folder_id) if f.folder_id else ""
    if src and src.is_file():
        dst = _unique_path(_ensure_dir(rel_dir) / f.name)
        src.rename(dst)
        f.storage_path = to_rel(dst)
    f.deleted = 0
    db.commit()
    return {"message": "已恢复"}


@router.post("/api/fs/folder/{folder_id}/restore")
def fs_folder_restore(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.deleted == 1).first()
    if not folder:
        raise HTTPException(404, "回收站中不存在该文件夹")
    # 在回收站里找对应目录（.trash_meta.json 记录 folder_id）
    hit = None
    for d in _trash_dir().iterdir():
        if d.is_dir():
            meta_p = d / ".trash_meta.json"
            if meta_p.is_file():
                try:
                    if json.loads(meta_p.read_text(encoding="utf-8")).get("folder_id") == folder_id:
                        hit = d
                        break
                except Exception:
                    pass
    if hit:
        rel = folder_rel_path(db, folder.parent_id or 0) if folder.parent_id else ""
        dst = _unique_path(_ensure_dir(rel) / folder.name)
        hit.rename(dst)
        prefix_old = to_rel(hit) + "/"
        for r in db.query(FileModel).filter(FileModel.storage_path.like(prefix_old + "%")).all():
            r.storage_path = (f"storage/library/{rel}/{folder.name}/"
                              + r.storage_path[len(prefix_old):]) if rel else \
                             ("storage/library/" + folder.name + "/" + r.storage_path[len(prefix_old):])
        meta_p = hit / ".trash_meta.json"
        if meta_p.exists():
            meta_p.unlink()
    ids = _folder_descendant_ids(db, folder_id)
    db.query(FileModel).filter(FileModel.folder_id.in_(ids)).update({"deleted": 0}, synchronize_session=False)
    db.query(Folder).filter(Folder.id.in_(ids)).update({"deleted": 0}, synchronize_session=False)
    db.commit()
    return {"message": "已恢复"}


@router.delete("/api/fs/file/{file_id}/purge")
def fs_file_purge(file_id: int, db: Session = Depends(get_db)):
    f = db.query(FileModel).filter(FileModel.id == file_id, FileModel.deleted == 1).first()
    if not f:
        raise HTTPException(404, "回收站中不存在该文件")
    src = fs.to_abs_local(f.storage_path)
    if src and src.is_file():
        try:
            src.unlink()
        except OSError:
            pass
    shutil.rmtree(fs.artifacts_dir(file_id), ignore_errors=True)
    db.query(FileTask).filter(FileTask.file_id == file_id).delete()
    db.delete(f)
    db.commit()
    return {"message": "已彻底删除"}


@router.delete("/api/fs/folder/{folder_id}/purge")
def fs_folder_purge(folder_id: int, db: Session = Depends(get_db)):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.deleted == 1).first()
    if not folder:
        raise HTTPException(404, "回收站中不存在该文件夹")
    ids = _folder_descendant_ids(db, folder_id)
    for r in db.query(FileModel).filter(FileModel.folder_id.in_(ids)).all():
        src = fs.to_abs_local(r.storage_path)
        if src and src.is_file():
            try:
                src.unlink()
            except OSError:
                pass
        shutil.rmtree(fs.artifacts_dir(r.id), ignore_errors=True)
    db.query(FileTask).filter(FileTask.file_id.in_(
        [r.id for r in db.query(FileModel).filter(FileModel.folder_id.in_(ids)).all()]))\
        .delete(synchronize_session=False)
    db.query(FileModel).filter(FileModel.folder_id.in_(ids)).delete(synchronize_session=False)
    db.query(Folder).filter(Folder.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"message": "已彻底删除"}


# ================= 上传 =================

@router.post("/api/fs/upload")
async def fs_upload(
    folder_id: int = Form(0),
    rel_path: str = Form(""),          # webkitdirectory 相对路径（可选，自动建子目录）
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if folder_id and not db.query(Folder).filter(
            Folder.id == folder_id, Folder.deleted == 0).first():
        raise HTTPException(400, "目标文件夹不存在")

    name = fs.sanitize_name(file.filename or "未命名")
    ext = Path(name).suffix.lstrip(".").lower()
    if ext in fs.UPLOAD_DENY_EXTS:
        raise HTTPException(400, f"不允许上传 {ext} 类文件")

    # webkitdirectory：按相对路径自动建子目录
    rel_dir = folder_rel_path(db, folder_id) if folder_id else ""
    if rel_path:
        parts = [fs.sanitize_name(x) for x in Path(rel_path.replace("\\", "/")).parts[:-1]]
        if parts:
            parent = folder_id
            for p in parts:
                exist = db.query(Folder).filter(Folder.parent_id == parent,
                                                Folder.name == p, Folder.deleted == 0).first()
                if not exist:
                    exist = Folder(subject="all", name=p, parent_id=parent)
                    db.add(exist)
                    db.commit()
                    db.refresh(exist)
                parent = exist.id
            folder_id = parent
            rel_dir = folder_rel_path(db, folder_id)

    dst_dir = _ensure_dir(rel_dir)
    dst = _unique_path(dst_dir / name)
    with open(dst, "wb") as out:
        shutil.copyfileobj(file.file, out)
    size = dst.stat().st_size
    digest = await _sha256_async(dst)

    # 去重：同目录同哈希 → 丢弃刚拷的副本，返回已有记录
    dup = db.query(FileModel).filter(FileModel.deleted == 0, FileModel.folder_id == folder_id,
                                FileModel.sha256 == digest).first()
    if dup:
        dst.unlink(missing_ok=True)
        return {"id": dup.id, "name": dup.name, "duplicate": True, "message": "同名同内容文件已存在，已跳过"}

    kind = fs.detect_kind(ext)
    row = FileModel(folder_id=folder_id or 0, name=dst.name, ext=ext, kind=kind,
               size=size, sha256=digest, storage_path=to_rel(dst),
               status="ready", sort_order=9999)
    db.add(row)
    db.commit()
    db.refresh(row)

    # Office/PDF 自动进产物管线（转换→提取→总结）
    if kind in ("office", "pdf"):
        fs.enqueue_pipeline(row.id)
    return {"id": row.id, "name": row.name, "kind": kind, "duplicate": False,
            "status": row.status, "message": "上传成功"}


async def _sha256_async(path: Path) -> str:
    import asyncio
    return await asyncio.to_thread(fs.sha256_file, path)


# ================= 查看器 / 产物 =================

@router.get("/api/files/{file_id}/raw")
def file_raw(file_id: int, download: int = Query(0), db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    src = fs.to_abs_local(f.storage_path)
    if not src:
        raise HTTPException(404, "文件不存在")
    media = MIME.get(f"::{f.ext}".replace("::", "."), "application/octet-stream")
    return FileResponse(str(src), media_type=media,
                        filename=f.name if download else None)


@router.get("/api/files/{file_id}/view")
def file_view(file_id: int, db: Session = Depends(get_db)):
    """查看流：Office 类优先给转换后的 derived.pdf，其余给原文件"""
    f = _get_file(db, file_id)
    derived = fs.artifact_path(file_id, "derived.pdf")
    if f.kind in ("office", "pdf") and derived.is_file():
        return FileResponse(str(derived), media_type="application/pdf")
    src = fs.to_abs_local(f.storage_path)
    if not src:
        raise HTTPException(404, "文件不存在")
    media = MIME.get("." + f.ext, "application/octet-stream")
    return FileResponse(str(src), media_type=media)


@router.get("/api/files/{file_id}/artifacts/{name}")
def file_artifact(file_id: int, name: str, db: Session = Depends(get_db)):
    _get_file(db, file_id)
    if name not in fs.ARTIFACT_NAMES:
        raise HTTPException(400, "未知产物")
    p = fs.artifact_path(file_id, name)
    if not p.is_file():
        raise HTTPException(404, "产物不存在（可能仍在处理）")
    media = {"thumb.png": "image/png", "derived.pdf": "application/pdf"}.get(name,
             "text/plain; charset=utf-8")
    return FileResponse(str(p), media_type=media)


@router.get("/api/files/{file_id}/summary")
def file_summary(file_id: int, db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    content = di.read_artifact(file_id, "summary.md") or ""
    return {"id": file_id, "name": f.name, "status": f.status,
            "content": content, "has_summary": bool(content.strip())}


@router.get("/api/files/{file_id}/status")
def file_status(file_id: int, db: Session = Depends(get_db)):
    f = _get_file(db, file_id)
    tasks = db.query(FileTask).filter(FileTask.file_id == file_id)\
        .order_by(FileTask.id.asc()).all()
    return {
        "id": f.id, "name": f.name, "kind": f.kind, "status": f.status,
        "status_msg": f.status_msg or "",
        "artifacts": {n: fs.artifact_path(file_id, n).is_file()
                      for n in fs.ARTIFACT_NAMES},
        "tasks": [{"id": t.id, "type": t.task_type, "status": t.status,
                   "progress": t.progress, "error": t.error} for t in tasks],
    }


@router.get("/api/files/{file_id}/text")
def file_text(file_id: int, page: int = Query(0), db: Session = Depends(get_db)):
    """逐页/全文文本（查看器划词、agent 引用定位用）"""
    _get_file(db, file_id)
    raw = di.read_artifact(file_id, "pages.json")
    if not raw:
        raise HTTPException(404, "尚未提取文本")
    pages = json.loads(raw)
    if page:
        for p in pages:
            if p["page"] == page:
                return {"page": page, "text": p["text"]}
        raise HTTPException(404, f"没有第 {page} 页")
    return {"pages": len(pages),
            "text": "\n\n".join(f"[第{p['page']}页]\n{p['text']}" for p in pages)}


@router.post("/api/files/{file_id}/reprocess")
def file_reprocess(file_id: int, db: Session = Depends(get_db)):
    """手动重跑产物管线（转换/提取/总结）"""
    f = _get_file(db, file_id)
    if f.kind not in ("office", "pdf"):
        raise HTTPException(400, f"{f.kind} 类型无需处理")
    # 清掉旧的失败/待办任务重新排队
    db.query(FileTask).filter(
        FileTask.file_id == file_id,
        FileTask.status.in_(("pending", "running", "error"))).delete()
    db.commit()
    fs.enqueue_pipeline(file_id)
    return {"message": "已重新排队处理"}


@router.get("/api/fs/search")
def fs_search(keyword: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """全局搜索：文件名（含子串）。返回平铺结果（带目录路径）"""
    kw = f"%{(keyword or '').strip()}%"
    rows = db.query(FileModel).filter(FileModel.deleted == 0, FileModel.name.like(kw)).limit(100).all()
    out = []
    for r in rows:
        out.append({**_file_info(r), "path": folder_rel_path(db, r.folder_id or 0)})
    return {"results": out}

# ================= 页面/帧转图（截图复用：同页重复提问不重传） =================

@router.get("/api/files/{file_id}/page-image")
def file_page_image(file_id: int, page: int = Query(...), db: Session = Depends(get_db)):
    """把 PDF 的某一页渲染成 JPEG（长边 1600px），缓存为 artifacts/{id}/snap_p{N}.jpg"""
    f = _get_file(db, file_id)
    if f.kind not in ("office", "pdf"):
        raise HTTPException(400, "仅 PDF/Office 文件支持页转图")
    snap = fs.artifact_path(file_id, f"snap_p{page}.jpg")
    if snap.is_file() and snap.stat().st_size > 0:
        return FileResponse(str(snap), media_type="image/jpeg")
    # 找源 PDF：office 用 derived.pdf，pdf 用原文件
    src = fs.artifact_path(file_id, "derived.pdf")
    if not src.is_file():
        src = fs.to_abs_local(f.storage_path)
    if not src or not Path(src).is_file():
        raise HTTPException(404, "源 PDF 不存在")
    try:
        import fitz
        with fitz.open(str(src)) as doc:
            if page < 1 or page > len(doc):
                raise HTTPException(404, f"页码超出范围（共 {len(doc)} 页）")
            pg = doc[page - 1]
            zoom = 1600.0 / max(pg.rect.width, 1)
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            fs.write_bytes(file_id, f"snap_p{page}.jpg", pix.tobytes("jpeg"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"页渲染失败: {e}")
    return FileResponse(str(snap), media_type="image/jpeg")


# ================= 章节大纲（获取 / 编辑 / 懒生成摘要） =================

@router.get("/api/files/{file_id}/outline")
def file_outline(file_id: int, db: Session = Depends(get_db)):
    _get_file(db, file_id)
    ol = di.get_outline(file_id)
    if not ol:
        raise HTTPException(404, "尚未生成章节大纲")
    return ol


@router.post("/api/files/{file_id}/outline/rename")
def outline_rename(file_id: int, data: dict, db: Session = Depends(get_db)):
    """重命名章节：{cid, title}"""
    _get_file(db, file_id)
    ol = di.get_outline(file_id)
    if not ol:
        raise HTTPException(404, "无大纲")
    ch = next((c for c in ol["chapters"] if c["id"] == data.get("cid")), None)
    if not ch:
        raise HTTPException(404, "章节不存在")
    ch["title"] = fs.sanitize_name(data.get("title") or "")[:80] or ch["title"]
    di.save_outline(file_id, ol)
    return {"message": "已重命名", "chapter": ch}


@router.post("/api/files/{file_id}/outline/merge")
def outline_merge(file_id: int, data: dict, db: Session = Depends(get_db)):
    """合并到上一章：{cid}（范围并入前一章，子节转移）"""
    _get_file(db, file_id)
    ol = di.get_outline(file_id)
    chapters = ol.get("chapters") or []
    idx = next((i for i, c in enumerate(chapters) if c["id"] == data.get("cid")), None)
    if idx is None or idx == 0:
        raise HTTPException(400, "不能合并（不存在或已是第一个）")
    cur, prev = chapters[idx], chapters[idx - 1]
    prev["end"] = max(prev["end"], cur["end"])
    for c in chapters:
        if c.get("parent_id") == cur["id"]:
            c["parent_id"] = prev["id"]
    chapters.pop(idx)
    di.save_outline(file_id, ol)
    return {"message": "已合并"}


@router.post("/api/files/{file_id}/outline/split")
def outline_split(file_id: int, data: dict, db: Session = Depends(get_db)):
    """在指定页拆分：{cid, page}"""
    _get_file(db, file_id)
    ol = di.get_outline(file_id)
    chapters = ol.get("chapters") or []
    idx = next((i for i, c in enumerate(chapters) if c["id"] == data.get("cid")), None)
    if idx is None:
        raise HTTPException(404, "章节不存在")
    cur = chapters[idx]
    page = int(data.get("page") or 0)
    if not (cur["start"] < page <= cur["end"]):
        raise HTTPException(400, f"拆分页需在 {cur['start'] + 1}-{cur['end']} 之间")
    new_ch = {
        "id": f"c{max(int(c['id'][1:]) for c in chapters) + 1}",
        "parent_id": cur.get("parent_id"), "level": cur["level"],
        "title": cur["title"] + "（下）", "start": page, "end": cur["end"],
    }
    cur["end"] = page - 1
    cur["title"] = cur["title"] + "（上）"
    for c in chapters:
        if c.get("parent_id") == cur["id"] and c["start"] >= page:
            c["parent_id"] = new_ch["id"]
    chapters.insert(idx + 1, new_ch)
    di.save_outline(file_id, ol)
    return {"message": "已拆分", "new_id": new_ch["id"]}


@router.post("/api/files/{file_id}/chapter-summary")
async def chapter_summary(file_id: int, data: dict, db: Session = Depends(get_db)):
    """章节摘要懒生成：{cid, manual?}。已缓存直接返回。"""
    f = _get_file(db, file_id)
    ol = di.get_outline(file_id)
    if not ol:
        raise HTTPException(404, "无大纲")
    ch = next((c for c in ol["chapters"] if c["id"] == data.get("cid")), None)
    if not ch:
        raise HTTPException(404, "章节不存在")
    cid = ch["id"]
    cached = di.read_artifact(file_id, f"chap_{cid}.md")
    if cached:
        return {"cached": True, "chapter": ch["title"], "content": cached}
    raw = di.read_artifact(file_id, "pages.json")
    if not raw:
        raise HTTPException(404, "无页面文本（先提取）")
    pages = json.loads(raw)
    manual = bool(data.get("manual"))
    if not di.llm_gate_ok(file_id, "chapter", manual=manual):
        raise HTTPException(429, f"已达自动上限（{di.MAX_LLM_CALLS_PER_DOC} 次），可手动重试")
    from ...services.doc_index import compose_chapter_prompt
    prompt = di.compose_chapter_prompt(f.name, ol, pages, ch)
    from ...services.llm_service import SUMMARY_LOCK, chat_completion_simple
    async with SUMMARY_LOCK:
        content = await chat_completion_simple([{"role": "user", "content": prompt}],
                                               max_tokens=2500, temperature=0.3)
    if not (content or "").strip():
        raise HTTPException(500, "生成内容为空")
    di.write_text(file_id, f"chap_{cid}.md", content)
    di.llm_record(file_id, "chapter", auto=not manual)
    return {"cached": False, "chapter": ch["title"], "content": content}


@router.get("/api/files/{file_id}/llm-usage")
def file_llm_usage(file_id: int, db: Session = Depends(get_db)):
    _get_file(db, file_id)
    return di.llm_usage(file_id)


@router.get("/api/files/{file_id}/search")
def file_search(file_id: int, q: str = Query(..., min_length=1),
                top: int = Query(3), db: Session = Depends(get_db)):
    _get_file(db, file_id)
    return {"hits": di.search_chunks(file_id, q, top_n=max(1, min(top, 5)))}


# ================= 科目知识地图 =================

def _subtree_ids(db, fid):
    out, frontier, hops = [], [fid], 0
    while frontier and hops < 64:
        nxt = [f.id for f in db.query(Folder).filter(Folder.parent_id.in_(frontier)).all()
               if f.id not in out]
        out += nxt
        frontier = nxt
        hops += 1
    return [fid] + out


@router.get("/api/subject-map")
def subject_map_get(subject: str = Query(...), db: Session = Depends(get_db)):
    return {"subject": subject, "content": di.get_subject_map(subject)}


@router.post("/api/subject-map/rebuild")
async def subject_map_rebuild(data: dict, db: Session = Depends(get_db)):
    """收集该科目所有文件的 overview，LLM 合并成一张学科知识地图"""
    subject = (data.get("subject") or "").strip()
    if not subject:
        raise HTTPException(400, "科目不能为空")
    docs = []
    for top in db.query(Folder).filter(Folder.name == subject, Folder.deleted == 0).all():
        ids = _subtree_ids(db, top.id)
        rows = db.query(FileModel).filter(FileModel.deleted == 0,
                                          FileModel.folder_id.in_(ids)).all()
        for r in rows:
            ov = di.read_artifact(r.id, "overview.md") or di.read_artifact(r.id, "summary.md")
            if ov and ov.strip():
                docs.append((r.name, ov[:1500]))
    if not docs:
        raise HTTPException(404, "该科目下还没有已生成总览的文档")
    from ...services.doc_index import compose_subject_map
    from ...services.llm_service import SUMMARY_LOCK, chat_completion_simple
    prompt = compose_subject_map(subject, docs)
    async with SUMMARY_LOCK:
        content = await chat_completion_simple([{"role": "user", "content": prompt}],
                                               max_tokens=1800, temperature=0.3)
    if not (content or "").strip():
        raise HTTPException(500, "生成内容为空")
    di.save_subject_map(subject, content)
    return {"subject": subject, "docs": len(docs)}


# ============ 设置：上下文编排模板 + 转换引擎（透明化层，共识 D5） ============

@router.get("/api/fs/settings")
def fs_settings():
    """编排器配置 + x2t 引擎状态（设置页用；非黑盒：每个模板字段可见可改）"""
    from ...services.orchestrator import load_orchestrator_config, TEMPLATES_DEFAULT
    cfg = load_orchestrator_config()
    x2t = fs._x2t_config()
    soffice = bool(fs._find_soffice())   # 与转换实际用的探测逻辑一致（PATH + 常见安装目录）
    return {
        "orchestrator": cfg,
        "templates_default": TEMPLATES_DEFAULT,
        "x2t": {"path": x2t.get("path", ""), "configured": bool(x2t.get("path")),
                "found": bool(x2t.get("path") and Path(x2t["path"]).is_file())},
        "libreoffice": soffice,
    }


@router.post("/api/fs/settings")
def fs_settings_save(data: dict):
    """保存编排模板（按 kind 逐字段）+ x2t 路径，保存即生效"""
    from ...services.orchestrator import load_orchestrator_config, save_orchestrator_config
    cfg = load_orchestrator_config()
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    for kind, tpl in (data.get("templates") or {}).items():
        base = cfg["templates"].setdefault(kind, {})
        for k, v in tpl.items():
            if k in base:
                base[k] = v
    save_orchestrator_config(cfg)
    x2t_in = data.get("x2t") or {}
    if "path" in x2t_in:
        from ...models import PlatformConfig
        from ...core.database import SessionLocal
        sdb = SessionLocal()
        try:
            row = sdb.query(PlatformConfig).filter(PlatformConfig.key == "x2t").first()
            val = json.dumps({"path": (x2t_in.get("path") or "").strip()}, ensure_ascii=False)
            if row:
                row.value = val
            else:
                sdb.add(PlatformConfig(key="x2t", value=val))
            sdb.commit()
        finally:
            sdb.close()
    return {"message": "已保存，立即生效"}

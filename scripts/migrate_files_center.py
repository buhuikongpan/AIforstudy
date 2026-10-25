# -*- coding: utf-8 -*-
"""文件中心迁移脚本：videos → files（保留原 id，幂等可重复执行）

做法（与 开发计划-全资料AI辅助.md §2.6 一致）：
1. videos 表每行 → files 行（id 不变，kind=video，storage_path 沿用旧相对路径——
   物理文件留在 storage/videos/ 不搬，core/paths.resolve() 继续能找到）
2. 视频特有字段 → video_meta（1:1）
3. 旧字幕 storage/subtitles/{id}.txt / 旧总结 storage/summaries/{id}.md
   → 拷贝为 storage/artifacts/{id}/extracted.txt / summary.md（原文件保留）
4. folders 不动（继续作为树的一部分）

用法：python scripts/migrate_files_center.py [--dry-run]
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.models import Video, File, VideoMeta  # noqa: E402
from app.core.config import settings  # noqa: E402


def main(dry_run: bool = False):
    init_db()  # 建新表（files / video_meta / file_tasks）
    db = SessionLocal()
    videos = db.query(Video).all()
    print(f"发现 {len(videos)} 个视频记录")

    created = skipped = meta_created = artifacts = 0
    for v in videos:
        exists = db.query(File).filter(File.id == v.id).first()
        if exists:
            skipped += 1
            continue
        if dry_run:
            created += 1
            continue
        row = File(
            id=v.id,
            folder_id=v.folder_id or 0,
            name=v.filename,
            ext=(Path(v.filename).suffix.lstrip(".") or "").lower(),
            kind="video",
            size=v.file_size or 0,
            sha256="",
            storage_path=v.file_path,
            status="ready",
            status_msg="",
            sort_order=v.sort_order or 9999,
        )
        db.add(row)
        created += 1

        if not db.query(VideoMeta).filter(VideoMeta.file_id == v.id).first():
            db.add(VideoMeta(
                file_id=v.id,
                duration=v.duration or 0,
                subtitle_path=v.subtitle_path or "",
                subtitle_status=v.subtitle_status or "pending",
                summary_path=v.summary_path or "",
                summary_status=v.summary_status or "none",
                compressed_path=v.compressed_path or "",
                compressed_status=v.compressed_status or "none",
            ))
            meta_created += 1

        # 旧产物 → artifacts/{id}/（拷贝，原件不动）
        art = Path(settings.artifacts_dir) / str(v.id)
        art.mkdir(parents=True, exist_ok=True)
        if v.subtitle_path:
            sp = Path(settings.storage_dir) / v.subtitle_path.replace("storage/", "", 1) \
                if v.subtitle_path.startswith("storage/") else Path(v.subtitle_path)
            if not sp.is_file():
                sp = Path(settings.storage_dir).parent / v.subtitle_path
            if sp.is_file():
                dst = art / "extracted.txt"
                if not dst.exists():
                    shutil.copy2(sp, dst)
                    artifacts += 1
        if v.summary_status == "done" and v.summary_path:
            mp = Path(settings.storage_dir) / v.summary_path.replace("storage/", "", 1) \
                if v.summary_path.startswith("storage/") else Path(v.summary_path)
            if not mp.is_file():
                mp = Path(settings.storage_dir).parent / v.summary_path
            if mp.is_file():
                dst = art / "summary.md"
                if not dst.exists():
                    shutil.copy2(mp, dst)
                    artifacts += 1

    if not dry_run:
        db.commit()
    db.close()
    print(f"完成：files 新建 {created}，跳过（已存在）{skipped}，"
          f"video_meta 新建 {meta_created}，产物拷贝 {artifacts}")
    if dry_run:
        print("（dry-run，未写库）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    main(ap.parse_args().dry_run)

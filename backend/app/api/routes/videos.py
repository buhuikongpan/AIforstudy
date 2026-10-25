"""视频管理接口"""
import os
import re
import shutil
import subprocess
import asyncio
import datetime
from pathlib import Path
from typing import List
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.database import SessionLocal
from ...core.paths import resolve, to_abs, to_rel
from ...models import Video, Subtitle, Folder
from ..deps import get_db
from ...services.asr_service import transcribe_audio
from ...services.llm_service import summarize_lecture, SUMMARY_LOCK


router = APIRouter(prefix="/api/videos", tags=["视频管理"])

# 全局串行锁：同一时刻只允许一个视频在执行字幕提取（ASR），避免并发触发云接口限流
_extract_lock = asyncio.Lock()
# 课程总结生成同样串行（分段提取 + 合并要调多次 LLM，防并发限流）——
# 与文档总结（file_service）共用 llm_service.SUMMARY_LOCK，两边不会同时打 LLM
_summary_lock = SUMMARY_LOCK

# 批量补生成总结的进度状态（一键补齐存量视频）
_batch_state = {"running": False, "queued": 0, "done": 0, "failed": 0}

SUBJECTS = {"math": "数学", "english": "英语", "politics": "政治", "zhuanye": "专业课"}

SEGMENT_SEC = 1200  # 总结分段：20 分钟一段

# 逐句对齐参数：云 ASR 的 50s 窗口只给整段文本、无句子时间戳。
MS_PER_CHAR = 220    # 正常中文语速约每字 220 毫秒
SNAP_WINDOW = 2.0    # 句尾吸附静音中点的最大偏差（秒）

MIN_SENT_SEC = 0.5   # 单句最短时长（秒）
GAP_SEC = 0.05       # 句与句之间的微小气口（防严格无缝）


def _per_sentence_align(chunks, silences, voice_start=2.0,
                        ms_per_char=MS_PER_CHAR, snap_window=SNAP_WINDOW,
                        video_id=0, subject=""):
    """逐句对齐（云引擎无句子时间戳时的替代方案）：

    一次识别 50s 得到整段连续文本 → 切成一句一句 → 逐句顺序排位：
      每句时长 ≈ 字数 × ms_per_char；句尾吸附到最近的停顿中点。
    相比旧的"字符占比硬摊整段"，逐句累积 + 停顿校准对语速不均要准得多。
    返回: [{"video_id","subject","start_time","end_time","text","seq"}, ...]
    """
    import re as _re

    # 停顿中点候选（跳过开头 2 秒空白，避免片头空白被当作句尾）
    split_candidates = sorted({
        (s + e) / 2.0 for s, e in (silences or []) if (s + e) / 2.0 > 2.0
    })

    rows = []
    seq = 0
    prev_end = -1e9   # 全局上一句 end：吸附/封顶都不得早于它（保证时间轴单调）

    # 预计算每段起点：chunk["start"] 是识别窗口的段起点（0/50/100…）
    seg_starts = [float(c.get("start") or 0) for c in chunks]
    n_seg = len(seg_starts)

    for idx, chunk in enumerate(chunks):
        offset = float(chunk.get("start") or 0)
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        # 本段硬高水位：中间段用"下一段起点"封顶（防溢出到下一段）；最后一段无下一段
        # 则不设上限（260ms/字累积自然结束，避免用错误的 CHUNK_SEC 误压智谱30s/腾讯60s）
        seg_end_hi = seg_starts[idx + 1] if idx + 1 < n_seg else None

        # ---- 切句：按句号/问号/感叹号断句；逗号片段并入所在句后再合并短句 ----
        by_sent = _re.split(r'(?<=[。？！])', text)
        by_comma = []
        for s in by_sent:
            if '，' in s or '、' in s or '；' in s:
                for p in _re.split(r'(?<=[，、；])', s):
                    if p.strip():
                        by_comma.append(p.strip())
            else:
                s2 = s.strip()
                if s2:
                    by_comma.append(s2)
        # 短句(<8字)并入前一句，形成完整断句单元
        final = []
        for s in by_comma:
            if final and len(s) < 8:
                final[-1] += s
            else:
                final.append(s)
        if not final:
            continue

        # 本段起点：第一段用音量检测到的人声起点；否则该段窗口起点（不得早于上一句 end）
        cur = max(voice_start if (offset == 0 and seg_starts[0] <= 0.1) else offset,
                  prev_end + GAP_SEC)

        for s in final:
            n = len(s)
            duration = max(n * (ms_per_char / 1000.0), MIN_SENT_SEC)
            start = cur
            end = start + duration
            # 句尾吸附最近的停顿中点（±snap_window；无邻近停顿则保留估长 end）
            if split_candidates:
                best = None
                best_dist = 1e9
                for cp in split_candidates:
                    d = abs(cp - end)
                    if d < best_dist:
                        best_dist = d
                        best = cp
                # 吸附需满足：窗口内 + 不早于上一句 end + 不早于本句 start（防倒退）
                if best is not None and best_dist <= snap_window \
                        and best >= prev_end + GAP_SEC and best > start + 0.05:
                    end = best
            # 封顶：不越过本段高水位与下段；最后一段(seg_end_hi=None)不封顶
            if seg_end_hi is not None and end > seg_end_hi:
                end = max(seg_end_hi, prev_end + GAP_SEC)
            if end <= start:
                end = start + MIN_SENT_SEC

            rows.append({
                "video_id": video_id,
                "subject": subject,
                "start_time": round(start, 1),
                "end_time": round(end, 1),
                "text": s,
                "seq": seq,
            })
            seq += 1
            prev_end = end
            cur = end + GAP_SEC

    return rows


def _fmt_ts(sec: float) -> str:
    m, s = int(sec // 60), int(sec % 60)
    return f"{m:02d}:{s:02d}"


def _split_subtitle_segments(subs) -> list:
    """把 (start_time, text) 字幕按 20 分钟聚合成带时间戳的文本段

    分段提取是保证总结信息密度的关键：模型逐段扫描小段文本，
    不会像一次性压缩全文那样稀释细节。
    """
    segments, cur, cur_start = [], [], None
    for s in subs:
        if cur_start is None or s.start_time - cur_start >= SEGMENT_SEC:
            if cur:
                segments.append("".join(cur))
            cur = [f"[{_fmt_ts(s.start_time)}] {s.text}"]
            cur_start = s.start_time
        else:
            cur.append(f"[{_fmt_ts(s.start_time)}] {s.text}")
    if cur:
        segments.append("".join(cur))
    return segments


def _get_video_duration(file_path: str) -> float:
    """用 ffprobe 获取视频时长（秒）。

    CPU/IO 密集（subprocess），供 async 流程用时放进线程池（run_in_threadpool），
    避免阻塞事件循环；rescan（sync route → starlette 本身在线程池跑）直接调用即可。
    """
    return _probe_duration(file_path)


def _probe_duration(file_path: str) -> float:
    """真正的 ffprobe 调用（纯同步，供线程池包装）。"""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ], capture_output=True, text=True, timeout=60)
        if result.stdout:
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


def rescan_videos(db: Session = Depends(get_db)):
    """扫描 storage/videos 目录恢复数据库"""
    import glob
    # 一次性载入全部现有 file_path，避免循环内逐条 SELECT（N+1 → 1）
    # 库里存的是相对项目根的路径（core/paths.py），比较前先归一
    existing = {to_rel(p) for (p,) in db.query(Video.file_path).all()}
    added = 0
    for subject in ["math", "english", "politics", "zhuanye"]:
        pattern = os.path.join(settings.video_dir, subject, "*")
        for file_path in glob.glob(pattern):
            filename = os.path.basename(file_path)
            if not filename.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv')):
                continue
            rel_path = to_rel(file_path)
            if rel_path in existing:
                continue
            sort_order = 9999
            nums = re.findall(r'(\d+)', Path(filename).stem)
            if nums:
                sort_order = int(nums[-1])
            duration = _get_video_duration(file_path)
            video = Video(
                subject=subject,
                filename=filename,
                title=Path(filename).stem,
                file_path=rel_path,
                file_size=os.path.getsize(file_path),
                duration=duration,
                sort_order=sort_order,
                subtitle_status="pending",
            )
            db.add(video)
            existing.add(rel_path)
            added += 1
    db.commit()
    return {"message": f"扫描完成，新增 {added} 个视频"}


@router.post("/rescan")
def rescan(db: Session = Depends(get_db)):
    return rescan_videos(db)


@router.get("/subjects")
def get_subjects():
    return {"subjects": SUBJECTS}


def _validate_folder(db: Session, subject: str, folder_id: int) -> int:
    """校验上传目标文件夹：0=未分类；非 0 时须存在且属于同科目。返回有效的 folder_id。"""
    if not folder_id:
        return 0
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if not folder:
        raise HTTPException(400, "目标文件夹不存在")
    if folder.subject != subject:
        raise HTTPException(400, "目标文件夹不属于当前科目")
    return folder_id


async def _save_single_video(
    subject: str,
    file: UploadFile,
    title: str,
    db: Session,
    folder_id: int = 0,
) -> dict:
    """保存单个视频到磁盘和数据库"""
    folder_id = _validate_folder(db, subject, folder_id)
    subject_dir = Path(settings.video_dir) / subject
    subject_dir.mkdir(parents=True, exist_ok=True)

    # 从文件名提取序号（取最后一位数字，如 "01_导数.mp4" → 1）
    sort_order = 9999
    name_no_ext = Path(file.filename).stem
    nums = re.findall(r'(\d+)', name_no_ext)
    if nums:
        sort_order = int(nums[-1])

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # 清理文件名：webkitdirectory 会把路径带进来，如 "文件夹/视频.mp4"
    clean_name = file.filename.replace("/", "_").replace("\\", "_")
    safe_filename = f"{timestamp}_{clean_name}"
    file_path = subject_dir / safe_filename

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = os.path.getsize(file_path)
    # ffprobe 探测时长走线程池，避免阻塞事件循环（大文件/远端盘可能较慢）
    duration = await asyncio.to_thread(_probe_duration, str(file_path))

    video = Video(
        subject=subject,
        filename=clean_name,
        title=title or clean_name,
        file_path=to_rel(file_path),
        file_size=file_size,
        duration=duration,
        subtitle_status="pending",
        sort_order=sort_order,
        folder_id=folder_id,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    return {
        "id": video.id,
        "filename": video.filename,
        "title": video.title,
        "file_size": file_size,
        "subject": subject,
        "subtitle_status": video.subtitle_status,
        "message": "上传成功",
    }


@router.post("/upload")
async def upload_video(
    subject: str = Form(...),
    title: str = Form(""),
    folder_id: int = Form(0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传单个视频"""
    if subject not in SUBJECTS:
        raise HTTPException(400, f"不支持的科目: {subject}")
    return await _save_single_video(subject, file, title, db, folder_id)


@router.post("/upload-batch")
async def upload_videos_batch(
    subject: str = Form(...),
    folder_id: int = Form(0),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """批量上传视频"""
    if subject not in SUBJECTS:
        raise HTTPException(400, f"不支持的科目: {subject}")
    if len(files) > 50:
        raise HTTPException(400, "单次最多上传 50 个视频")

    results = []
    errors = []
    for file in files:
        try:
            result = await _save_single_video(subject, file, "", db, folder_id)
            results.append(result)
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})

    return {
        "total": len(files),
        "success": len(results),
        "failed": len(errors),
        "videos": results,
        "errors": errors,
    }


@router.get("/list")
def list_videos(
    subject: str = Query("", description="筛选科目"),
    db: Session = Depends(get_db),
):
    """获取视频列表"""
    query = db.query(Video)
    if subject and subject in SUBJECTS:
        query = query.filter(Video.subject == subject)
    videos = query.order_by(Video.sort_order.asc(), Video.filename.asc()).all()

    return {
        "videos": [
            {
                "id": v.id,
                "subject": v.subject,
                "subject_name": SUBJECTS.get(v.subject, v.subject),
                "filename": v.filename,
                "title": v.title,
                "file_size": v.file_size,
                "duration": v.duration,
                "sort_order": v.sort_order,
                "subtitle_status": v.subtitle_status,
                "summary_status": v.summary_status,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ]
    }


@router.get("/stream/{video_id}")
def stream_video(video_id: int, db: Session = Depends(get_db)):
    """流式播放视频。

    FileResponse（starlette ≥0.36）原生支持 HTTP Range（206 / 416 / If-Range），
    浏览器请求头里自带 Range 即实现拖拽跳播，无需手写分段逻辑。
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    src = resolve(video.file_path)
    if not src:
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(str(src), media_type="video/mp4")


@router.get("/{video_id}/subtitles")
def get_subtitles(video_id: int, db: Session = Depends(get_db)):
    """获取视频字幕（含时间戳）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")

    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.seq).all()

    return {
        "video_id": video_id,
        "subtitle_status": video.subtitle_status,
        "subtitles": [
            {"seq": s.seq, "start": s.start_time, "end": s.end_time, "text": s.text}
            for s in subtitles
        ],
    }


@router.get("/{video_id}/subtitles.vtt")
def get_subtitles_vtt(video_id: int, db: Session = Depends(get_db)):
    """WebVTT 格式字幕 → HTML5 <track> 实时显示在视频画面上"""
    from fastapi.responses import PlainTextResponse
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.seq).all()
    if not subtitles:
        raise HTTPException(404, "暂无字幕")

    def _fmt(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}"

    lines = ["WEBVTT\n"]
    for i, sub in enumerate(subtitles, 1):
        lines.append(str(i))
        # line:90% 保证字幕固定在底部同一位置，不会上下跳动
        lines.append(f"{_fmt(sub.start_time)} --> {_fmt(sub.end_time)} line:90%")
        lines.append(sub.text)
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/vtt; charset=utf-8")


@router.post("/{video_id}/extract-subtitle")
async def extract_subtitle(video_id: int, db: Session = Depends(get_db)):
    """提取视频字幕（ASR）—— 串行处理：全局锁保证同一时刻只有一个视频在识别，
    多个请求自动排队等待，避免并发触发云 ASR 限流/超时
    """
    async with _extract_lock:
        return await _extract_subtitle_impl(video_id, db)


async def _extract_subtitle_impl(video_id: int, db: Session):
    """提取视频字幕（ASR）实际处理体"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")

    # 如果卡在 processing 超过 30 分钟，自动重置
    if video.subtitle_status == "processing":
        if video.created_at:
            elapsed = (datetime.datetime.utcnow() - video.created_at).total_seconds()
            if elapsed < 1800:  # 30分钟以内，还在正常处理
                raise HTTPException(400, "正在提取中，请稍候")
        # 超过 30 分钟，视为卡住，重置重试
        video.subtitle_status = "pending"
        db.commit()

    video.subtitle_status = "processing"
    video.created_at = datetime.datetime.utcnow()  # 刷新开始时间
    db.commit()

    try:
        src = resolve(video.file_path)
        if not src:
            raise HTTPException(404, "视频文件不存在")
        file_path = str(src)

        # 1. 用 ffmpeg 提取音频（耗时，放线程池避免阻塞事件循环）
        audio_path = file_path + ".mp3"
        await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-i", file_path, "-vn", "-ar", "16000", "-ac", "1", audio_path, "-y"],
            capture_output=True,
            timeout=3600,
        )

        # 2. 读取音频并调用 ASR（含静音检测）
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        asr_result = await transcribe_audio(audio_data, audio_format="mp3", audio_path=audio_path)
        os.remove(audio_path)

        if not asr_result or not asr_result.get("chunks"):
            video.subtitle_status = "failed"
            db.commit()
            raise HTTPException(500, "语音识别失败")

        chunks = asr_result["chunks"]
        silences = asr_result.get("silences", [])
        duration = asr_result.get("duration", 0)

        # 3. 保存原文字幕
        plain_text = "\n".join(c["text"] for c in chunks)
        sub_file = Path(settings.subtitle_dir) / f"{video_id}.txt"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text(plain_text, encoding="utf-8")

        # 3.5 本地精确时间轴：每条 chunk 已带真实 start/end，直接落库，无需字符占比近似
        if asr_result.get("precise"):
            db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()
            rows = []
            for c in chunks:
                st = float(c["start"])
                en = float(c["end"])
                if en <= st:
                    en = st + 2.0
                rows.append({
                    "video_id": video_id, "subject": video.subject,
                    "start_time": round(st, 1), "end_time": round(en, 1),
                    "text": c["text"], "seq": len(rows),
                })
            # 批量插入（一条 executemany），避免几千条逐条 ORM add 的开销
            db.bulk_insert_mappings(Subtitle, rows)
            video.subtitle_path = to_rel(sub_file)
            video.subtitle_status = "done"
            db.commit()
            asyncio.create_task(_auto_generate_summary(video_id))
            return {"status": "done", "message": "字幕提取完成"}

        # 4. 逐句对齐：云引擎的 50s 窗口只给整段文本、无句子时间戳，
        #    按"每字约 ms_per_char 毫秒"估句长 → 逐句顺序排位 → 句尾吸附到最近的停顿中点。
        #    （不再用字符占比把整段 50s 硬摊开——那对语速不均必然错位。）
        db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()

        # 用音量检测找前 50 秒内人声开始的位置（第一段的真实起点）
        voice_start = 0.0
        try:
            vr = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-i", file_path, "-t", "50",
                 "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60,
            )
            rms_values = []
            for line in vr.stderr.split("\n"):
                if "RMS_level" in line or "rms" in line.lower():
                    try:
                        val = float(line.split(":")[-1].strip().replace(" dB", ""))
                        rms_values.append(val)
                    except Exception:
                        pass
            for i, v in enumerate(rms_values):
                if v > -25:
                    voice_start = max(2.0, i - 2)
                    break
        except Exception:
            voice_start = 2.0

        batch_rows = _per_sentence_align(
            chunks, silences,
            voice_start=voice_start,
            ms_per_char=MS_PER_CHAR,
            video_id=video_id, subject=video.subject,
        )

        if batch_rows:  # 批量插入（一条 executemany），避免逐条 ORM add
            db.bulk_insert_mappings(Subtitle, batch_rows)

        video.subtitle_path = to_rel(sub_file)
        video.subtitle_status = "done"
        db.commit()

        # 字幕完成 → 后台自动生成课程总结（不阻塞本请求，串行排队防限流）
        asyncio.create_task(_auto_generate_summary(video_id))

        return {"status": "done", "message": "字幕提取完成"}

    except HTTPException:
        raise
    except Exception as e:
        video.subtitle_status = "failed"
        db.commit()
        raise HTTPException(500, f"字幕提取失败: {str(e)}")


@router.get("/{video_id}/subtitle-file")
def download_subtitle_file(video_id: int, db: Session = Depends(get_db)):
    """下载字幕文件（纯文本，便于单独保存/整理）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if video.subtitle_status != "done" or not video.subtitle_path:
        raise HTTPException(400, "字幕尚未生成")
    sub_file = resolve(video.subtitle_path)
    if not sub_file:
        raise HTTPException(404, "字幕文件不存在")
    return FileResponse(
        str(sub_file),
        media_type="text/plain",
        filename=f"{video.subject}_{video.title}_字幕.txt",
    )


# ===== 课程总结（字幕完成后自动生成，可手动重试）=====
async def _generate_summary_impl(video_id: int, db: Session) -> dict:
    """实际生成总结：分段提取 → 合并防漏 → 写 markdown 文件。

    调用方需保证对同一视频串行（外层持 _summary_lock）。
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if video.subtitle_status != "done":
        raise HTTPException(400, "请先提取字幕")
    subs = db.query(Subtitle).filter(Subtitle.video_id == video_id)\
        .order_by(Subtitle.seq).all()
    if not subs:
        raise HTTPException(400, "无字幕内容")

    video.summary_status = "processing"
    db.commit()
    try:
        segments = _split_subtitle_segments(subs)
        summary = await summarize_lecture(video.title or video.filename, segments)
        if not summary.strip():
            raise RuntimeError("总结内容为空")
        sub_file = Path(settings.summary_dir) / f"{video_id}.md"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text(summary, encoding="utf-8")
        video.summary_path = to_rel(sub_file)
        video.summary_status = "done"
        db.commit()
        return {"status": "done", "message": "总结生成完成", "summary": summary}
    except HTTPException:
        raise
    except Exception as e:
        video.summary_status = "failed"
        db.commit()
        raise HTTPException(500, f"总结生成失败: {str(e)}")


async def _auto_generate_summary(video_id: int):
    """字幕提取完成后的后台任务：串行排队生成总结，失败静默（可手动重试）"""
    try:
        async with _summary_lock:
            db = SessionLocal()
            try:
                video = db.query(Video).filter(Video.id == video_id).first()
                if video and video.subtitle_status == "done" \
                        and video.summary_status in ("none", "pending", "failed"):
                    await _generate_summary_impl(video_id, db)
            finally:
                db.close()
    except Exception as e:
        print(f"  [i] 后台总结生成失败 #{video_id}: {e}")


@router.post("/{video_id}/generate-summary")
async def generate_summary(video_id: int, db: Session = Depends(get_db)):
    """手动生成/重试课程总结（同步执行，等待结果返回）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    async with _summary_lock:
        cur = db.query(Video).filter(Video.id == video_id).first()
        if cur and cur.summary_status == "processing":
            raise HTTPException(400, "总结生成中，请稍候")
        return await _generate_summary_impl(video_id, db)


@router.post("/batch-summary")
async def batch_generate_summaries(db: Session = Depends(get_db)):
    """一键补生成：所有「字幕已完成但总结缺失」的视频排队生成总结（后台串行，不阻塞请求）"""
    if _batch_state["running"]:
        raise HTTPException(400, f"批量生成正在进行中（已完成 {_batch_state['done']}/{_batch_state['queued']}），请稍候")
    videos = db.query(Video).filter(
        Video.subtitle_status == "done",
        Video.summary_status.in_(("none", "pending", "failed")),
    ).all()
    if not videos:
        return {"running": False, "queued": 0, "done": 0, "failed": 0,
                "message": "没有需要生成总结的视频"}
    _batch_state.update(running=True, queued=len(videos), done=0, failed=0)
    ids = [v.id for v in videos]
    asyncio.create_task(_run_batch_summary(ids))
    return {"running": True, "queued": len(videos), "done": 0, "failed": 0,
            "message": f"已排队 {len(videos)} 个视频，逐个串行生成中（约 10~60 秒/个）"}


async def _run_batch_summary(ids: list):
    """后台串行批量生成：逐个持锁调用 _generate_summary_impl，失败计入 failed 继续下一个"""
    try:
        for vid in ids:
            async with _summary_lock:
                db = SessionLocal()
                try:
                    try:
                        await _generate_summary_impl(vid, db)
                        _batch_state["done"] += 1
                    except Exception as e:
                        _batch_state["failed"] += 1
                        print(f"  [i] 批量总结失败 #{vid}: {e}")
                finally:
                    db.close()
    finally:
        _batch_state["running"] = False
        print(f"  [i] 批量总结结束：done={_batch_state['done']} failed={_batch_state['failed']}")


@router.get("/batch-summary/status")
def batch_summary_status():
    """批量生成进度查询（前端轮询用）"""
    return dict(_batch_state)


@router.get("/{video_id}/summary")
def get_summary(video_id: int, db: Session = Depends(get_db)):
    """读取课程总结（无则返回当前状态）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if video.summary_status == "done" and video.summary_path:
        sm = resolve(video.summary_path)
        if sm:
            content = sm.read_text(encoding="utf-8")
            return {"status": "done", "content": content}
    return {"status": video.summary_status or "none", "content": ""}


@router.put("/reorder")
def reorder_videos(data: dict, db: Session = Depends(get_db)):
    """批量更新视频排序"""
    orders = data.get("orders", {})  # {video_id: sort_order}
    ids = [int(vid) for vid in orders.keys() if str(vid).isdigit()]
    if not ids:
        db.commit()
        return {"message": "排序已更新"}
    # 一次性加载涉及的视频，避免逐条 SELECT（N+1 → 1）
    by_id = {v.id: v for v in db.query(Video).filter(Video.id.in_(ids)).all()}
    for vid_s, order in orders.items():
        video = by_id.get(int(vid_s))
        if video:
            video.sort_order = int(order)
    db.commit()
    return {"message": "排序已更新"}


@router.delete("/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    """删除视频"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    src = resolve(video.file_path)
    if src:
        try:
            os.remove(src)
        except OSError:
            pass
    db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()
    db.delete(video)
    db.commit()
    return {"message": "删除成功"}


@router.post("/batch-delete")
def batch_delete_videos(data: dict, db: Session = Depends(get_db)):
    """批量删除视频（前端勾选多个后调用）。

    body: {"ids": [1,2,3]}  — 逐个删除：物理视频文件 + 字幕记录 + 视频记录。
    不存在的 id 静默跳过；返回成功/失败计数。
    """
    ids = data.get("ids") or []
    # 过滤并去重为合法正整数
    clean_ids = sorted({int(i) for i in ids if str(i).lstrip('-').isdigit()})
    if not clean_ids:
        return {"message": "删除成功", "total": 0, "success": 0, "failed": 0}

    videos = db.query(Video).filter(Video.id.in_(clean_ids)).all()
    found = {v.id: v for v in videos}
    success = 0
    failed = 0
    for vid in clean_ids:
        video = found.get(vid)
        if not video:
            continue  # id 存在但数据库无记录 → 跳过
        try:
            src = resolve(video.file_path)
            if src:
                try:
                    os.remove(src)
                except OSError:
                    pass
            db.query(Subtitle).filter(Subtitle.video_id == vid).delete()
            db.delete(video)
            success += 1
        except Exception:
            failed += 1
    db.commit()
    return {"message": "删除成功", "total": len(clean_ids), "success": success, "failed": failed}

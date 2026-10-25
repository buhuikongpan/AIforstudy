"""考研学习平台后端（裸跑版，不依赖 Docker）"""
import os
import shutil
import atexit
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings, BASE_DIR
from .core.database import init_db, SessionLocal
from .core.path_migration import normalize_paths
from .models import Video, FileTask
from .api.routes import videos, chat, config, folders, voice, files

# SQLite 数据库：放在项目 data 目录（避免容器化遗留路径）
DB_PATH = str(BASE_DIR / "data" / "kaoyan.db")
FRONTEND_DIR = str(BASE_DIR / "frontend")

app = FastAPI(title="考研学习平台", version="2.3.0")

# 退出时备份数据库（data/kaoyan.db.bak）
def _backup_db():
    try:
        # 数据库已启用 WAL 模式：直接拷贝主库会漏掉 -wal 文件里的最新提交。
        # 备份前先 checkpoint，把 WAL 内容合并回主库，再拷贝主库（连同残留 wal/shm 一并排除）。
        import sqlite3 as _sqlite3
        try:
            with _sqlite3.connect(DB_PATH, timeout=5.0) as _conn:
                _conn.execute("BEGIN")
                _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass  # checkpoint 失败不阻断备份尝试，仍拷贝（可能略旧）
        shutil.copy2(DB_PATH, DB_PATH + ".bak")
        print(f"[ok] 数据库已备份: {DB_PATH}.bak")
    except Exception:
        pass
atexit.register(_backup_db)

# CORS：本地部署（FastAPI 托管前端静态文件 + 前端可配置 API_BASE 指向其他端口）。
# 不再允许 credentials 与 "*" 组合（浏览器会拒绝该组合，属于无效配置）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权：.env 配置 PLATFORM_TOKEN 后，所有 /api 接口需携带
#   Authorization: Bearer <token>
# 或（仅视频流，因 <video> 标签无法加请求头）：?token=<token>
# 未配置 token 时保持全开放（开箱即用，与旧版行为一致）。
# /api/health 永远匿名放行（健康检查/监控用途）。
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = settings.platform_token
    if token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        header = request.headers.get("authorization", "")
        query_token = request.query_params.get("token", "")
        if header != f"Bearer {token}" and query_token != token:
            return JSONResponse(
                {"detail": "未授权：请在设置中配置访问令牌（PLATFORM_TOKEN）"},
                status_code=401,
            )
    return await call_next(request)


@app.middleware("http")
async def no_cache_frontend(request: Request, call_next):
    """前端源码禁缓存：/js/ 与 /css/ 下的文件（含 ES module 子模块）每次都用最新版。

    为什么必须这样：ES module 里 `import './tree.js'` 是不带查询串的，
    只有入口 main.js 带 ?v=N —— 子模块会一直被浏览器缓存，改了看不到效果
    （排查时反复踩这个坑）。这里用响应头统一禁缓存，改完刷新即生效。
    vendor/ 下的第三方库体积大、变动少，保留正常缓存。
    """
    resp = await call_next(request)
    path = request.url.path
    if path.startswith(("/js/", "/css/")) or path.endswith((".html",)):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "考研学习平台后端运行中"}

# 挂载路由器
app.include_router(videos.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(folders.router)
app.include_router(voice.router)
app.include_router(files.router)

# 初始化数据库
init_db()

# 启动时重置卡住的任务状态（字幕提取 / 课程总结生成）
try:
    s = SessionLocal()
    stuck = s.query(Video).filter(Video.subtitle_status == "processing").all()
    for v in stuck:
        v.subtitle_status = "pending"
        print(f"  [i] 重置卡住的视频 #{v.id}: {v.title}")
    stuck_sum = s.query(Video).filter(Video.summary_status == "processing").all()
    for v in stuck_sum:
        v.summary_status = "none"  # 重新触发（摘要生成是幂等的）
        print(f"  [i] 重置卡住的总结 #{v.id}: {v.title}")
    if stuck or stuck_sum:
        s.commit()
    s.close()
except Exception:
    pass

# 启动恢复：上次中断的文件产物任务（服务重启/崩溃时线程被杀，任务会卡在 running/pending）
try:
    import asyncio as _aio
    import threading as _th
    _s2 = SessionLocal()
    _stuck = _s2.query(FileTask).filter(
        FileTask.status.in_(("pending", "running"))).all()
    _fids = sorted({t.file_id for t in _stuck})
    for _t in _stuck:
        _t.status = "pending"
        _t.error = ""
    _s2.commit()
    _s2.close()
    if _fids:
        from .services.file_service import _run_pipeline as _rp
        for _fid in _fids:
            _th.Thread(target=lambda f=_fid: _aio.run(_rp(f)),
                       daemon=True, name=f"pipeline-resume-{_fid}").start()
        print(f"  [i] 已恢复 {len(_fids)} 个文件的后台产物任务")
except Exception as _e:
    print(f"  [i] 任务恢复跳过：{_e}")

# 启动巡检：把库里的绝对路径规范化为相对项目根的路径（搬家/改名后自动修复，幂等）
try:
    s = SessionLocal()
    _st = normalize_paths(s)
    if _st["normalized"] or _st["relocated"] or _st["chat"]:
        print(f"  [paths] 路径已规范化：转相对 {_st['normalized']}、重定位 {_st['relocated']}、"
              f"聊天图片 {_st['chat']}")
    if _st["missing"]:
        print(f"  [paths] 有 {_st['missing']} 处文件没找到（保留原值，未改动）")
    s.close()
except Exception as e:
    print(f"  [paths] 路径巡检跳过：{e}")

# 创建存储目录
os.makedirs(settings.video_dir, exist_ok=True)
os.makedirs(settings.subtitle_dir, exist_ok=True)
os.makedirs(settings.summary_dir, exist_ok=True)
os.makedirs(settings.data_dir, exist_ok=True)
# 文件中心：托管库 / 回收站 / AI 产物
os.makedirs(settings.library_dir, exist_ok=True)
os.makedirs(settings.library_trash_dir, exist_ok=True)
os.makedirs(settings.artifacts_dir, exist_ok=True)
# 科目（顶层文件夹）完全由用户动态创建（＋科目按钮），不再预建默认科目
# 用户上传的图片（落盘后直接内联进消息；历史重看走 read 工具 / /api/chat/uploads 接口）
os.makedirs(os.path.join(settings.storage_dir, "uploads"), exist_ok=True)

# 前端静态文件
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

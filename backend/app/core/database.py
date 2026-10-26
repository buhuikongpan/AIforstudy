"""数据库：引擎 / 会话工厂 / 声明基类 / 初始化迁移"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from .config import settings

# SQLite 并发优化：
# - WAL 模式：读写不互斥（读不阻塞写、写不阻塞读），SSE 长流式读 + 后台总结写入
#   同时进行时不再报 database is locked；也显著降低磁盘同步次数。
# - busy_timeout=5000：写锁短暂被占时等待重试而非立刻报锁错误。
# - synchronous=NORMAL：WAL 下 checkpoint 频率降低，写入吞吐更高（崩溃最多丢最近提交，
#   对本平台聊天/状态数据可接受；进度/字幕属可重建数据）。
def _set_sqlite_pragma(dbapi_conn, _):
    try:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA wal_autocheckpoint=1000")
        cur.close()
    except Exception:
        pass


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
if settings.database_url.startswith("sqlite"):
    event.listen(engine, "connect", _set_sqlite_pragma)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    """建表 + 老库增量迁移（模型在函数内延迟导入，避免与 models.py 循环依赖）"""
    from .. import models  # noqa: F401  确保所有模型注册到 Base.metadata
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """老库增量迁移（缺列则补，已存在则跳过）"""
    import sqlalchemy as sa
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())

    def add_column(table: str, col: str, ddl: str):
        if table in tables and col not in {c["name"] for c in insp.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            print(f"[migrate] {table} 已添加 {col} 列")

    # 历史版本逐个补列
    add_column("chat_sessions", "name", "TEXT")
    add_column("chat_sessions", "model", "VARCHAR(120) DEFAULT ''")
    add_column("chat_sessions", "effort", "VARCHAR(20) DEFAULT ''")
    add_column("videos", "summary_status", "VARCHAR(20) DEFAULT 'none'")
    add_column("videos", "summary_path", "VARCHAR(500) DEFAULT ''")
    add_column("videos", "compressed_path", "VARCHAR(500) DEFAULT ''")
    add_column("videos", "compressed_status", "VARCHAR(20) DEFAULT 'none'")
    add_column("chat_messages", "quote", "TEXT")
    add_column("chat_messages", "tool_calls", "TEXT")
    add_column("chat_messages", "tool_call_id", "VARCHAR(64) DEFAULT ''")
    # 文件中心：会话可绑定文件（上下文跟随当前查看的文件）
    add_column("chat_sessions", "file_id", "INTEGER DEFAULT 0")
    # 文件中心：沙箱分级（聚焦文件夹 + 锁定标志，共识 Q4b）
    add_column("chat_sessions", "focus_folder_id", "INTEGER DEFAULT 0")
    add_column("chat_sessions", "focus_locked", "INTEGER DEFAULT 0")
    # 文件中心：软删除（回收站）
    add_column("folders", "deleted", "INTEGER DEFAULT 0")
    # 动态科目：旧固定科目键迁移为顶层文件夹名（幂等）
    if "chat_sessions" in tables:
        with engine.begin() as conn:
            r = conn.execute(sa.text(
                "UPDATE chat_sessions SET subject='数学' WHERE subject='math'"))
            if r.rowcount:
                print(f"[migrate] chat_sessions: {r.rowcount} 条 math → 数学")
    if "chat_messages" not in tables:
        print("[migrate] 将创建 chat_messages 表")
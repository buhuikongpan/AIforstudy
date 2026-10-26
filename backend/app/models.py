"""ORM 模型（SQLAlchemy）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float

from .core.database import Base


class Folder(Base):
    """文件夹/章节目录"""
    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(20), index=True, nullable=False)  # math / english / politics
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, default=0, index=True)  # 0 = 根级
    sort_order = Column(Integer, default=9999)
    deleted = Column(Integer, default=0)  # 1 = 在回收站（文件中心软删除）
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(20), index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    title = Column(String(255), default="")
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    duration = Column(Float, default=0)
    sort_order = Column(Integer, default=9999)
    folder_id = Column(Integer, default=0, index=True)  # 0 = 未分类
    subtitle_path = Column(String(500), default="")
    subtitle_status = Column(String(20), default="pending")
    summary_path = Column(String(500), default="")    # 课程总结 markdown 文件
    summary_status = Column(String(20), default="none")  # none / pending / processing / done / failed
    compressed_path = Column(String(500), default="")   # 480p 压缩版视频路径（同步到云端用的就是它）
    compressed_status = Column(String(20), default="none")  # none / processing / done / failed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Subtitle(Base):
    __tablename__ = "subtitles"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, index=True, nullable=False)
    subject = Column(String(20), index=True, nullable=False)
    start_time = Column(Float, default=0)
    end_time = Column(Float, default=0)
    text = Column(Text, default="")
    seq = Column(Integer, default=0)


class ChatSession(Base):
    """对话会话（按科目/模式存储，重启不丢，支持重命名）"""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    conv_id = Column(String(64), unique=True, index=True, nullable=False)  # 平台侧会话 ID
    mode = Column(String(8), index=True, nullable=False)  # A / B / C
    subject = Column(String(20), default="math", index=True)
    name = Column(String(255), default="新对话")  # 会话名（首条提问自动命名 / 可重命名）
    model = Column(String(120), default="")  # 会话级模型（空 = 用全局主模型配置）
    effort = Column(String(20), default="")  # 会话级思考强度（low/medium/high/xhigh/max；空 = 用全局默认 high）
    file_id = Column(Integer, default=0)  # 绑定的文件（文件中心：上下文跟随当前查看的文件，0 = 未绑定）
    # 沙箱分级（共识 Q4b：跟随+可锁定）：聚焦文件夹 = list_files/read 的作用域。
    # focus_folder_id=0 未锁定时跟随播放器文件所属文件夹；>0 且 focus_locked=1 时锁定用户手动选择的文件夹。
    focus_folder_id = Column(Integer, default=0)
    focus_locked = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class ChatMessage(Base):
    """对话消息（持久化，服务重启不丢）"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conv_id = Column(String(64), index=True, nullable=False)
    subject = Column(String(20), default="math")
    mode = Column(String(8), default="A")
    role = Column(String(16), nullable=False)  # user / assistant / tool
    content = Column(Text, default="")  # 纯文本，或含图片时的 JSON 数组
    reasoning = Column(Text, default="")  # DeepSeek 思维链（下轮回填保持连续性）
    quote = Column(Text, default="")  # 引用原文（用户引用 AI 回答某段进行特别说明）
    tool_calls = Column(Text, default="")  # assistant 要求调用工具时的 tool_calls（JSON 数组）
    tool_call_id = Column(String(64), default="")  # role=tool 时对应的 tool_call id
    seq = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PlatformConfig(Base):
    """平台运行时配置（模型 API 等，key-value JSON），设置页保存即生效"""
    __tablename__ = "platform_config"

    key = Column(String(64), primary_key=True)  # 如 model_config
    value = Column(Text, default="{}")          # JSON 字符串
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


# ================= 文件中心（全资料 AI 辅助改造）=================

class File(Base):
    """统一文件表：视频/文档/图片/音频全部作为「一种文件」进托管库。

    迁移约定：旧 videos 表的记录迁移到本表时**保留原 id**，
    因此 subtitles.video_id、旧总结文件名 {id}.md、聊天里引用的视频 id 全部继续有效。
    """
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(Integer, default=0, index=True)   # 0 = 根级（library/ 直下）
    name = Column(String(255), nullable=False)            # 显示名（含扩展名）
    ext = Column(String(20), default="")                  # 小写扩展名（不含点）
    kind = Column(String(20), default="other", index=True)  # video|pdf|office|image|text|markdown|audio|other
    size = Column(Integer, default=0)
    sha256 = Column(String(64), default="", index=True)   # 上传去重
    storage_path = Column(String(500), nullable=False)    # 相对项目根（core/paths.py 约定）
    status = Column(String(20), default="ready", index=True)   # ready|processing|error
    status_msg = Column(String(500), default="")          # 任务进度/失败原因
    sort_order = Column(Integer, default=9999)
    deleted = Column(Integer, default=0)  # 1 = 在回收站（文件中心软删除）
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class VideoMeta(Base):
    """视频特有元数据（从 videos 表迁移而来，1:1 对应 files.id）"""
    __tablename__ = "video_meta"

    file_id = Column(Integer, primary_key=True, index=True)
    duration = Column(Float, default=0)
    subtitle_path = Column(String(500), default="")
    subtitle_status = Column(String(20), default="pending")
    summary_path = Column(String(500), default="")
    summary_status = Column(String(20), default="none")
    compressed_path = Column(String(500), default="")
    compressed_status = Column(String(20), default="none")


class FileTask(Base):
    """文件后台任务队列（转换/提取/总结），前端轮询 /api/files/{id}/status"""
    __tablename__ = "file_tasks"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, index=True, nullable=False)
    task_type = Column(String(30), nullable=False)   # convert_pdf|extract_text|summarize
    status = Column(String(20), default="pending", index=True)  # pending|running|done|error
    progress = Column(Float, default=0)              # 0~1
    error = Column(String(500), default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
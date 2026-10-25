"""应用配置"""
from pathlib import Path
from pydantic_settings import BaseSettings

# 项目根目录（backend/ 的上级）
# 本文件位于 app/core/config.py：parents[3] = core → app → backend → 项目根
BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # 服务端口
    host: str = "0.0.0.0"
    port: int = 8000

    # 访问令牌：为空 = 不鉴权（默认开箱即用）；设置后所有 /api 接口需
    # Authorization: Bearer <token>（视频流额外支持 ?token= 查询参数，
    # 因为 <video> 标签无法携带请求头）
    platform_token: str = ""

    # 存储路径（项目内相对路径，脱离 Docker 后可移植）
    storage_dir: str = str(BASE_DIR / "storage")
    video_dir: str = str(BASE_DIR / "storage" / "videos")
    subtitle_dir: str = str(BASE_DIR / "storage" / "subtitles")
    summary_dir: str = str(BASE_DIR / "storage" / "summaries")  # 课程总结 markdown

    # 文件中心（全资料 AI 辅助）
    library_dir: str = str(BASE_DIR / "storage" / "library")          # 托管库根（资源管理器本体）
    library_trash_dir: str = str(BASE_DIR / "storage" / "library_trash")  # 回收站
    artifacts_dir: str = str(BASE_DIR / "storage" / "artifacts")      # AI 产物（按 file_id 集中）
    # x2t（OnlyOffice 转换引擎）可执行文件路径；留空 = 未配置（Office 文件不转 PDF，
    # 状态标 error 并提示）。外部依赖，不 vendored 进仓库（AGPL）。
    x2t_path: str = ""

    # 数据库（SQLite 放在项目 data 目录）
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'kaoyan.db'}"
    data_dir: str = str(BASE_DIR / "data")  # 持久化备份目录

    # DeepSeek 配置（主聊天模型）
    deepseek_api_key: str = ""

    # 智谱 GLM-4V 配置（视觉分析模型）
    zhipu_api_key: str = ""

    # 千问（语音识别 / ASR，字幕提取）
    qwen_api_key: str = ""

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"
        # .env 里可能存在已废弃的键（迁移历史），忽略而非启动崩溃
        extra = "ignore"


settings = Settings()

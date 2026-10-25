"""模型 API 运行时配置（设置页保存即生效，.env 作为兜底）

结构：
{
  "main":   {"base_url", "api_key", "model"},
  "asr":    {"base_url", "api_key", "model", "provider", "size", "voice_provider",
             "engines": {"qwen": {"api_key"}, "zhipu": {"api_key"},
                         "tencent": {"app_id","secret_id","secret_key"},
                         "siliconflow": {"api_key"}}},
}
存储于 platform_config 表（key="model_config"）。
asr 顶层 base_url/api_key/model 兼容旧配置（属于千问），已自动迁移进 engines.qwen。
"vision"（视觉辅助模型）已废弃：主模型本身多模态，看图走图片直接内联 + read 工具，
旧配置里的该小节会在载入时忽略（不再出现在生效配置里）。
"""
import json
import datetime
import time
from typing import Optional

from .database import SessionLocal
from .config import settings
from ..models import PlatformConfig

# ---------- 配置缓存（TTL） ----------
# load_model_config 在每次请求 / 每次 LLM 调用时都会被调多次（单次 SSE 回答内反复调用），
# 每次都开新的 DB Session 读表 + 深合并 + JSON 解析，成为隐藏热点。
# 用带 TTL 的内存缓存：设置保存时写入失效，读取在 TTL 内直接命中；DB 写入只有保存时发生，
# 故缓存一致性由 save 时主动失效保证，TTL 仅作兜底防止极端情况下漏失效。
_CONFIG_CACHE_TTL = 5.0   # 5 秒兜底
_config_cache = {"t": 0.0, "cfg": None}


def _default_config() -> dict:
    """.env 兜底默认值（首次使用 / DB 无记录时）"""
    return {
        # 分层提供商条目库（每层可多个提供商，active 标记当前生效）：
        #   tiers[tier] = [{id, name, base_url, api_key, models:[...], enabled:{model:bool}, active:bool}]
        # 首次访问由 get_tiers 自动从 main/asr 收集为“未命名”条目
        # （vision 层已随视觉辅助一起删除；旧库里残留的 vision 键会被忽略）
        "tiers": {"text": [], "asr": []},
        # 两个用途槽位（文本/语音）各配一个提供商的名称，默认“未命名”
        "main": {
            "name": "未命名",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": settings.deepseek_api_key or "",
            "model": "deepseek-v4-flash",
        },
        "asr": {
            "name": "未命名",   # 语音识别提供商名字
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key": settings.qwen_api_key or "",
            "model": "qwen3-asr-flash",
            # 视频字幕识别引擎：local = 本地 faster-whisper（免费/离线）；qwen = 千问云 API；
            # zhipu = 智谱 GLM-ASR-2512；tencent = 腾讯云实时语音识别极速版；
            # siliconflow = 硅基流动 SenseVoiceSmall；auto = 自动降级链
            # （智谱 → 千问 → 腾讯 → 硅基流动 → 本地，按已配 key 的引擎依次尝试）
            "provider": "auto",
            # provider=local 时的模型档位：small / medium / large-v3-turbo 等
            "size": "small",
            # 语音输入（🎤）独立引擎：auto = 走自动降级链（智谱 → 千问 → ... → 本地），
            # 也可显式指定 local / qwen / zhipu / tencent / siliconflow
            "voice_provider": "auto",
            # 各云引擎独立凭据（key 留空 = 未配置，自动降级链会跳过该引擎）：
            "engines": {
                "qwen":        {"api_key": settings.qwen_api_key or ""},
                "zhipu":       {"api_key": settings.zhipu_api_key or ""},
                "tencent":     {"app_id": "", "secret_id": "", "secret_key": ""},
                "siliconflow": {"api_key": ""},
            },
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """字典深合并：override 里非空字段覆盖 base，嵌套 dict 递归合并"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _invalidate_config_cache():
    _config_cache["t"] = 0.0
    _config_cache["cfg"] = None


def load_model_config() -> dict:
    """读取模型配置：DB 记录叠加 .env 默认（DB 优先，缺字段自动补齐）。

    带 TTL 内存缓存：高频繁读取（请求/LLM 调用）命中缓存，避免反复开 Session + 深合并；
    保存配置时主动失效缓存，保证读到最新值。
    """
    now = time.monotonic()
    cached = _config_cache["cfg"]
    if cached is not None and (now - _config_cache["t"]) < _CONFIG_CACHE_TTL:
        return cached

    db = SessionLocal()
    try:
        row = db.query(PlatformConfig).filter(PlatformConfig.key == "model_config").first()
        saved = json.loads(row.value) if row and row.value else {}
    except Exception:
        saved = {}
    finally:
        db.close()
    cfg = _deep_merge(_default_config(), saved)
    # 清理已废弃的「视觉辅助」配置（旧库里可能还存着 vision 小节与 tiers.vision）：
    # 主模型已多模态，视觉辅助整套已删，留着也不读——从生效配置里去掉，
    # 免得设置页/接口回显出一堆无意义的旧值。
    cfg.pop("vision", None)
    _tiers = cfg.get("tiers")
    if isinstance(_tiers, dict):
        _tiers.pop("vision", None)
    # 迁移：旧版配置里 asr.api_key/base_url/model 属于千问，挪进 engines.qwen
    asr = cfg.get("asr") or {}
    eng = asr.get("engines") or {}
    if isinstance(eng, dict):
        qwen_eng = eng.get("qwen") or {}
        if not qwen_eng.get("api_key") and asr.get("api_key"):
            qwen_eng["api_key"] = asr["api_key"]
            eng["qwen"] = qwen_eng
        asr["engines"] = eng

    # 只缓存不可变引用快照，避免调用方改 dict 污染缓存
    _config_cache["cfg"] = json.loads(json.dumps(cfg, ensure_ascii=False))
    _config_cache["t"] = now
    return _config_cache["cfg"]


def save_model_config(cfg: dict) -> dict:
    """保存模型配置：api_key 传空字符串表示"不修改原 key"；返回合并后的完整配置"""
    current = load_model_config()
    # 保留旧 key：前端不回传明文，空 key 时不覆盖
    for section, fields in (("main", ("api_key",)), ("asr", ("api_key",))):
        if section in cfg and isinstance(cfg[section], dict):
            if not cfg[section].get("api_key"):
                cfg[section]["api_key"] = current.get(section, {}).get("api_key", "")
    # asr.engines 各引擎 key 同样保留旧值（空 = 不修改）
    if "asr" in cfg and isinstance(cfg["asr"], dict):
        new_eng = cfg["asr"].get("engines")
        if isinstance(new_eng, dict):
            cur_eng = current.get("asr", {}).get("engines", {}) or {}
            for k, v in new_eng.items():
                if not isinstance(v, dict):
                    continue
                base = cur_eng.get(k) or {}
                for fk, fv in v.items():
                    if not fv and isinstance(base, dict):
                        v[fk] = base.get(fk, "")
    # tiers（分层提供商条目库）是整体替换语义：先替换再合并（支持删除条目）
    if "tiers" in cfg:
        tiers_in = dict(cfg.get("tiers") or {})
        tiers_in.pop("vision", None)          # 已删的层不允许再写回去
        current["tiers"] = tiers_in
    merged = _deep_merge(current, cfg)
    db = SessionLocal()
    try:
        row = db.query(PlatformConfig).filter(PlatformConfig.key == "model_config").first()
        if row:
            row.value = json.dumps(merged, ensure_ascii=False)
            row.updated_at = datetime.datetime.utcnow()
        else:
            db.add(PlatformConfig(key="model_config",
                                  value=json.dumps(merged, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    # 保存后立即失效缓存，确保下次读取拿到最新配置（无需等 TTL）
    _invalidate_config_cache()
    return merged


def get_endpoint(base_url: str, path: str) -> str:
    """拼接完整端点：兼容用户输入 base（.../v1）或完整端点（.../chat/completions）"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return path
    if base_url.endswith(path):
        return base_url
    return base_url + path


def mask_key(key: str) -> str:
    """掩码 key 用于回显：只显示首尾（不泄露明文）"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def public_view(cfg: dict) -> dict:
    """供 GET /api/config/model 回显：key 只显示掩码 + has_key 标记"""
    out = {}
    for section, fields in (("main", ("name", "base_url", "model")),
                            ("asr", ("name", "base_url", "model", "provider", "size", "voice_provider"))):
        s = cfg.get(section, {})
        item = {f: s.get(f) for f in fields}
        item["has_key"] = bool(s.get("api_key"))
        if section == "asr":
            # 各云引擎的 key 配置状态（前端据此显示/决定自动降级顺序）
            eng = s.get("engines", {}) or {}
            item["engines"] = {
                "qwen": bool((eng.get("qwen") or {}).get("api_key")),
                "zhipu": bool((eng.get("zhipu") or {}).get("api_key")),
                "tencent": bool((eng.get("tencent") or {}).get("secret_id")
                                and (eng.get("tencent") or {}).get("secret_key")),
                "siliconflow": bool((eng.get("siliconflow") or {}).get("api_key")),
            }
        out[section] = item
    return out
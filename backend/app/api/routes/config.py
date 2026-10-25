"""配置管理接口：模型 API 配置（设置页读写，保存即生效）+ 状态"""
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.config import settings
from ...core.model_config import load_model_config, save_model_config, public_view

router = APIRouter(prefix="/api/config", tags=["配置"])


@router.get("/status")
def get_config_status():
    """返回平台所需 Key 的配置状态（配合 /api/config/model 使用）"""
    cfg = load_model_config()
    asr_eng = cfg["asr"].get("engines") or {}
    tencent = asr_eng.get("tencent") or {}
    return {
        "deepseek": bool(cfg["main"].get("api_key")),
        "qwen": bool((asr_eng.get("qwen") or {}).get("api_key") or cfg["asr"].get("api_key")),
        "zhipu_asr": bool((asr_eng.get("zhipu") or {}).get("api_key")),
        "tencent_asr": bool((tencent.get("secret_id") or "").strip()
                            and (tencent.get("secret_key") or "").strip()),
        "siliconflow_asr": bool((asr_eng.get("siliconflow") or {}).get("api_key")),
        "auth_enabled": bool(settings.platform_token),  # 访问令牌是否已启用
    }


class MainModelIn(BaseModel):
    name: str = ""      # 提供商名字（空=保留原）
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class AsrModelIn(BaseModel):
    name: str = ""      # 提供商名字（空=保留原）
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = ""   # auto / local / qwen / zhipu / tencent / siliconflow
    size: str = ""       # faster-whisper 模型档位：small / medium
    voice_provider: str = ""   # 语音输入引擎：auto / local / qwen / zhipu / tencent / siliconflow
    engines: Optional[dict] = None   # 各云引擎凭据：{"qwen": {...}, "zhipu": {...}, "tencent": {...}, "siliconflow": {...}}


class ModelConfigIn(BaseModel):
    main: Optional[MainModelIn] = None
    asr: Optional[AsrModelIn] = None


@router.get("/model")
def get_model_config():
    """读取模型配置（key 只回显掩码 + has_key 标记，不回传明文）"""
    return public_view(load_model_config())


@router.post("/model")
def post_model_config(data: ModelConfigIn):
    """保存模型配置；api_key / base_url / model / name / provider / size / voice_provider
    传空 = 保留原值（支持部分提交）；保存后立即生效（无需重启）"""
    cfg = data.model_dump(exclude_none=True)
    if data.main is None:
        cfg.pop("main", None)
    if data.asr is None:
        cfg.pop("asr", None)
    # pydantic 默认空串会覆盖已有配置：把空字段剔除（api_key 空也剔除，交给保留逻辑）。
    # provider/size/voice_provider 必须一起剔除：它们在 AsrModelIn 里默认是空串，
    # 若不剔除，一次只带 provider 的部分提交会把 size / voice_provider 静默清空。
    for section in ("main", "asr"):
        sec = cfg.get(section)
        if not isinstance(sec, dict):
            continue
        for k in ("base_url", "model", "api_key", "name",
                  "provider", "size", "voice_provider"):
            if not sec.get(k):
                sec.pop(k, None)
    saved = save_model_config(cfg)
    return {"message": "配置已保存，立即生效", "config": public_view(saved)}


class ListModelsIn(BaseModel):
    base_url: str = ""
    api_key: str = ""


@router.post("/list-models")
async def list_models(data: ListModelsIn):
    """从 OpenAI 兼容端点拉取真实可用的模型列表（GET {base_url}/models）。

    用当前表单填写的地址和 Key 临时请求（不落库）；地址兼容两种写法：
      - base 形式：https://api.deepseek.com/v1
      - 完整端点：https://api.deepseek.com/v1/chat/completions（自动提取 base）
    端点不支持时返回 error，由前端提示用户手动输入模型名。
    """
    base_url = (data.base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(400, "请先填写 API 地址")
    models, error = await _fetch_models(base_url, data.api_key)
    return {"models": models, "error": error}


@router.post("/chat-models")
async def chat_models():
    """对话框模型下拉：用**已保存**的主模型配置（服务端 Key，明文不出服务器）
    拉取可用模型列表，供对话框一键切换会话级模型。
    **过滤**：只保留设置页「文本层当前提供商」模型列表里勾选启用的模型；
    未勾选过（旧数据）不过滤；当前使用的模型始终保留在列表。
    """
    cfg = load_model_config()
    main = cfg.get("main") or {}
    base_url = (main.get("base_url") or "").strip()
    models, error = await _fetch_models(base_url, main.get("api_key") or "")
    # 按 text 层 active 条目的 enabled 勾选过滤
    tiers = cfg.get("tiers") or {}
    text = tiers.get("text") or []
    active = next((p for p in text if p.get("active")), text[0] if text else None)
    enabled = (active or {}).get("enabled") or {}
    if active and enabled:
        keep = {m for m, on in enabled.items() if on}
        current = (main.get("model") or "").strip()
        models = [m for m in models if m in keep or (current and m == current)]
    return {"models": models, "current": (main.get("model") or "").strip(), "error": error}


async def _fetch_models(base_url: str, api_key: str) -> tuple:
    """请求 GET {base_url}/models，返回 (models, error)。base_url 兼容完整端点写法。"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return [], "未配置 API 地址"
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")].rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
            resp = await client.get(base_url + "/models", headers=headers)
        if resp.status_code != 200:
            return [], f"接口返回 HTTP {resp.status_code}"
        payload = resp.json()
        models = [m.get("id") for m in payload.get("data", []) if m.get("id")]
        return models, ""
    except Exception as e:
        return [], str(e)[:200]


# ==================== 分层提供商条目库（tiers：每层可多个提供商，模型列表可勾选） ====================
TIER_MAP = {"text": "main", "asr": "asr"}
TIER_NAMES = "/".join(TIER_MAP)


def _ensure_tiers(cfg: dict):
    """tiers 为空时，自动从 main/asr 现有配置收集为初始条目（名字=未命名）"""
    tiers = dict(cfg.get("tiers") or {})
    changed = False
    for tier, section in TIER_MAP.items():
        lst = tiers.get(tier)
        if lst is None:
            tiers[tier] = []
        if not tiers[tier]:
            sec = cfg.get(section) or {}
            base = (sec.get("base_url") or "").strip()
            if base:
                tiers[tier].append({
                    "id": "t_" + uuid4().hex[:8],
                    "name": (sec.get("name") or "").strip() or "未命名",
                    "base_url": base,
                    "api_key": (sec.get("api_key") or "").strip(),
                    "models": [m for m in [(sec.get("model") or "").strip()] if m],
                    "enabled": {},
                    "active": True,
                })
                changed = True
    return tiers, changed


class TierProviderIn(BaseModel):
    id: str = ""
    name: str = ""
    base_url: str = ""
    api_key: str = ""        # 留空 = 保留原 key
    models: list = []        # 模型列表（获取模型拉取/手动录入，包含未勾选的）
    enabled: Optional[dict] = None   # {model: true/false} 勾选启用状态
    active: bool = False     # 该层当前生效


class TiersSaveIn(BaseModel):
    tier: str                # text / asr
    providers: Optional[list[TierProviderIn]] = None


class TierActivateIn(BaseModel):
    tier: str
    provider_id: str


@router.get("/tiers")
def get_tiers():
    """分层提供商条目（key 掩码）；首次自动从 main/asr 收集"""
    cfg = load_model_config()
    tiers, changed = _ensure_tiers(cfg)
    if changed:
        save_model_config({"tiers": tiers})
    out = {}
    for tier in TIER_MAP:
        out[tier] = []
        for p in tiers.get(tier, []):
            out[tier].append({
                "id": p.get("id"),
                "name": p.get("name") or "未命名",
                "base_url": p.get("base_url") or "",
                "has_key": bool(p.get("api_key")),
                "key_mask": (p.get("api_key") or "")[:4] + "****" if p.get("api_key") else "",
                "models": p.get("models") or [],
                "enabled": p.get("enabled") or {},
                "active": bool(p.get("active")),
            })
    return {"tiers": out}


@router.post("/tiers")
def save_tiers(data: TiersSaveIn):
    """保存某一层全部条目（新增/编辑/删除/勾选状态整体提交；key 留空保留）"""
    if data.tier not in TIER_MAP:
        raise HTTPException(400, f"tier 仅支持 {TIER_NAMES}")
    cfg = load_model_config()
    tiers, _ = _ensure_tiers(cfg)
    cur = {p.get("id"): p for p in tiers.get(data.tier, [])}
    new_lst = []
    for p in (data.providers or []):
        pid = (p.id or "").strip()
        if not pid:
            pid = "t_" + uuid4().hex[:8]
        entry = {
            "id": pid,
            "name": (p.name or "").strip() or "未命名",
            "base_url": (p.base_url or "").strip().rstrip("/"),
            "models": p.models or [],
            "enabled": p.enabled or {},
            "active": bool(p.active),
        }
        if (p.api_key or "").strip():
            entry["api_key"] = p.api_key.strip()
        elif pid in cur and cur[pid].get("api_key"):
            entry["api_key"] = cur[pid]["api_key"]
        else:
            entry["api_key"] = ""
        new_lst.append(entry)
    # active 只保留一个
    acts = [e for e in new_lst if e["active"]]
    if len(acts) > 1:
        for e in acts[1:]:
            e["active"] = False
    if not new_lst:
        raise HTTPException(400, "至少保留一个提供商")
    tiers[data.tier] = new_lst
    save_model_config({"tiers": tiers})
    return {"message": "提供商已保存"}


@router.post("/tiers/activate")
def activate_tier_provider(data: TierActivateIn):
    """把某条目设为该层当前生效，并同步 base_url/key/model 到 main/asr"""
    if data.tier not in TIER_MAP:
        raise HTTPException(400, f"tier 仅支持 {TIER_NAMES}")
    cfg = load_model_config()
    tiers, _ = _ensure_tiers(cfg)
    lst = tiers.get(data.tier, [])
    target = None
    for p in lst:
        p["active"] = (p.get("id") == data.provider_id)
        if p["active"]:
            target = p
    if not target:
        raise HTTPException(404, "提供商不存在")
    tiers[data.tier] = lst
    section = TIER_MAP[data.tier]
    sec = dict(cfg.get(section) or {})
    sec["base_url"] = (target.get("base_url") or "").strip()
    if target.get("api_key"):
        sec["api_key"] = target["api_key"]
    if target.get("models"):
        sec["model"] = target["models"][0]
    updates = {"tiers": tiers, section: sec}
    save_model_config(updates)
    return {"message": f"已设为当前：{target.get('name') or '未命名'}"}

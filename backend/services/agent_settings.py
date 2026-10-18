"""Agent 的模型配置与密钥存储。

Phase 6 从 `agent.py` 拆出来的第一块：它只跟「设置文件 + 密钥加密」打交道，
不碰计划、工具或执行逻辑。拆出来之后改设置相关代码不用在 2000 行里找锚点。

对外仍通过 `agent.settings_view()` / `agent.update_settings()` 暴露
（`agent.py` 只做再导出），所以 `main.py` 与测试都不用改。
"""
from __future__ import annotations

import os

from .. import fsutil, secrets, store

DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-flash"

# 界面上可选的模型（也允许自己填）
MODEL_OPTIONS = [
    "deepseek-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-chat",
    "deepseek-reasoner",
]


# ---------------------------------------------------------------------------
# 解析：设置文件优先，环境变量兜底
#
# 改设置文件**不需要重启服务**，所以界面改了立刻生效。
# ---------------------------------------------------------------------------

def resolve_key() -> str:
    stored = secrets.unprotect((store.read_agent_settings().get("api_key") or "").strip())
    return stored or (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def resolve_model() -> str:
    stored = (store.read_agent_settings().get("model") or "").strip()
    return stored or DEFAULT_MODEL


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * 8
    return f"{key[:5]}…{key[-4:]}"


def settings_view() -> dict:
    """给前端的视图：**绝不回传完整 key**，只回掩码。

    `storage` 如实说明磁盘上那条密钥是什么形态：
      dpapi             已加密（正常）
      plain-dev         开发模式明文（FILEPILOT_ALLOW_PLAINTEXT_SECRETS=1）
      legacy-plaintext  旧版本没能加密、直接落的明文 → 界面提示重新保存一次
      none              未配置
    """
    stored = store.read_agent_settings()
    raw_stored = (stored.get("api_key") or "").strip()
    stored_key = secrets.unprotect(raw_stored)
    env_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    key = stored_key or env_key
    storage = secrets.storage_state(raw_stored)

    warning = ""
    if storage == secrets.STORAGE_LEGACY:
        warning = "检测到旧版本以明文保存的 API Key，请重新保存一次以加密"
    elif storage == secrets.STORAGE_PLAIN_DEV:
        warning = "当前是开发模式，API Key 以明文保存（未加密）"

    return {
        "configured": bool(key),
        "masked": _mask_key(key),
        "model": resolve_model(),
        "custom_model": bool((stored.get("model") or "").strip()),
        "source": "settings" if stored_key else ("env" if env_key else "none"),
        "storage": storage,
        "storage_warning": warning,
        "model_options": MODEL_OPTIONS,
        "checked_at": fsutil.now_iso(),
    }


def update_settings(api_key: str | None = None, model: str | None = None) -> dict:
    """写设置。api_key 传空串表示清除；model 传空串表示回到默认。

    只动 settings.json —— 配方（recipes.json）与所有任务记录都不受影响。

    ★ `secrets.protect()` 在 DPAPI 不可用时抛 `SecretStorageError`，**在落盘之前**
      就中断 —— 所以加密失败不会留下明文，也不会留下半份设置。
    """
    agent_settings = store.read_agent_settings()

    if api_key is not None:
        value = api_key.strip()
        if value:
            agent_settings["api_key"] = secrets.protect(value)
        else:
            agent_settings.pop("api_key", None)

    if model is not None:
        value = model.strip()
        if value:
            agent_settings["model"] = value
        else:
            agent_settings.pop("model", None)

    store.save_agent_settings(agent_settings)
    return settings_view()

"""Agent 的模型配置与密钥存储。

Phase 6 从 `agent.py` 拆出来的第一块：它只跟「设置文件 + 密钥加密」打交道，
不碰计划、工具或执行逻辑。拆出来之后改设置相关代码不用在 2000 行里找锚点。

对外仍通过 `agent.settings_view()` / `agent.update_settings()` 暴露
（`agent.py` 只做再导出），所以 `main.py` 与测试都不用改。
"""
from __future__ import annotations

import os

from .. import fsutil, providers, secrets, store

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
    """key 的解析交给 providers：设置文件 > 当前服务商专属环境变量。

    自定义服务没填地址时 providers 会抛错，这里吞掉当成"未配置"——
    status 探活不该因为一条配置没填就整页报错。
    """
    try:
        return providers.api_key()
    except ValueError:
        return ""


def resolve_model() -> str:
    """设置文件 > 该服务商的默认模型 > 全局兜底。

    切到 OpenAI 之后不该还默认 `deepseek-flash` —— 那个模型名在别的服务上不存在。
    """
    stored = (store.read_agent_settings().get("model") or "").strip()
    if stored:
        return stored
    try:
        spec = providers.resolve()
    except ValueError:
        spec = providers.BUILTIN[providers.current_id()]
    # 自定义服务没有预设模型 → 返回空串，让界面显示「未选择模型」。
    # 这里如果兜底成 deepseek-flash，用户会以为当前用的是那个模型（其实是发给对方的
    # 一个不存在的模型名），调用必然失败却看不出原因。
    return spec.default_model


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
    key = resolve_key()
    env_key = "" if stored_key else key
    storage = secrets.storage_state(raw_stored)

    # 当前服务商（Phase 12）。自定义服务没填地址时不报错，只把原因带给界面 ——
    # 否则用户刚点开「自定义」还没填，整页就红了。
    provider_id = providers.current_id()
    try:
        spec = providers.resolve()
        provider_error = ""
    except ValueError as exc:
        spec = providers.BUILTIN[provider_id]
        provider_error = str(exc)

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
        # 模型候选跟着服务商走。自定义网关的模型名没法预知 —— 给空列表，
        # 界面只留「使用自定义模型」入口。硬塞一排 deepseek 的名字，
        # 用户点了必然 404，不如不列。
        "model_options": list(spec.models),
        # ---- 服务商（Phase 12）----
        "provider": provider_id,
        "provider_label": spec.label,
        "provider_options": providers.options(),
        "llm_base_url": (stored.get("llm_base_url") or "").strip(),
        "provider_error": provider_error,
        "checked_at": fsutil.now_iso(),
    }


def update_settings(
    api_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    llm_base_url: str | None = None,
) -> dict:
    """写设置。api_key 传空串表示清除；model 传空串表示回到默认。

    只动 settings.json —— 配方（recipes.json）与所有任务记录都不受影响。

    ★ `secrets.protect()` 在 DPAPI 不可用时抛 `SecretStorageError`，**在落盘之前**
      就中断 —— 所以加密失败不会留下明文，也不会留下半份设置。

    ★ **换服务商时自动清掉已保存的 key**：你在 DeepSeek 填的 key 不该在切到
      自定义地址后原样发过去。用户不会想到这一点，所以不能靠他记得。
      同一次调用里如果既换服务商又给了新 key，按先清后写处理（顺序见下）。
    """
    agent_settings = store.read_agent_settings()

    # ---- 顺序要紧：先换服务商（可能清 key），再写新 key ----
    if provider is not None:
        new_id = (provider or "").strip().lower() or providers.DEFAULT_PROVIDER_ID
        if new_id not in providers.BUILTIN:
            raise ValueError(f"不支持的服务商：{provider}")
        old_id = agent_settings.get("provider") or providers.LEGACY_PROVIDER_ID
        if new_id != old_id:
            # 换了服务商：旧 key 一律作废，必须重填
            agent_settings.pop("api_key", None)
        agent_settings["provider"] = new_id

    if llm_base_url is not None:
        value = llm_base_url.strip().rstrip("/")
        if value:
            if not value.startswith(("http://", "https://")):
                raise ValueError("接口地址必须以 http:// 或 https:// 开头")
            agent_settings["llm_base_url"] = value
        else:
            agent_settings.pop("llm_base_url", None)

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

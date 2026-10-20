"""模型服务提供方抽象（Phase 12）。

## 为什么需要这一层

抽取前，「往哪发」这件事散在 4 个地方（`agent.py` 的 `_call_deepseek` /
`_call_deepseek_tools` / `_iter_deepseek_stream`，以及 `explain.py` 的「?」解释），
每处都硬编码 `https://api.deepseek.com/chat/completions` 和 key 的读法。

后果是加第二个服务商要改四处，而且**漏掉的那处不会报错** —— 它会静默地继续打
DeepSeek，用的还是上一个服务商的 key。这类 bug 在测试里很难被发现，
因为"请求发出去了、也有回复"，只是发错了地方。

## 这一层管什么

只回答三个问题：往哪发（`chat_url`）、带哪个 key（`api_key`）、默认什么模型。

**不管**请求体拼装、重试、SSE 解析 —— 三个调用方的 body 形状本来就不同
（单轮 JSON / tool_calls / 流式），强行统一只会让每个人多一层适配。

## 安全约定

**切换服务商时自动清除已保存的 API Key。** 否则你在 DeepSeek 填的 key
会在切到自定义地址后原样发给那个地址 —— 用户不会想到这一点，所以不能靠他记得。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

from . import secrets, store


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    default_model: str
    key_env: str
    # 需要用户自己填地址（自建网关 / 第三方兼容服务）
    requires_base_url: bool = False
    # 界面上给的候选模型；空表示"让用户自己填"（自建网关的模型名没法预知）
    models: tuple[str, ...] = ()


DEFAULT_PROVIDER_ID = "deepseek"

BUILTIN: dict[str, Provider] = {
    "deepseek": Provider(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        default_model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY",
        models=("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash",
                "deepseek-chat", "deepseek-reasoner"),
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        key_env="OPENAI_API_KEY",
        # 只列确定可用的几个；新模型用「使用自定义模型」手填，不在这里猜
        models=("gpt-4o", "gpt-4o-mini", "gpt-4.1"),
    ),
    "custom": Provider(
        id="custom",
        label="自定义（OpenAI 兼容）",
        base_url="",
        default_model="",
        key_env="FILEPILOT_LLM_API_KEY",
        requires_base_url=True,
    ),
}

# 老配置里没有 provider 字段，一律按它处理
LEGACY_PROVIDER_ID = "deepseek"


def options() -> list[dict]:
    """给设置面板用的清单（不暴露 key_env 这类实现细节）。"""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "requires_base_url": spec.requires_base_url,
            "default_model": spec.default_model,
        }
        for spec in BUILTIN.values()
    ]


def current_id() -> str:
    """设置文件里的 provider id；非法值回退到 DeepSeek 而不是报错。

    为什么回退而不是报错：这个字段可能是人手改坏的，让整个 Agent 页起不来
    代价太大 —— 退回默认值至少还能用，界面上会显示实际用的是哪个。
    """
    raw = str(store.read_agent_settings().get("provider") or "").strip().lower()
    return raw if raw in BUILTIN else LEGACY_PROVIDER_ID


def resolve() -> Provider:
    """当前生效的 provider。自定义服务没填地址时抛错（明确告知，不静默回退）。"""
    spec = BUILTIN[current_id()]

    if not spec.requires_base_url:
        return spec

    base = str(store.read_agent_settings().get("llm_base_url") or "").strip().rstrip("/")
    if not base:
        raise ValueError("自定义服务需要先填写接口地址（Base URL），否则不知道该往哪发")
    if not base.startswith(("http://", "https://")):
        raise ValueError("接口地址必须以 http:// 或 https:// 开头")

    # 自定义服务不知道默认模型，交给用户填的 model 或调用方兜底
    return replace(spec, base_url=base)


def chat_url(spec: Provider | None = None) -> str:
    """OpenAI 兼容协议：`{base}/chat/completions`。"""
    spec = spec or resolve()
    return f"{spec.base_url.rstrip('/')}/chat/completions"


def api_key(spec: Provider | None = None) -> str:
    """key 的优先级：设置文件（加密） > 当前服务商专属环境变量。

    DeepSeek 的专属变量兼容旧用户；绝不把一个服务商的 key 发给另一个服务商。
    """
    spec = spec or resolve()
    stored = secrets.unprotect((store.read_agent_settings().get("api_key") or "").strip())
    if stored:
        return stored
    return (os.getenv(spec.key_env) or "").strip()


def label() -> str:
    """用于错误提示：告诉用户是哪个服务出的问题，而不是笼统的"调用失败"。"""
    try:
        return resolve().label
    except ValueError:
        return BUILTIN[current_id()].label

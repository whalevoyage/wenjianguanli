"""服务商抽象（Phase 12）。

抽取前「往哪发」散在 4 处（agent.py ×3 + explain.py ×1），每处硬编码 DeepSeek 的地址。
加第二个服务商时，漏掉的那处**不会报错** —— 它只是静默地继续打 DeepSeek，
而 UI 上显示的是你已经切过去了。这组测试守的就是这件事。

另外守一条安全行为：**换服务商必须重填 key**。
你在 DeepSeek 填的 key 不该在切到自定义地址后原样发过去。
"""
from __future__ import annotations

import pytest

from backend import providers, secrets, store
from backend.services import agent_settings


@pytest.fixture
def memory_settings(monkeypatch):
    """把设置换成内存态，用例之间互不影响，也不需要 DPAPI。"""
    state: dict = {}

    def fake_read():
        return dict(state)

    def fake_save(new):
        state.clear()
        state.update(new)

    monkeypatch.setattr(store, "read_agent_settings", fake_read)
    monkeypatch.setattr(store, "save_agent_settings", fake_save)
    monkeypatch.setattr(secrets, "protect", lambda value: f"enc:{value}")
    monkeypatch.setattr(secrets, "unprotect", lambda raw: (raw or "").removeprefix("enc:"))
    return state


# ---------------------------------------------------------------------------
# 地址解析
# ---------------------------------------------------------------------------

def test_defaults_to_deepseek(memory_settings):
    assert providers.current_id() == "deepseek"
    assert providers.chat_url() == "https://api.deepseek.com/chat/completions"


def test_switching_provider_changes_url(memory_settings):
    agent_settings.update_settings(provider="openai")
    assert providers.chat_url() == "https://api.openai.com/v1/chat/completions"


def test_custom_provider_requires_a_base_url(memory_settings):
    agent_settings.update_settings(provider="custom")
    with pytest.raises(ValueError) as info:
        providers.resolve()
    assert "接口地址" in str(info.value)


def test_custom_provider_uses_user_base_url(memory_settings):
    agent_settings.update_settings(provider="custom", llm_base_url="https://gw.example.com/v1/")
    assert providers.chat_url() == "https://gw.example.com/v1/chat/completions"


def test_custom_base_url_must_be_http(memory_settings):
    with pytest.raises(ValueError) as info:
        agent_settings.update_settings(provider="custom", llm_base_url="gw.example.com/v1")
    assert "http" in str(info.value)


def test_unknown_provider_falls_back_instead_of_crashing(memory_settings):
    """人手改坏了字段不该让整个 Agent 页起不来 —— 回退到默认，界面显示实际用的。"""
    memory_settings["provider"] = "some-gateway-from-the-future"
    assert providers.current_id() == "deepseek"


def test_update_rejects_unknown_provider(memory_settings):
    with pytest.raises(ValueError) as info:
        agent_settings.update_settings(provider="not-a-provider")
    assert "不支持" in str(info.value)


# ---------------------------------------------------------------------------
# key 解析
# ---------------------------------------------------------------------------

def test_stored_key_wins(memory_settings, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FILEPILOT_LLM_API_KEY", raising=False)
    memory_settings["api_key"] = "enc:sk-stored"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    assert providers.api_key() == "sk-stored"


def test_provider_specific_env_is_used(memory_settings, monkeypatch):
    agent_settings.update_settings(provider="openai")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("FILEPILOT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert providers.api_key() == "sk-openai"


def test_legacy_deepseek_env_still_works(memory_settings, monkeypatch):
    """老用户机器上只有 DEEPSEEK_API_KEY，抽 provider 当天不该突然"未配置"。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FILEPILOT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-legacy")
    assert providers.api_key() == "sk-legacy"


def test_openai_never_falls_back_to_deepseek_env(memory_settings, monkeypatch):
    agent_settings.update_settings(provider="openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FILEPILOT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-must-not-leak")
    assert providers.api_key() == ""


def test_custom_never_falls_back_to_deepseek_env(memory_settings, monkeypatch):
    agent_settings.update_settings(provider="custom", llm_base_url="https://gw.example.com/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FILEPILOT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-must-not-leak")
    assert providers.api_key() == ""


def test_custom_provider_specific_env_is_used(memory_settings, monkeypatch):
    agent_settings.update_settings(provider="custom", llm_base_url="https://gw.example.com/v1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FILEPILOT_LLM_API_KEY", "sk-custom")
    assert providers.api_key() == "sk-custom"


# ---------------------------------------------------------------------------
# ★ 换服务商必须重填 key
# ---------------------------------------------------------------------------

def test_switching_provider_clears_stored_key(memory_settings):
    agent_settings.update_settings(api_key="sk-deepseek-secret")
    assert memory_settings.get("api_key") == "enc:sk-deepseek-secret"

    agent_settings.update_settings(provider="custom", llm_base_url="https://evil.example.com/v1")

    assert "api_key" not in memory_settings, "换服务商后旧 key 必须作废，否则会被发到新地址"


def test_switching_provider_and_setting_key_at_once(memory_settings):
    """同一次请求既换服务商又给新 key：先清后写，新 key 必须留下来。"""
    agent_settings.update_settings(api_key="sk-old")
    agent_settings.update_settings(
        provider="custom", llm_base_url="https://gw.example.com/v1", api_key="sk-new",
    )
    assert memory_settings.get("api_key") == "enc:sk-new"


def test_switching_back_and_forth_keeps_clearing(memory_settings):
    agent_settings.update_settings(api_key="sk-a")
    agent_settings.update_settings(provider="openai")
    assert "api_key" not in memory_settings
    agent_settings.update_settings(provider="deepseek")
    assert "api_key" not in memory_settings


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

def test_settings_view_exposes_provider_fields(memory_settings):
    view = agent_settings.settings_view()
    assert view["provider"] == "deepseek"
    assert view["provider_label"] == "DeepSeek"
    assert len(view["provider_options"]) >= 2
    assert view["provider_error"] == ""


def test_settings_view_reports_provider_error_instead_of_raising(memory_settings):
    """刚点开「自定义」还没填地址时，界面要能知道原因，而不是整页 500。"""
    memory_settings["provider"] = "custom"
    view = agent_settings.settings_view()
    assert view["provider"] == "custom"
    assert "接口地址" in view["provider_error"]
    assert view["configured"] is False


def test_resolve_key_swallows_missing_base_url(memory_settings):
    """status 探活不该因为一条配置没填就抛异常。"""
    memory_settings["provider"] = "custom"
    assert agent_settings.resolve_key() == ""


# ---------------------------------------------------------------------------
# 模型默认值跟随服务商
# ---------------------------------------------------------------------------

def test_model_default_follows_provider(memory_settings):
    assert agent_settings.resolve_model() == "deepseek-flash"
    agent_settings.update_settings(provider="openai")
    assert agent_settings.resolve_model() == "gpt-4o-mini"


def test_explicit_model_overrides_provider_default(memory_settings):
    agent_settings.update_settings(provider="openai", model="gpt-4o")
    assert agent_settings.resolve_model() == "gpt-4o"


def test_call_sites_have_no_hardcoded_urls():
    """守护「漏掉一处」：业务代码里不该再出现写死的 DeepSeek 地址。"""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "backend"
    offenders = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or path.name in {"providers.py", "config.py"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "api.deepseek.com" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], f"这些地方还写死了 DeepSeek 地址：{offenders}"


# ---------------------------------------------------------------------------
# 模型候选必须跟着服务商变
# ---------------------------------------------------------------------------

def test_model_options_follow_provider(memory_settings):
    """切到 OpenAI 之后还列 deepseek 的模型名，等于给出必然报错的选项。"""
    deepseek_view = agent_settings.settings_view()
    assert "deepseek-flash" in deepseek_view["model_options"]

    agent_settings.update_settings(provider="openai")
    openai_view = agent_settings.settings_view()
    assert "gpt-4o" in openai_view["model_options"]
    assert "deepseek-flash" not in openai_view["model_options"]


def test_custom_provider_lists_no_models(memory_settings):
    """自建网关的模型名没法预知 —— 宁可给空列表，也不列一排点了必然 404 的名字。

    界面上的「使用自定义模型」入口仍然在，用户手填即可。
    """
    memory_settings["provider"] = "custom"
    memory_settings["llm_base_url"] = "https://gw.example.com/v1"
    view = agent_settings.settings_view()
    assert view["provider"] == "custom"
    assert view["model_options"] == []


def test_custom_provider_has_no_default_model(memory_settings):
    """自定义服务不该显示 deepseek-flash —— 那个名字发给对方是不存在的模型。"""
    memory_settings["provider"] = "custom"
    memory_settings["llm_base_url"] = "https://gw.example.com/v1"
    assert agent_settings.resolve_model() == ""


def test_custom_provider_uses_user_model(memory_settings):
    memory_settings["provider"] = "custom"
    memory_settings["llm_base_url"] = "https://gw.example.com/v1"
    agent_settings.update_settings(model="my-local-model")
    assert agent_settings.resolve_model() == "my-local-model"

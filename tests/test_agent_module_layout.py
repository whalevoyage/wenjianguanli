"""Agent 后端拆分的结构不变式。

Phase 6 把 `agent.py`（2033 行）拆出了两个模块：

  agent_settings.py      模型配置与密钥存储
  agent_local_rules.py   本地意图规则（parse_local + 配套正则/辅助）

拆分的原则是**只动结构、不动行为**：`agent.py` 退化成薄门面，`main.py` 与测试
继续按 `agent.xxx()` 调用，一行都不用改。

这组测试守两件事：
  1. 门面导出的必须是**同一个对象**，不是复制品（用 `is` 而不是 `==`）；
  2. 实现不许再长回 `agent.py`（否则下次又会变成 2000 行的巨石）。
"""
from __future__ import annotations

import inspect

import pytest

from backend.services import agent, agent_local_rules, agent_settings


# ---------------------------------------------------------------------------
# 一、门面是同一个对象
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "settings_view",
    "update_settings",
    "resolve_key",
    "resolve_model",
    "DEFAULT_MODEL",
    "MODEL_OPTIONS",
])
def test_settings_api_comes_from_agent_settings(name):
    assert getattr(agent, name) is getattr(agent_settings, name), (
        f"{name} 不是同一个对象 —— 说明又被复制了一份实现"
    )


@pytest.mark.parametrize("name", [
    "parse_local",
    "_extract_paths",
    "_extract_filters",
    "ACTION_HINT_RE",
])
def test_local_rules_come_from_agent_local_rules(name):
    assert getattr(agent, name) is getattr(agent_local_rules, name), (
        f"{name} 不是同一个对象 —— 说明又被复制了一份实现"
    )


# ---------------------------------------------------------------------------
# 二、实现不许长回去
# ---------------------------------------------------------------------------

def test_agent_module_does_not_reimplement_moved_functions():
    source = inspect.getsource(agent)
    for forbidden in (
        "def parse_local(",
        "def settings_view(",
        "def update_settings(",
        "def resolve_key(",
        "def _extract_paths(",
    ):
        assert forbidden not in source, f"`{forbidden}` 又出现在 agent.py 里了"


def test_split_modules_do_not_import_agent():
    """被拆出去的模块不许反向依赖 agent.py —— 那样就成了循环导入。"""
    for module in (agent_settings, agent_local_rules):
        source = inspect.getsource(module)
        assert "from .agent import" not in source
        assert "import agent\n" not in source


# ---------------------------------------------------------------------------
# 三、对外 API 一个都不能少
# ---------------------------------------------------------------------------

def test_public_api_is_intact():
    """`__all__` 里声明过的名字必须都还能从 agent 取到。"""
    missing = [name for name in agent.__all__ if not hasattr(agent, name)]
    assert missing == [], f"拆完之后这些公开名字丢了：{missing}"


def test_callable_api_still_callable():
    for name in ("chat", "stream_chat", "query_local", "settings_view", "parse_local"):
        assert callable(getattr(agent, name)), name

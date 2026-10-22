"""ToolSpec 单一事实源的一致性测试。

为什么要有这一组：项目真实发生过「新增工具漏改一张表 → 静默失效」——
`large_scan` 在 ALLOWED_ACTIONS / ACTION_PARAMS / PATH_PARAMS 里都有，
**唯独缺 TOOL_REGISTRY**。它不会崩，代价更阴：没有 label、也不进
function-calling schema，模型根本看不见这个工具，用户以为 Agent 不支持。

现在工具只在 `tool_specs.py` 声明一次、其余派生，这组测试守住这条线：
任何「只改了一半」的改动，在测试阶段就红。
"""
from __future__ import annotations

import pytest

from backend.services import agent_tools as T
from backend.services import tool_specs as S
from backend.services.agent_intent import compute_risk
from backend.services.agent_planner import normalize_steps


# ---------------------------------------------------------------------------
# 一、单一事实源本身
# ---------------------------------------------------------------------------

def test_specs_are_self_consistent():
    assert S.validate() == [], f"ToolSpec 声明有问题：{S.validate()}"


def test_agent_tools_only_reexports():
    """六个常量必须就是 tool_specs 里那几个对象本身，不能是复制品。

    用 `is` 而不是 `==`：有人把常量又抄一份进 agent_tools 时，值可能相等，
    但对象不同 —— 那种「看起来一致、实际两份」正是要防的东西。
    """
    assert T.ALLOWED_ACTIONS is S.ALLOWED_ACTIONS
    assert T.TOOL_REGISTRY is S.TOOL_REGISTRY
    assert T.WRITE_ACTIONS is S.WRITE_ACTIONS
    assert T.ACTION_PARAMS is S.ACTION_PARAMS
    assert T.PATH_PARAMS is S.PATH_PARAMS
    assert T.TOOL_GROUPS is S.TOOL_GROUPS
    assert T.QUERY_TOOLS is S.QUERY_TOOLS
    assert T.HELPER_TOOLS is S.HELPER_TOOLS


def test_action_order_is_stable():
    """ALLOWED_ACTIONS 直接回给前端（all_actions），顺序也算行为。"""
    assert S.ALLOWED_ACTIONS == [spec.name for spec in S.ACTION_SPECS]
    assert S.ALLOWED_ACTIONS[-1] == "virtual_organize"


# ---------------------------------------------------------------------------
# 二、每张派生表都覆盖每个动作（旧的「漏改一处」就死在这里）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", S.ALLOWED_ACTIONS)
def test_every_action_appears_in_every_derived_table(name):
    assert name in T.TOOL_REGISTRY
    assert name in T.ACTION_PARAMS
    assert name in T.PATH_PARAMS
    assert bool(T.TOOL_REGISTRY[name]["write"]) == (name in T.WRITE_ACTIONS)


def test_schemas_cover_every_declared_tool():
    """模型能看见的工具集合 == 声明的工具集合。一个不多、一个不少。"""
    names = {item["function"]["name"] for item in T.tool_schemas()}
    assert names == set(S.EVERY_TOOL)
    assert len(names) == len(T.tool_schemas()), "schema 里有重名工具"


def test_no_declared_action_is_missing_from_the_registry():
    """反向检查：白名单里的每个动作都必须有注册表条目。"""
    assert set(S.ALLOWED_ACTIONS) - set(T.TOOL_REGISTRY) == set()


# ---------------------------------------------------------------------------
# 三、写动作的元信息必须完整
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(S.WRITE_ACTIONS))
def test_write_action_is_a_job(name):
    assert S.EVERY_TOOL[name].kind == "job", f"{name} 声明了写权限却不是 job"


@pytest.mark.parametrize("name", sorted(S.WRITE_ACTIONS))
def test_write_action_forces_a_confirmation_step(name):
    """写动作前必须被本地强制插一个 confirm —— 模型绕不过这一关。"""
    steps = normalize_steps([{"tool": name, "kind": "job"}], {"scope": {}})
    kinds = [step["kind"] for step in steps]
    assert "confirm" in kinds, f"{name} 的计划里没有确认步骤"
    assert kinds.index("confirm") < kinds.index("job"), f"{name} 的确认步骤插在了执行之后"


@pytest.mark.parametrize("name", sorted(S.WRITE_ACTIONS))
def test_write_action_has_a_locally_computed_risk(name):
    """风险等级只能来自本地计算，且必须是三档之一、必须带人话理由。"""
    level, reason = compute_risk(name, {"path": "C:/Users"}, {}, {"file_count": 1})
    assert level in {"low", "medium", "high"}
    assert reason.strip()


def test_trash_purge_is_always_high_risk():
    """显式清空隔离区的动作永远是最高风险，文案必须说「不可恢复」。"""
    level, reason = compute_risk("trash_purge", {}, {}, {"file_count": 3})
    assert level == "high"
    assert "不可恢复" in reason


# ---------------------------------------------------------------------------
# 四、每个查询 / 辅助工具都有真正的分发实现
# ---------------------------------------------------------------------------

def _stub_leaf_calls(monkeypatch):
    """把所有真正干活的 query_* / check_* / estimate_task 打成桩。

    这样调用 run_tool 不会真去扫盘，但仍然能验证「分发表里有没有这个键」——
    分发表是 run_tool 内部的局部 dict，只有走一遍才知道有没有漏。
    """
    for attr in dir(T):
        if not attr.startswith(("query_", "check_", "estimate_")):
            continue
        value = getattr(T, attr)
        if callable(value):
            monkeypatch.setattr(T, attr, lambda *a, **k: {"ok": True}, raising=False)


@pytest.mark.parametrize("name", [spec.name for spec in (*S.QUERY_SPECS, *S.HELPER_SPECS)])
def test_query_and_helper_tools_have_a_dispatch(monkeypatch, name):
    _stub_leaf_calls(monkeypatch)
    result = T.run_tool(name, {})
    assert "暂未实现" not in str(result), f"{name} 在 run_tool 的分发表里没有条目"


def test_unknown_tool_is_rejected():
    result = T.run_tool("erase_all_disk", {})
    assert "未注册" in str(result) or "暂未实现" in str(result)


# ---------------------------------------------------------------------------
# 五、给前端的视图形状不变
# ---------------------------------------------------------------------------

def test_registry_view_shape():
    view = T.registry_view()
    assert set(view) == {"groups", "query", "helper", "execute", "all_actions"}
    assert view["all_actions"] == S.ALLOWED_ACTIONS
    for group, members in view["groups"].items():
        for name in members:
            assert name in S.EVERY_TOOL, f"TOOL_GROUPS[{group}] 引用了未注册的 {name}"

"""永久删除的语义一致性。

这组测试守的是「说法必须等于做法」：

  - 普通「清理垃圾 / 释放空间」**不许**被理解成物理删除；
  - 物理删除只认强意图（彻底 / 清空 / 物理删除）；
  - 不可逆的操作，风险说明里必须写清「不可恢复」；
  - 反过来，风险元信息里**不许**出现「可恢复」这种承诺 ——
    清理类走的是「隔离 + 立刻清空同一批」，那句话说出去就是假的。
"""
from __future__ import annotations

import pytest

from backend import config
from backend.services.agent import parse_local
from backend.services.agent_intent import compute_risk
from backend.services.agent_planner import PIPELINES, normalize_steps

CLEAN_ACTIONS = ("junk_clean", "duplicate_clean", "similar_clean", "large_clean")


# ---------------------------------------------------------------------------
# 一、意图分级
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "清理 C 盘垃圾",
    "帮我释放点空间",
    "清一下临时文件",
    "删除缓存",
    "把垃圾清掉",
])
def test_ordinary_clean_never_means_permanent_delete(message):
    """日常说法一律不许落到物理删除口上。"""
    plan = parse_local(message, {})
    assert plan is None or plan.get("action") != "trash_purge", f"「{message}」被理解成永久删除了"


@pytest.mark.parametrize("message", [
    "彻底删除隔离区",
    "清空回收站",
    "物理删除隔离区里的东西",
])
def test_permanent_delete_requires_explicit_wording(message):
    plan = parse_local(message, {})
    assert plan, f"「{message}」没被识别"
    assert plan["action"] == "trash_purge"


# ---------------------------------------------------------------------------
# 二、风险说明
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", CLEAN_ACTIONS)
def test_clean_risk_text_says_irreversible(action):
    level, reason = compute_risk(action, {"path": "C:/tmp"}, {}, {"file_count": 3})
    assert level in {"low", "medium", "high"}
    assert "不可恢复" in reason, f"{action} 的风险说明没说清不可恢复：{reason}"


def test_purge_risk_is_always_high():
    level, reason = compute_risk("trash_purge", {}, {}, {"file_count": 1})
    assert level == "high"
    assert "不可恢复" in reason


def test_risk_meta_never_promises_recovery():
    """等级说明是全站通用的，不能替具体动作做「可恢复」的承诺。"""
    for level, meta in config.AGENT_RISK_META.items():
        note = meta.get("note", "")
        assert "可恢复" not in note, f"{level} 的说明承诺了可恢复：{note}"
        assert "可还原" not in note, f"{level} 的说明承诺了可还原：{note}"


def test_migrate_risk_is_not_destructive():
    """迁移不是删除 —— 说明里不该出现「永久删除」，否则用户会误判。"""
    _, reason = compute_risk("migrate_execute", {"source_dir": "D:/a", "target_dir": "E:/b"}, {}, {})
    assert "永久删除" not in reason


# ---------------------------------------------------------------------------
# 三、计划模板
# ---------------------------------------------------------------------------

def test_purge_pipeline_confirmation_warns_irreversible():
    steps = PIPELINES["purge"]
    confirms = [step for step in steps if step["kind"] == "confirm"]
    assert confirms, "purge 流水线没有确认步骤"
    assert any("不可恢复" in step["title"] for step in confirms), (
        "purge 的确认步骤标题必须写明不可恢复，"
        f"现在是：{[s['title'] for s in confirms]}"
    )


@pytest.mark.parametrize("action", sorted({"junk_clean", "duplicate_clean", "similar_clean", "large_clean"}))
def test_clean_pipelines_confirm_before_executing(action):
    """不管走哪条流水线，写动作前面必须有一个 confirm。"""
    steps = normalize_steps([{"tool": action, "kind": "job"}], {"scope": {}})
    kinds = [step["kind"] for step in steps]
    assert "confirm" in kinds and kinds.index("confirm") < kinds.index("job")

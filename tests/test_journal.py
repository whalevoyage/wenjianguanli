"""操作流水（journal）的语义。

守三件事：

  1. 该记的记全 —— 类型 / 时间 / 来源 / 成功失败项 / 能不能撤销；
  2. 撤销判定不能骗人 —— Agent 的「隔离 + 立刻清空」是真的没了，必须标不可撤销；
  3. 有界 —— 不设上限的话这个文件会无限长，而它要整份读进内存。

撤销本身复用 `JobManager.rollback()`（逐条反 actions），这里用替身验证调用链，
真正的回滚正确性由 test_execution_guard 的往返用例覆盖。
"""
from __future__ import annotations

import pytest

from backend import journal


@pytest.fixture
def store_path(monkeypatch, tmp_path):
    """把流水写到 tmp_path，避免用例之间互相污染。"""
    target = tmp_path / "journal.json"
    monkeypatch.setattr(journal, "_path", lambda: target)
    return target


def _job(**overrides) -> dict:
    base = {
        "id": "job-1",
        "type": "clean",
        "title": "清理 3 个项目",
        "target": "D:/tmp",
        "params": {},
        "actions": [{"op": "trash", "from": "D:/tmp/a.txt", "to": "D:/trash/a.txt"}],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------

def test_record_captures_the_required_fields(store_path):
    entry = journal.record(_job(), {"cleaned_count": 3, "errors": ["一个失败"]})

    assert entry["type"] == "clean"
    assert entry["done"] == 3
    assert entry["failed"] == 1
    assert entry["source"] == "user"
    assert entry["undoable"] is True
    assert entry["undone"] is False
    assert entry["at"] and entry["job_id"] == "job-1"


@pytest.mark.parametrize("job_type,result,expected", [
    ("clean", {"cleaned_count": 5}, 5),
    ("migrate", {"copied_count": 4, "deleted_count": 4}, 8),
    ("virtual", {"moved": 7}, 7),
    ("agent_task", {"summary": {"done": 2}}, 2),
    ("scan", {"file_count": 99}, 0),   # 非写操作不报成功项
])
def test_success_counts_are_normalized_per_service(store_path, job_type, result, expected):
    entry = journal.record(_job(type=job_type), result)
    assert entry["done"] == expected


@pytest.mark.parametrize("job_id,params,expected", [
    ("job-a", {}, "user"),
    ("job-b", {"_source": "scheduler"}, "scheduler"),
    ("job-c", {"_source": "agent"}, "agent"),
])
def test_source_is_recorded(store_path, job_id, params, expected):
    entry = journal.record(_job(id=job_id, params=params), {"cleaned_count": 1})
    assert entry["source"] == expected


def test_agent_task_defaults_to_agent_source(store_path):
    entry = journal.record(_job(type="agent_task", params={}), {"summary": {"done": 1}})
    assert entry["source"] == "agent"


# ---------------------------------------------------------------------------
# 可撤销判定（最要紧的一条）
# ---------------------------------------------------------------------------

def test_permanent_deletion_is_not_undoable(store_path):
    """Agent 清理是「隔离 + 立刻清空同批」，文件真的没了 —— 绝不能标成可撤销。"""
    entry = journal.record(
        _job(type="agent_task"),
        {"permanent": True, "summary": {"done": 10}},
    )
    assert entry["undoable"] is False


def test_typed_job_without_actions_is_not_undoable(store_path):
    entry = journal.record(_job(actions=[]), {"cleaned_count": 0})
    assert entry["undoable"] is False


def test_backup_is_not_undoable(store_path):
    """备份只是产出一份 ZIP，不构成"改动了用户文件"。"""
    entry = journal.record(_job(type="backup"), {"archive": "D:/b.zip"})
    assert entry["undoable"] is False


def test_agent_organize_is_undoable(store_path):
    """Agent 的就地整理会记 move actions，这批能撤回。"""
    entry = journal.record(
        _job(type="agent_task", actions=[{"op": "move", "from": "a", "to": "b"}]),
        {"summary": {"done": 3}},
    )
    assert entry["undoable"] is True


# ---------------------------------------------------------------------------
# 有界与查询
# ---------------------------------------------------------------------------

def test_journal_is_bounded(store_path, monkeypatch):
    monkeypatch.setattr(journal, "MAX_ENTRIES", 10)
    for index in range(25):
        journal.record(_job(id=f"job-{index}"), {"cleaned_count": index})

    rows = journal.entries(limit=1000)
    assert len(rows) == 10
    assert rows[0]["job_id"] == "job-24", "留下的应该是最新的"
    assert all(row["job_id"] != "job-0" for row in rows)


def test_entries_are_newest_first_and_filterable(store_path):
    journal.record(_job(id="a", params={"_source": "user"}), {"cleaned_count": 1})
    journal.record(_job(id="b", params={"_source": "scheduler"}), {"cleaned_count": 1})

    all_rows = journal.entries()
    assert [row["job_id"] for row in all_rows] == ["b", "a"]

    only_scheduler = journal.entries(source="scheduler")
    assert [row["job_id"] for row in only_scheduler] == ["b"]


def test_stats_counts_undoable(store_path):
    journal.record(_job(id="a"), {"cleaned_count": 1})
    journal.record(_job(id="b", type="backup"), {"archive": "x.zip"})

    stats = journal.stats()
    assert stats["total"] == 2
    assert stats["undoable"] == 1
    assert stats["undone"] == 0


# ---------------------------------------------------------------------------
# 撤销
# ---------------------------------------------------------------------------

class _FakeManager:
    def __init__(self):
        self.calls: list[str] = []

    def rollback(self, job_id: str) -> dict:
        self.calls.append(job_id)
        return {"restored": [{"path": "D:/tmp/a.txt"}], "errors": []}


@pytest.fixture
def fake_manager(monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr("backend.jobs.manager", fake)
    return fake


def test_undo_delegates_to_rollback_and_marks_undone(store_path, fake_manager):
    entry = journal.record(_job(), {"cleaned_count": 1})

    result = journal.undo(entry["id"])

    assert fake_manager.calls == ["job-1"]
    assert result["entry"]["undone"] is True
    assert result["entry"]["undo_result"]["restored"] == 1
    assert journal.stats()["undone"] == 1


def test_undo_twice_is_rejected(store_path, fake_manager):
    entry = journal.record(_job(), {"cleaned_count": 1})
    journal.undo(entry["id"])

    with pytest.raises(ValueError) as info:
        journal.undo(entry["id"])
    assert "已经撤销过" in str(info.value)
    assert len(fake_manager.calls) == 1, "第二次不该再调 rollback"


def test_undo_rejects_non_undoable_entry(store_path, fake_manager):
    entry = journal.record(_job(type="backup"), {"archive": "x.zip"})
    with pytest.raises(ValueError) as info:
        journal.undo(entry["id"])
    assert "不可撤销" in str(info.value)
    assert fake_manager.calls == []


def test_undo_rejects_unknown_entry(store_path, fake_manager):
    with pytest.raises(ValueError) as info:
        journal.undo("op-does-not-exist")
    assert "找不到" in str(info.value)


def test_manual_rollback_marks_entry_too(store_path, fake_manager):
    """用户从「任务中心」点回滚时，流水里那条也要跟着标记，否则能重复撤。"""
    journal.record(_job(), {"cleaned_count": 1})
    journal.mark_all_undone_for_job("job-1")
    assert journal.stats()["undone"] == 1

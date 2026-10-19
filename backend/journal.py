"""操作流水与撤销入口（Phase 8）。

## 与运行日志的分工

| | 用途 | 内容 |
|---|---|---|
| `store.write_log()` | 给人看的运行日志（Logs 页） | 时间 / 类型 / 成功失败 / 释放空间 |
| 本模块 | 给**撤销**用的操作流水 | 批次标识 / 来源 / 成功失败项数 / 能不能退回 |

两者不能合并：运行日志是流水账，一年几千条也没关系；操作流水要被撤销逻辑消费，
必须能唯一定位「哪一次操作动了哪些文件」。

## 为什么一条记录 = 一个批次，而不是一个文件

需求要的字段是「成功项 / 失败项」——这本身就是批次口径。
逐文件记录的话，一次「清理 5000 个垃圾」就能把上限撑爆，
而且用户点撤销时想撤的显然是**那一次清理**，不是一个文件。

单文件的还原信息本来就在 `job["actions"]` 里（`{op, from, to}`），
journal 只存批次摘要 + 指向那个 job 的 id，撤销时复用既有的 `manager.rollback()`。

## 有界

保留最近 `MAX_ENTRIES` 条，超出丢最旧的。不设上限的话这个文件会无限长大，
而它是要整份读进内存的（撤销列表要一次显示）。
"""
from __future__ import annotations

import uuid

from . import config, fsutil, store

MAX_ENTRIES = 500

# 这些类型的任务会产生「可回滚的文件动作」；backup / scan 之类不在其中。
UNDOABLE_TYPES = {"clean", "migrate", "virtual"}


def _path():
    return config.STATE_DIR / "journal.json"


# ---------------------------------------------------------------------------
# 读 / 写
# ---------------------------------------------------------------------------

def entries(limit: int = 100, *, source: str | None = None,
            undoable: bool | None = None) -> list[dict]:
    """最近的条目，新的在前。"""
    data = store.load_json(_path(), [])
    if not isinstance(data, list):
        return []
    rows = [row for row in data if isinstance(row, dict)]
    if source:
        rows = [row for row in rows if row.get("source") == source]
    if undoable is not None:
        rows = [row for row in rows if bool(row.get("undoable")) is undoable]
    rows.reverse()
    return rows[: max(1, int(limit))]


def find(entry_id: str) -> dict | None:
    for row in store.load_json(_path(), []):
        if isinstance(row, dict) and row.get("id") == entry_id:
            return row
    return None


def stats() -> dict:
    rows = [row for row in store.load_json(_path(), []) if isinstance(row, dict)]
    return {
        "total": len(rows),
        "undoable": sum(1 for row in rows if row.get("undoable") and not row.get("undone")),
        "undone": sum(1 for row in rows if row.get("undone")),
        "limit": MAX_ENTRIES,
    }


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------

def _counts(job_type: str, result: dict) -> tuple[int, int]:
    """从各服务各自的 result 结构里抽出「成功项 / 失败项」。

    每个服务返回的字段名都不一样（cleaned_count / moved / deleted…），
    这里做一次归一，缺的按 0 处理 —— 宁可显示 0，也不要因为字段名对不上而抛异常，
    那会让整个任务在收尾阶段失败。
    """
    errors = result.get("errors")
    fail = len(errors) if isinstance(errors, list) else 0

    if job_type == "clean":
        return int(result.get("cleaned_count") or 0), fail
    if job_type == "migrate":
        # 迁移的"成功"= 复制 + 删源，分开统计会让用户看不懂，合并成一个数
        done = int(result.get("copied_count") or result.get("copied") or 0)
        done += int(result.get("deleted_count") or result.get("deleted") or 0)
        return done, fail
    if job_type == "virtual":
        return int(result.get("moved") or result.get("moved_count") or 0), fail
    if job_type == "agent_task":
        summary = result.get("summary") or {}
        if isinstance(summary, dict):
            return int(summary.get("done") or 0), len(summary.get("errors") or []) or fail
        return 0, fail
    return 0, fail


def _source_of(job: dict) -> str:
    """来源：用户点的 / Agent 规划的 / 定时任务跑的。

    这个字段是审计的关键 —— 出问题时第一个要回答的就是「谁让它跑的」。
    """
    explicit = (job.get("params") or {}).get("_source")
    if explicit in ("user", "agent", "scheduler"):
        return explicit
    if job.get("type") == "agent_task":
        return "agent"
    return "user"


def record(job: dict, result: dict | None = None) -> dict | None:
    """任务成功结束后调用一次。失败 / 取消不记 —— 那些没产生可撤销的动作。"""
    result = result if isinstance(result, dict) else {}
    job_type = str(job.get("type") or "")
    actions = job.get("actions") or []
    done, fail = _counts(job_type, result)

    # 不可撤销的两种情况：
    #   1. 类型本身不产生文件动作（scan / backup / index…）
    #   2. Agent 的清理类：确认后已「隔离 + 立刻清空同批」，文件真的没了
    permanent = bool(result.get("permanent"))
    undoable = bool(actions) and job_type in UNDOABLE_TYPES and not permanent
    # Agent 的就地整理走的是 virtual 的移动动作，但任务类型是 agent_task —— 单独放行
    if job_type == "agent_task" and actions and not permanent:
        undoable = True

    entry = {
        "id": f"op-{uuid.uuid4().hex[:12]}",
        "at": fsutil.now_iso(),
        "job_id": job.get("id") or "",
        "type": job_type,
        "title": job.get("title") or "",
        "target": job.get("target") or "",
        "source": _source_of(job),
        "done": done,
        "failed": fail,
        "action_count": len(actions),
        "undoable": undoable,
        "undone": False,
    }

    rows = store.load_json(_path(), [])
    if not isinstance(rows, list):
        rows = []
    rows.append(entry)
    # 有界：只留最近 MAX_ENTRIES 条
    if len(rows) > MAX_ENTRIES:
        rows = rows[-MAX_ENTRIES:]
    store.save_json(_path(), rows)
    return entry


# ---------------------------------------------------------------------------
# 撤销
# ---------------------------------------------------------------------------

def undo(entry_id: str) -> dict:
    """撤销一条操作。

    真正的还原逻辑复用 `JobManager.rollback()` —— 它早就按 actions 逐条反向了
    （move 搬回、copy 删副本、trash 从隔离区取回、rmdir 重建空目录）。
    这里只负责：找到条目、判断能不能撤、调它、记下「已撤销」防止重复点。
    """
    rows = store.load_json(_path(), [])
    if not isinstance(rows, list):
        rows = []
    target: dict | None = None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == entry_id:
            target = row
            break

    if target is None:
        raise ValueError("找不到这条操作记录（可能已被更早的记录挤出保留范围）")
    if target.get("undone"):
        raise ValueError("这条操作已经撤销过了")
    if not target.get("undoable"):
        raise ValueError("这条操作不可撤销（文件已被彻底删除，或本来就没有可回滚的动作）")

    job_id = target.get("job_id") or ""
    if not job_id:
        raise ValueError("这条操作缺少任务号，无法撤销")

    # 延迟导入：jobs 模块在导入期会碰到 store，这里放在函数内避免循环依赖
    from .jobs import manager

    result = manager.rollback(job_id)

    target["undone"] = True
    target["undone_at"] = fsutil.now_iso()
    target["undo_result"] = {
        "restored": len(result.get("restored") or []),
        "errors": (result.get("errors") or [])[:20],
    }
    store.save_json(_path(), rows)
    return {"entry": target, **result}


def mark_all_undone_for_job(job_id: str) -> None:
    """任务被回滚后，把它的记录一并标记（避免用户在两处点重复撤销）。"""
    rows = store.load_json(_path(), [])
    if not isinstance(rows, list):
        return
    changed = False
    for row in rows:
        if isinstance(row, dict) and row.get("job_id") == job_id and not row.get("undone"):
            row["undone"] = True
            row["undone_at"] = fsutil.now_iso()
            changed = True
    if changed:
        store.save_json(_path(), rows)

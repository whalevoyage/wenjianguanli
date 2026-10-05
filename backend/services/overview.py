"""首页总览：把全局状态聚合成一次请求。"""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import config, fsutil, store
from ..jobs import JOB_CANCELLED, JOB_DONE, JOB_FAILED, JOB_RUNNING, JOB_WAITING, manager
from . import clean
import threading
import time

_CACHE = {"time": 0.0, "value": None}
_CACHE_LOCK = threading.RLock()

# 首页「近 N 天任务趋势」的窗口
TREND_DAYS = 14


def _job_stats(jobs: list[dict]) -> dict:
    """从任务历史聚合出首页图表要用的统计量（纯计算，不读盘、不写盘）。"""
    by_status: dict[str, int] = {}
    by_type: dict[str, dict] = {}
    for job in jobs:
        status = job.get("status") or "pending"
        by_status[status] = by_status.get(status, 0) + 1
        key = job.get("type") or "unknown"
        bucket = by_type.setdefault(
            key, {"type": key, "label": job.get("type_label") or key, "count": 0}
        )
        bucket["count"] += 1

    # 近 14 天：按「结束时间」归到当天；没有结束时间就用创建时间
    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=i) for i in range(TREND_DAYS - 1, -1, -1)]
    buckets = {d.isoformat(): {"date": d.isoformat(), "done": 0, "failed": 0} for d in days}
    for job in jobs:
        status = job.get("status")
        if status not in (JOB_DONE, JOB_FAILED):
            continue
        stamp = job.get("finished_at") or job.get("created_at") or ""
        day = str(stamp)[:10]
        if day in buckets:
            buckets[day]["done" if status == JOB_DONE else "failed"] += 1

    done = by_status.get(JOB_DONE, 0)
    failed = by_status.get(JOB_FAILED, 0)
    finished = done + failed

    # 只统计趋势窗口内的量：首页那张卡的曲线是 14 天，数字也必须同口径，否则名不副实。
    window_done = sum(bucket["done"] for bucket in buckets.values())
    window_failed = sum(bucket["failed"] for bucket in buckets.values())
    window_finished = window_done + window_failed

    return {
        "by_status": by_status,
        "by_type": sorted(by_type.values(), key=lambda item: item["count"], reverse=True),
        "daily": list(buckets.values()),
        "done": done,
        "failed": failed,
        "cancelled": by_status.get(JOB_CANCELLED, 0),
        "success_rate": round(done / finished * 100, 1) if finished else None,
        "window": {
            "done": window_done,
            "failed": window_failed,
            "success_rate": round(window_done / window_finished * 100, 1) if window_finished else None,
        },
    }


def get_overview() -> dict:
    with _CACHE_LOCK:
        if _CACHE["value"] is not None and time.monotonic() - _CACHE["time"] < 2.0:
            return _CACHE["value"]
    disks = fsutil.disk_stats()
    totals = {
        "total": sum(item["total"] for item in disks),
        "used": sum(item["used"] for item in disks),
        "free": sum(item["free"] for item in disks),
    }
    totals["total_text"] = fsutil.format_size(totals["total"])
    totals["used_text"] = fsutil.format_size(totals["used"])
    totals["free_text"] = fsutil.format_size(totals["free"])
    totals["percent"] = round(totals["used"] / totals["total"] * 100, 1) if totals["total"] else 0.0

    jobs = manager.list(200)
    running = [job for job in jobs if job.get("status") == JOB_RUNNING]
    waiting = [job for job in jobs if job.get("status") == JOB_WAITING]

    # 汇总最近一次垃圾扫描 / 查重给出的"可释放空间"，并按来源拆开（首页图表用）
    junk_reclaimable = 0
    dup_reclaimable = 0
    for job in jobs:
        if job.get("status") != JOB_DONE:
            continue
        payload = job.get("result") or {}
        if job.get("type") == "junk":
            junk_reclaimable = max(junk_reclaimable, int(payload.get("safe_size") or 0))
        elif job.get("type") == "duplicate":
            dup_reclaimable = max(dup_reclaimable, int(payload.get("reclaimable_size") or 0))
    reclaimable = junk_reclaimable + dup_reclaimable

    trash = clean.list_trash()
    logs = store.read_logs()
    api_stats = {}
    for item in logs:
        key = item.get("type") or "未知"
        bucket = api_stats.setdefault(key, {"api": key, "count": 0, "last_time": item.get("time")})
        bucket["count"] += 1
        bucket["last_time"] = max(bucket["last_time"] or "", item.get("time") or "")
    api_total = len(logs) or 1

    result = {
        "disks": disks,
        "disk_total": totals,
        "jobs": {
            "total": len(jobs),
            "running": len(running),
            "waiting": len(waiting),
            "recent": jobs[:8],
        },
        "trash": {
            "count": trash["count"],
            "size": trash["total_size"],
            "size_text": trash["total_size_text"],
            "trash_dir": trash["trash_dir"],
        },
        "reclaimable_size": reclaimable,
        "reclaimable_size_text": fsutil.format_size(reclaimable),
        "reclaimable_breakdown": {
            "junk": junk_reclaimable,
            "junk_text": fsutil.format_size(junk_reclaimable),
            "duplicate": dup_reclaimable,
            "duplicate_text": fsutil.format_size(dup_reclaimable),
        },
        "job_stats": _job_stats(jobs),
        "logs_count": len(logs),
        "api_stats": [
            {**item, "percentage": round(item["count"] / api_total * 100, 1)}
            for item in sorted(api_stats.values(), key=lambda value: value["count"], reverse=True)[:8]
        ],
        "rules": {
            "excludes": config.DEFAULT_EXCLUDES,
            "junk_rule_count": len(config.JUNK_RULES),
        },
        "generated_at": fsutil.now_iso(),
    }
    with _CACHE_LOCK:
        _CACHE.update(time=time.monotonic(), value=result)
    return result

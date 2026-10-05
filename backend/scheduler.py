"""Local recurring task runner for the desktop application."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config, database, fsutil

ALLOWED = {"scan", "junk", "duplicate", "backup"}


def list_schedules() -> list[dict]:
    return database.list_schedules(config.DATABASE_FILE)


def save_schedule(schedule: dict) -> dict:
    database.upsert_schedule(config.DATABASE_FILE, schedule)
    return next(item for item in list_schedules() if item["id"] == schedule["id"])


def remove_schedule(schedule_id: str) -> None:
    database.delete_schedule(config.DATABASE_FILE, schedule_id)


def new_schedule(name: str, kind: str, params: dict, interval_minutes: int, next_run_at: str | None = None) -> dict:
    if kind not in ALLOWED:
        raise ValueError(f"不支持的定时任务类型：{kind}")
    if not next_run_at:
        next_run_at = (datetime.now().astimezone() + timedelta(minutes=interval_minutes)).isoformat(timespec="seconds")
    return save_schedule({"id": uuid.uuid4().hex[:12], "name": name, "kind": kind, "params": params, "enabled": True, "interval_minutes": interval_minutes, "next_run_at": next_run_at, "last_run_at": None, "last_job_id": None, "error": None})


def start_runner() -> None:
    thread = threading.Thread(target=_run_loop, name="filepilot-scheduler", daemon=True)
    thread.start()


def _run_loop() -> None:
    while True:
        try:
            _run_due()
        except Exception:
            pass
        time.sleep(30)


def _run_due() -> None:
    from .jobs import manager
    from .services import backup, duplicates, junk, scan
    workers = {"scan": scan.run_scan, "junk": junk.run_junk_scan, "duplicate": duplicates.run_duplicate_scan, "backup": backup.run_backup}
    now = datetime.now().astimezone()
    for item in list_schedules():
        if not item["enabled"] or item["kind"] not in workers:
            continue
        try:
            due = datetime.fromisoformat(item["next_run_at"]) <= now
        except (TypeError, ValueError):
            due = False
        if not due:
            continue
        job = manager.create(item["kind"], f"定时：{item['name']}", str(item["params"].get("path") or item["params"].get("source_dir") or ""), item["params"])
        manager.start(job["id"], workers[item["kind"]])
        item["last_run_at"] = now.isoformat(timespec="seconds")
        item["last_job_id"] = job["id"]
        item["next_run_at"] = (now + timedelta(minutes=item["interval_minutes"])).isoformat(timespec="seconds")
        item["error"] = None
        save_schedule(item)

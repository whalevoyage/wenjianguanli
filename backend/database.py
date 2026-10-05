"""Local SQLite persistence for records that are queried frequently."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

_lock = threading.RLock()

# 会话标题的「噪音字符」：纯符号/一两个字的消息当标题没有信息量，会被退回默认名
_TITLE_NOISE_RE = re.compile(r"[\s·.。,，、\-—_~!！?？:：;；'\"“”‘’()（）\[\]【】]+")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT NOT NULL,
        type TEXT NOT NULL,
        result TEXT NOT NULL,
        path TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS trash (
        id TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        params TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        interval_minutes INTEGER NOT NULL,
        next_run_at TEXT NOT NULL,
        last_run_at TEXT,
        last_job_id TEXT,
        error TEXT
    );
    CREATE TABLE IF NOT EXISTS agent_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_agent_memory_conversation ON agent_memory(conversation_id, id);
    CREATE TABLE IF NOT EXISTS agent_conversation_meta (
        conversation_id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
    CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(time);
    CREATE INDEX IF NOT EXISTS idx_logs_type ON logs(type);
    """)
    return conn


def append_log(path: Path, entry: dict[str, Any], limit: int) -> None:
    with _lock, connect(path) as conn:
        extras = {k: v for k, v in entry.items() if k not in {"time", "type", "result", "path", "note"}}
        conn.execute(
            "INSERT INTO logs(time,type,result,path,note,payload) VALUES(?,?,?,?,?,?)",
            (entry.get("time", ""), entry.get("type", ""), entry.get("result", ""), entry.get("path", ""), entry.get("note", ""), json.dumps(extras, ensure_ascii=False)),
        )
        conn.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT ?)", (limit,))


def read_logs(path: Path, limit: int) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in reversed(rows):
        item = {"time": row["time"], "type": row["type"], "result": row["result"], "path": row["path"], "note": row["note"]}
        try:
            item.update(json.loads(row["payload"] or "{}"))
        except json.JSONDecodeError:
            pass
        result.append(item)
    return result


def replace_trash(path: Path, entries: list[dict]) -> None:
    with _lock, connect(path) as conn:
        conn.execute("DELETE FROM trash")
        conn.executemany("INSERT INTO trash(id,payload) VALUES(?,?)", [(str(e.get("id")), json.dumps(e, ensure_ascii=False)) for e in entries if e.get("id")])


def read_trash(path: Path) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT payload FROM trash").fetchall()
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["payload"]))
        except json.JSONDecodeError:
            continue
    return result


def replace_jobs(path: Path, jobs: list[dict], limit: int) -> None:
    with _lock, connect(path) as conn:
        conn.executemany(
            "INSERT INTO jobs(id,created_at,status,payload) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at,status=excluded.status,payload=excluded.payload",
            [(str(job["id"]), str(job.get("created_at") or ""), str(job.get("status") or ""), json.dumps(job, ensure_ascii=False)) for job in jobs if job.get("id")],
        )
        conn.execute("DELETE FROM jobs WHERE id NOT IN (SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?)", (limit,))


def read_jobs(path: Path, limit: int) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT payload FROM jobs ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        try:
            result.append(json.loads(row["payload"]))
        except json.JSONDecodeError:
            continue
    return result


def upsert_schedule(path: Path, schedule: dict) -> None:
    with _lock, connect(path) as conn:
        conn.execute(
            "INSERT INTO schedules(id,name,kind,params,enabled,interval_minutes,next_run_at,last_run_at,last_job_id,error) VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,params=excluded.params,enabled=excluded.enabled,interval_minutes=excluded.interval_minutes,next_run_at=excluded.next_run_at,last_run_at=excluded.last_run_at,last_job_id=excluded.last_job_id,error=excluded.error",
            (schedule["id"], schedule["name"], schedule["kind"], json.dumps(schedule.get("params") or {}, ensure_ascii=False), int(bool(schedule.get("enabled", True))), int(schedule.get("interval_minutes") or 10080), schedule["next_run_at"], schedule.get("last_run_at"), schedule.get("last_job_id"), schedule.get("error")),
        )


def list_schedules(path: Path) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT * FROM schedules ORDER BY name").fetchall()
    result = []
    for row in rows:
        try:
            params = json.loads(row["params"] or "{}")
        except json.JSONDecodeError:
            params = {}
        result.append({"id": row["id"], "name": row["name"], "kind": row["kind"], "params": params, "enabled": bool(row["enabled"]), "interval_minutes": row["interval_minutes"], "next_run_at": row["next_run_at"], "last_run_at": row["last_run_at"], "last_job_id": row["last_job_id"], "error": row["error"]})
    return result


def delete_schedule(path: Path, schedule_id: str) -> None:
    with _lock, connect(path) as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))


def append_memory(path: Path, conversation_id: str, role: str, content: str, created_at: str, limit: int = 400) -> None:
    with _lock, connect(path) as conn:
        conn.execute("INSERT INTO agent_memory(conversation_id,role,content,created_at) VALUES(?,?,?,?)", (conversation_id, role, content, created_at))
        conn.execute("DELETE FROM agent_memory WHERE conversation_id=? AND id NOT IN (SELECT id FROM agent_memory WHERE conversation_id=? ORDER BY id DESC LIMIT ?)", (conversation_id, conversation_id, limit))


def read_memory(path: Path, conversation_id: str, limit: int = 20) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT role,content,created_at FROM agent_memory WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
    return [{"role": row["role"], "content": row["content"], "created_at": row["created_at"]} for row in reversed(rows)]


def clear_memory(path: Path, conversation_id: str) -> None:
    with _lock, connect(path) as conn:
        conn.execute("DELETE FROM agent_memory WHERE conversation_id=?", (conversation_id,))


def set_conversation_title(path: Path, conversation_id: str, title: str, updated_at: str) -> None:
    """记一个会话的自定义标题（为空则清掉，退回按首条消息自动生成）。"""
    with _lock, connect(path) as conn:
        if title:
            conn.execute(
                "INSERT INTO agent_conversation_meta(conversation_id,title,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at",
                (conversation_id, title, updated_at),
            )
        else:
            conn.execute("DELETE FROM agent_conversation_meta WHERE conversation_id=?", (conversation_id,))


def list_conversations(path: Path, limit: int = 50) -> list[dict]:
    """列出所有会话桶（按最近活跃倒序）。标题优先用自定义的，否则取该会话第一条用户消息。"""
    with _lock, connect(path) as conn:
        rows = conn.execute(
            "SELECT conversation_id, COUNT(*) AS count, MIN(created_at) AS created_at, MAX(created_at) AS updated_at "
            "FROM agent_memory GROUP BY conversation_id ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        custom = {
            r["conversation_id"]: (r["title"] or "")
            for r in conn.execute("SELECT conversation_id, title FROM agent_conversation_meta").fetchall()
        }
        result = []
        for row in rows:
            first = conn.execute(
                "SELECT content FROM agent_memory WHERE conversation_id=? AND role='user' ORDER BY id ASC LIMIT 1",
                (row["conversation_id"],),
            ).fetchone()
            custom_title = (custom.get(row["conversation_id"]) or "").strip()
            if custom_title:
                title = custom_title
            else:
                derived = (first["content"] if first else "").strip().replace("\n", " ")
                title = derived[:40] if len(_TITLE_NOISE_RE.sub("", derived)) >= 2 else "新任务"
            result.append({
                "conversation_id": row["conversation_id"],
                "count": row["count"],
                "title": title,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return result

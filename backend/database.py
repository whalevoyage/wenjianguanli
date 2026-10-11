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

# 检索高亮的标记字符：FTS5 的 highlight()/snippet() 直接把这两颗字符插在命中词两侧，
# 前端按它们切段渲染，**不走 v-html**（文件名/正文内容不可信，不能直接当 HTML 注入）。
SEARCH_MARK_OPEN = "\u0001"
SEARCH_MARK_CLOSE = "\u0002"


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
        created_at TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT ''
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
    _ensure_memory_payload(conn)
    _ensure_search(conn)
    return conn


def _ensure_memory_payload(conn: sqlite3.Connection) -> None:
    """老库补列：`agent_memory.payload` 是后加的。

    `CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做，所以只改建表语句**补不上**
    已有数据库的列——必须在连接后显式 ALTER 一次。历史消息本来就没有计划可还原，
    因此老行的 payload 保持空字符串（= 没有 payload），不编造。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_memory)").fetchall()}
    if "payload" not in columns:
        conn.execute("ALTER TABLE agent_memory ADD COLUMN payload TEXT NOT NULL DEFAULT ''")


# ---------------------------------------------------------------------------
# 全文检索（B1）：索引根目录 / 文件元数据 / FTS5 索引
# ---------------------------------------------------------------------------

def _ensure_search(conn: sqlite3.Connection) -> None:
    """检索用表。FTS5 是 SQLite 的内置扩展，但**个别构建可能没编进去** ——
    那种情况下只跳过虚拟表，其余功能照常；检索页据 `search_available()` 明确提示，
    不静默失败、也不影响任何既有功能。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS search_root (
        id          TEXT PRIMARY KEY,
        path        TEXT NOT NULL,
        added_at    TEXT NOT NULL,
        indexed_at  TEXT,
        file_count  INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS search_file (
        id         INTEGER PRIMARY KEY,
        root_id    TEXT NOT NULL,
        path       TEXT NOT NULL UNIQUE,
        name       TEXT NOT NULL,
        ext        TEXT NOT NULL DEFAULT '',
        size       INTEGER NOT NULL DEFAULT 0,
        mtime      REAL    NOT NULL DEFAULT 0,
        indexed_at TEXT    NOT NULL,
        is_text    INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_search_file_root ON search_file(root_id);
    """)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
            "name, body, tokenize='unicode61')"
        )
    except sqlite3.OperationalError:
        # 该 SQLite 构建没有 FTS5：检索自降级，其余功能一律不受影响
        pass


def _has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone()
    return row is not None


def search_available(path: Path) -> bool:
    """当前构建是否支持全文索引（供接口如实告知前端）。"""
    with _lock, connect(path) as conn:
        return _has_fts(conn)


def search_roots(path: Path) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT * FROM search_root ORDER BY added_at").fetchall()
    return [
        {"id": row["id"], "path": row["path"], "added_at": row["added_at"],
         "indexed_at": row["indexed_at"], "file_count": row["file_count"]}
        for row in rows
    ]


def search_state(path: Path, root_id: str) -> dict[str, list]:
    """path -> [rowid, size, mtime]。

    **一次性读进内存**，供增量比对 —— 逐文件查库是 O(n) 次 I/O，
    正是 GOTCHAS「哈希缓存别每文件读一遍写一遍」踩过的坑。
    """
    with _lock, connect(path) as conn:
        rows = conn.execute(
            "SELECT id,path,size,mtime FROM search_file WHERE root_id=?", (root_id,)
        ).fetchall()
    return {row["path"]: [row["id"], row["size"], row["mtime"]] for row in rows}


def search_apply(
    path: Path,
    root_id: str,
    root_path: str,
    records: list[dict],
    deleted_paths: list[str],
    indexed_at: str,
) -> None:
    """把一批「新增/更新」记录与「失联」路径写进索引（单事务）。

    `records[i]` 的 `rowid` 为空表示新增，否则是就地更新（先删 FTS 行再按同一 rowid 重插）。
    """
    with _lock, connect(path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO search_root(id,path,added_at,indexed_at,file_count) VALUES(?,?,?,?,0)",
            (root_id, root_path, indexed_at, indexed_at),
        )
        if _has_fts(conn):
            for record in records:
                if record.get("rowid"):
                    row_id = int(record["rowid"])
                    conn.execute(
                        "UPDATE search_file SET name=?,ext=?,size=?,mtime=?,indexed_at=?,is_text=? WHERE id=?",
                        (record["name"], record["ext"], record["size"], record["mtime"],
                         indexed_at, record["is_text"], row_id),
                    )
                    conn.execute("DELETE FROM search_fts WHERE rowid=?", (row_id,))
                else:
                    cursor = conn.execute(
                        "INSERT INTO search_file(root_id,path,name,ext,size,mtime,indexed_at,is_text) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (root_id, record["path"], record["name"], record["ext"], record["size"],
                         record["mtime"], indexed_at, record["is_text"]),
                    )
                    row_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO search_fts(rowid,name,body) VALUES(?,?,?)",
                    (row_id, record["name_tokens"], record.get("body_tokens") or ""),
                )
            for gone in deleted_paths:
                row = conn.execute("SELECT id FROM search_file WHERE path=?", (gone,)).fetchone()
                if row:
                    conn.execute("DELETE FROM search_fts WHERE rowid=?", (row["id"],))
                    conn.execute("DELETE FROM search_file WHERE id=?", (row["id"],))
        else:
            # 没有 FTS5 时仍维护元数据，只是"按内容搜"不可用
            for record in records:
                if record.get("rowid"):
                    conn.execute(
                        "UPDATE search_file SET name=?,ext=?,size=?,mtime=?,indexed_at=?,is_text=? WHERE id=?",
                        (record["name"], record["ext"], record["size"], record["mtime"],
                         indexed_at, record["is_text"], int(record["rowid"])),
                    )
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO search_file(root_id,path,name,ext,size,mtime,indexed_at,is_text) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (root_id, record["path"], record["name"], record["ext"], record["size"],
                         record["mtime"], indexed_at, record["is_text"]),
                    )
            for gone in deleted_paths:
                conn.execute("DELETE FROM search_file WHERE path=?", (gone,))
        conn.execute(
            "UPDATE search_root SET file_count=(SELECT COUNT(*) FROM search_file WHERE root_id=?), indexed_at=? WHERE id=?",
            (root_id, indexed_at, root_id),
        )


def search_forget_root(path: Path, root_id: str) -> int:
    with _lock, connect(path) as conn:
        ids = [row["id"] for row in conn.execute(
            "SELECT id FROM search_file WHERE root_id=?", (root_id,)
        ).fetchall()]
        if _has_fts(conn):
            for row_id in ids:
                conn.execute("DELETE FROM search_fts WHERE rowid=?", (row_id,))
        conn.execute("DELETE FROM search_file WHERE root_id=?", (root_id,))
        conn.execute("DELETE FROM search_root WHERE id=?", (root_id,))
    return len(ids)


def search_known(path: Path, file_path: str) -> bool:
    """这个文件在不在索引里。

    给「在资源管理器打开」做护栏 —— 那个接口会去碰系统 shell，
    只允许定位**已经被索引过**的文件，不能被拿来探任意路径。
    """
    with _lock, connect(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM search_file WHERE path=?", (file_path,)
        ).fetchone()
    return row is not None


def search_query(path: Path, match: str, limit: int, root_id: str = "") -> list[dict]:
    """按 FTS5 MATCH 表达式检索。

    - `highlight(...,0,...)` 取**完整**文件名（带标记）而不是片段 —— snippet 对没有命中的
      列会返回开头截断的一小段，拿来当文件名会显示成残缺的名字。
    - `snippet(...,1,...)` 取正文命中片段。
    """
    sql = (
        "SELECT f.id, f.path, f.name, f.ext, f.size, f.mtime, f.root_id, f.is_text, "
        "highlight(search_fts, 0, ?, ?) AS name_hl, "
        "snippet(search_fts, 1, ?, ?, '…', 20) AS body_hl "
        "FROM search_fts JOIN search_file f ON f.id = search_fts.rowid "
        "WHERE search_fts MATCH ? "
    )
    params: list[Any] = [SEARCH_MARK_OPEN, SEARCH_MARK_CLOSE, SEARCH_MARK_OPEN, SEARCH_MARK_CLOSE, match]
    if root_id:
        sql += "AND f.root_id = ? "
        params.append(root_id)
    sql += "ORDER BY bm25(search_fts), f.mtime DESC LIMIT ?"
    params.append(limit)
    with _lock, connect(path) as conn:
        if not _has_fts(conn):
            return []
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


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


def append_memory(
    path: Path,
    conversation_id: str,
    role: str,
    content: str,
    created_at: str,
    limit: int = 400,
    payload: dict | None = None,
) -> None:
    """写一条对话记忆。`payload` 存这条消息额外要还原的结构（计划、意图、步骤…）。

    正文仍然只存 content —— 喂给模型的是 build_context_memory，它只读 content，
    所以 payload 再大也不会挤占模型的上下文预算。
    """
    raw = json.dumps(payload, ensure_ascii=False) if payload else ""
    with _lock, connect(path) as conn:
        conn.execute(
            "INSERT INTO agent_memory(conversation_id,role,content,created_at,payload) VALUES(?,?,?,?,?)",
            (conversation_id, role, content, created_at, raw[:20000]),
        )
        conn.execute("DELETE FROM agent_memory WHERE conversation_id=? AND id NOT IN (SELECT id FROM agent_memory WHERE conversation_id=? ORDER BY id DESC LIMIT ?)", (conversation_id, conversation_id, limit))


def read_memory(path: Path, conversation_id: str, limit: int = 20) -> list[dict]:
    with _lock, connect(path) as conn:
        rows = conn.execute("SELECT role,content,created_at,payload FROM agent_memory WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conversation_id, limit)).fetchall()
    result = []
    for row in reversed(rows):
        item = {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
        raw = row["payload"]
        if raw:
            try:
                item["payload"] = json.loads(raw)
            except json.JSONDecodeError:
                # 坏 payload 当没有处理，不影响正文与其它消息
                pass
        result.append(item)
    return result


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

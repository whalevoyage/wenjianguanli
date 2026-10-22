"""JSON 持久层。

项目不用数据库，所有状态以 JSON 文件存在 .filepilot/ 下：
jobs.json（任务）、logs.json（审计日志）、trash.json（隔离区账本）、recipes.json（Agent 配方）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from . import config
from . import database

_lock = threading.RLock()


def load_json(path: Path, default: Any) -> Any:
    with _lock:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default


def save_json(path: Path, data: Any) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with temporary.open("r+b") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

def read_logs() -> list[dict]:
    data = database.read_logs(config.DATABASE_FILE, config.MAX_LOG_ENTRIES)
    if data:
        return data
    legacy = load_json(config.LOG_FILE, [])
    if isinstance(legacy, list) and legacy:
        for entry in legacy:
            database.append_log(config.DATABASE_FILE, entry, config.MAX_LOG_ENTRIES)
        return legacy[-config.MAX_LOG_ENTRIES:]
    return []


def reclaimed_stats() -> dict:
    """累计腾出：把所有任务日志里记的 reclaimed 加起来。

    ⚠️ 口径要说清：**文件是先进隔离区的，清空隔离区后才真正释放磁盘空间。**
       所以这个数字叫「已腾出」而不是「已释放」，界面上也会这么写。
       老日志没有 reclaimed 字段 → 自然算成 0，不会被误计。
    """
    from .fsutil import format_size, now_iso

    logs = read_logs()
    today_prefix = now_iso()[:10]
    total = 0
    today = 0
    count = 0
    for entry in logs:
        if entry.get("result") != "成功":
            continue
        value = entry.get("reclaimed")
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        total += int(value)
        count += 1
        if str(entry.get("time") or "").startswith(today_prefix):
            today += int(value)
    return {
        "total": total,
        "total_text": format_size(total),
        "today": today,
        "today_text": format_size(today),
        "count": count,
        "note": "文件先进隔离区，清空隔离区后才真正释放磁盘空间。",
    }


def write_log(kind: str, result: str, path: str = "", note: str = "", **extra: Any) -> dict:
    """写一条审计日志。字段固定为 time/type/result/path/note，其余进 extra。"""
    from .fsutil import now_iso

    entry = {"time": now_iso(), "type": kind, "result": result, "path": path, "note": note}
    if extra:
        entry.update(extra)
    database.append_log(config.DATABASE_FILE, entry, config.MAX_LOG_ENTRIES)
    return entry


# ---------------------------------------------------------------------------
# 隔离区账本
# ---------------------------------------------------------------------------

def read_trash() -> list[dict]:
    data = database.read_trash(config.DATABASE_FILE)
    if data:
        return data
    legacy = load_json(config.TRASH_MANIFEST, [])
    if isinstance(legacy, list) and legacy:
        database.replace_trash(config.DATABASE_FILE, legacy)
        return legacy
    return []


def save_trash(entries: list[dict]) -> None:
    database.replace_trash(config.DATABASE_FILE, entries)


# ---------------------------------------------------------------------------
# Agent 配方
# ---------------------------------------------------------------------------

def read_recipes() -> list[dict]:
    data = load_json(config.RECIPES_FILE, [])
    return data if isinstance(data, list) else []


def save_recipes(entries: list[dict]) -> None:
    save_json(config.RECIPES_FILE, entries)


# ---------------------------------------------------------------------------
# 用户设置（目前只有 Agent 的模型配置）
#
# API Key 必须由 agent_settings 调用 secrets.protect() 后再交给通用 JSON 层；
# Windows 发行版使用 DPAPI，失败时拒绝保存。显式非 Windows 开发明文模式会带 plain: 标记。
# ---------------------------------------------------------------------------

def read_settings() -> dict:
    data = load_json(config.SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_settings(settings: dict) -> None:
    save_json(config.SETTINGS_FILE, settings)


def read_agent_settings() -> dict:
    agent = read_settings().get("agent")
    return agent if isinstance(agent, dict) else {}


def save_agent_settings(agent: dict) -> None:
    settings = read_settings()
    settings["agent"] = agent
    save_settings(settings)


def read_safety_settings() -> dict:
    """权限档位（safety.mode）与写保护相关的用户设置。"""
    safety = read_settings().get("safety")
    return safety if isinstance(safety, dict) else {}


def save_safety_settings(safety: dict) -> None:
    settings = read_settings()
    settings["safety"] = safety
    save_settings(settings)


# ---------------------------------------------------------------------------
# Agent 偏好记忆（prefs.json）
#
# 与对话记忆分开：对话记忆是逐条历史（存在 filepilot.db），prefs 是提炼后的
# 长期结论（常用路径 / 排除标签 / 永久约束 / 常用策略），用户可在 Agent 页编辑。
# 读取时按 config.PREFS_DEFAULT 补齐缺键，前端不必到处判空。
# ---------------------------------------------------------------------------

def read_prefs() -> dict:
    data = load_json(config.PREFS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    prefs = {key: (list(value) if isinstance(value, list) else value)
             for key, value in config.PREFS_DEFAULT.items()}
    for key, value in data.items():
        prefs[key] = value
    return prefs


def save_prefs(prefs: dict) -> None:
    save_json(config.PREFS_FILE, prefs)


# ---------------------------------------------------------------------------
# 每个会话的最近一次意图快照（agent_state.json）
#
# 只存结论：最近作用范围、最近动作、最近一次任务 id。供「就刚才那个目录」
# 这类指代复用。原文仍在 conversation memory 里，这里不重复存。
# 单文件容量不做硬清理，只保留最近 MAX 个会话。
# ---------------------------------------------------------------------------

AGENT_STATE_KEEP = 30


def read_agent_state() -> dict:
    data = load_json(config.AGENT_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def get_conversation_state(conversation_id: str) -> dict:
    state = read_agent_state().get(conversation_id)
    return state if isinstance(state, dict) else {}


def save_conversation_state(conversation_id: str, patch: dict) -> dict:
    state = read_agent_state()
    current = state.get(conversation_id)
    merged = {**(current if isinstance(current, dict) else {}), **patch}
    state[conversation_id] = merged
    if len(state) > AGENT_STATE_KEEP:
        ordered = sorted(
            state.items(),
            key=lambda item: str(item[1].get("updated_at") or ""),
            reverse=True,
        )[:AGENT_STATE_KEEP]
        state = dict(ordered)
    save_json(config.AGENT_STATE_FILE, state)
    return merged


def clear_conversation_state(conversation_id: str) -> None:
    state = read_agent_state()
    if state.pop(conversation_id, None) is not None:
        save_json(config.AGENT_STATE_FILE, state)

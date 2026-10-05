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
# 注意：这里的 api_key 是明文存在本机 .filepilot/settings.json 里
# （该目录已在 .gitignore 中，不会进仓库）。个人自用工具，可接受；
# 对外分发前应先换成系统凭据管理器 / DPAPI。
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

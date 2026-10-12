"""扫描快照：保存目录扫描结果，支持两次快照对比（回答"比上次哪里变大了"）。

只存**目录级**体积聚合（不含逐文件），所以快照文件体积可控；对比时给出
新增 / 消失 / 变大 / 变小 的目录与总量差。

口径护栏（与"扫描差异"的边界一致）：
- 只在同一 root 下比；root 不同直接报错。
- 排除规则或扫描深度不同 → 标 `scope_mismatch`，界面提示"范围不一致"，
  不让用户拿不同范围的两次扫描硬比出误导性的"增长"。
"""
from __future__ import annotations

import uuid
from typing import Any

from .. import config, fsutil
from ..store import load_json, save_json

MAX_SNAPSHOTS_PER_ROOT = 20


def _normalize_root(root: str) -> str:
    # 归一化斜杠 / 结尾分隔符 / 大小写，让同一目录的不同写法落到同一个归档桶。
    # Windows 文件系统大小写不敏感，所以整条路径做大小写无关归一化（盘符大写、其余小写）。
    raw = str(root).strip().replace("\\", "/").rstrip("/")
    if len(raw) >= 2 and raw[1] == ":":
        raw = raw[0].upper() + raw[2:].lower()
    else:
        raw = raw.lower()
    return raw


def save_snapshot(result: dict) -> dict:
    """把一个扫描结果存成快照。返回快照元信息（不含 dir_sizes）。"""
    root = _normalize_root(result.get("root") or "")
    if not root:
        raise ValueError("快照缺少 root")
    dir_sizes = {str(k): int(v) for k, v in (result.get("dir_sizes") or {}).items()}
    snapshot = {
        "id": uuid.uuid4().hex,
        "time": fsutil.now_iso(),
        "root": root,
        "sources": result.get("sources") or [root],
        "excludes": list(result.get("excludes") or []),
        "max_depth": int(result.get("max_depth") or 0),
        "total_size": int(result.get("total_size") or 0),
        "total_files": int(result.get("total_files") or 0),
        "total_dirs": int(result.get("total_dirs") or 0),
        "dir_sizes": dir_sizes,
    }
    data: dict[str, Any] = load_json(config.SNAPSHOT_FILE, {})
    if not isinstance(data, dict):
        data = {}
    bucket = data.setdefault(root, [])
    bucket.append(snapshot)
    if len(bucket) > MAX_SNAPSHOTS_PER_ROOT:
        data[root] = bucket[-MAX_SNAPSHOTS_PER_ROOT:]
    save_json(config.SNAPSHOT_FILE, data)
    return _meta(snapshot)


def _meta(snap: dict) -> dict:
    return {
        "id": snap["id"],
        "time": snap["time"],
        "root": snap["root"],
        "sources": snap.get("sources"),
        "excludes": snap.get("excludes"),
        "max_depth": snap.get("max_depth"),
        "total_size": snap["total_size"],
        "total_size_text": fsutil.format_size(snap["total_size"]),
        "total_files": snap["total_files"],
        "total_dirs": snap["total_dirs"],
    }


def list_snapshots(root: str) -> list[dict]:
    data = load_json(config.SNAPSHOT_FILE, {})
    if not isinstance(data, dict):
        return []
    return [_meta(s) for s in (data.get(_normalize_root(root)) or [])]


def get_snapshot(root: str, snap_id: str) -> dict | None:
    data = load_json(config.SNAPSHOT_FILE, {})
    if not isinstance(data, dict):
        return None
    for snap in data.get(_normalize_root(root)) or []:
        if snap.get("id") == snap_id:
            return snap
    return None


def compare_snapshots(root: str, base_id: str, target_id: str) -> dict:
    base = get_snapshot(root, base_id)
    target = get_snapshot(root, target_id)
    if base is None or target is None:
        raise ValueError("找不到指定的快照")
    return _diff(base, target)


def _diff(base: dict, target: dict) -> dict:
    scope_mismatch = (
        set(base.get("excludes") or []) != set(target.get("excludes") or [])
        or int(base.get("max_depth") or 0) != int(target.get("max_depth") or 0)
    )
    a = base.get("dir_sizes") or {}
    b = target.get("dir_sizes") or {}
    all_dirs = set(a) | set(b)
    added, removed, grown, shrunk = [], [], [], []
    for d in all_dirs:
        sa = a.get(d)
        sb = b.get(d)
        if sa is None:
            added.append((d, sb))            # 只在后一次出现 = 新增目录
        elif sb is None:
            removed.append((d, sa))          # 只在前一次出现 = 消失目录
        else:
            delta = sb - sa
            if delta > 0:
                grown.append((d, delta))
            elif delta < 0:
                shrunk.append((d, -delta))
    added.sort(key=lambda item: item[1], reverse=True)
    removed.sort(key=lambda item: item[1], reverse=True)
    grown.sort(key=lambda item: item[1], reverse=True)
    shrunk.sort(key=lambda item: item[1], reverse=True)

    total_delta = target["total_size"] - base["total_size"]
    return {
        "root": target["root"],
        "base": _meta(base),
        "target": _meta(target),
        "scope_mismatch": scope_mismatch,
        "total_size_delta": total_delta,
        "total_size_delta_text": _signed(total_delta),
        "total_files_delta": target["total_files"] - base["total_files"],
        "total_dirs_delta": target["total_dirs"] - base["total_dirs"],
        "added": [_row(d, s) for d, s in added[:50]],
        "removed": [_row(d, s) for d, s in removed[:50]],
        "grown": [_delta_row(d, dd) for d, dd in grown[:50]],
        "shrunk": [_delta_row(d, dd) for d, dd in shrunk[:50]],
    }


def _row(path: str, size: int) -> dict:
    return {"path": path, "size": size, "size_text": fsutil.format_size(size)}


def _delta_row(path: str, delta: int) -> dict:
    return {"path": path, "delta": delta, "delta_text": _signed(delta)}


def _signed(value: int) -> str:
    text = fsutil.format_size(abs(value))
    return ("+" if value > 0 else "-" if value < 0 else "") + text

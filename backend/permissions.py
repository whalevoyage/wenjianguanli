"""权限档位：readonly / standard / full。

全项目**唯一的档位解析处**。档位存 `.filepilot/settings.json` 的 `safety.mode`，
默认 `standard`（等于历史行为）。

三条底限与档位无关，任何情况下都成立：
1. 删除一律先进隔离区，永不直接删（由 clean 的实现保证）；
2. 自保护目录（运行数据、项目自身、隔离区账本）不可写；
3. 切到「完全访问」需要显式确认（confirm=True），否则拒绝。
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config, store


def current() -> str:
    """当前档位。设置文件里的值非法或缺失时回落到默认档。"""
    stored = (store.read_safety_settings().get("mode") or "").strip().lower()
    return stored if stored in config.PERMISSION_MODE_IDS else config.DEFAULT_PERMISSION_MODE


def label(mode: str | None = None) -> str:
    target = mode or current()
    for item in config.PERMISSION_MODES:
        if item["id"] == target:
            return item["label"]
    return target


def describe() -> dict:
    mode = current()
    return {
        "mode": mode,
        "label": label(mode),
        "modes": config.PERMISSION_MODES,
        "allows_write": mode != "readonly",
        "relaxes_protection": mode == "full",
        "guards": [
            "删除一律先进隔离区，永不直接删",
            "运行数据、项目自身与隔离区账本任何档位都不可写",
        ],
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def set_mode(mode: str, confirm: bool = False) -> dict:
    """切换档位。full 需要 confirm=True，否则报错——这是「完全访问」的第二道确认。"""
    value = (mode or "").strip().lower()
    if value not in config.PERMISSION_MODE_IDS:
        raise ValueError(f"未知的权限档位：{mode}")

    if value == "full" and not confirm:
        raise PermissionError("切到「完全访问」需要显式确认")

    payload = store.read_safety_settings()
    payload["mode"] = value
    store.save_safety_settings(payload)
    return describe()

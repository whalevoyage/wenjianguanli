"""权限档位：readonly / standard / full。

全项目**唯一的档位解析处**。档位存 `.filepilot/settings.json` 的 `safety.mode`，
默认 `standard`（等于历史行为）。

三条底限与档位无关，任何情况下都成立：
1. 删除一律先进隔离区，永不直接删（由 clean 的实现保证）；
2. 自保护目录（运行数据、项目自身、隔离区账本）不可写；
3. 切到「完全访问」需要显式确认（confirm=True），否则拒绝。

**门禁要加在服务层**（`require_write()`），不能只写在路由上。
只写在 `main._launch()` 时，不走 `_launch` 的写入路径会全漏 —— 2026-10-08 审计实测：
只读档下「回滚任务」和「还原隔离区」两条 HTTP 200、**真的改动了文件**；
「永久清空隔离区」「新建定时任务」两条则连检查都没有。
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import config, store
from .fsutil import BlockedPathError


def current() -> str:
    """当前档位。设置文件里的值非法或缺失时回落到默认档。"""
    stored = (store.read_safety_settings().get("mode") or "").strip().lower()
    return stored if stored in config.PERMISSION_MODE_IDS else config.DEFAULT_PERMISSION_MODE


def readonly_error(action: str = "写操作") -> BlockedPathError:
    """只读档的统一拒绝异常。文案只在这里定义一次。

    `execution` 层要能复用同一句话：批量循环里档位已经取过，不该再读一遍设置文件，
    但拒绝时的说法必须和 `require_write()` 一模一样 —— 否则同一个限制会出现两种措辞。
    """
    return BlockedPathError(
        f"当前是「只读」档，不允许{action}。切到「标准」或「完全访问」后再试"
    )


def require_write(action: str = "写操作") -> None:
    """写入口的统一门禁：只读档直接拒，其余档位放行。

    **必须放在服务层，不能只写在路由上。** 门禁只加在 `main._launch()` 里时，
    任何不走 `_launch` 的写入路径都会漏掉 —— 回滚、还原隔离区、清空隔离区、
    建定时任务四条当时就都是裸的（2026-10-08 审计实测：只读档下前两条真的改动了文件）。

    `action` 只用来拼错误信息，让用户知道被拒的是哪件事。
    抛 BlockedPathError（ValueError 的子类），路由层的
    `except (OSError, ValueError)` 会把它翻成 400 + 原文，不会变成 500。
    """
    if current() == "readonly":
        raise readonly_error(action)


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
            "「只读」档下新建写入任务、回滚、还原/清空隔离区、建定时任务一律拒绝",
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

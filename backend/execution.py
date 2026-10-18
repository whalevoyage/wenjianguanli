"""会改动用户文件的底层操作，统一从这里走。

## 为什么要有这一层

门禁以前散在各服务里，靠「记得调」。2026-10-08 审计实测过一次漏调：只读档下
「回滚任务」和「还原隔离区」两条 HTTP 200、**真的改动了文件**。2026-10-17 又发现
`restore_trash` 只检查了权限档，没检查**还原目标**是不是软保护目录 —— 也就是说
「从隔离区还原到桌面」在 standard 档下是放行的。

规则改为：**服务层不直接调 `shutil.move` / `unlink` / `rmtree`**，
一律经过这里，检查顺序固定：

1. 权限档（readonly → 拒）
2. 目标写保护（自保护 / 软保护 / 系统只读区）
3. 源路径的符号链接逃逸
4. 目标必须落在允许的根内（调用方给了 `dest_root` 时）

`into_tool_state=True` 表示目标是**工具自己的数据**（隔离区 / 默认备份目录）。
这类写入不套自保护门禁 —— 与 `backup.run_backup` 的口径一致：工具管理自己的状态，
不拿自己的护栏拦自己。但**权限档仍然生效**，只读档一样动不了。

## 与 fsutil 的分工

`fsutil` 是纯原语（路径判定、哈希、复制），不自知权限档，也不知道业务语义。
这一层是唯一的「写」入口，负责把「能不能写」和「怎么写」绑在一起。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import fsutil, permissions

__all__ = [
    "assert_can_write",
    "move",
    "remove",
    "remove_empty_dir",
    "recycle",
    "mkdir",
]


def assert_can_write(
    path: Path | str,
    *,
    action: str = "写操作",
    mode: str | None = None,
    into_tool_state: bool = False,
) -> str:
    """纯检查，不动文件。返回生效的档位，供批量调用方在循环里复用。

    `mode` 是调用方在批量开始前取好的档位。传 None 就现取一次 ——
    单个操作不必在意开销，上千个文件的循环应该自己取一次传进来。
    """
    if mode is None:
        permissions.require_write(action)
        mode = permissions.current()
    elif mode == "readonly":
        raise permissions.readonly_error(action)

    if not into_tool_state:
        fsutil.assert_writable(Path(path), mode)
    return mode


def _check_source(source: Path | str, *, check_symlink: bool) -> Path:
    """源路径只做链接逃逸检查，不做写保护 —— 读是允许的，写才受限。"""
    path = Path(source)
    if check_symlink:
        fsutil.assert_no_symlink(path)
    if not path.exists():
        raise ValueError(f"源路径不存在：{path}")
    return path


def _check_dest(
    target: Path | str,
    *,
    action: str,
    mode: str | None,
    into_tool_state: bool,
    dest_root: Path | str | None,
) -> Path:
    path = Path(target)
    assert_can_write(path, action=action, mode=mode, into_tool_state=into_tool_state)
    if dest_root is not None:
        # 目标必须落在指定根内（就地整理：不许移动到根外面去）
        fsutil.assert_within(path, dest_root)
    return path


def move(
    source: Path | str,
    target: Path | str,
    *,
    action: str = "移动文件",
    mode: str | None = None,
    into_tool_state: bool = False,
    dest_root: Path | str | None = None,
    check_symlink: bool = True,
) -> Path:
    """移动文件或目录。跨盘时由 shutil 退化成「复制 + 删源」，与原行为一致。"""
    src = _check_source(source, check_symlink=check_symlink)
    dst = _check_dest(
        target,
        action=action,
        mode=mode,
        into_tool_state=into_tool_state,
        dest_root=dest_root,
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def remove(
    path: Path | str,
    *,
    recursive: bool = False,
    action: str = "删除文件",
    mode: str | None = None,
    into_tool_state: bool = False,
    check_symlink: bool = True,
) -> None:
    """删除文件；`recursive=True` 时删整棵目录树。

    ★ 这是**不可恢复**的操作。全项目只有两处该调用它：
      `clean.purge_trash`（清空隔离区）与 `migrate`（校验通过后删源）。
      任何新增调用点都必须先过用户确认。
    """
    target = Path(path)
    if not target.exists() and not target.is_symlink():
        return
    assert_can_write(target, action=action, mode=mode, into_tool_state=into_tool_state)
    if check_symlink:
        fsutil.assert_no_symlink(target)

    if target.is_dir() and not target.is_symlink():
        if not recursive:
            target.rmdir()          # 非空会自己抛 OSError，不静默吞掉
            return
        shutil.rmtree(target, ignore_errors=False)
        return
    target.unlink()


def remove_empty_dir(
    path: Path | str,
    *,
    action: str = "删除空目录",
    mode: str | None = None,
    into_tool_state: bool = False,
) -> None:
    """删空目录。非空会抛 OSError —— 调用方按「跳过」处理，不要当成功。"""
    target = Path(path)
    if not target.exists():
        return
    assert_can_write(target, action=action, mode=mode, into_tool_state=into_tool_state)
    target.rmdir()


def recycle(
    path: Path | str,
    *,
    action: str = "送到回收站",
    mode: str | None = None,
) -> bool:
    """送系统回收站。返回 False 表示系统不肯删（占用、权限等），不抛异常。"""
    target = Path(path)
    assert_can_write(target, action=action, mode=mode)
    return fsutil.send_to_recycle_bin(target)


def mkdir(
    path: Path | str,
    *,
    action: str = "创建目录",
    mode: str | None = None,
    into_tool_state: bool = False,
) -> Path:
    target = Path(path)
    if target.exists():
        return target
    assert_can_write(target, action=action, mode=mode, into_tool_state=into_tool_state)
    target.mkdir(parents=True, exist_ok=True)
    return target

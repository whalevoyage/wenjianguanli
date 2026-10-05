"""文件系统底层工具。

只做"怎么安全地碰文件系统"这一件事：路径校验、安全拦截、遍历、哈希、
容量查询、目录树、原子移动。业务语义（扫描/查重/垃圾/迁移）放在 services 里。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from . import config

ProgressFn = Callable[[str, int, int | None, str], None]
CancelFn = Callable[[], bool]


# ---------------------------------------------------------------------------
# 基础格式化
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_size(value: int | float) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# 路径解析与安全拦截
# ---------------------------------------------------------------------------

class BlockedPathError(Exception):
    """命中了不可碰的安全黑名单。"""


def _resolved(path: Path | str) -> Path:
    """Resolve a path for security comparisons without requiring it to exist."""
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return Path(path).expanduser().absolute()


def assert_within(path: Path | str, root: Path | str) -> Path:
    """Return the resolved path only when it is inside root (or root itself)."""
    target = _resolved(path)
    base = _resolved(root)
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise BlockedPathError(f"路径越出允许目录：{target}") from exc
    return target


def assert_no_symlink(path: Path | str) -> Path:
    """Reject symlinks in every existing component of a path."""
    candidate = Path(path).expanduser()
    current = candidate
    while True:
        if current.exists() and current.is_symlink():
            raise BlockedPathError(f"不允许通过符号链接操作：{current}")
        if current.parent == current:
            break
        current = current.parent
    return _resolved(candidate)


def assert_migration_paths(source: Path | str, target: Path | str) -> tuple[Path, Path]:
    """Validate source/target roots before a copy or move operation."""
    source_path = assert_no_symlink(source)
    target_path = _resolved(target)
    if source_path == target_path:
        raise ValueError("源目录和目标目录不能相同")
    try:
        target_path.relative_to(source_path)
    except ValueError:
        pass
    else:
        raise ValueError("目标目录不能位于源目录内部")
    try:
        source_path.relative_to(target_path)
    except ValueError:
        pass
    else:
        raise ValueError("源目录不能位于目标目录内部")
    return source_path, target_path


def normalize(value: str) -> Path:
    raw = (value or "").strip().strip('"')
    if not raw:
        raise ValueError("路径不能为空")
    return Path(raw).expanduser()


def is_blocked(path: Path) -> str | None:
    """命中黑名单时返回原因，允许操作时返回 None。"""
    try:
        target = path.resolve()
    except (OSError, RuntimeError):
        target = path

    for blocked in config.SAFETY_BLOCKLIST:
        try:
            resolved_blocked = blocked.resolve()
        except (OSError, RuntimeError):
            resolved_blocked = blocked
        if target == resolved_blocked or resolved_blocked in target.parents:
            return f"「{resolved_blocked.name}」属于保护目录，禁止改动"

    return None


def is_readonly_zone(path: Path) -> bool:
    """系统目录：允许扫描，禁止写入/删除/移动。"""
    text = str(path).replace("/", "\\").lower()
    return any(text.startswith(prefix) for prefix in config.SYSTEM_READONLY_PREFIXES)


def write_block_reason(path: Path, mode: str = config.DEFAULT_PERMISSION_MODE) -> str | None:
    """按权限档位判断能否写入。返回拦截原因，允许时返回 None。

    这是全项目**唯一的写入门禁**，档位由调用方传入（本模块保持纯原语，不读设置）。
      - 自保护目录：任何档位都拦（运行数据 / 项目自身 / 隔离区账本）
      - 软保护目录（桌面）与系统只读区：只有 full 档放开
    """
    try:
        target = path.resolve()
    except (OSError, RuntimeError):
        target = path

    for blocked in config.SELF_PROTECTED_DIRS:
        try:
            resolved = blocked.resolve()
        except (OSError, RuntimeError):
            resolved = blocked
        if target == resolved or resolved in target.parents:
            return f"「{resolved.name}」是工具自身的保护目录，任何权限档位都禁止改动"

    if mode == "full":
        return None

    for blocked in config.SOFT_PROTECTED_DIRS:
        try:
            resolved = blocked.resolve()
        except (OSError, RuntimeError):
            resolved = blocked
        if target == resolved or resolved in target.parents:
            return f"「{resolved.name}」属于保护目录，禁止改动（切到「完全访问」才能改动）"

    if is_readonly_zone(target):
        return "系统目录只允许查看，不允许改动（切到「完全访问」才能改动）"

    return None


def assert_writable(path: Path, mode: str = config.DEFAULT_PERMISSION_MODE) -> None:
    reason = write_block_reason(path, mode)
    if reason:
        raise BlockedPathError(reason)


def resolve_dir(value: str) -> Path:
    path = normalize(value)
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{path}")
    if not path.is_dir():
        raise NotADirectoryError(f"不是文件夹：{path}")
    return path


def resolve_target(value: str) -> Path:
    path = normalize(value)
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{path}")
    return path


# ---------------------------------------------------------------------------
# 遍历（带排除、深度限制、进度、可中断）
# ---------------------------------------------------------------------------

def _pruned(excludes: set[str], name: str) -> bool:
    return name in excludes


def iter_files(
    root: Path,
    *,
    excludes: Iterable[str] | None = None,
    max_depth: int | None = None,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
) -> Iterator[Path]:
    """遍历文件。会跳过排除目录、保护目录，超限自动停止。

    max_depth=0 表示不限深度；1 表示只看 root 下一层。
    """
    exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
    if excludes is not None:
        exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

    root_depth = len(root.resolve().parts)
    scanned = 0

    for current, subdirs, filenames in os.walk(root):
        if cancelled and cancelled():
            return

        current_path = Path(current)
        current_depth = len(current_path.resolve().parts) - root_depth

        # 深度裁剪 + 排除裁剪（max_depth 为 0/None 表示不限深度）
        if max_depth and current_depth >= max_depth:
            subdirs[:] = []
        else:
            kept = []
            for name in subdirs:
                if _pruned(exclude_set, name):
                    continue
                if is_blocked(current_path / name) or is_readonly_zone(current_path / name):
                    continue
                kept.append(name)
            subdirs[:] = kept

        for name in filenames:
            scanned += 1
            if scanned > config.SCAN_ENTRY_LIMIT:
                return
            if progress and scanned % 200 == 0:
                progress("正在遍历目录", scanned, None, str(current_path))
            yield current_path / name


def count_entries(root: Path, *, excludes: Iterable[str] | None = None) -> int:
    """快速估算条目数，用于进度百分比。"""
    total = 0
    for _ in iter_files(root, excludes=excludes):
        total += 1
    return total


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

CHUNK = 1024 * 1024


def hash_file(path: Path, *, cancelled: CancelFn | None = None) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            if cancelled and cancelled():
                raise InterruptedError("已取消")
            block = stream.read(CHUNK)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def quick_hash(path: Path, size: int = 4096) -> str:
    """只读文件头，用于查重前两级快速过滤。"""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        hasher.update(stream.read(size))
    return hasher.hexdigest()


def verify_copy(source: Path, target: Path) -> bool:
    """逐字节校验两个文件是否一致（先比大小，再比哈希）。"""
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
    except OSError:
        return False
    return hash_file(source) == hash_file(target)


# ---------------------------------------------------------------------------
# 磁盘容量
# ---------------------------------------------------------------------------

def disk_stats() -> list[dict]:
    result = []
    for letter in config.drive_letters():
        root = f"{letter}:\\"
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        percent = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
        result.append({
            "letter": letter,
            "root": root,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "total_text": format_size(usage.total),
            "used_text": format_size(usage.used),
            "free_text": format_size(usage.free),
            "percent": percent,
            "level": "danger" if percent >= 90 else "warning" if percent >= 75 else "ok",
        })
    return result


def disk_free(path: Path) -> int:
    try:
        return shutil.disk_usage(path.anchor or str(path)).free
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# 目录树（空间地图用）
# ---------------------------------------------------------------------------

def build_tree(
    root: Path,
    *,
    depth: int = config.TREE_DEFAULT_DEPTH,
    excludes: Iterable[str] | None = None,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
) -> dict:
    """构建带大小的目录树，只展开 depth 层，深层合并进 other。"""

    def walk(path: Path, level: int) -> dict:
        node = {"name": path.name or str(path), "path": str(path), "size": 0, "is_dir": True, "children": []}
        try:
            entries = list(os.scandir(path))
        except OSError:
            return node

        exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
        if excludes is not None:
            exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

        for entry in entries:
            if cancelled and cancelled():
                return node
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in exclude_set or is_blocked(Path(entry.path)):
                        continue
                    child = walk(Path(entry.path), level + 1) if level < depth else {
                        "name": entry.name, "path": entry.path, "size": 0, "is_dir": True, "children": [], "collapsed": True,
                    }
                    node["size"] += child["size"]
                    node["children"].append(child)
                else:
                    stat = entry.stat()
                    node["size"] += stat.st_size
                    node["children"].append({
                        "name": entry.name, "path": entry.path,
                        "size": stat.st_size, "is_dir": False,
                    })
            except OSError:
                continue

        node["children"].sort(key=lambda item: item["size"], reverse=True)
        if progress:
            progress("正在统计占用", node["size"], None, str(path))
        return node

    tree = walk(root, 1)
    tree["size_text"] = format_size(tree["size"])
    return tree


# ---------------------------------------------------------------------------
# 移动 / 复制
# ---------------------------------------------------------------------------

def ensure_unique(path: Path) -> Path:
    """目标已存在时追加序号，绝不覆盖。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def copy_with_progress(
    source: Path,
    target: Path,
    *,
    cancel: CancelFn | None = None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target = ensure_unique(target)
    try:
        with source.open("rb") as src, target.open("wb") as dst:
            while True:
                # 每写一块就查一次取消：大文件复制中途可中断，
                # 否则几 GB 的文件要等全部写完才响应。
                if cancel and cancel():
                    raise InterruptedError("已取消")
                block = src.read(CHUNK)
                if not block:
                    break
                dst.write(block)
    except InterruptedError:
        # 半成品不留垃圾：取消时删掉没写完的目标文件
        try:
            target.unlink()
        except OSError:
            pass
        raise
    shutil.copystat(source, target, follow_symlinks=False)


def remove_empty_dirs(root: Path, *, cancelled: CancelFn | None = None) -> list[str]:
    """只删空文件夹，绝不碰任何文件。返回被删掉的路径。"""
    removed: list[str] = []
    for path in sorted((p for p in root.rglob("*")), key=lambda item: len(item.parts), reverse=True):
        if cancelled and cancelled():
            break
        try:
            if path.is_dir():
                path.rmdir()
                removed.append(str(path))
        except OSError:
            continue
    return removed


def create_junction(link_path: Path, target: Path) -> bool:
    """在 Windows 上创建目录联接，让老路径仍能指向新位置。"""
    if os.name != "nt":
        return False
    if link_path.exists():
        return False
    try:
        import _winapi  # type: ignore

        _winapi.CreateJunction(str(target), str(link_path))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 系统回收站（ctypes 调 Shell API，零依赖）
# ---------------------------------------------------------------------------

def send_to_recycle_bin(path: Path) -> bool:
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    FOF_NOERRORUI = 0x0400

    operation = SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    # 双 \0 结尾是 Shell API 的要求
    operation.pFrom = str(path) + "\0\0"
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        return result == 0 and not operation.fAnyOperationsAborted
    except Exception:
        return False


def human_error(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "没有权限访问（可能被占用或需要管理员）"
    if isinstance(exc, FileNotFoundError):
        return "文件或目录不存在"
    if isinstance(exc, InterruptedError):
        return "操作已取消"
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 112:
        return "目标磁盘空间不足"
    return str(exc)


def sleep_short(seconds: float = 0.02) -> None:
    """限速用：给磁盘留出 IO 空隙，避免打满。"""
    time.sleep(seconds)

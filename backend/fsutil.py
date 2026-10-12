"""文件系统底层工具。

只做"怎么安全地碰文件系统"这一件事：路径校验、安全拦截、遍历、哈希、
容量查询、目录树、原子移动。业务语义（扫描/查重/垃圾/迁移）放在 services 里。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from contextlib import contextmanager
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


def format_mtime(ts: float) -> str:
    """时间戳 → "YYYY-MM-DD HH:MM"。

    刻意用 f-string 拼，不用 `strftime` —— 实测 3000 个时间戳 9.2ms → 3.7ms（**2.5×**），
    输出逐字节一致。逐文件调用时这是实打实的钱（扫描分析里 strftime 曾占 16%）。
    """
    try:
        d = datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return "—"
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {d.hour:02d}:{d.minute:02d}"


def format_date(ts: float) -> str:
    """时间戳 → "YYYY-MM-DD"（项目雷达用）。同 format_mtime，理由一样。"""
    try:
        d = datetime.fromtimestamp(ts)
    except (OSError, OverflowError, ValueError):
        return "—"
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


# ---------------------------------------------------------------------------
# 路径解析与安全拦截
# ---------------------------------------------------------------------------

class BlockedPathError(ValueError):
    """命中了不可碰的安全黑名单。

    继承 ValueError 而不是 Exception：路由层的统一兜底是 `except (OSError, ValueError)`，
    继承 Exception 的话它会直接逃出去变成 500 —— 明明是一次「友好拒绝」，却报成服务端崩溃。
    """


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


_SELF_PROTECTED_NORMS: list[tuple[str, str]] | None = None


def _self_protected_norms() -> list[tuple[str, str]]:
    """把自保护目录预解析成「小写绝对前缀」，只算一次。

    SELF_PROTECTED_DIRS 在进程启动时就已经由 config 定死（STATE_DIR 跟着环境变量走），
    运行期不会变，所以缓存是安全的；长度对不上时重建，避免测试里改了配置却读到旧值。
    """
    global _SELF_PROTECTED_NORMS
    if _SELF_PROTECTED_NORMS is None or len(_SELF_PROTECTED_NORMS) != len(config.SELF_PROTECTED_DIRS):
        out: list[tuple[str, str]] = []
        for blocked in config.SELF_PROTECTED_DIRS:
            try:
                text = str(blocked.resolve())
            except (OSError, RuntimeError):
                text = str(blocked)
            out.append((blocked.name, os.path.normcase(os.path.abspath(text)).rstrip("\\/") + os.sep))
        _SELF_PROTECTED_NORMS = out
    return _SELF_PROTECTED_NORMS


def is_self_protected(path: Path, *, assume_real: bool = False) -> str | None:
    """**读取**遍历的黑名单：只含工具自身的目录，命中时返回原因。

    以前这里查的是 `SAFETY_BLOCKLIST`（软保护 + 自保护），于是「扫描 C 盘」会**静默跳过桌面** ——
    而桌面只是**写**保护（`full` 档本来就允许写），读却读不到，档位说一套做一套。
    现在读剪枝只看自保护（运行数据 / 项目自身 / 隔离区账本），
    软保护与系统只读区交给 `write_block_reason` 按档位处理。

    `assume_real=True`：调用方保证这个路径是**真实目录**（不是符号链接/联接点），
    于是走纯字符串比较、不做 `resolve()`。遍历时必须传它 —— `os.walk(followlinks=False)`
    和 `entry.is_dir(follow_symlinks=False)` 都不会进链接，路径就是真身，
    而 `resolve()` 是逐目录一次的系统调用，在大树上很贵。
    """
    if assume_real:
        try:
            norm = os.path.normcase(os.path.abspath(str(path))).rstrip("\\/") + os.sep
        except (OSError, ValueError):
            return None
        for name, prefix in _self_protected_norms():
            if norm.startswith(prefix):
                return f"「{name}」属于保护目录，禁止改动"
        return None

    try:
        target = path.resolve()
    except (OSError, RuntimeError):
        target = path

    for blocked in config.SELF_PROTECTED_DIRS:
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

DirHook = Callable[[str, int, list[str], list[str]], None]


def iter_entries(
    root: Path,
    *,
    excludes: Iterable[str] | None = None,
    max_depth: int | None = None,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    total: int | None = None,
    count_only: bool = False,
    on_dir: DirHook | None = None,
    skip_self_protected: bool = True,
) -> Iterator[os.DirEntry]:
    """遍历文件，产出 `os.DirEntry`。

    **为什么要给 DirEntry 而不是 Path**：Windows 上目录列表本身就把 size/mtime 带回来了，
    `entry.stat()` 直接复用、**零系统调用**；而只给路径的话，调用方拿大小必须再
    `path.stat()` 一次 —— 每个文件一次系统调用，在大树上就是几倍的差距。

    max_depth=0 表示不限深度；1 表示只看 root 下一层。

    `total`：调用方已知总条目数时可传进来，进度就能算真实百分比。
    不传时，只要带了 `progress` 就**先自己数一遍**再开始。

    `count_only`：内部专用，给 `count_entries` 标记"我就是在数数"，免得递归。

    `on_dir(path, depth, raw_dirs, raw_files)`：每进一个目录回调一次，
    给的是**裁剪前**的原始子目录名与文件名 —— 有些服务（如虚拟整理）要靠
    `.git` 这类"不会被遍历但存在即证据"的目录名判断项目归属，
    光看 yield 出来的文件是看不到的。传了才会有额外开销（多筛一遍名字）。

    `skip_self_protected=False`：只剪系统只读区，不剪自保护目录。
    只读预览类服务用得上 —— 否则预览自己的项目目录会是一片空白。
    """
    exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
    if excludes is not None:
        exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

    if not count_only and total is None and progress is not None:
        # 注意签名：ProgressFn 是 (message, current, total, path)，第一个是消息
        def _counting(_message: str, done: int, _total: int | None, where: str) -> None:
            # **数字写进消息里**：这个阶段还没有总数（正因为不知道才要数），
            # 百分比无从算起，唯一真实且在动的信号就是"已经数了多少"。
            # 不带它的话界面只剩一句静止的提示，看着像卡死。
            progress(f"正在统计条目数 —— 已数 {done:,} 项", done, None, where)

        total = count_entries(
            root, excludes=excludes, max_depth=max_depth, cancelled=cancelled, progress=_counting
        ) or None

    scanned = 0
    stack: list[tuple[str, int]] = [(str(root), 0)]
    want_raw = on_dir is not None

    while stack:
        if cancelled and cancelled():
            return
        current, depth = stack.pop()

        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError:
            continue

        raw_dirs: list[str] = []
        raw_files: list[str] = []

        if want_raw:
            # 先分拣一遍（is_dir 在 Windows 上命中 DirEntry 自己的缓存，不额外打系统调用），
            # 好让 on_dir **在这个目录的文件 yield 之前**就拿到全部原始条目名 ——
            # 要靠目录名提前建立上下文的调用方（虚拟整理的项目归属）依赖这个顺序。
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                (raw_dirs if is_dir else raw_files).append(entry.name)
            on_dir(current, depth, raw_dirs, raw_files)

        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name in exclude_set:
                    continue
                if max_depth and depth + 1 > max_depth:
                    continue
                child = Path(entry.path)
                # assume_real=True：既不跟随链接，拿到的是真身路径，不必 resolve()
                if (skip_self_protected and is_self_protected(child, assume_real=True)) or is_readonly_zone(child):
                    continue
                stack.append((entry.path, depth + 1))
                continue

            scanned += 1
            if scanned > config.SCAN_ENTRY_LIMIT:
                return
            if progress and scanned % 200 == 0:
                progress("正在遍历目录", scanned, total, current)
            yield entry


def resolve_sources(params: dict) -> tuple[list[Path], list[str]]:
    """从任务参数里取出**多个来源目录**。返回 `(roots, notes)`。

    优先 `paths`（列表），回退到 `path` / `source_dir`（单个）—— 保留单参数形式是为了
    **不打破既有调用方**：Agent 意图解析 / CLI / MCP 三处现在都只发单个 `path`。

    两处刻意的严格/宽松：

    - **任何一个来源不存在就整体失败**（抛异常，不跑一半）。
      半份结果比报错更危险 —— 用户会以为另外那几个目录也"没东西"。
    - **嵌套的根自动跳过**（写进 notes，不报错）。同时给了 `D:\\A` 与 `D:\\A\\sub` 时，
      sub 下的文件会被遍历**两次**：在查重里表现为"每个文件都是自己的重复"，凭空造出一批假重复。
      这只是用户多选了一下，不该因此跑不起来。
    """
    raw = [str(item).strip() for item in (params.get("paths") or []) if str(item).strip()]
    if not raw:
        single = str(params.get("path") or params.get("source_dir") or "").strip()
        raw = [single] if single else []
    if not raw:
        raise ValueError("没有指定任何来源目录")

    candidates = [(value, normalize(value)) for value in raw]
    # 短的排前面：父目录先被留下，子目录才能被判成"已被包含"
    candidates.sort(key=lambda item: len(str(item[1])))

    kept: list[Path] = []
    notes: list[str] = []
    for _value, path in candidates:
        if not path.exists():
            raise FileNotFoundError(f"路径不存在：{path}")
        if not path.is_dir():
            raise NotADirectoryError(f"不是文件夹：{path}")
        parent = next((item for item in kept if _path_contains(item, path)), None)
        if parent is not None:
            if _path_contains(parent, path) and str(parent).rstrip("\\/").lower() == str(path).rstrip("\\/").lower():
                notes.append(f"{path} 重复选择了，已自动合并")
            else:
                notes.append(f"{path} 已包含在 {parent} 内，已自动跳过")
            continue
        kept.append(path)
    return kept, notes


def sources_phrase(prefix: str, roots: list[Path]) -> str:
    """任务阶段名：「正在列举 D:\\x」/「正在列举 3 个来源（D:\\x 等）」"""
    if len(roots) <= 1:
        return f"{prefix} {roots[0]}" if roots else prefix
    return f"{prefix} {len(roots)} 个来源（{roots[0]} 等）"


def source_of(path: Path | str, roots: list[Path]) -> str:
    """这条结果来自哪个来源目录。

    用前缀匹配而不是在遍历时另存一份映射：多源时每个文件都记一份来源会白白多占一份内存
    （几十万文件时是几十 MB），而结果条目通常只有几百条，按前缀回查几乎不要钱。
    """
    text = str(path)
    for root in roots:
        head = str(root).rstrip("\\/")
        if text == head or text.startswith(head + "\\") or text.startswith(head + "/"):
            return str(root)
    return str(roots[0]) if roots else ""


def _path_contains(parent: Path, child: Path) -> bool:
    """parent 是不是 child 本身或它的祖先（按路径字符串比，不碰磁盘）。"""
    head = str(parent).rstrip("\\/").lower()
    tail = str(child).rstrip("\\/").lower()
    return tail == head or tail.startswith(head + "\\") or tail.startswith(head + "/")


def iter_entries_multi(
    roots: list[Path],
    *,
    excludes: Iterable[str] | None = None,
    max_depth: int | None = None,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    **kwargs,
) -> Iterator[tuple[Path, os.DirEntry]]:
    """按顺序遍历多个根目录，产出 `(root, entry)`。

    与"外面自己 for 一遍 iter_entries"的区别只有两点，但**两点都是必须的**：

    1. **进度全局单调** —— 先数完所有根，再用 offset 把各根的进度接起来。
       逐个调的话，每换一个根百分比就归零重来，界面看起来像来回跳。
    2. **条目预算全局共享** —— `SCAN_ENTRY_LIMIT` 在 `iter_entries` 里是**每个根**一份，
       多源会变成 N× 预算。这里自己按总量卡一道。
    """
    roots = [Path(root) for root in roots]
    if not roots:
        return

    if len(roots) == 1:
        for entry in iter_entries(roots[0], excludes=excludes, max_depth=max_depth,
                                  progress=progress, cancelled=cancelled, **kwargs):
            yield roots[0], entry
        return

    totals: list[int] = []
    for index, root in enumerate(roots):
        if cancelled and cancelled():
            return
        if progress:
            def _counting(_message: str, done: int, _total: int | None, where: str,
                          _index: int = index, _n: int = len(roots)) -> None:
                progress(f"正在统计条目数（第 {_index + 1}/{_n} 个来源）—— 已数 {done:,} 项",
                         done, None, where)
        else:
            _counting = None
        totals.append(count_entries(
            root, excludes=excludes, max_depth=max_depth, cancelled=cancelled, progress=_counting,
        ) or 0)

    grand = sum(totals) or None
    budget = config.SCAN_ENTRY_LIMIT
    scanned = 0
    for index, (root, total) in enumerate(zip(roots, totals)):
        offset = scanned

        def _relay(message: str, current: int, _total: int | None, where: str,
                   _index: int = index, _n: int = len(roots), _offset: int = offset) -> None:
            progress(f"{message}（第 {_index + 1}/{_n} 个来源）", _offset + current, grand, where)

        for entry in iter_entries(
            root, excludes=excludes, max_depth=max_depth,
            # total 传进来是为了**阻止它自己再数一遍**（它见 progress 且 total=None 会先预统计）
            total=total, progress=_relay if progress else None, cancelled=cancelled, **kwargs,
        ):
            yield root, entry
            scanned += 1
            if scanned >= budget:
                return
        # 这个根实际产出的条目数可能与预统计不同（期间文件有增减）—— 用预估值推进 offset 即可，
        # 进度条不必逐字节精确，但不能倒退。
        scanned = offset + total
        if scanned < 0:
            scanned = offset


def iter_files(root: Path, **kwargs) -> Iterator[Path]:
    """遍历文件，产出 `Path`（既有契约，5 个服务在用）。

    内部走 `iter_entries`，遍历逻辑只写一遍；这里只是把入口路径包成 Path。
    新代码如果需要文件大小，**优先用 `iter_entries`** —— 能省掉每文件一次系统调用。
    """
    for entry in iter_entries(root, **kwargs):
        yield Path(entry.path)


def count_entries(
    root: Path,
    *,
    excludes: Iterable[str] | None = None,
    max_depth: int | None = None,
    cancelled: CancelFn | None = None,
    progress: ProgressFn | None = None,
) -> int:
    """数一遍会产出多少条目，用于给进度算百分比。

    内部走 `count_only=True`：只负责数，不再触发 iter_entries 的自动计数（会递归）。
    带了 `progress` 就边数边报 —— 大目录上计数本身也要跑一会儿，不能静默。
    """
    total = 0
    for _ in iter_entries(root, excludes=excludes, max_depth=max_depth,
                          cancelled=cancelled, count_only=True, progress=progress):
        total += 1
    return total


# ---------------------------------------------------------------------------
# 哈希
# ---------------------------------------------------------------------------

CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# 进程级「同时读盘」闸门
#
# 为什么需要它：每个查重任务会自己开 config.HASH_WORKERS 个线程，而任务队列同时能跑 2 个
# （JobManager 的 ThreadPoolExecutor(max_workers=2)）—— 两个查重并发就是 8×2=16 个并发读盘，
# **过订阅**。实测每个任务反而慢 2.3×、总吞吐低 20%（见 docs/GOTCHAS.md 第八节）。
#
# 为什么放在这一层：这是「同一时刻最多有几个读盘」的策略，所有从这里读文件的地方
# （整文件哈希、文件头哈希、复制校验）都该受它约束。单线程调用方拿一次信号量几乎不花钱
# （无竞争时几十纳秒）。
#
# 为什么用信号量、而不是直接把 HASH_WORKERS 调小：任务少时照样能跑满 8 路，
# 任务多了自动互相让路；**被挡住的线程阻塞在信号量上、不烧 CPU**。
# ---------------------------------------------------------------------------

_IO_GATE: threading.BoundedSemaphore | None = None
_IO_GATE_LOCK = threading.Lock()


def _io_gate() -> threading.BoundedSemaphore:
    """懒建 —— 这样测试里改完 `config.HASH_WORKERS` 再建也生效。"""
    global _IO_GATE
    if _IO_GATE is None:
        with _IO_GATE_LOCK:
            if _IO_GATE is None:
                _IO_GATE = threading.BoundedSemaphore(max(1, config.HASH_WORKERS))
    return _IO_GATE


def reset_io_gate() -> None:
    """丢掉现有闸门，下次用时按当前 `config.HASH_WORKERS` 重建（测试用）。"""
    global _IO_GATE
    with _IO_GATE_LOCK:
        _IO_GATE = None


@contextmanager
def _io_slot() -> Iterator[None]:
    """占一个读盘名额。并发读文件都要从这里过，详见上面那段说明。"""
    gate = _io_gate()
    gate.acquire()
    try:
        yield
    finally:
        gate.release()


class _CancelAwareReader:
    """包一层文件对象，让 `hashlib.file_digest` 也能响应取消。

    `file_digest` 是 C 实现，比 Python 里 `while: read()` 循环快一截；
    但它一口气读到尾，中间没有插取消检查的机会 —— 大文件上"点了取消没反应"。
    包一层 `readinto` / `read`，每块检查一次，两全。
    """

    __slots__ = ("_stream", "_cancelled")

    def __init__(self, stream, cancelled: CancelFn | None) -> None:
        self._stream = stream
        self._cancelled = cancelled

    def _check(self) -> None:
        if self._cancelled is not None and self._cancelled():
            raise InterruptedError("已取消")

    def readable(self) -> bool:
        # file_digest 会先问这一句，没有就直接拒收
        return True

    def readinto(self, buffer):  # noqa: ANN001
        self._check()
        return self._stream.readinto(buffer)

    def read(self, size: int = -1):  # noqa: ANN201
        self._check()
        return self._stream.read(size)


_FILE_DIGEST = getattr(hashlib, "file_digest", None)


def _hash_file_loop(stream, cancelled: CancelFn | None) -> str:
    hasher = hashlib.sha256()
    while True:
        if cancelled and cancelled():
            raise InterruptedError("已取消")
        block = stream.read(CHUNK)
        if not block:
            break
        hasher.update(block)
    return hasher.hexdigest()


def hash_file(path: Path, *, cancelled: CancelFn | None = None) -> str:
    # 读盘走进程级闸门：串行调用方几乎不花钱，并发时自动限流（见 _io_slot）
    with _io_slot(), path.open("rb") as stream:
        if _FILE_DIGEST is not None:
            try:
                return _FILE_DIGEST(_CancelAwareReader(stream, cancelled), "sha256").hexdigest()
            except ValueError:
                # 某些版本（如 3.13 的 C 实现）对 fileobj 更挑剔，退回循环读块
                stream.seek(0)
        return _hash_file_loop(stream, cancelled)


def quick_hash(path: Path, size: int = 4096) -> str:
    """只读文件头，用于查重前两级快速过滤。"""
    hasher = hashlib.sha256()
    with _io_slot(), path.open("rb") as stream:
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
    """构建带大小的目录树，只展开 depth 层，深层折叠。

    顺带回收两样**界面必须知道、但以前拿不到**的事实（少了它们这张图就读不懂）：

    - 每个文件节点的 `category`：界面按类型上色靠它。以前不返回，
      所有文件都落到「其他」那一档灰，整张图变成一个颜色。
    - `skipped`：被剪掉的目录名 + 次数。系统只读区 / 用户排除项 / 自保护目录
      **永远不会进这棵树**，于是「C 盘只用了 3.4 GB」这种数字会误导人 ——
      界面要靠它如实写出「跳过了什么、跳过了几个」。
    """

    # 排除表只算一次。原来在每个目录里都重建两个 set，纯浪费。
    exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
    if excludes is not None:
        exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

    skipped: dict[str, int] = {}

    def walk(path: Path, level: int) -> dict:
        node = {"name": path.name or str(path), "path": str(path), "size": 0, "is_dir": True, "children": []}
        try:
            entries = list(os.scandir(path))
        except OSError:
            return node

        for entry in entries:
            if cancelled and cancelled():
                return node
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in exclude_set or is_self_protected(Path(entry.path)):
                        skipped[entry.name] = skipped.get(entry.name, 0) + 1
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
                        "category": config.category_for(entry.path),
                    })
            except OSError:
                continue

        node["children"].sort(key=lambda item: item["size"], reverse=True)
        if progress:
            progress("正在统计占用", node["size"], None, str(path))
        return node

    tree = walk(root, 1)
    tree["size_text"] = format_size(tree["size"])
    tree["depth"] = depth
    # 按出现次数排；只留前 12 个名字，够界面说清楚「跳过了什么」
    tree["skipped"] = [
        {"name": name, "count": count}
        for name, count in sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    ]
    tree["skipped_total"] = sum(skipped.values())
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

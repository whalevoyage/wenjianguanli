"""重复文件识别。

三层过滤（相比旧版"每个文件都算 SHA256"快几十倍）：
  1. 按文件大小分组，大小唯一的直接淘汰
  2. 只有同大小的文件才读前 4KB 算快速哈希
  3. 前 4KB 相同的才算完整 SHA256
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import threading

from .. import config, fsutil
from ..jobs import JobContext

_CACHE_PATH = config.STATE_DIR / "hash_cache.json"
_CACHE_LOCK = threading.RLock()
_CACHE_KEEP = 50000

# 缓存**驻留内存**，整轮扫描只在结束时落盘一次。
#
# 原来是「每算一个哈希 → 读一遍整份 JSON → 改一条 → 写一遍整份 JSON」。
# 缓存在用户机器上已经 4MB 时，100 个候选文件就是 ~800MB 的 JSON I/O，
# 而且是 O(n²)：文件越多，每个文件越慢。哈希本身反而成了小头。
_HASH_CACHE: dict | None = None


def _hash_cache() -> dict:
    global _HASH_CACHE
    if _HASH_CACHE is None:
        # 双重检查 + 锁：并行哈希时多个线程会同时进来，
        # 不加锁的话后进来的那个会把先建立的那份替换掉，先前写进去的条目就丢了。
        with _CACHE_LOCK:
            if _HASH_CACHE is None:
                try:
                    data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                    _HASH_CACHE = data if isinstance(data, dict) else {}
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    _HASH_CACHE = {}
    return _HASH_CACHE


def _flush_hash_cache() -> None:
    """把内存里的缓存落盘。整轮扫描只调一次（收尾时）。"""
    if _HASH_CACHE is None:
        return
    with _CACHE_LOCK:
        try:
            trimmed = dict(list(_HASH_CACHE.items())[-_CACHE_KEEP:])
            _CACHE_PATH.write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _cached_hash(path: Path, cancelled=None, *, size: int | None = None, mtime_ns: int | None = None) -> str:
    """带缓存的整文件哈希。

    两个刻意的设计：

    1. **缓存键用绝对路径字符串，不再 `path.resolve()`。**
       在 Windows 上 resolve 是**每文件一次真实系统调用**，实测 0.42ms/文件 ——
       比哈希本身还贵，而且是纯粹的浪费：遍历出来的路径本来就是绝对的
       （root 已 resolve，且 scandir 不跟随链接）。
    2. **size / mtime_ns 由调用方从 `DirEntry.stat()` 顺手带来**，
       这样这里连 stat 都不用再打一次。传不进来才自己 stat（保证独立可用）。
    """
    try:
        if size is None or mtime_ns is None:
            stat = path.stat()
            size, mtime_ns = stat.st_size, stat.st_mtime_ns
        key = str(path)
        cache = _hash_cache()
        cached = cache.get(key)
        if cached and cached[0] == size and cached[1] == mtime_ns:
            return cached[2]
        digest = fsutil.hash_file(path, cancelled=cancelled)
        with _CACHE_LOCK:
            cache[key] = [size, mtime_ns, digest]
        return digest
    except InterruptedError:
        raise
    except OSError:
        return fsutil.hash_file(path, cancelled=cancelled)


def _run_pool(items, worker, cancelled):
    """把 worker 并行作用到 items 上，**结果按输入顺序**逐个吐出来。

    为什么能快：这两层剩下的唯一大头是打开文件，而 Windows 的 CreateFile、
    `readinto`、`hashlib.update(>2KB)` 都会释放 GIL —— 实测 8 线程下
    整文件哈希 2.2×、读文件头 1.76×（NVMe）。

    **真正的并发上限由 `fsutil._io_slot` 那个进程级闸门管**，不是这里的线程数：
    本方法只负责「把活摊开」，同一个进程里跑几个查询任务，读盘总数始终 ≤ `HASH_WORKERS`。
    线程多一点没关系 —— 抢不到名额的线程阻塞在信号量上，不烧 CPU。

    为什么用生成器而不是 `list(...)`：调用方（主线程）要边收边报进度。
    进度、错误收集、取消检查全都留在主线程上，工作线程只做纯计算。
    """
    workers = config.HASH_WORKERS
    if workers <= 1 or len(items) <= 1:
        for item in items:
            yield worker(item)
        return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(worker, items)


def _quick_one(item):
    """第二层：读文件头；**小文件顺带把完整哈希也算出来**。

    为什么合并：一次 open 在 Windows 上约 0.22ms，多读 16KB 只约 0.016ms。
    对 ≤ `config.DUP_SMALL_FILE_BYTES` 的文件，与其「开一次读 4KB、需要时再开一次读全量」，
    不如一次读完、两个哈希一起算 —— 第三层就**不用再打开**了。
    实测候选里 80% 是这类小文件，整轮 **1.44×**；最坏情况（全被头哈希剪掉）
    每个文件也只多读 16KB，损失有界（见 config 里那段盈亏平衡的推导）。

    返回 `(size, path, mtime_ns, 头哈希, 完整哈希或None, 错误或None)`。
    异常变成返回值，好在主线程按原顺序记错误日志。
    """
    size, path, mtime_ns = item
    try:
        if size <= config.DUP_SMALL_FILE_BYTES:
            with fsutil._io_slot():
                data = path.read_bytes()
            # `quick_hash` 的定义就是 sha256(前 4096 字节) —— 从同一份内容里取，结果逐字节一致
            head = hashlib.sha256(data[:4096]).hexdigest()
            return size, path, mtime_ns, head, hashlib.sha256(data).hexdigest(), None
        return size, path, mtime_ns, fsutil.quick_hash(path), None, None
    except InterruptedError:
        raise
    except OSError as exc:
        return size, path, mtime_ns, None, None, f"{path}：{fsutil.human_error(exc)}"


def _remember(path: Path, size: int, mtime_ns: int, digest: str) -> None:
    """把第二层顺带算出的完整哈希写进缓存 —— 下次扫描第三层直接命中，连打开都省。"""
    with _CACHE_LOCK:
        _hash_cache()[str(path)] = [size, mtime_ns, digest]


def _full_one(item, prefetched, cancelled):
    """第三层：完整 SHA256（走哈希缓存）。异常处理同上。"""
    size, path, mtime_ns = item
    try:
        pre = prefetched.get(path)
        if pre is not None:
            p_size, p_mtime, digest = pre
            _remember(path, p_size, p_mtime, digest)
            return size, path, mtime_ns, digest, None
        digest = _cached_hash(path, cancelled, size=size, mtime_ns=mtime_ns)
    except InterruptedError:
        raise
    except OSError as exc:
        return size, path, mtime_ns, None, f"{path}：{fsutil.human_error(exc)}"
    return size, path, mtime_ns, digest, None


def run_duplicate_scan(ctx: JobContext) -> dict:
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    excludes = params.get("excludes")
    min_size = int(params.get("min_size") or 1)

    ctx.phase(0, f"正在列举 {root} 下的文件")
    # 存 (Path, mtime_ns)：mtime 从 DirEntry.stat() 免费拿到，一路带到第三层算哈希时
    # 校验缓存是否过期 —— 这样整条链路每个候选文件只 stat 一次
    size_map: dict[int, list[tuple[Path, int]]] = defaultdict(list)
    scanned = 0
    skipped = 0
    errors: list[str] = []

    for entry in fsutil.iter_entries(root, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            stat = entry.stat()          # Windows 上目录列表已带回属性，零系统调用
        except OSError as exc:
            errors.append(f"{entry.path}：{fsutil.human_error(exc)}")
            continue
        scanned += 1
        if stat.st_size < min_size:
            skipped += 1
            continue
        size_map[stat.st_size].append((Path(entry.path), stat.st_mtime_ns))

    # 第一层：只保留"同大小"的候选
    candidates = {size: items for size, items in size_map.items() if len(items) >= 2}
    candidate_files = sum(len(items) for items in candidates.values())
    eliminated = scanned - candidate_files
    ctx.phase(1, f"按大小淘汰 {eliminated} 个文件，{candidate_files} 个进入哈希比对")

    # 第二层：前 4KB 快速哈希（**小文件顺带把完整哈希也算出来**，见 _quick_one）
    #
    # 两层哈希都走线程池。理由是实测出来的：这两层剩下的唯一大头就是**打开文件**
    # （`_io.open` 占整轮一半），而 Windows 的 CreateFile / readinto / hashlib.update(>2KB)
    # 都会释放 GIL —— 8 线程下整文件哈希 2.2×、读文件头 1.76×（本机 NVMe）。
    #
    # 只并行**计算**：进度上报与错误收集仍在主线程按输入顺序做，
    # ctx 的调用一个都不会落到工作线程上。
    quick_items = [
        (size, path, mtime_ns)
        for size, items in candidates.items()
        for path, mtime_ns in items
    ]
    quick_map: dict[tuple[int, str], list[tuple[Path, int]]] = defaultdict(list)
    prefetched: dict[Path, tuple[int, int, str]] = {}
    processed = 0
    for size, path, mtime_ns, digest, full_digest, error in _run_pool(
        quick_items, _quick_one, ctx.cancelled
    ):
        ctx.check_cancel()
        processed += 1
        ctx.progress("正在比对文件头", processed, candidate_files, str(path))
        if error:
            errors.append(error)
            continue
        quick_map[(size, digest)].append((path, mtime_ns))
        if full_digest is not None:
            prefetched[path] = (size, mtime_ns, full_digest)

    second_pass = {key: items for key, items in quick_map.items() if len(items) >= 2}
    second_files = sum(len(items) for items in second_pass.values())

    # 第三层：完整 SHA256（小文件在第二层已经算好，这里不再打开）
    full_items = [
        (size, path, mtime_ns)
        for (size, _), items in second_pass.items()
        for path, mtime_ns in items
    ]
    full_map: dict[tuple[int, str], list[tuple[Path, int]]] = defaultdict(list)
    processed = 0
    for size, path, mtime_ns, digest, error in _run_pool(
        full_items, lambda item: _full_one(item, prefetched, ctx.cancelled), ctx.cancelled
    ):
        ctx.check_cancel()
        processed += 1
        ctx.progress("正在计算完整哈希", processed, second_files, str(path))
        if error:
            errors.append(error)
            continue
        full_map[(size, digest)].append((path, mtime_ns))

    ctx.phase(2, "正在生成重复组")
    groups = []
    for (size, checksum), items in full_map.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda item: str(item[0]))
        records = [{
            "path": str(path),
            "name": path.name,
            "size": size,
            "size_text": fsutil.format_size(size),
            "modified_time": _fmt_mtime(mtime_ns),
        } for path, mtime_ns in items]
        groups.append({
            "sha256": checksum,
            "sha256_short": checksum[:12],
            "size": size,
            "size_text": fsutil.format_size(size),
            "count": len(records),
            "file_count": len(records),
            "original": records[0],
            "redundant_files": records[1:],
            "files": records,
        })

    groups.sort(key=lambda item: item["size"] * (item["count"] - 1), reverse=True)
    redundant = sum(len(item["redundant_files"]) for item in groups)
    reclaimable = sum(item["size"] * len(item["redundant_files"]) for item in groups)

    # 整轮只在这里落一次盘（取消/异常时缓存留在内存里，下次仍能用）
    _flush_hash_cache()

    return {
        "root": str(root),
        "scanned_files": scanned,
        "skipped_files": skipped,
        "candidate_files": candidate_files,
        "eliminated_by_size": eliminated,
        "group_count": len(groups),
        "redundant_file_count": redundant,
        "reclaimable_size": reclaimable,
        "reclaimable_size_text": fsutil.format_size(reclaimable),
        "groups": groups[:500],
        "errors": errors[:100],
    }


def _fmt_mtime(mtime_ns: int) -> str:
    """mtime_ns → "YYYY-MM-DD HH:MM"。

    直接吃第一层 `DirEntry.stat()` 拿到的纳秒值，**不再为每个重复文件补一次 stat**。
    """
    return fsutil.format_mtime(mtime_ns / 1e9)

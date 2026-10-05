"""重复文件识别。

三层过滤（相比旧版"每个文件都算 SHA256"快几十倍）：
  1. 按文件大小分组，大小唯一的直接淘汰
  2. 只有同大小的文件才读前 4KB 算快速哈希
  3. 前 4KB 相同的才算完整 SHA256
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import threading

from .. import config, fsutil
from ..jobs import JobContext

_CACHE_PATH = config.STATE_DIR / "hash_cache.json"
_CACHE_LOCK = threading.RLock()


def _cached_hash(path: Path, cancelled=None) -> str:
    try:
        stat = path.stat()
        key = str(path.resolve())
        with _CACHE_LOCK:
            try:
                cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                cache = {}
            cached = cache.get(key)
            signature = [stat.st_size, stat.st_mtime_ns]
            if cached and cached[:2] == signature:
                return cached[2]
        digest = fsutil.hash_file(path, cancelled=cancelled)
        with _CACHE_LOCK:
            cache[key] = [stat.st_size, stat.st_mtime_ns, digest]
            trimmed = dict(list(cache.items())[-50000:])
            _CACHE_PATH.write_text(json.dumps(trimmed, ensure_ascii=False), encoding="utf-8")
        return digest
    except OSError:
        return fsutil.hash_file(path, cancelled=cancelled)


def run_duplicate_scan(ctx: JobContext) -> dict:
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    excludes = params.get("excludes")
    min_size = int(params.get("min_size") or 1)

    ctx.phase(0, f"正在列举 {root} 下的文件")
    size_map: dict[int, list[Path]] = defaultdict(list)
    scanned = 0
    skipped = 0
    errors: list[str] = []

    for path in fsutil.iter_files(root, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            size = path.stat().st_size
        except OSError as exc:
            errors.append(f"{path}：{fsutil.human_error(exc)}")
            continue
        scanned += 1
        if size < min_size:
            skipped += 1
            continue
        size_map[size].append(path)

    # 第一层：只保留"同大小"的候选
    candidates = {size: paths for size, paths in size_map.items() if len(paths) >= 2}
    candidate_files = sum(len(paths) for paths in candidates.values())
    eliminated = scanned - candidate_files
    ctx.phase(1, f"按大小淘汰 {eliminated} 个文件，{candidate_files} 个进入哈希比对")

    # 第二层：前 4KB 快速哈希
    quick_map: dict[tuple[int, str], list[Path]] = defaultdict(list)
    processed = 0
    for size, paths in candidates.items():
        for path in paths:
            ctx.check_cancel()
            processed += 1
            ctx.progress("正在比对文件头", processed, candidate_files, str(path))
            try:
                quick_map[(size, fsutil.quick_hash(path))].append(path)
            except OSError as exc:
                errors.append(f"{path}：{fsutil.human_error(exc)}")

    second_pass = {key: paths for key, paths in quick_map.items() if len(paths) >= 2}
    second_files = sum(len(paths) for paths in second_pass.values())

    # 第三层：完整 SHA256
    full_map: dict[tuple[int, str], list[Path]] = defaultdict(list)
    processed = 0
    for (size, _), paths in second_pass.items():
        for path in paths:
            ctx.check_cancel()
            processed += 1
            ctx.progress("正在计算完整哈希", processed, second_files, str(path))
            try:
                full_map[(size, _cached_hash(path, ctx.cancelled))].append(path)
            except InterruptedError:
                raise
            except OSError as exc:
                errors.append(f"{path}：{fsutil.human_error(exc)}")

    ctx.phase(2, "正在生成重复组")
    groups = []
    for (size, checksum), paths in full_map.items():
        if len(paths) < 2:
            continue
        paths.sort(key=lambda item: str(item))
        items = [{
            "path": str(path),
            "name": path.name,
            "size": size,
            "size_text": fsutil.format_size(size),
            "modified_time": _mtime(path),
        } for path in paths]
        groups.append({
            "sha256": checksum,
            "sha256_short": checksum[:12],
            "size": size,
            "size_text": fsutil.format_size(size),
            "count": len(items),
            "file_count": len(items),
            "original": items[0],
            "redundant_files": items[1:],
            "files": items,
        })

    groups.sort(key=lambda item: item["size"] * (item["count"] - 1), reverse=True)
    redundant = sum(len(item["redundant_files"]) for item in groups)
    reclaimable = sum(item["size"] * len(item["redundant_files"]) for item in groups)

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


def _mtime(path: Path) -> str:
    try:
        from datetime import datetime

        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "—"

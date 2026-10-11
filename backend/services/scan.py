"""扫描分析 + 空间地图（目录树）。"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from .. import config, fsutil
from ..jobs import JobContext


def _fmt_time(ts: float) -> str:
    return fsutil.format_mtime(ts)


def run_scan(ctx: JobContext) -> dict:
    """扫描一个目录：数量、体积、类型分布、大文件、长期未动文件。"""
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    excludes = params.get("excludes")
    max_depth = params.get("max_depth") or 0
    stale_days = int(params.get("stale_days") or 30)
    stale_min_size = int(params.get("stale_min_size") or 0)

    started = time.perf_counter()
    ctx.phase(0, f"正在扫描 {root}")

    total_files = 0
    total_size = 0
    dirs: set[str] = set()
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "size": 0})
    large_files: list[dict] = []
    stale_files: list[dict] = []
    warnings: list[str] = []
    now = time.time()
    stale_cutoff = now - stale_days * 86400

    for entry in fsutil.iter_entries(root, excludes=excludes, max_depth=max_depth,
                                     progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            # DirEntry.stat()：Windows 上目录列表已经把属性带回来了，**零系统调用**。
            # 换成 iter_files 拿 Path 再 path.stat() 的话，这里是每文件一次系统调用。
            stat = entry.stat()
        except OSError as exc:
            warnings.append(f"无法读取 {entry.path}：{fsutil.human_error(exc)}")
            continue

        size = stat.st_size
        name = entry.name
        total_files += 1
        total_size += size
        dirs.add(os.path.dirname(entry.path))

        category = config.category_for(name)
        bucket = categories[category]
        bucket["count"] += 1
        bucket["size"] += size

        # 只有真的入选（大文件 / 长期未动）才建记录。
        # 原来每个文件都建一个 8 键 dict，绝大多数根本用不上 —— 几十万文件时这是大头开销。
        is_large = size >= config.LARGE_FILE_THRESHOLD
        is_stale = size >= stale_min_size and stat.st_mtime < stale_cutoff
        if not (is_large or is_stale):
            continue

        record = {
            "name": name,
            "path": entry.path,
            "size": size,
            "size_text": fsutil.format_size(size),
            "category": category,
            "type": os.path.splitext(name)[1].lower() or "无扩展名",
            "modified_time": _fmt_time(stat.st_mtime),
            "modified_ts": stat.st_mtime,
        }
        if is_large:
            large_files.append(record)
        if is_stale:
            stale_files.append(record)

    ctx.phase(1, "正在汇总统计")
    large_files.sort(key=lambda item: item["size"], reverse=True)
    stale_files.sort(key=lambda item: item["size"], reverse=True)

    category_stats = []
    for name, value in sorted(categories.items(), key=lambda item: item[1]["size"], reverse=True):
        percent = round(value["size"] / total_size * 100, 1) if total_size else 0.0
        category_stats.append({
            "key": name,
            "name": name,
            "label": name,
            "count": value["count"],
            "size": value["size"],
            "size_text": fsutil.format_size(value["size"]),
            "percentage": percent,
        })

    duration = round(time.perf_counter() - started, 2)
    reclaimable = sum(item["size"] for item in stale_files)

    return {
        "root": str(root),
        "scan_time": fsutil.now_iso(),
        "duration_seconds": duration,
        "total_files": total_files,
        "total_dirs": len(dirs),
        "total_size": total_size,
        "total_size_text": fsutil.format_size(total_size),
        "category_stats": category_stats,
        "large_files": large_files[:200],
        "large_file_count": len(large_files),
        "largest_file": large_files[0] if large_files else None,
        "stale_files": stale_files[:200],
        "stale_file_count": len(stale_files),
        "stale_days": stale_days,
        "reclaimable_size": reclaimable,
        "reclaimable_size_text": fsutil.format_size(reclaimable),
        "warnings": warnings[:100],
        "excludes": list(excludes or config.DEFAULT_EXCLUDES),
    }


def run_tree(ctx: JobContext) -> dict:
    """构建目录树，供空间地图 treemap 钻取。"""
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    depth = int(params.get("depth") or config.TREE_DEFAULT_DEPTH)

    ctx.phase(0, f"正在统计 {root} 的占用")
    tree = fsutil.build_tree(
        root,
        depth=depth,
        excludes=params.get("excludes"),
        progress=ctx.tick,
        cancelled=ctx.cancelled,
    )
    ctx.phase(1, "统计完成")
    return {"root": str(root), "depth": depth, "tree": tree}

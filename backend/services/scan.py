"""扫描分析 + 空间地图（目录树）。"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .. import config, fsutil
from ..jobs import JobContext


def _fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


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

    for path in fsutil.iter_files(root, excludes=excludes, max_depth=max_depth, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            stat = path.stat()
        except OSError as exc:
            warnings.append(f"无法读取 {path}：{fsutil.human_error(exc)}")
            continue

        total_files += 1
        total_size += stat.st_size
        dirs.add(str(path.parent))

        category = config.category_for(path)
        categories[category]["count"] += 1
        categories[category]["size"] += stat.st_size

        name = path.name
        record = {
            "name": name,
            "path": str(path),
            "size": stat.st_size,
            "size_text": fsutil.format_size(stat.st_size),
            "category": category,
            "type": path.suffix.lower() or "无扩展名",
            "modified_time": _fmt_time(stat.st_mtime),
            "modified_ts": stat.st_mtime,
        }
        if stat.st_size >= config.LARGE_FILE_THRESHOLD:
            large_files.append(record)
        if stat.st_size >= stale_min_size and stat.st_mtime < stale_cutoff:
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

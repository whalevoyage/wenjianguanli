"""项目雷达：把全盘「这是个项目」的目录一个个拎出来，看体积、活跃度和僵尸项目。

项目判定复用 config.PROJECT_MARKERS（与整理预览的「按项目」同一套规则）。

硬约束：只读。只 stat + 聚合，不改动被扫描的任何文件。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from .. import config, fsutil
from ..jobs import JobContext


def _fmt_time(ts: float) -> str:
    if not ts:
        return "—"
    return fsutil.format_date(ts)


def run_project_scan(ctx: JobContext) -> dict:
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    excludes = params.get("excludes")
    max_depth = int(params.get("max_depth") or 0)
    zombie_days = int(params.get("zombie_days") or config.ZOMBIE_DAYS)
    limit = int(params.get("limit") or config.PROJECT_LIMIT)

    exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
    if excludes is not None:
        exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

    started = time.perf_counter()
    now = time.time()
    zombie_cutoff = now - zombie_days * 86400
    root_path = root.resolve()
    root_str = str(root_path)

    projects: dict[str, dict] = {}
    warnings: list[str] = []
    scanned_files = 0
    truncated = False

    ctx.phase(0, f"正在识别 {root} 下的项目")

    # 先数一遍：只枚举目录、不做逐文件 stat，用来给进度算百分比。
    # 不带这一步，进度就只能永远是 0%（前端看到的正是「17 分钟 0%」）。
    # 计数本身也报进度 —— 大目录上它也要跑一会儿，不能静默。
    total_files = fsutil.count_entries(
        root, excludes=excludes, max_depth=max_depth, cancelled=ctx.cancelled,
        progress=lambda _msg, done, _total, where: ctx.progress("正在统计条目数（用于进度）", done, None, where),
    )
    total_files = total_files or None

    # 主遍历：用 os.scandir 而不是 os.walk —— 关键在于 entry.stat()。
    # Windows 上目录列表本身就带回了 size/mtime，DirEntry.stat() 直接复用、**不需要系统调用**；
    # 而 os.walk 只给名字，拿大小必须再 path.stat() 一次，等于每个文件多打一次系统调用。
    # 在几十万文件的大树上，这一项就是成倍的差距。
    stack: list[tuple[str, int, str | None]] = [(root_str, 0, None)]

    while stack:
        ctx.check_cancel()
        current, depth, inherited_owner = stack.pop()

        try:
            with os.scandir(current) as iterator:
                entries = list(iterator)
        except OSError as exc:
            warnings.append(f"无法读取 {current}：{fsutil.human_error(exc)}")
            continue

        # 项目标记必须看「裁剪前的原始条目」：.git / node_modules 这类目录
        # 不会遍历进去，但它们的存在本身就是「这是一个项目」的证据。
        marker = config.project_marker_for({entry.name for entry in entries})

        key = current
        if marker:
            try:
                rel = os.path.relpath(current, root_str)
            except ValueError:
                rel = os.path.basename(current)
            projects.setdefault(key, {
                "path": key,
                "name": os.path.basename(current) or current,
                "shell": marker,
                "rel": rel,
                "depth": depth,
                "size": 0,
                "file_count": 0,
                "last_active": 0.0,
                "extensions": defaultdict(int),
            })

        # 归属最近的祖先项目（含自身）：父目录先于子目录处理，归属随栈往下传
        owner = key if key in projects else inherited_owner

        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue

            if is_dir:
                if max_depth and depth + 1 > max_depth:
                    continue
                if entry.name in exclude_set:
                    continue
                # 只读服务只统计不写入，所以保护目录（桌面/.workbuddy/项目自身）
                # 允许进入 —— 否则扫自己的项目目录会一个文件都看不到。
                # 真正要改文件时，migrate / clean 会各自把关。
                if fsutil.is_readonly_zone(Path(entry.path)):
                    continue
                stack.append((entry.path, depth + 1, owner))
                continue

            scanned_files += 1
            if scanned_files > config.SCAN_ENTRY_LIMIT:
                truncated = True
                break
            if scanned_files % 200 == 0:
                ctx.progress("正在统计项目占用", scanned_files, total_files, current)
            if not owner:
                continue

            try:
                stat = entry.stat()          # Windows 上零系统调用
            except OSError as exc:
                warnings.append(f"无法读取 {entry.path}：{fsutil.human_error(exc)}")
                continue

            record = projects[owner]
            record["size"] += stat.st_size
            record["file_count"] += 1
            if stat.st_mtime > record["last_active"]:
                record["last_active"] = stat.st_mtime
            if len(record["extensions"]) < 40:
                ext = os.path.splitext(entry.name)[1].lower() or "无扩展名"
                record["extensions"][ext] += 1

        if truncated:
            break

    ctx.phase(1, "正在汇总项目清单")

    total_size = 0
    zombie_count = 0
    zombie_size = 0
    shells: dict[str, dict] = {}
    rows: list[dict] = []

    for record in projects.values():
        size = record["size"]
        last_active = record["last_active"] or 0.0
        inactive_days = int((now - last_active) / 86400) if last_active else None
        is_zombie = bool(last_active) and last_active < zombie_cutoff
        extensions = sorted(record["extensions"].items(), key=lambda item: item[1], reverse=True)[:6]

        total_size += size
        if is_zombie:
            zombie_count += 1
            zombie_size += size

        bucket = shells.setdefault(record["shell"], {"label": record["shell"], "count": 0, "size": 0})
        bucket["count"] += 1
        bucket["size"] += size

        rows.append({
            "path": record["path"],
            "name": record["name"],
            "rel": record["rel"],
            "shell": record["shell"],
            "depth": record["depth"],
            "size": size,
            "size_text": fsutil.format_size(size),
            "file_count": record["file_count"],
            "last_active": last_active,
            "last_active_text": _fmt_time(last_active),
            "inactive_days": inactive_days,
            "is_zombie": is_zombie,
            "extensions": [{"key": key, "count": count} for key, count in extensions],
        })

    rows.sort(key=lambda item: item["size"], reverse=True)
    shell_rows = [
        {"label": value["label"], "count": value["count"],
         "size": value["size"], "size_text": fsutil.format_size(value["size"])}
        for value in sorted(shells.values(), key=lambda item: item["size"], reverse=True)
    ]

    duration = round(time.perf_counter() - started, 2)
    return {
        "root": str(root),
        "zombie_days": zombie_days,
        "project_count": len(rows),
        "projects": rows[:limit],
        "truncated": len(rows) > limit or truncated,
        "total_size": total_size,
        "total_size_text": fsutil.format_size(total_size),
        "zombie_count": zombie_count,
        "zombie_size": zombie_size,
        "zombie_size_text": fsutil.format_size(zombie_size),
        "shells": shell_rows,
        "scanned_files": scanned_files,
        "duration_seconds": duration,
        "warnings": warnings[:100],
        "generated_at": fsutil.now_iso(),
        "readonly": True,
        "note": "只统计项目占用，没有移动任何文件。",
    }

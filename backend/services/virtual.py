"""虚拟整理预览：把文件按类型 / 时间 / 体积 / 项目聚成「假想整理后」的结构。

硬约束：这个模块**只读**。
它只做 stat 与分组，不创建目录、不移动、不删除、不写任何文件。
界面上的「整理后的样子」全部活在内存里；真要动手必须由用户确认后交给 migrate
（复制 → 逐文件校验 → 校验通过才删源，可回滚）。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from .. import config, fsutil
from ..jobs import JobContext

MODE_BY_ID = {mode["id"]: mode for mode in config.VIRTUAL_GROUP_MODES}


# ---------------------------------------------------------------------------
# 分档
# ---------------------------------------------------------------------------

def _time_bucket(mtime: float, now: float) -> dict:
    age_days = (now - mtime) / 86400
    for bucket in config.TIME_BUCKETS:
        if bucket["days"] is None or age_days <= bucket["days"]:
            return bucket
    return config.TIME_BUCKETS[-1]


def _size_bucket(size: int) -> dict:
    for bucket in config.SIZE_BUCKETS:
        if bucket["max"] is None or size <= bucket["max"]:
            return bucket
    return config.SIZE_BUCKETS[-1]


def _project_marker(names: set[str]) -> str | None:
    """项目标记判断统一走 config，规则只写一处。"""
    return config.project_marker_for(names)


def _classify(
    group_by: str,
    path: Path,
    stat: os.stat_result,
    now: float,
    project: dict | None,
) -> tuple[str, str, str]:
    """返回 (分组 key, 分组标签, 相对分组路径)。"""
    if group_by == "type":
        name = config.category_for(path)
        return f"type:{name}", name, name

    if group_by == "time":
        bucket = _time_bucket(stat.st_mtime, now)
        return f"time:{bucket['id']}", bucket["label"], bucket["label"]

    if group_by == "size":
        bucket = _size_bucket(stat.st_size)
        return f"size:{bucket['id']}", bucket["label"], bucket["label"]

    # 按项目
    if project:
        label = Path(project["path"]).name
        return f"project:{project['path']}", label, project["rel"]
    return config.LOOSE_PROJECT_KEY, "散落文件", "散落文件"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_virtual(ctx: JobContext) -> dict:
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    group_by = params.get("group_by") or "type"
    if group_by not in MODE_BY_ID:
        raise ValueError(f"不支持的分组方式：{group_by}")
    mode = MODE_BY_ID[group_by]
    excludes = params.get("excludes")
    min_size = int(params.get("min_size") or 0)
    max_depth = int(params.get("max_depth") or 0)
    item_limit = int(params.get("limit") or config.VIRTUAL_ITEM_LIMIT)

    exclude_set = set(config.DEFAULT_EXCLUDES) | set(config.IMPLICIT_EXCLUDES)
    if excludes is not None:
        exclude_set = set(excludes) | set(config.IMPLICIT_EXCLUDES)

    started = time.perf_counter()
    now = time.time()
    root_path = root.resolve()
    root_depth = len(root_path.parts)

    buckets: dict[str, dict] = {}
    project_of: dict[str, dict | None] = {}
    warnings: list[str] = []
    scanned = 0
    kept_files = 0
    total_size = 0
    skipped_small = 0
    truncated = False

    ctx.phase(0, f"正在扫描 {root}")

    for current, subdirs, filenames in os.walk(root):
        ctx.check_cancel()
        current_path = Path(current)

        try:
            depth = len(current_path.resolve().parts) - root_depth
        except OSError:
            depth = 0

        # 项目标记看「裁剪前的原始条目」：.git 这类目录不遍历，但它的存在就是项目证据
        marker = _project_marker(set(subdirs) | set(filenames))

        if max_depth and depth >= max_depth:
            subdirs[:] = []
        else:
            kept = []
            for name in subdirs:
                child = current_path / name
                if name in exclude_set:
                    continue
                # 只读服务只统计不写入：保护目录（桌面/.workbuddy/项目自身）允许进入，
                # 否则预览自己的项目目录会是一片空白。写操作由 migrate / clean 把关。
                if fsutil.is_readonly_zone(child):
                    continue
                kept.append(name)
            subdirs[:] = kept

        # 项目归属：os.walk 是自上而下，父目录一定先于子目录处理过
        if marker:
            try:
                rel = str(current_path.resolve().relative_to(root_path))
            except ValueError:
                rel = current_path.name
            project_of[str(current_path)] = {
                "path": str(current_path),
                "rel": rel or current_path.name,
                "label": marker,
            }
        else:
            project_of[str(current_path)] = project_of.get(str(current_path.parent))

        for name in filenames:
            scanned += 1
            if scanned > config.SCAN_ENTRY_LIMIT:
                truncated = True
                break
            if scanned % 200 == 0:
                ctx.progress("正在统计", scanned, None, str(current_path))

            path = current_path / name
            try:
                stat = path.stat()
            except OSError as exc:
                warnings.append(f"无法读取 {path}：{fsutil.human_error(exc)}")
                continue
            if stat.st_size < min_size:
                skipped_small += 1
                continue

            key, label, group_rel = _classify(
                group_by, path, stat, now, project_of.get(str(current_path))
            )

            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "key": key,
                    "label": label,
                    "group_rel": group_rel,
                    "group_path": f"{mode['group_dir']}/{group_rel}",
                    "size": 0,
                    "count": 0,
                    "items": [],
                    "truncated": False,
                    "extensions": defaultdict(lambda: {"count": 0, "size": 0}),
                }
                buckets[key] = bucket
            bucket["size"] += stat.st_size
            bucket["count"] += 1
            kept_files += 1
            total_size += stat.st_size

            if group_by == "type":
                ext = path.suffix.lower() or "无扩展名"
                bucket["extensions"][ext]["count"] += 1
                bucket["extensions"][ext]["size"] += stat.st_size

            if len(bucket["items"]) < item_limit:
                bucket["items"].append({
                    "name": name,
                    "path": str(path),
                    "size": stat.st_size,
                    "size_text": fsutil.format_size(stat.st_size),
                    "modified_time": _fmt_time(stat.st_mtime),
                    "category": config.category_for(path),
                    "preview_target": str(root / mode["group_dir"] / group_rel / name),
                })
            else:
                bucket["truncated"] = True

        if truncated:
            break

    # ---- 汇总 ----
    ctx.phase(1, "正在汇总分组")
    result_buckets: list[dict] = []
    for bucket in buckets.values():
        percent = round(bucket["size"] / total_size * 100, 1) if total_size else 0.0
        extensions = sorted(
            ({"key": key, **value, "size_text": fsutil.format_size(value["size"])}
             for key, value in bucket["extensions"].items()),
            key=lambda item: item["size"],
            reverse=True,
        )[:8]
        result_buckets.append({
            "key": bucket["key"],
            "label": bucket["label"],
            "group_rel": bucket["group_rel"],
            "group_path": bucket["group_path"],
            "size": bucket["size"],
            "size_text": fsutil.format_size(bucket["size"]),
            "count": bucket["count"],
            "percentage": percent,
            "items": bucket["items"],
            "truncated": bucket["truncated"],
            "extensions": extensions,
        })

    result_buckets.sort(key=lambda item: item["size"], reverse=True)
    duration = round(time.perf_counter() - started, 2)

    return {
        "root": str(root),
        "group_by": group_by,
        "mode": {
            "id": mode["id"], "label": mode["label"],
            "note": mode["note"], "group_dir": mode["group_dir"],
        },
        "scanned_files": scanned,
        "kept_files": kept_files,
        "skipped_small": skipped_small,
        "scanned_size": total_size,
        "scanned_size_text": fsutil.format_size(total_size),
        "bucket_count": len(result_buckets),
        "duration_seconds": duration,
        "truncated": truncated,
        "item_limit": item_limit,
        "buckets": result_buckets,
        "warnings": warnings[:100],
        "generated_at": fsutil.now_iso(),
        "readonly": True,
        "note": "这只是预览：没有创建目录，也没有移动任何文件。",
    }


def _fmt_time(ts: float) -> str:
    from datetime import datetime

    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# 清单导出（仍然只读：只写一份 CSV 到 .filepilot/reports/，不碰被扫描的文件）
# ---------------------------------------------------------------------------

def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return '"' + text.replace('"', '""') + '"'


def export_manifest(root: str, group_by: str, items: list[dict]) -> dict:
    """把预览清单落成 CSV，方便先留着对账、之后再决定要不要真整理。"""
    from datetime import datetime

    if not items:
        raise ValueError("没有可导出的清单")

    mode = MODE_BY_ID.get(group_by) or {"id": group_by or "unknown", "label": group_by or "未知"}
    limited = items[:config.VIRTUAL_EXPORT_LIMIT]
    truncated = len(items) > len(limited)

    folder = config.STATE_DIR / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"virtual_{mode['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    lines = ["分组,文件名,当前位置,整理后位置,大小(字节)"]
    for item in limited:
        lines.append(",".join([
            _csv_cell(item.get("group_label") or mode.get("label", "")),
            _csv_cell(item.get("name") or Path(str(item.get("source", ""))).name),
            _csv_cell(item.get("source")),
            _csv_cell(item.get("target")),
            str(int(item.get("size") or 0)),
        ]))
    if truncated:
        lines.append(f'"注意","清单已截断",,,"只导出前 {config.VIRTUAL_EXPORT_LIMIT} 行"')

    target.write_text("\n".join(lines), encoding="utf-8-sig")
    size = target.stat().st_size
    return {
        "success": True,
        "path": str(target),
        "count": len(limited),
        "truncated": truncated,
        "root": root,
        "group_by": mode["id"],
        "size": size,
        "size_text": fsutil.format_size(size),
        "exported_at": fsutil.now_iso(),
    }

"""虚拟整理：预览（只读）+ 真执行（写）。

- `run_virtual` / `export_manifest`：**只读**。只做 stat 与分组，界面上的「整理后的样子」
  全部活在内存里，不创建目录、不移动、不删除。
- `run_organize`（2026-10-14 新增）：**写**。用户在预览上确认后，把文件**移动**进各分组
  子目录。注意它**只移动、不删除**，每个文件记一条 `{"op":"move"}` 动作 —— 任务页可整体回滚。

为什么不复用 `migrate`：migrate 有「目标不能落在来源内」的检查，而整理的目标
（`root/按类型/图片` 之类）**天然就在来源 root 里面**，那条检查会直接拒掉。
所以就地整理必须有个自己的执行器，但安全语义保持一致（逐条校验 + 可回滚 + 只移动）。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from .. import config, execution, fsutil, permissions
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

    started = time.perf_counter()
    now = time.time()
    # 相对路径用字符串切，不用 resolve() —— 那在 Windows 上是每个目录一次系统调用
    base_len = len(str(root))

    buckets: dict[str, dict] = {}
    project_of: dict[str, dict | None] = {}
    warnings: list[str] = []
    scanned = 0
    kept_files = 0
    total_size = 0
    skipped_small = 0
    truncated = False

    ctx.phase(0, f"正在扫描 {root}")

    def _on_dir(current: str, _depth: int, raw_dirs: list[str], raw_files: list[str]) -> None:
        """目录级上下文：项目标记看**裁剪前的原始条目名** ——
        `.git` / `package.json` 这类东西自己不一定被遍历，但它存在就是项目证据。"""
        marker = _project_marker(set(raw_dirs) | set(raw_files))
        if marker:
            rel = current[base_len:].strip("\\/")
            project_of[current] = {
                "path": current,
                "rel": rel or Path(current).name,
                "label": marker,
            }
        else:
            # 自上而下遍历，父目录一定先于子目录处理过
            project_of[current] = project_of.get(os.path.dirname(current))

    for entry in fsutil.iter_entries(
        root,
        excludes=excludes,
        max_depth=max_depth,
        progress=ctx.tick,
        cancelled=ctx.cancelled,
        on_dir=_on_dir,
        # 只读预览：自保护目录（桌面 / .workbuddy / 项目自身）也要能进，
        # 否则预览自己的项目目录会是一片空白。写操作由 migrate / clean 把关。
        skip_self_protected=False,
    ):
        ctx.check_cancel()
        scanned += 1
        if scanned > config.SCAN_ENTRY_LIMIT:
            truncated = True
            break

        name = entry.name
        path = Path(entry.path)
        parent = entry.path[:entry.path.rfind(os.sep)] if os.sep in entry.path else entry.path
        try:
            stat = entry.stat()          # Windows 上目录列表已带回属性，零系统调用
        except OSError as exc:
            warnings.append(f"无法读取 {path}：{fsutil.human_error(exc)}")
            continue
        if stat.st_size < min_size:
            skipped_small += 1
            continue

        key, label, group_rel = _classify(
            group_by, path, stat, now, project_of.get(parent)
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
    return fsutil.format_mtime(ts)


# ---------------------------------------------------------------------------
# 真执行：把预览的「假想结构」落盘（移动，不删除）
# ---------------------------------------------------------------------------

def run_organize(ctx: JobContext) -> dict:
    """按预览的分组把文件**移动**进目标子目录。

    安全语义与其它写操作一致：
    - **只移动、不删除**，每个文件一条 `{"op":"move","from","to"}` —— 任务页可整体回滚；
    - 逐条校验：目标必须落在本次整理的根目录内（防 `..` 越界 / 联接绕过），
      源不存在或已在目标位就跳过；
    - 目标重名走 `ensure_unique` 加序号，**不覆盖**任何既有文件。
    """
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    raw_items = params.get("items") or []
    if not raw_items:
        raise ValueError("没有要整理的清单 —— 先在预览里确认要整理哪些，再执行")

    # ★ 服务层门禁：`virtual` 这个 job 类型同时承担「预览（只读）」和「就地整理（写）」，
    #   路由层按 job type 判写权限时区分不了，所以门禁必须放在真正动手的这个函数里。
    permissions.require_write("就地整理（会移动文件）")

    permission = permissions.current()
    fsutil.assert_writable(root, permission)
    root_resolved = root.resolve()

    started = time.perf_counter()
    total = len(raw_items)
    moved = 0
    skipped = 0
    moved_size = 0
    errors: list[str] = []
    actions: list[dict] = []
    flushed = 0

    ctx.phase(0, f"准备整理 {total} 个文件")
    for index, item in enumerate(raw_items, start=1):
        ctx.check_cancel()
        src_raw = item.get("source") or item.get("path")
        dst_raw = item.get("target") or item.get("preview_target")
        if not src_raw or not dst_raw:
            errors.append("缺少源路径或目标路径，已跳过")
            continue

        src, dst = Path(src_raw), Path(dst_raw)
        # 目标必须落在整理根目录内：`..` 越界、指向外部的联接都在这里被挡下
        try:
            dst.resolve().relative_to(root_resolved)
        except ValueError:
            errors.append(f"{src.name}：目标不在整理目录内，已跳过")
            continue

        if not src.exists():
            skipped += 1
            continue
        if src.resolve() == dst.resolve():
            skipped += 1          # 本来就在目标位置，不用动
            continue

        try:
            final = fsutil.ensure_unique(dst) if dst.exists() else dst
            # 目标必须落在整理根内 —— 上面的手写检查给的是友好文案，
            # 这里再过一道 execution 的统一判定（路径越界 / 写保护 / 权限档）。
            execution.move(
                src,
                final,
                action="就地整理",
                mode=permission,
                dest_root=root_resolved,
                check_symlink=False,
            )
            moved += 1
            moved_size += int(item.get("size") or 0)
            actions.append({"op": "move", "from": str(src), "to": str(final)})
        except (OSError, ValueError) as exc:
            errors.append(f"{src}：{fsutil.human_error(exc)}")

        if index % 10 == 0 or index == total:
            ctx.progress(f"正在整理 {index}/{total}", index, total, str(src))
        # 分批落账：中途被杀也留下已搬移的痕迹，便于回滚
        if len(actions) - flushed >= 20:
            ctx.add_actions(actions[flushed:])
            flushed = len(actions)
        ctx.sleep(0.01)

    ctx.add_actions(actions[flushed:])
    ctx.phase(6, "整理完成")
    duration = round(time.perf_counter() - started, 2)

    return {
        "root": str(root),
        "group_by": params.get("group_by") or "",
        "requested_count": total,
        "moved_count": moved,
        "skipped_count": skipped,
        "moved_size": moved_size,
        "moved_size_text": fsutil.format_size(moved_size),
        "duration_seconds": duration,
        "errors": errors[:100],
        "readonly": False,
        "note": "已按分组把文件移动到目标子目录；只移动不删除，任务页可整体回滚。",
        "generated_at": fsutil.now_iso(),
    }


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

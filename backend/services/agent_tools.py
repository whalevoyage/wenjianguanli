"""Agent 本地工具层。

给 Agent 一套**受控工具**，而不是让模型直接拼 API：

- 动作白名单（ALLOWED_ACTIONS / TOOL_REGISTRY / ACTION_PARAMS / PATH_PARAMS）
  在这里唯一定义，agent.py 与 agent_intent.py 都从这里取，避免三处各写一份。
- **执行工具**只描述元信息——真正动文件仍由 jobs.py 的 Job 完成，
  并且必须先过 fsutil.write_block_reason() 的权限门禁。工具层自身不做任何写操作。
- **查询工具**与**辅助工具**是本文件内的纯本地只读实现，零 token、可断网。

查询工具刻意做成「有界」的：Agent 问「这个目录多大」绝不能退化成一次全盘重扫，
所以统一走 _bounded_walk()，条目数与耗时双上限，超限就在结果里如实标注 truncated。
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from pathlib import Path

from .. import config, database, fsutil, permissions, store

# ---------------------------------------------------------------------------
# 一、动作白名单（执行工具 / 导航 / 状态页）
# ---------------------------------------------------------------------------

# 垃圾规则 + 预编译正则，与 junk 服务共用 config 里那张表（见 config.junk_matchers）。
# 跳过集合与 junk 服务略有不同：这里零字节另有单独分支处理。
_JUNK_MATCHERS = config.junk_matchers(frozenset({"empty_dir", "huge_file", "browser_cache"}))

ALLOWED_ACTIONS = [
    "open_tab", "overview", "scan", "tree", "virtual_preview", "projects_scan",
    "duplicate_scan", "junk_scan", "migrate_plan",
    "jobs_list", "trash_list", "logs_list",
    "duplicate_clean", "junk_clean", "migrate_execute", "backup",
    "trash_restore", "job_cancel", "job_rollback",
    # 2026-10-13「能力放开」新增（★ 必须在 ACTION_PARAMS 里，sanitize_plan 会取它）
    "similar_scan", "similar_clean", "large_clean", "large_scan",
    "search_index", "trash_purge",
    # 2026-10-14：整理真执行
    "virtual_organize",
]

# 工具注册表：动作名称、用途、分组和风险集中管理，后续新增能力只需扩展这里。
TOOL_REGISTRY = {
    "open_tab": {"label": "打开功能页", "kind": "navigation", "group": "query", "write": False},
    "overview": {"label": "查看首页概况", "kind": "query", "group": "query", "write": False},
    "jobs_list": {"label": "查看任务中心", "kind": "query", "group": "query", "write": False},
    "trash_list": {"label": "查看回收站", "kind": "query", "group": "query", "write": False},
    "logs_list": {"label": "查看操作日志", "kind": "query", "group": "query", "write": False},
    "scan": {"label": "扫描目录", "kind": "job", "group": "query", "write": False},
    "tree": {"label": "生成空间地图", "kind": "job", "group": "query", "write": False},
    "virtual_preview": {"label": "生成整理预览", "kind": "job", "group": "execute", "write": False},
    # 2026-10-14：整理真执行（就地移动到分组子目录，只移动不删除、可回滚）
    "virtual_organize": {"label": "执行整理", "kind": "job", "group": "execute", "write": True},
    "projects_scan": {"label": "扫描项目雷达", "kind": "job", "group": "query", "write": False},
    "duplicate_scan": {"label": "查找重复文件", "kind": "job", "group": "query", "write": False},
    "junk_scan": {"label": "查找垃圾文件", "kind": "job", "group": "query", "write": False},
    "migrate_plan": {"label": "创建迁移计划", "kind": "job", "group": "execute", "write": False},
    "duplicate_clean": {"label": "清理重复文件", "kind": "job", "group": "execute", "write": True},
    "junk_clean": {"label": "清理垃圾文件", "kind": "job", "group": "execute", "write": True},
    "migrate_execute": {"label": "执行跨盘迁移", "kind": "job", "group": "execute", "write": True},
    "backup": {"label": "创建项目备份", "kind": "job", "group": "execute", "write": True},
    "trash_restore": {"label": "恢复隔离区文件", "kind": "job", "group": "execute", "write": True},
    # job_cancel 不算写：它只是让一个任务停下来，不碰任何文件（只读档也该能停掉跑疯的任务）。
    # 这里必须与 WRITE_ACTIONS 保持一致 —— 前端按 WRITE_ACTIONS 判定，两处打架会误导后来人。
    "job_cancel": {"label": "取消任务", "kind": "job", "group": "execute", "write": False},
    "job_rollback": {"label": "回滚任务", "kind": "job", "group": "execute", "write": True},
    # ---- 2026-10-13「能力放开」：把页面有、Agent 却没有的能力接上 ----
    # 相似图片：此前 Agent 完全碰不到（应用里的完整功能）
    "similar_scan": {"label": "查找相似图片", "kind": "job", "group": "query", "write": False},
    "similar_clean": {"label": "清理相似图片", "kind": "job", "group": "execute", "write": True},
    # 大文件：此前能列不能清
    "large_clean": {"label": "清理大文件", "kind": "job", "group": "execute", "write": True},
    # 全文检索：此前 Agent 用不了
    "search_index": {"label": "建立全文索引", "kind": "job", "group": "query", "write": False},
    # 隔离区彻底删除：**全项目唯一的物理删除口**，最高风险
    "trash_purge": {"label": "彻底删除隔离区内容", "kind": "job", "group": "execute", "write": True},
}

# 工具分组：与第七阶段计划树里的「输入 / 输出 / 风险」展示一一对应
TOOL_GROUPS: dict[str, list[str]] = {
    "query": [
        "drive_status", "dir_size", "dir_tree", "type_distribution", "recent_files",
        "large_files", "duplicate_files", "junk_files", "project_dirs",
        "job_status", "trash_contents", "operation_logs",
        "search", "backup_verify", "snapshot_list", "snapshot_compare",
        "similar_scan", "search_index",
    ],
    "execute": [
        "virtual_preview", "virtual_organize", "migrate_plan", "migrate_execute", "junk_clean",
        "duplicate_clean", "backup", "trash_restore", "job_cancel", "job_rollback",
        "similar_clean", "large_clean", "trash_purge",
    ],
    "helper": [
        "check_disk_space", "check_path_exists", "check_path_protected",
        "check_target_conflict", "check_user_permission", "estimate_task",
    ],
}

# 写类动作：会真正动到用户的文件。Agent 侧只负责「出计划」，执行前必须再过一道确认；
# 前端拿到 write=True 会换警告样式，readonly 档直接拒发。
#
# job_cancel 不算写：它只是让一个任务停下来，不碰任何文件。
# 而且「只读档也想停掉一个跑疯了的任务」是完全合理的诉求，不该被拦住。
WRITE_ACTIONS = {
    "duplicate_clean", "junk_clean", "migrate_execute", "backup",
    "trash_restore", "job_rollback",
    # 2026-10-13 放开后新增的三个写动作
    "similar_clean", "large_clean", "trash_purge",
    # 2026-10-14：就地整理（只移动、不删除、可回滚），但仍是真动文件
    "virtual_organize",
}

# 每个动作允许的参数键：模型多吐的字段一律丢弃
ACTION_PARAMS = {
    "open_tab": {"route", "path"}, "overview": set(), "jobs_list": set(), "trash_list": set(), "logs_list": set(),
    "scan": {"path", "min_size_mb", "extensions"},
    "virtual_preview": {"path", "group_by", "min_size_mb"},
    "virtual_organize": {"path", "group_by"},
    "projects_scan": {"path", "zombie_days", "max_depth"},
    "tree": {"path", "min_size_mb", "extensions"},
    "duplicate_scan": {"path", "min_size_mb", "extensions"},
    "junk_scan": {"path"},
    "migrate_plan": {"source_dir", "target_dir", "min_size_mb", "extensions"},
    "duplicate_clean": {"path", "keep", "min_size_mb", "extensions"},
    "junk_clean": {"path"},
    "migrate_execute": {"source_dir", "target_dir", "min_size_mb", "extensions"},
    "backup": {"source_dir", "dest_dir"},
    "trash_restore": {"path"},
    "job_cancel": set(),
    "job_rollback": set(),
    # ---- 2026-10-13「能力放开」新增动作 ----
    "similar_scan": {"path", "min_size_kb", "threshold", "rotations"},
    "similar_clean": {"path", "keep", "min_size_mb", "extensions"},
    "large_clean": {"path", "min_mb", "limit"},
    "large_scan": {"path", "min_mb", "limit"},
    "search_index": {"paths"},
    # 隔离区彻底删除：要么给 ids，要么 all=True（清空整个隔离区）
    "trash_purge": {"ids", "all"},
}

# 需要真实存在的目录（目标目录除外——迁移目标允许尚不存在，由 migrate 服务创建）
PATH_PARAMS = {
    "open_tab": [], "overview": [], "jobs_list": [], "trash_list": [], "logs_list": [],
    "scan": ["path"], "tree": ["path"],
    "virtual_preview": ["path"], "virtual_organize": ["path"], "projects_scan": ["path"],
    "duplicate_scan": ["path"], "junk_scan": ["path"],
    "duplicate_clean": ["path"], "junk_clean": ["path"],
    "migrate_plan": ["source_dir"], "migrate_execute": ["source_dir"],
    "backup": ["source_dir"],
    "trash_restore": ["path"],
    "job_cancel": [], "job_rollback": [],
    # 2026-10-13 新增（trash_purge 不碰用户目录，只按隔离区 id 删）
    "similar_scan": ["path"], "similar_clean": ["path"], "large_clean": ["path"],
    "large_scan": ["path"],
    "search_index": ["paths"],
    "trash_purge": [],
}

# 辅助工具的展示元信息，前端计划树按 id 取标题与说明
HELPER_TOOLS = {
    "check_disk_space": {"label": "检查磁盘空间", "params": ["path", "need_mb"]},
    "check_path_exists": {"label": "检查路径是否存在", "params": ["path"]},
    "check_path_protected": {"label": "检查路径是否受保护", "params": ["path"]},
    "check_target_conflict": {"label": "检查目标是否冲突", "params": ["source_dir", "target_dir"]},
    "check_user_permission": {"label": "检查用户权限", "params": []},
    "estimate_task": {"label": "估算耗时与释放空间", "params": ["action", "items"]},
}

QUERY_TOOLS = {
    "drive_status": {"label": "查询磁盘状态", "params": ["path"]},
    "dir_size": {"label": "查询目录大小", "params": ["path", "max_depth"]},
    "dir_tree": {"label": "查询目录树", "params": ["path", "depth"]},
    "type_distribution": {"label": "查询文件类型分布", "params": ["path"]},
    "recent_files": {"label": "查询最近修改文件", "params": ["path", "days", "limit"]},
    "large_files": {"label": "查询大文件", "params": ["path", "min_mb", "limit"]},
    "duplicate_files": {"label": "查询重复文件", "params": ["path", "min_mb", "limit"]},
    "junk_files": {"label": "查询垃圾文件", "params": ["path", "limit"]},
    "project_dirs": {"label": "查询项目目录", "params": ["path", "max_depth"]},
    "job_status": {"label": "查询任务状态", "params": ["limit"]},
    "trash_contents": {"label": "查询回收站内容", "params": ["limit"]},
    "operation_logs": {"label": "查询历史操作记录", "params": ["limit"]},
    # 2026-10-13「能力放开」补的四个：检索 / 备份核对 / 快照
    "search": {"label": "全文检索文件内容", "params": ["keyword", "limit"]},
    "backup_verify": {"label": "核对备份能否还原", "params": ["zip_path", "mode"]},
    "snapshot_list": {"label": "列出扫描快照", "params": ["root"]},
    "snapshot_compare": {"label": "对比两个扫描快照", "params": ["root", "base_id", "target_id"]},
}

# 从目录名反推盘符根，用于「C 盘」这类口语
DRIVE_ROOT_RE = None  # 由 agent_intent 提供，这里不做正则

TYPE_BUCKETS = {
    "视频": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm"],
    "图片": ["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "tif", "tiff"],
    "音频": ["mp3", "wav", "flac", "aac", "ogg", "m4a"],
    "文档": ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md", "csv"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "xz"],
    "安装包": ["exe", "msi", "dmg", "pkg"],
    "代码": ["py", "js", "ts", "java", "c", "cpp", "cs", "go", "rs", "html", "css", "json"],
}


def _ext_of(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _bucket_of(ext: str) -> str:
    for label, exts in TYPE_BUCKETS.items():
        if ext in exts:
            return label
    return "其他"


# ---------------------------------------------------------------------------
# 二、有界遍历：查询工具的地基
#
# 与 fsutil.iter_files 的区别：这里额外加了「条目数」和「墙钟时间」两个硬上限。
# 目的不是扫全，而是毫秒级给出数量级判断；超限通过 truncated 如实上报，
# 由 Agent 在回复里说明「这是抽样估算」，绝不假装精确。
# ---------------------------------------------------------------------------

def _bounded_walk(
    root: Path,
    *,
    excludes: list[str] | None = None,
    max_depth: int | None = None,
    max_entries: int | None = None,
    max_seconds: float | None = None,
):
    """返回 (files, truncated)。files 是 Path 列表，只含成功 stat 的普通文件。"""
    cap_entries = max_entries or config.AGENT_QUERY_MAX_ENTRIES
    cap_seconds = max_seconds or config.AGENT_QUERY_MAX_SECONDS
    deadline = time.monotonic() + cap_seconds
    found: list[Path] = []
    truncated = False

    try:
        for path in fsutil.iter_files(root, excludes=excludes, max_depth=max_depth):
            if len(found) >= cap_entries or time.monotonic() > deadline:
                truncated = True
                break
            try:
                if path.is_file():
                    found.append(path)
            except OSError:
                continue
    except OSError:
        truncated = True
    return found, truncated


def _stat_of(path: Path):
    try:
        return path.stat()
    except OSError:
        return None


def _result(data: dict, note: str = "", truncated: bool = False) -> dict:
    return {"ok": True, "data": data, "note": note, "truncated": truncated}


def _failure(note: str) -> dict:
    return {"ok": False, "data": {}, "note": note, "truncated": False}


# ---------------------------------------------------------------------------
# 三、查询工具（12 个，纯本地只读）
# ---------------------------------------------------------------------------

def query_drive_status(path: str = "") -> dict:
    drives = fsutil.disk_stats()
    return _result({"drives": drives}, f"共 {len(drives)} 个盘位")


def query_dir_size(path: str, max_depth: int | None = None, excludes: list[str] | None = None) -> dict:
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes, max_depth=max_depth or None)
    total = 0
    for path_obj in files:
        stat = _stat_of(path_obj)
        if stat:
            total += stat.st_size
    note = f"{len(files)} 个文件，合计 {fsutil.format_size(total)}"
    if truncated:
        note += "（达到遍历上限，为抽样估算）"
    return _result(
        {"path": str(root), "bytes": total, "size_text": fsutil.format_size(total), "file_count": len(files)},
        note, truncated,
    )


def query_dir_tree(path: str, depth: int = 2, excludes: list[str] | None = None) -> dict:
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))
    try:
        tree = fsutil.build_tree(root, depth=max(1, min(depth, 4)), excludes=excludes)
    except (OSError, TypeError):
        return _failure("目录树生成失败，路径可能不可读")
    return _result({"path": str(root), "tree": tree}, "已生成占用树")


def query_type_distribution(path: str, excludes: list[str] | None = None) -> dict:
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes)
    buckets: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
    for path_obj in files:
        stat = _stat_of(path_obj)
        if not stat:
            continue
        bucket = buckets[_bucket_of(_ext_of(path_obj))]
        bucket["count"] += 1
        bucket["bytes"] += stat.st_size

    rows = [
        {"label": label, "count": item["count"], "bytes": item["bytes"],
         "size_text": fsutil.format_size(item["bytes"]), "share": 0.0}
        for label, item in buckets.items()
    ]
    rows.sort(key=lambda row: row["bytes"], reverse=True)
    grand = sum(row["bytes"] for row in rows) or 1
    for row in rows:
        row["share"] = round(row["bytes"] / grand, 4)
    note = f"共 {len(files)} 个文件，{len(rows)} 类" + ("（抽样）" if truncated else "")
    return _result({"path": str(root), "rows": rows, "file_count": len(files)}, note, truncated)


def query_recent_files(path: str, days: int = 7, limit: int = 50, excludes: list[str] | None = None) -> dict:
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes)
    cutoff = time.time() - max(1, days) * 86400
    hit = []
    for path_obj in files:
        stat = _stat_of(path_obj)
        if stat and stat.st_mtime >= cutoff:
            hit.append((stat.st_mtime, path_obj, stat.st_size))
    hit.sort(reverse=True)
    rows = [
        {"path": str(item[1]), "name": item[1].name,
         "modified": fsutil.format_mtime(item[0]),
         "size": item[2], "size_text": fsutil.format_size(item[2])}
        for item in hit[: max(1, min(limit, 200))]
    ]
    note = f"近 {days} 天有 {len(hit)} 个文件被修改" + ("（抽样）" if truncated else "")
    return _result({"path": str(root), "rows": rows, "count": len(hit), "days": days}, note, truncated)


def query_large_files(path: str, min_mb: int = 100, limit: int = 50, excludes: list[str] | None = None) -> dict:
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes)
    floor = max(1, min_mb) * 1024 * 1024
    hit = []
    for path_obj in files:
        stat = _stat_of(path_obj)
        if stat and stat.st_size >= floor:
            hit.append((stat.st_size, path_obj))
    hit.sort(reverse=True)
    rows = [
        {"path": str(item[1]), "name": item[1].name, "size": item[0],
         "size_text": fsutil.format_size(item[0])}
        for item in hit[: max(1, min(limit, 200))]
    ]
    total = sum(item[0] for item in hit)
    note = f"≥ {min_mb} MB 的文件 {len(hit)} 个，合计 {fsutil.format_size(total)}"
    if truncated:
        note += "（抽样）"
    return _result({"path": str(root), "rows": rows, "count": len(hit), "bytes": total}, note, truncated)


def query_duplicate_files(
    path: str, min_mb: int = 1, limit: int = 20, excludes: list[str] | None = None
) -> dict:
    """重复文件**候选**查询：先按体积分组，再对小组成员做快速哈希确认。

    刻意不调用完整的查重 Job——那是重活。这里只回答「大致有没有、有多少」，
    真要清理仍需用户去重复文件页或走 Agent 的查重 Job。
    """
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes)
    floor = max(1, min_mb) * 1024 * 1024
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path_obj in files:
        stat = _stat_of(path_obj)
        if stat and stat.st_size >= floor:
            by_size[stat.st_size].append(path_obj)

    groups = []
    redundant_bytes = 0
    for size, members in by_size.items():
        if len(members) < 2:
            continue
        try:
            by_hash: dict[str, list[Path]] = defaultdict(list)
            for member in members[:12]:
                by_hash[fsutil.quick_hash(member)].append(member)
        except OSError:
            continue
        for digest, same in by_hash.items():
            if len(same) < 2:
                continue
            groups.append({
                "size": size, "size_text": fsutil.format_size(size), "hash": digest,
                "files": [str(item) for item in same],
            })
            redundant_bytes += size * (len(same) - 1)
        if len(groups) >= limit:
            break

    groups.sort(key=lambda group: group["size"] * (len(group["files"]) - 1), reverse=True)
    note = f"确认重复 {len(groups)} 组" + ("（抽样）" if truncated else "")
    return _result(
        {"path": str(root), "groups": groups, "count": len(groups),
         "redundant_bytes": redundant_bytes, "redundant_text": fsutil.format_size(redundant_bytes)},
        note, truncated,
    )


def query_junk_files(path: str, limit: int = 50, excludes: list[str] | None = None) -> dict:
    """垃圾文件轻量查询：只做文件名规则匹配 + 零字节，不跑完整 Job。"""
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    files, truncated = _bounded_walk(root, excludes=excludes)
    levels: dict[str, list[dict]] = defaultdict(list)
    total_bytes = 0

    for path_obj in files:
        name = path_obj.name.lower()
        matched = None
        for rule, matcher in _JUNK_MATCHERS:
            if matcher is not None and matcher.match(name):
                matched = rule
                break
        stat = _stat_of(path_obj)
        if matched is None and stat and stat.st_size == 0:
            matched = next((rule for rule in config.JUNK_RULES if rule["id"] == "zero_byte"), None)
        if matched is None:
            continue
        if len(levels[matched["level"]]) >= limit:
            continue
        size = stat.st_size if stat else 0
        total_bytes += size
        levels[matched["level"]].append({
            "path": str(path_obj), "name": path_obj.name, "size": size,
            "size_text": fsutil.format_size(size), "rule": matched["name"],
            "rule_id": matched["id"], "level": matched["level"],
        })

    rows = [
        {"level": level, "label": config.JUNK_LEVEL_META.get(level, {}).get("label", level),
         "items": items, "count": len(items)}
        for level, items in levels.items()
    ]
    rows.sort(key=lambda row: {"safe": 0, "confirm": 1, "caution": 2}.get(row["level"], 3))
    safe_count = next((row["count"] for row in rows if row["level"] == "safe"), 0)
    note = f"安全级 {safe_count} 项，合计约 {fsutil.format_size(total_bytes)}"
    if truncated:
        note += "（抽样）"
    return _result(
        {"path": str(root), "levels": rows, "bytes": total_bytes,
         "size_text": fsutil.format_size(total_bytes)},
        note, truncated,
    )


def query_project_dirs(path: str, max_depth: int = 3, excludes: list[str] | None = None) -> dict:
    """找项目根：目录里出现 PROJECT_MARKERS 里的标记文件即命中。"""
    try:
        root = fsutil.resolve_dir(path)
    except (FileNotFoundError, ValueError) as exc:
        return _failure(fsutil.human_error(exc))

    depth = max(1, min(max_depth, 6))
    found: list[dict] = []
    # 深度用「相对 root 数分隔符」算 —— 逐目录 resolve() 是每目录一次系统调用
    root_str = str(root).rstrip("\\/")
    base_len = len(root_str)
    truncated = False
    deadline = time.monotonic() + config.AGENT_QUERY_MAX_SECONDS

    try:
        for current, subdirs, filenames in os.walk(root):
            if time.monotonic() > deadline or len(found) >= 200:
                truncated = True
                break
            current_path = Path(current)
            current_depth = current[base_len:].count(os.sep) if len(current) > base_len else 0
            if current_depth >= depth:
                subdirs[:] = []
                continue
            subdirs[:] = [name for name in subdirs if not fsutil.is_self_protected(current_path / name)]
            marker = config.project_marker_for(filenames)
            if marker:
                found.append({"path": str(current_path), "name": current_path.name, "marker": marker})
                subdirs[:] = []
    except OSError:
        truncated = True

    note = f"识别到 {len(found)} 个项目目录" + ("（抽样）" if truncated else "")
    return _result({"path": str(root), "projects": found, "count": len(found)}, note, truncated)


def query_job_status(limit: int = 10) -> dict:
    from ..jobs import manager

    jobs = manager.list(max(1, min(limit, 50)))
    running = [job for job in jobs if job.get("status") in ("running", "waiting", "pending")]
    return _result({"jobs": jobs, "running": running, "count": len(jobs)},
                   f"最近 {len(jobs)} 条任务，其中 {len(running)} 条在跑或等待")


def query_trash_contents(limit: int = 20) -> dict:
    from . import clean

    data = clean.list_trash()
    entries = (data.get("entries") or [])[: max(1, min(limit, 100))]
    return _result(
        {"entries": entries, "count": data.get("count", 0),
         "size": data.get("total_size", 0), "size_text": data.get("total_size_text", "0 B"),
         "trash_dir": data.get("trash_dir")},
        f"隔离区 {data.get('count', 0)} 项，可恢复空间约 {data.get('total_size_text', '0 B')}",
    )


def query_operation_logs(limit: int = 20) -> dict:
    logs = store.read_logs()
    return _result({"logs": logs[-max(1, min(limit, 200)):], "count": len(logs)},
                   f"共 {len(logs)} 条操作日志")


# ---- 2026-10-13「能力放开」补的四个查询工具 ----

def query_search(keyword: str, limit: int = 20) -> dict:
    """全文检索（只读）。没建过索引会如实说，不让调用方以为"搜过了没结果"。"""
    from . import index

    if not database.search_available(config.DATABASE_FILE):
        return _failure("全文检索不可用：当前 SQLite 构建没有编进 FTS5")
    text = (keyword or "").strip()
    if len(text) < 1:
        return _failure("搜索词为空")
    # 「没有索引」和「索引了但没命中」必须分清：前者该提示去建索引，
    # 后者是正常结果。query() 的 enabled 只说明 FTS5 编没编入，说明不了有没有建过。
    if not (index.status().get("roots") or []):
        return _failure("还没有建立任何索引，先让我建一个再说")
    data = index.query(text, max(1, min(limit, 100)))
    results = data.get("results") or []
    return _result(
        {"results": results, "count": data.get("total", len(results)), "match": data.get("match")},
        f"「{text}」命中 {data.get('total', len(results))} 个文件",
    )


def query_backup_verify(zip_path: str, mode: str = "verify") -> dict:
    """核对备份 ZIP 能否真正还原（只读校验，不写用户文件）。"""
    from . import backup

    target = (zip_path or "").strip().strip('"')
    if not target:
        return _failure("没有给出备份文件路径")
    if not os.path.exists(target):
        return _failure(f"找不到备份文件：{target}")
    return backup.verify_backup_restore(target, mode if mode in ("verify", "restore_test") else "verify")


def query_snapshot_list(root: str) -> dict:
    """列出某个目录的扫描快照（只读）。"""
    from . import snapshot

    target = (root or "").strip().strip('"')
    if not target:
        return _failure("没有给出目录")
    snaps = snapshot.list_snapshots(target)
    return _result({"snapshots": snaps, "count": len(snaps), "root": target},
                   f"{target} 有 {len(snaps)} 份快照")


def query_snapshot_compare(root: str, base_id: str, target_id: str) -> dict:
    """对比两份扫描快照的目录增减（只读）。"""
    from . import snapshot

    target = (root or "").strip().strip('"')
    if not target or not base_id or not target_id:
        return _failure("对比快照需要目录和两个快照 id")
    return snapshot.compare_snapshots(target, base_id, target_id)


# ---------------------------------------------------------------------------
# 四、辅助工具（6 个，纯本地只读）
# ---------------------------------------------------------------------------

def check_disk_space(path: str, need_mb: int | None = None) -> dict:
    try:
        free = fsutil.disk_free(Path(str(path) or "C:\\"))
    except (FileNotFoundError, OSError, ValueError):
        return _failure(f"无法读取 {path} 所在盘的空间")
    drive = Path(str(path)).drive or str(path)
    enough = None
    if need_mb:
        enough = free >= int(need_mb) * 1024 * 1024
    return _result(
        {"drive": drive, "free": free, "free_text": fsutil.format_size(free),
         "need_mb": need_mb, "enough": enough},
        f"{drive} 可用 {fsutil.format_size(free)}"
        + ("" if enough is None else ("，够用" if enough else "，不够")),
    )


def check_path_exists(path: str) -> dict:
    target = Path(str(path).strip().strip('"'))
    exists = target.exists()
    return _result(
        {"path": str(target), "exists": exists,
         "is_dir": target.is_dir() if exists else False},
        f"{target} {'存在' if exists else '不存在'}",
    )


def check_path_protected(path: str, mode: str | None = None) -> dict:
    """复用 fsutil 的写保护判定——这是唯一权威，工具层不另写一套规则。"""
    current = mode or permissions.current()
    target = Path(str(path).strip().strip('"'))
    reason = fsutil.write_block_reason(target, current)
    readable = not bool(fsutil.is_self_protected(target))
    return _result(
        {"path": str(target), "mode": current, "writable": reason is None,
         "reason": reason, "readable": readable},
        reason or "当前档位下可写",
    )


def check_target_conflict(source_dir: str, target_dir: str) -> dict:
    """迁移前的目标冲突预检：目标盘是否已有同名文件。"""
    source = Path(str(source_dir))
    if not source.exists():
        return _failure(f"来源目录不存在：{source}")
    target = Path(str(target_dir))
    conflicts: list[str] = []
    checked = 0
    try:
        for current, subdirs, filenames in os.walk(source):
            if len(conflicts) >= 50 or checked > 20000:
                break
            relative = Path(current).relative_to(source)
            for name in filenames:
                checked += 1
                if (target / relative / name).exists():
                    conflicts.append(str(relative / name) if str(relative) != "." else name)
    except OSError:
        pass
    return _result(
        {"source_dir": str(source), "target_dir": str(target),
         "conflicts": conflicts, "count": len(conflicts), "checked": checked},
        f"预检 {checked} 个文件，发现 {len(conflicts)} 个同名冲突",
    )


def check_user_permission() -> dict:
    """档位信息统一从 permissions.describe() 取，工具层不重复实现档位规则。"""
    info = permissions.describe()
    return _result(
        {
            "mode": info.get("mode"),
            "label": info.get("label"),
            "allows_write": info.get("allows_write"),
            "relaxes_protection": info.get("relaxes_protection"),
            "modes": info.get("modes") or [],
            "guards": info.get("guards") or [],
        },
        f"当前是「{info.get('label')}」档"
        + ("，写操作在入口就被拒绝" if not info.get("allows_write") else ""),
    )


def estimate_task(action: str = "", items: int = 0, bytes_: int = 0) -> dict:
    """按经验吞吐给出数量级估算：解释「预计释放多少、大概多久」。"""
    throughput = config.AGENT_ESTIMATE_BYTES_PER_SEC or 1.0
    count = max(0, int(items or 0))
    total = max(0, int(bytes_ or 0))
    seconds = int(total / throughput) if total else 0
    if count and not total:
        seconds = max(1, int(count / 200))          # 纯删除/校验类，按条数估
    batches = max(1, (count + config.AGENT_BATCH_SIZE - 1) // config.AGENT_BATCH_SIZE) if count else 0
    return _result(
        {"action": action, "file_count": count, "bytes": total,
         "free_text": fsutil.format_size(total), "seconds": seconds,
         "duration_text": _duration_text(seconds), "batches": batches,
         "batch_size": config.AGENT_BATCH_SIZE},
        f"预计释放 {fsutil.format_size(total)}，约 {_duration_text(seconds)}",
    )


def _duration_text(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{round(seconds / 3600, 1)} 小时"


# ---------------------------------------------------------------------------
# 五、统一调度入口
# ---------------------------------------------------------------------------

def run_tool(name: str, params: dict | None = None) -> dict:
    """按名字调用查询/辅助工具。未注册的名字一律拒绝——模型不能凭空发明工具。"""
    params = params or {}
    if name in QUERY_TOOLS:
        table = {
            "drive_status": lambda: query_drive_status(params.get("path", "")),
            "dir_size": lambda: query_dir_size(params["path"], params.get("max_depth"), params.get("excludes")),
            "dir_tree": lambda: query_dir_tree(params["path"], params.get("depth", 2), params.get("excludes")),
            "type_distribution": lambda: query_type_distribution(params["path"], params.get("excludes")),
            "recent_files": lambda: query_recent_files(
                params["path"], params.get("days", 7), params.get("limit", 50), params.get("excludes")),
            "large_files": lambda: query_large_files(
                params["path"], params.get("min_mb", 100), params.get("limit", 50), params.get("excludes")),
            "duplicate_files": lambda: query_duplicate_files(
                params["path"], params.get("min_mb", 1), params.get("limit", 20), params.get("excludes")),
            "junk_files": lambda: query_junk_files(
                params["path"], params.get("limit", 50), params.get("excludes")),
            "project_dirs": lambda: query_project_dirs(
                params["path"], params.get("max_depth", 3), params.get("excludes")),
            "job_status": lambda: query_job_status(params.get("limit", 10)),
            "trash_contents": lambda: query_trash_contents(params.get("limit", 20)),
            "operation_logs": lambda: query_operation_logs(params.get("limit", 20)),
            # 2026-10-13 新增
            "search": lambda: query_search(params.get("keyword", ""), params.get("limit", 20)),
            "backup_verify": lambda: query_backup_verify(
                params.get("zip_path", ""), params.get("mode", "verify")),
            "snapshot_list": lambda: query_snapshot_list(params.get("root", "")),
            "snapshot_compare": lambda: query_snapshot_compare(
                params.get("root", ""), params.get("base_id", ""), params.get("target_id", "")),
        }
        handler = table.get(name)
        if not handler:
            return _failure(f"工具 {name} 暂未实现")
        try:
            return handler()
        except KeyError as exc:
            return _failure(f"工具 {name} 缺少参数 {exc}")
        except (OSError, ValueError) as exc:
            return _failure(fsutil.human_error(exc))

    if name in HELPER_TOOLS:
        table = {
            "check_disk_space": lambda: check_disk_space(params.get("path", ""), params.get("need_mb")),
            "check_path_exists": lambda: check_path_exists(params.get("path", "")),
            "check_path_protected": lambda: check_path_protected(params.get("path", ""), params.get("mode")),
            "check_target_conflict": lambda: check_target_conflict(
                params.get("source_dir", ""), params.get("target_dir", "")),
            "check_user_permission": lambda: check_user_permission(),
            "estimate_task": lambda: estimate_task(
                params.get("action", ""), params.get("items", 0), params.get("bytes", 0)),
        }
        handler = table.get(name)
        if not handler:
            return _failure(f"辅助工具 {name} 暂未实现")
        try:
            return handler()
        except (OSError, ValueError) as exc:
            return _failure(fsutil.human_error(exc))

    return _failure(f"未注册的工具：{name}")


def registry_view() -> dict:
    """给前端的工具清单：三组 + 每个工具的说明，计划树按 id 展示。"""
    return {
        "groups": TOOL_GROUPS,
        "query": QUERY_TOOLS,
        "helper": HELPER_TOOLS,
        "execute": {name: TOOL_REGISTRY[name] for name in TOOL_GROUPS["execute"] if name in TOOL_REGISTRY},
        "all_actions": ALLOWED_ACTIONS,
    }


# ---------------------------------------------------------------------------
# 2026-10-13「能力放开」：把工具注册表折成 function-calling schema
#
# 目的：让模型用**工具调用**而不是"背一份 action 白名单"来理解用户的话。
# 好处是它可以说人话（"把桌面那个空文件夹相关的垃圾清一下"）而不必命中固定话术。
#
# ★ schema 只是"模型能看见什么"，**不等于"模型能做什么"**：
#   调用结果回来还要过 normalize_steps（工具名必须在册、写动作前强制插 confirm）
#   和 sanitize_plan（路径必须真实存在）。模型编一个工具名会被直接丢掉。
# ---------------------------------------------------------------------------

# 参数名 → JSON Schema 类型。没列到的按 string 处理（宁可松一点，也别把整数拒了）。
PARAM_TYPES: dict[str, str] = {
    "path": "string", "paths": "array", "ids": "array", "keyword": "string",
    "min_mb": "integer", "min_size_mb": "integer", "min_size_kb": "integer",
    "limit": "integer", "days": "integer", "depth": "integer", "max_depth": "integer",
    "zombie_days": "integer", "threshold": "integer", "need_mb": "integer",
    "items": "integer", "bytes": "integer",
    "rotations": "boolean", "all": "boolean",
    "keep": "string", "mode": "string", "group_by": "string", "route": "string",
    "source_dir": "string", "target_dir": "string", "dest_dir": "string",
    "zip_path": "string", "root": "string", "base_id": "string", "target_id": "string",
    "extensions": "array", "excludes": "array", "action": "string",
}

# 这些参数没有它工具就跑不了 → 标 required，让模型别漏
_REQUIRED_EXTRA: dict[str, set[str]] = {
    "search": {"keyword"},
    "backup_verify": {"zip_path"},
    "snapshot_list": {"root"},
    "snapshot_compare": {"root", "base_id", "target_id"},
    "open_tab": {"route"},
}


def _param_schema(key: str) -> dict:
    kind = PARAM_TYPES.get(key, "string")
    if kind == "array":
        return {"type": "array", "items": {"type": "string"},
                "description": "字符串列表"}
    if kind == "integer":
        return {"type": "integer"}
    if kind == "boolean":
        return {"type": "boolean"}
    return {"type": "string"}


def _tool_schema(name: str, label: str, keys) -> dict:
    props = {key: _param_schema(key) for key in keys}
    required = sorted(set(PATH_PARAMS.get(name) or []) | _REQUIRED_EXTRA.get(name, set()))
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": label,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def tool_schemas() -> list[dict]:
    """全部工具的 function-calling schema（动作 + 查询 + 辅助）。"""
    out: list[dict] = []
    for name, meta in TOOL_REGISTRY.items():
        out.append(_tool_schema(name, meta["label"], sorted(ACTION_PARAMS.get(name) or [])))
    for name, meta in QUERY_TOOLS.items():
        out.append(_tool_schema(name, meta["label"], meta.get("params") or []))
    for name, meta in HELPER_TOOLS.items():
        out.append(_tool_schema(name, meta["label"], meta.get("params") or []))
    return out

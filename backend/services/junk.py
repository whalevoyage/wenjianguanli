"""垃圾文件识别：三级安全分级的规则引擎。

🟢 safe    默认勾选
🟡 confirm 默认不勾选，需要逐个确认
🔴 caution 默认隐藏，可能是个人文件
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from .. import config, fsutil, permissions
from ..jobs import JobContext

CACHE_MARKERS = (
    "\\cache\\",
    "\\code cache\\",
    "\\gpucache\\",
    "\\cachestorage\\",
    "\\thumbnails\\",
    "\\inetcache\\",
    "\\crashpad\\",
    "\\crashes\\",
)

RULE_BY_ID = {rule["id"]: rule for rule in config.JUNK_RULES}


def _match_rule(path: Path) -> str | None:
    name = path.name.lower()
    for rule in config.JUNK_RULES:
        if rule["id"] in {"empty_dir", "zero_byte", "huge_file", "browser_cache"}:
            continue
        for pattern in rule["patterns"]:
            if fnmatch.fnmatch(name, pattern.lower()):
                return rule["id"]
    return None


def _is_cache(path: Path) -> bool:
    text = str(path).lower().replace("/", "\\")
    return any(marker in text for marker in CACHE_MARKERS)


def run_junk_scan(ctx: JobContext) -> dict:
    params = ctx.params
    root = fsutil.resolve_dir(params.get("path", ""))
    excludes = params.get("excludes")
    enabled_rules = set(params.get("rules") or [rule["id"] for rule in config.JUNK_RULES])
    include_caution = bool(params.get("include_caution", False))
    # 档位在这里取一次：full 档把软保护目录（桌面）也纳入扫描
    permission = permissions.current()

    ctx.phase(0, f"正在检查 {root}")
    buckets: dict[str, list[dict]] = {rule["id"]: [] for rule in config.JUNK_RULES}
    errors: list[str] = []
    scanned = 0

    for path in fsutil.iter_files(root, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        scanned += 1
        try:
            stat = path.stat()
        except OSError as exc:
            errors.append(f"{path}：{fsutil.human_error(exc)}")
            continue

        rule_id = None
        if stat.st_size == 0:
            rule_id = "zero_byte"
        elif _is_cache(path):
            rule_id = "browser_cache"
        else:
            rule_id = _match_rule(path)

        if rule_id is None and stat.st_size >= config.HUGE_FILE_BYTES:
            rule_id = "huge_file"

        if rule_id is None or rule_id not in buckets or rule_id not in enabled_rules:
            continue

        level = RULE_BY_ID[rule_id]["level"]
        if level == "caution" and not include_caution:
            continue

        buckets[rule_id].append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "size_text": fsutil.format_size(stat.st_size),
            "level": level,
            "rule": rule_id,
            "is_dir": False,
            "protected": False,
        })

    # 空文件夹（单独一遍：必须自底向上，才能把嵌套空目录都清掉）
    if "empty_dir" in enabled_rules:
        ctx.phase(1, "正在查找空文件夹")
        for current, subdirs, filenames in os.walk(root, topdown=False):
            ctx.check_cancel()
            current_path = Path(current)
            if current_path == root:
                continue
            if fsutil.write_block_reason(current_path, permission) or fsutil.is_readonly_zone(current_path):
                continue
            names = set(subdirs) | set(filenames)
            if not names:
                buckets["empty_dir"].append({
                    "name": current_path.name,
                    "path": str(current_path),
                    "size": 0,
                    "size_text": "0 B",
                    "level": "safe",
                    "rule": "empty_dir",
                    "is_dir": True,
                    "protected": False,
                })

    ctx.phase(2, "正在汇总")
    levels: list[dict] = []
    rules_out: list[dict] = []
    total_count = 0
    total_size = 0

    for level_key in ("safe", "confirm", "caution"):
        meta = config.JUNK_LEVEL_META[level_key]
        items: list[dict] = []
        for rule in config.JUNK_RULES:
            if rule["level"] != level_key:
                continue
            bucket = buckets[rule["id"]]
            bucket.sort(key=lambda item: item["size"], reverse=True)
            if bucket:
                rules_out.append({
                    "id": rule["id"],
                    "name": rule["name"],
                    "level": rule["level"],
                    "desc": rule["desc"],
                    "count": len(bucket),
                    "size": sum(item["size"] for item in bucket),
                    "size_text": fsutil.format_size(sum(item["size"] for item in bucket)),
                })
                items.extend(bucket)
        items.sort(key=lambda item: item["size"], reverse=True)
        size_total = sum(item["size"] for item in items)
        total_count += len(items)
        total_size += size_total
        levels.append({
            "level": level_key,
            "label": meta["label"],
            "note": meta["note"],
            "count": len(items),
            "size": size_total,
            "size_text": fsutil.format_size(size_total),
            "items": items[:1000],
        })

    safe_level = levels[0]
    return {
        "root": str(root),
        "scanned_files": scanned,
        "total_count": total_count,
        "total_size": total_size,
        "total_size_text": fsutil.format_size(total_size),
        "safe_size": safe_level["size"],
        "safe_size_text": safe_level["size_text"],
        "safe_count": safe_level["count"],
        "levels": levels,
        "rules": rules_out,
        "errors": errors[:100],
        "caution_included": include_caution,
    }

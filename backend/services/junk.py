"""垃圾文件识别：三级安全分级的规则引擎。

🟢 safe    默认勾选
🟡 confirm 默认不勾选，需要逐个确认
🔴 caution 默认隐藏，可能是个人文件
"""
from __future__ import annotations

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


# 规则表在导入时编译成正则，之后每个文件只花一次正则匹配。
# 原来每个文件都要遍历 28 个 glob 模式、每个模式调一次 fnmatch（内部两次 normcase）——
# 实测这是本服务一半的耗时（见 config.glob_matcher 的注释）。
_MATCHER_ORDER = config.junk_matchers(
    frozenset({"empty_dir", "zero_byte", "huge_file", "browser_cache"})
)


def _match_rule(path: Path) -> str | None:
    name = path.name.lower()
    for rule, matcher in _MATCHER_ORDER:
        if matcher is not None and matcher.match(name):
            return rule["id"]
    return None


def _is_cache(path: Path) -> bool:
    text = str(path).lower().replace("/", "\\")
    return any(marker in text for marker in CACHE_MARKERS)


def run_junk_scan(ctx: JobContext) -> dict:
    params = ctx.params
    roots, source_notes = fsutil.resolve_sources(params)
    # 注：多来源时「根目录自身」不会被当成空目录 —— 空目录判定靠 on_dir 回调，
    # 它只在**进入子目录**时触发，根不在其中。以前这里还留着一个算好却没用的
    # `root_texts` 集合，2026-10-18 清 lint 时删掉了。
    excludes = params.get("excludes")
    enabled_rules = set(params.get("rules") or [rule["id"] for rule in config.JUNK_RULES])
    include_caution = bool(params.get("include_caution", False))
    # 档位在这里取一次：full 档把软保护目录（桌面）也纳入扫描
    permission = permissions.current()

    ctx.phase(0, fsutil.sources_phrase("正在检查", roots))
    buckets: dict[str, list[dict]] = {rule["id"]: [] for rule in config.JUNK_RULES}
    errors: list[str] = []
    scanned = 0

    for root, entry in fsutil.iter_entries_multi(roots, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        scanned += 1
        try:
            stat = entry.stat()          # Windows 上目录列表已带回属性，零系统调用
        except OSError as exc:
            errors.append(f"{entry.path}：{fsutil.human_error(exc)}")
            continue
        path = Path(entry.path)

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
            "source": fsutil.source_of(path, roots),
        })

    # 空文件夹：单独一趟「只数名字、不 stat」的遍历。
    #
    # **为什么不并进主遍历**：主遍历按用户排除项剪掉了 node_modules / __pycache__ /
    # build / dist —— 而空目录恰恰经常就在那里（清空后的 __pycache__、构建残留的 build）。
    # 并进去会静默丢掉这些候选，那是功能回退，不是提速。
    #
    # 但相对旧的 `os.walk(topdown=False)` 有两处实质改进：
    #   1. **不再钻进系统只读区**。旧版对 Windows / Program Files / $Recycle.Bin
    #      只是"记完就 continue"，`subdirs` 没清空，于是**整棵走进了 C:\Windows** ——
    #      这正是"扫 C 盘很慢"的一大来源。现在这些目录根本不进（走 IMPLICIT 排除）。
    #   2. 不逐文件 stat，只取目录列表里的名字。
    if "empty_dir" in enabled_rules:
        ctx.phase(1, "正在查找空文件夹")

        def _make_empty_dir_collector(source_root: Path):
            """空目录收集器。**每个来源各建一个**，闭包捕获各自的根。

            ★ 以前这里直接用主遍历的循环变量 `root`，有两个后果：
              ① 主遍历一个条目都没产出时（目录为空 / 不可读），`root` 从未绑定，
                 到这儿直接 `UnboundLocalError` 崩掉**整条任务**
                 （2026-10-12 实测：扫一个桌面下的空目录，Agent 任务失败）；
              ② 多来源时循环结束后 `root` 只是**最后一个**来源，
                 前面几个来源的空目录被静默漏掉（B8 多来源埋下的隐患）。
            """
            def _collect_empty_dir(current: str, _depth: int, raw_dirs: list[str], raw_files: list[str]) -> None:
                if raw_dirs or raw_files:
                    return
                current_path = Path(current)
                if current_path == source_root:
                    return
                if fsutil.write_block_reason(current_path, permission) or fsutil.is_readonly_zone(current_path):
                    return
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
            return _collect_empty_dir

        for source_root in roots:
            ctx.check_cancel()
            for _ in fsutil.iter_entries(
                source_root,
                excludes=[],                 # 只保留 IMPLICIT 排除：用户排除项不参与
                on_dir=_make_empty_dir_collector(source_root),
                skip_self_protected=False,   # 只读检测：自保护目录也要能进
                cancelled=ctx.cancelled,
            ):
                pass

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
        "root": str(roots[0]),
        "sources": [str(item) for item in roots],
        "source_count": len(roots),
        "notes": source_notes,
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

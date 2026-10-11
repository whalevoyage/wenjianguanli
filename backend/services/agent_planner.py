"""多步骤任务的编排器（第三阶段）。

把一个目标拆成一条**确定的流水线**，而不是把一句话硬映射成一个 action。
这里只负责「拆」和「记账」，真正的执行分两段：

  只读分析段（本文件驱动的 agent_task Job）
      分析盘占用 → 找大文件 → 找重复 → 找垃圾 → 套约束 → 汇总方案
      跑完停在「等待确认」，把预计释放空间 / 文件数 / 耗时 / 风险交给用户看。

  写入执行段（复用既有 Job）
      用户确认后仍走原来的 clean / migrate 任务，保留隔离区、分批、回滚
      这些已经在生产里验证过的安全路径。Agent 不另造一条写通道。

这么切的原因：编排层不该拥有写权限。「Agent 会拆任务」和「谁能动文件」
是两件事，前者可以放开做，后者必须继续锁在权限层里。
"""
from __future__ import annotations

from pathlib import Path

from .. import config, fsutil

# ---------------------------------------------------------------------------
# 步骤模板
#
# kind 取值：
#   query   本地只读查询工具（agent_tools.run_tool）
#   job     既有任务服务（scan / junk / duplicate / migrate …）
#   helper  本地辅助检查工具
#   report  本地汇总（不出任务，只算数）
#   confirm 等待用户确认（挂起点）
# ---------------------------------------------------------------------------

STEP_FREE_SPACE: list[dict] = [
    {"id": "disk", "title": "分析{scope}占用", "kind": "query", "tool": "drive_status",
     "risk": "low", "input": "{scope}", "output": "各盘剩余空间与占用比例"},
    {"id": "large", "title": "找出大文件", "kind": "query", "tool": "large_files",
     "risk": "low", "input": "体积阈值", "output": "大文件清单与合计体积"},
    {"id": "dup", "title": "找出重复文件", "kind": "job", "tool": "duplicate_scan",
     "risk": "low", "input": "{scope}", "output": "重复组数与可释放体积"},
    {"id": "junk", "title": "找出垃圾文件", "kind": "job", "tool": "junk_scan",
     "risk": "low", "input": "{scope}", "output": "分档垃圾清单"},
    {"id": "apply", "title": "套用你的约束", "kind": "report", "tool": "",
     "risk": "low", "input": "排除标签 / 体积阈值 / 安全级", "output": "实际纳入范围"},
    {"id": "propose", "title": "汇总清理方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "上一步的清单", "output": "预计释放空间 / 文件数 / 耗时"},
    {"id": "confirm", "title": "等待你确认", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案与风险等级", "output": "你的决定"},
]

STEP_MIGRATE: list[dict] = [
    {"id": "space", "title": "检查目标盘空间", "kind": "helper", "tool": "check_disk_space",
     "risk": "low", "input": "{target}", "output": "目标盘剩余空间"},
    {"id": "protect", "title": "核对来源与目标的写保护", "kind": "helper", "tool": "check_path_protected",
     "risk": "low", "input": "{scope} / {target}", "output": "是否允许写入"},
    {"id": "conflict", "title": "预检同名冲突", "kind": "helper", "tool": "check_target_conflict",
     "risk": "low", "input": "{scope} → {target}", "output": "同名文件数"},
    {"id": "plan", "title": "生成搬运清单", "kind": "job", "tool": "migrate_plan",
     "risk": "low", "input": "{scope}", "output": "待搬移条目与体积"},
    {"id": "propose", "title": "汇总迁移方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "搬运清单", "output": "文件数 / 体积 / 预计耗时"},
    {"id": "confirm", "title": "等待你确认", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案与风险等级", "output": "你的决定"},
]

# 整理是纯只读的：预览本身就是交付物，没有「等你确认后才执行」的下一步。
# 所以这条流水线**不带 confirm 步骤**——带了会挂起在一个永远点不动的确认卡上。
STEP_ORGANIZE: list[dict] = [
    {"id": "scan", "title": "扫描待整理目录", "kind": "job", "tool": "scan",
     "risk": "low", "input": "{scope}", "output": "文件数 / 体积 / 类型分布"},
    {"id": "preview", "title": "生成整理预览", "kind": "job", "tool": "virtual_preview",
     "risk": "low", "input": "{scope} + 整理方式", "output": "按分组的假想结构"},
]

PIPELINES = {
    "free_space": STEP_FREE_SPACE,
    "migrate": STEP_MIGRATE,
    "organize": STEP_ORGANIZE,
}

# 只是「查一下」的动作走单步，不必套整条流水线
SINGLE_ACTIONS = {
    "scan", "tree", "virtual_preview", "projects_scan", "duplicate_scan",
    "junk_scan", "migrate_plan", "overview", "jobs_list", "trash_list",
    "logs_list", "open_tab", "backup", "trash_restore", "job_cancel", "job_rollback",
}

# 目标型动作：用户要的是结果，不是某一次扫描
GOAL_ACTIONS = {"junk_clean", "duplicate_clean", "migrate_execute"}


def pick_pipeline(intent: dict) -> str:
    """按意图挑流水线。"""
    action = intent.get("action") or ""
    constraints = intent.get("constraints") or {}
    scope = ((intent.get("scope") or {}).get("path") or "")

    if action in ("migrate_execute", "migrate_plan"):
        return "migrate"

    if action in ("junk_clean", "duplicate_clean", "junk_scan", "duplicate_scan"):
        return "free_space"

    # 「帮我腾出点空间」「C 盘满了」——目标是结果不是某一次扫描，走完整流水线
    if constraints.get("space_goal"):
        return "free_space"

    # 目的就是某个盘符根 → 默认理解成「盘满了，帮我看看能清什么」
    if scope:
        try:
            target = Path(scope)
            if target.parent == target:
                return "free_space"
        except (OSError, ValueError):
            pass

    if action == "virtual_preview":
        return "organize"

    return ""


def plan_steps(intent: dict) -> list[dict]:
    """产出步骤列表。单步动作也包成一步，让前端只认一种结构。"""
    name = pick_pipeline(intent)
    template = PIPELINES.get(name)
    if not template:
        return [_single_step(intent)]

    scope = (intent.get("scope") or {}).get("path") or "未指定"
    target = (intent.get("scope") or {}).get("target") or "未指定"

    steps = []
    for index, item in enumerate(template):
        steps.append({
            "index": index,
            "id": item["id"],
            "title": item["title"].format(scope=scope, target=target),
            "kind": item["kind"],
            "tool": item.get("tool") or "",
            "risk": item.get("risk") or "low",
            "input": (item.get("input") or "").format(scope=scope, target=target),
            "output": item.get("output") or "",
            "status": "pending",
            "note": "",
            "digest": "",
            "started_at": None,
            "finished_at": None,
        })
    return steps


def _single_step(intent: dict) -> dict:
    return {
        "index": 0,
        "id": intent.get("action") or "step",
        "title": intent.get("goal") or "执行计划",
        "kind": "job",
        "tool": intent.get("action") or "",
        "risk": intent.get("risk_level") or "low",
        "input": (intent.get("scope") or {}).get("path") or "",
        "output": "",
        "status": "pending",
        "note": "",
        "digest": "",
        "started_at": None,
        "finished_at": None,
    }


def pipeline_names() -> list[str]:
    return list(PIPELINES)


EXECUTE_STEPS: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "medium", "tool": "clean",
     "title": "分批清理（先进隔离区）", "input": "已确认的清单", "output": "实际处理结果"},
    {"id": "report", "kind": "report", "risk": "low", "tool": "",
     "title": "汇报结果", "input": "执行结果", "output": "总结与下一步"},
]

EXECUTE_STEPS_MIGRATE: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "medium", "tool": "migrate_execute",
     "title": "复制 → 校验 → 删源", "input": "已确认的搬运清单", "output": "搬移与校验结果"},
    {"id": "report", "kind": "report", "risk": "low", "tool": "",
     "title": "汇报结果", "input": "执行结果", "output": "总结与下一步"},
]


def execution_steps(intent: dict, proposal: dict) -> list[dict]:
    """用户确认后追加的执行步骤。

    注意：这些步骤调用的仍是既有的 clean / migrate 服务——隔离区、分批、
    回滚都还是那一套，Agent 只是把它排进了计划树。
    """
    action = proposal.get("action") or intent.get("action") or ""
    template = EXECUTE_STEPS_MIGRATE if action == "migrate_execute" else EXECUTE_STEPS
    steps = []
    for item in template:
        steps.append({
            "index": 0,
            "id": item["id"],
            "title": item["title"],
            "kind": item["kind"],
            "tool": item.get("tool") or "",
            "risk": item.get("risk") or "medium",
            "input": item.get("input") or "",
            "output": item.get("output") or "",
            "status": "pending",
            "note": "",
            "digest": "",
            "started_at": None,
            "finished_at": None,
        })
    return steps


# ---------------------------------------------------------------------------
# 估算：把分析结果折成「预计释放多少、要多久」
# ---------------------------------------------------------------------------

ACTION_LABELS = {
    "junk_clean": "清理垃圾文件",
    "duplicate_clean": "清理重复文件",
    "junk_scan": "清理垃圾文件",
    "duplicate_scan": "清理重复文件",
    "scan": "清理垃圾文件",
    "migrate_execute": "执行跨盘迁移",
    "virtual_preview": "生成整理预览",
}

# 「腾出空间」这类目标一旦成立，对外就按清理来表述——
# 但清单仍会先摊出来等确认，不存在「一句话直接删」的路径。
GOAL_UPGRADE = {"junk_scan": "junk_clean", "scan": "junk_clean", "": "junk_clean"}

# 迁移同理：用户说「把 X 搬到 Y」时本地规则给的是只读的 migrate_plan，
# 但整条 migrate 流水线的终点就是执行，所以方案要按 migrate_execute 来折算——
# 否则清单恒为空、executable 恒为 false，用户永远确认不了。
MIGRATE_UPGRADE = {"migrate_plan": "migrate_execute", "": "migrate_execute"}


def build_proposal(intent: dict, step_results: dict, mode: str = "analyze") -> dict:
    """把各步骤的结果折成一份可执行的方案。

    step_results: {"dup": {...}, "junk": {...}, "large": {...}, ...}
    返回 proposal 会被前端直接用来渲染计划树 + 预计数字 + 确认按钮。
    """
    action = intent.get("action") or ""
    constraints = intent.get("constraints") or {}

    if action in MIGRATE_UPGRADE:
        action = MIGRATE_UPGRADE[action]
    elif constraints.get("space_goal") and action in GOAL_UPGRADE:
        action = GOAL_UPGRADE[action]

    junk = (step_results.get("junk") or {}).get("result") or {}
    dup = (step_results.get("dup") or {}).get("result") or {}
    large = (step_results.get("large") or {}).get("data") or {}
    migrate = (step_results.get("plan") or {}).get("result") or {}

    items: list[dict] = []
    freed = 0
    file_count = 0

    if action in ("junk_clean", "junk_scan"):
        # 只清安全级：这是硬约定，谨慎级必须去对应页面手动挑。
        # include_caution=False 已经在扫描参数里固定，这里再兜一道。
        for level in junk.get("levels") or []:
            if level.get("level") != "safe":
                continue
            for item in level.get("items") or []:
                items.append({
                    "path": item.get("path"), "is_dir": bool(item.get("is_dir")),
                    "size": item.get("size") or 0, "rule": item.get("rule"),
                    "level": "safe",
                })
        freed = sum(item["size"] for item in items)
        file_count = len(items)

    if action in ("duplicate_clean", "duplicate_scan"):
        keep = constraints.get("keep")
        for group in dup.get("groups") or []:
            redundant = list(group.get("redundant_files") or [])
            if keep == "newest" and group.get("files"):
                newest = None
                for file in group["files"]:
                    if not newest or str(file.get("modified_time") or "") > str(newest.get("modified_time") or ""):
                        newest = file
                redundant = [f for f in redundant if f.get("path") != (newest or {}).get("path")]
            for file in redundant:
                items.append({
                    "path": file.get("path"), "is_dir": False,
                    "size": file.get("size") or 0, "rule": "duplicate", "level": "safe",
                })
        freed = int(dup.get("reclaimable_size") or 0)
        file_count = len(items)

    if action == "migrate_execute":
        items = [
            {"source": entry.get("source"), "target": entry.get("target"), "size": entry.get("size") or 0}
            for entry in (migrate.get("plan") or [])
        ]
        freed = sum(item.get("size") or 0 for item in items)
        file_count = len(items)

    # 应用体积阈值与排除路径约束（这是「不要动项目和照片」真正生效的地方）
    min_bytes = (constraints.get("min_size_mb") or 0) * 1024 * 1024
    exclude_paths = [str(item).lower() for item in (constraints.get("exclude_paths") or [])]
    if min_bytes or exclude_paths:
        kept = []
        for item in items:
            if min_bytes and (item.get("size") or 0) < min_bytes:
                continue
            path = str(item.get("path") or item.get("source") or "").lower()
            if any(path.startswith(prefix) for prefix in exclude_paths):
                continue
            kept.append(item)
        items = kept
        freed = sum(item.get("size") or 0 for item in items)
        file_count = len(items)

    duration = (step_results.get("propose") or {}).get("data") or {}
    seconds = int(duration.get("seconds") or 0)
    if not seconds and freed:
        seconds = int(freed / (config.AGENT_ESTIMATE_BYTES_PER_SEC or 1))
    batches = max(1, (file_count + config.AGENT_BATCH_SIZE - 1) // config.AGENT_BATCH_SIZE) if file_count else 0

    missing = []
    if action == "junk_clean" and not items:
        missing.append("没有安全级可清理项")
    if action == "migrate_execute" and not items:
        missing.append("搬移清单为空")

    executable = action in ("junk_clean", "junk_scan", "duplicate_clean",
                            "duplicate_scan", "migrate_execute") and bool(items)

    # 迁移不是「释放空间」，是「换个地方放」——措辞跟着动作走，别让用户误会
    is_migrate = action == "migrate_execute"

    return {
        "action": action or ("junk_clean" if items else ""),
        "label": ACTION_LABELS.get(action or "", "执行方案"),
        "executable": executable,
        "freed_label": "搬运体积" if is_migrate else "预计释放",
        "items": items,
        "item_count": file_count,
        "freed_bytes": freed,
        "freed_text": fsutil.format_size(freed),
        "seconds": seconds,
        "duration_text": _duration_text(seconds),
        "batches": batches,
        "batch_size": config.AGENT_BATCH_SIZE,
        "min_size_mb": constraints.get("min_size_mb"),
        "exclude_tags": constraints.get("exclude_tags") or [],
        "exclude_paths": constraints.get("exclude_paths") or [],
        "safe_only": constraints.get("safe_only", True),
        "risk_level": intent.get("risk_level") or "medium",
        "risk_reason": intent.get("risk_reason") or "",
        "blocked": missing,
        "large_top": (large.get("rows") or [])[:5],
        "note": "执行仍走原有清理/迁移任务：先进隔离区、分批、可回滚。",
    }


def _duration_text(seconds: int) -> str:
    if seconds <= 0:
        return "不到 1 分钟"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    return f"{round(seconds / 3600, 1)} 小时"


def digest_of(step_id: str, payload: dict) -> str:
    """给每一步生成一行摘要，放进计划树，让用户看到「这一步产出了什么」。"""
    if step_id == "disk":
        disks = payload.get("data", {}).get("drives") or []
        tight = [d for d in disks if d.get("level") in ("warning", "danger")]
        if tight:
            return "、".join(f"{d.get('root')} 仅剩 {d.get('free_text')}" for d in tight)
        return "、".join(f"{d.get('root')} 剩 {d.get('free_text')}" for d in disks) or "未读到盘位"
    if step_id == "large":
        data = payload.get("data") or {}
        return f"{data.get('count', 0)} 个，合计 {_fmt(data.get('bytes'))}"
    if step_id == "dup":
        result = payload.get("result") or {}
        return f"{result.get('group_count', 0)} 组 · 可释放 {result.get('reclaimable_size_text') or '0 B'}"
    if step_id == "junk":
        result = payload.get("result") or {}
        return f"安全级 {result.get('safe_count', 0)} 项 · {result.get('safe_size_text') or '0 B'}"
    if step_id == "scan":
        result = payload.get("result") or {}
        return f"{result.get('total_files', 0)} 个文件 · {result.get('total_size_text') or '0 B'}"
    if step_id == "preview":
        result = payload.get("result") or {}
        mode = (result.get("mode") or {}).get("label") or ""
        return (f"{mode} · 聚成 {result.get('bucket_count', 0)} 组 · "
                f"{result.get('kept_files', 0)} 个文件")
    if step_id == "plan":
        result = payload.get("result") or {}
        return f"待搬移 {len(result.get('plan') or [])} 项"
    if step_id in ("apply", "protect"):
        return payload.get("note") or ""
    if step_id == "space":
        return payload.get("note") or ""
    if step_id == "conflict":
        data = payload.get("data") or {}
        return f"{data.get('count', 0)} 个同名冲突"
    if step_id in ("propose",):
        data = payload.get("data") or {}
        return f"预计释放 {_fmt(data.get('bytes'))} · {data.get('duration_text') or ''}"
    if step_id == "execute":
        result = payload.get("result") or {}
        done = int(result.get("done") or result.get("cleaned_count")
                   or result.get("copied_count") or result.get("moved") or 0)
        freed = int(result.get("freed") or result.get("freed_size")
                    or result.get("total_size") or 0)
        errors = result.get("errors") or []
        if done:
            text = f"处理 {done} 项"
            if freed:
                text += f" · {fsutil.format_size(freed)}"
        else:
            text = result.get("skipped") or "没有需要处理的项"
        return text + (f" · {len(errors)} 条错误" if errors else "")
    if step_id == "report":
        result = payload.get("result") or {}
        summary = result.get("summary") or {}
        return summary.get("message") or "已完成"
    return payload.get("note") or ""


def _fmt(value) -> str:
    try:
        return fsutil.format_size(int(value or 0))
    except (TypeError, ValueError):
        return "0 B"

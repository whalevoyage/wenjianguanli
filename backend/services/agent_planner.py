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
from .agent_tools import HELPER_TOOLS, QUERY_TOOLS, TOOL_REGISTRY, WRITE_ACTIONS

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

# 2026-10-14：整理「真执行」—— 预览本身就是清单来源，加一道 confirm 才动手。
# 与只读的 STEP_ORGANIZE 分开：那条不带 confirm（预览即交付），这条必须带。
STEP_ORGANIZE_WRITE: list[dict] = [
    {"id": "scan", "title": "扫描待整理目录", "kind": "job", "tool": "scan",
     "risk": "low", "input": "{scope}", "output": "文件数 / 体积 / 类型分布"},
    {"id": "preview", "title": "生成整理预览", "kind": "job", "tool": "virtual_preview",
     "risk": "low", "input": "{scope} + 整理方式", "output": "按分组的目标位置"},
    {"id": "propose", "title": "汇总整理方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "预览清单", "output": "将移动的文件数与体积"},
    {"id": "confirm", "title": "等待你确认", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案（逐条列出源 → 目标）", "output": "你的决定"},
]

# ---- 2026-10-13「能力放开」新增的三条 ----

# 相似图片：分析段换成「找出相似图片」。相似 ≠ 相同，清单必须逐张给人看。
STEP_SIMILAR: list[dict] = [
    {"id": "disk", "title": "分析{scope}占用", "kind": "query", "tool": "drive_status",
     "risk": "low", "input": "{scope}", "output": "各盘剩余空间与占用比例"},
    {"id": "similar", "title": "找出相似图片", "kind": "job", "tool": "similar_scan",
     "risk": "low", "input": "{scope}", "output": "相似分组与可省体积"},
    {"id": "apply", "title": "套用你的约束", "kind": "report", "tool": "",
     "risk": "low", "input": "保留策略 / 体积阈值", "output": "实际纳入范围"},
    {"id": "propose", "title": "汇总清理方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "上一步的清单", "output": "预计释放空间 / 文件数 / 耗时"},
    {"id": "confirm", "title": "等待你确认", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案与风险等级", "output": "你的决定"},
]

# 大文件：只留「找出大文件」一步分析（大文件清单本身就是重点）。
STEP_LARGE: list[dict] = [
    {"id": "disk", "title": "分析{scope}占用", "kind": "query", "tool": "drive_status",
     "risk": "low", "input": "{scope}", "output": "各盘剩余空间与占用比例"},
    {"id": "large", "title": "找出大文件", "kind": "query", "tool": "large_files",
     "risk": "low", "input": "体积阈值", "output": "大文件清单与合计体积"},
    {"id": "apply", "title": "套用你的约束", "kind": "report", "tool": "",
     "risk": "low", "input": "体积阈值 / 排除路径", "output": "实际纳入范围"},
    {"id": "propose", "title": "汇总清理方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "上一步的清单", "output": "预计释放空间 / 文件数 / 耗时"},
    {"id": "confirm", "title": "等待你确认", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案与风险等级", "output": "你的决定"},
]

# 隔离区彻底删除：**唯一物理删除口**。先读隔离区内容（确认卡要逐条列），再等高风险确认。
STEP_PURGE: list[dict] = [
    {"id": "trash", "title": "读取隔离区内容", "kind": "query", "tool": "trash_contents",
     "risk": "low", "input": "隔离区", "output": "待彻底删除的条目与体积"},
    {"id": "apply", "title": "核对删除范围", "kind": "report", "tool": "",
     "risk": "low", "input": "条目清单", "output": "将物理删除的内容"},
    {"id": "propose", "title": "汇总删除方案", "kind": "report", "tool": "estimate_task",
     "risk": "low", "input": "上一步的清单", "output": "将释放空间 / 条目数"},
    {"id": "confirm", "title": "等待你确认（不可恢复）", "kind": "confirm", "tool": "",
     "risk": "low", "input": "方案与风险等级", "output": "你的决定"},
]

PIPELINES = {
    "free_space": STEP_FREE_SPACE,
    "migrate": STEP_MIGRATE,
    "organize": STEP_ORGANIZE,
    "organize_write": STEP_ORGANIZE_WRITE,
    "similar": STEP_SIMILAR,
    "large": STEP_LARGE,
    "purge": STEP_PURGE,
}

# 只是「查一下」的动作走单步，不必套整条流水线
SINGLE_ACTIONS = {
    "scan", "tree", "virtual_preview", "projects_scan", "duplicate_scan",
    "junk_scan", "migrate_plan", "overview", "jobs_list", "trash_list",
    "logs_list", "open_tab", "backup", "trash_restore", "job_cancel", "job_rollback",
    # 2026-10-13「能力放开」新增
    "similar_scan", "search_index", "large_scan",
}

# 目标型动作：用户要的是结果，不是某一次扫描
GOAL_ACTIONS = {
    "junk_clean", "duplicate_clean", "migrate_execute",
    # 2026-10-13 放开：相似图片 / 大文件 / 隔离区彻底删除
    "similar_clean", "large_clean", "trash_purge",
    # 2026-10-14：整理真执行（就地移动到分组目录）
    "virtual_organize",
}


def pick_pipeline(intent: dict) -> str:
    """按意图挑流水线。"""
    action = intent.get("action") or ""
    constraints = intent.get("constraints") or {}
    scope = ((intent.get("scope") or {}).get("path") or "")

    if action in ("migrate_execute", "migrate_plan"):
        return "migrate"

    if action in ("junk_clean", "duplicate_clean", "junk_scan", "duplicate_scan"):
        return "free_space"

    # 2026-10-13「能力放开」：相似图片 / 大文件清理各自一条流水线（分析段换成对应扫描）
    if action in ("similar_clean", "similar_scan"):
        return "similar"

    if action == "large_clean":
        return "large"

    # 隔离区彻底删除：先读内容、再等高 Risk 确认
    if action == "trash_purge":
        return "purge"

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

    # 2026-10-14：整理真执行走「预览 → 确认 → 移动」那条带 confirm 的流水线
    if action == "virtual_organize":
        return "organize_write"

    return ""


def plan_steps(intent: dict) -> list[dict]:
    """产出步骤列表。单步动作也包成一步，让前端只认一种结构。

    2026-10-13「编排放开」：`intent["steps"]` 带了一份显式步骤列表时（LLM 工具调用
    或用户自己拼的），走 `normalize_steps` 做本地校验，而不是只认那几条预设流水线。
    """
    raw = intent.get("steps")
    if isinstance(raw, list) and raw:
        return normalize_steps(raw, intent)

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
    action = intent.get("action") or ""
    # large_scan 本质是"查一下"（large_files 查询工具），别套成 Job——
    # 套了会走到 _run_step 的 job 分支，然后因为没登记而抛"暂不支持"。
    is_query = action == "large_scan"
    return {
        "index": 0,
        "id": action or "step",
        "title": intent.get("goal") or "执行计划",
        "kind": "query" if is_query else "job",
        "tool": "large_files" if is_query else action,
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


# ---------------------------------------------------------------------------
# 自由编排：把外部给的步骤列表洗净成计划树
#
# 2026-10-13「编排放开」。原来只有 6 条预设流水线，模型不能自己组合步骤；
# 现在 intent["steps"] 可以带任意步骤，但**校验全在本地做**，模型说啥都不算数：
#   · 只认已登记的工具（动作 + 查询 + 辅助），不认识的直接丢
#   · 写动作前面**强制**插一个 confirm 步骤 —— plan-first 这条线不可绕过
#   · 至少留一步，否则整条计划作废（退回调用方处理）
# ---------------------------------------------------------------------------

# 允许出现在自由编排里的工具：已登记动作 + 查询工具 + 辅助工具
FREE_STEP_TOOLS: set[str] = set(TOOL_REGISTRY) | set(QUERY_TOOLS) | set(HELPER_TOOLS)

# 查询/辅助工具按 kind=query 跑；其余（会起 Job 的）按 kind=job
_JOB_TOOLS: set[str] = set(TOOL_REGISTRY)


def normalize_steps(raw: list, intent: dict) -> list[dict]:
    """把外部步骤列表洗净成计划树（本地校验，不采信外部判断）。"""
    scope = (intent.get("scope") or {}).get("path") or ""
    target = (intent.get("scope") or {}).get("target") or ""

    cleaned: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or item.get("id") or "").strip()
        if tool not in FREE_STEP_TOOLS:
            continue                      # 模型不能发明工具
        kind = item.get("kind") or ("job" if tool in _JOB_TOOLS else "query")
        if kind not in ("query", "job", "report", "helper"):
            kind = "query" if tool not in _JOB_TOOLS else "job"
        cleaned.append({
            "index": len(cleaned),
            "id": tool,
            "title": str(item.get("title") or TOOL_REGISTRY.get(tool, {}).get("label")
                         or QUERY_TOOLS.get(tool, {}).get("label") or tool),
            "kind": kind,
            "tool": tool,
            "risk": "high" if tool in WRITE_ACTIONS and tool == "trash_purge" else (
                "medium" if tool in WRITE_ACTIONS else "low"),
            "input": scope if kind != "helper" else (target or scope),
            "output": str(item.get("output") or ""),
            "status": "pending",
            "note": "",
            "digest": "",
            "started_at": None,
            "finished_at": None,
        })

    if not cleaned:
        return []

    # 写动作前面强制插 confirm：这是 plan-first 的最后一道闸，绕过它就等于"一句话直接删"
    out: list[dict] = []
    inserted = False
    for step in cleaned:
        if step["kind"] == "job" and step["tool"] in WRITE_ACTIONS and not inserted:
            out.append({
                "index": len(out), "id": "confirm", "title": "等待你确认",
                "kind": "confirm", "tool": "", "risk": "low",
                "input": "方案与风险等级", "output": "你的决定",
                "status": "pending", "note": "", "digest": "",
                "started_at": None, "finished_at": None,
            })
            inserted = True
        step["index"] = len(out)
        out.append(step)
    return out


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

# 隔离区彻底删除：**唯一物理删除口、不可回滚**，所以单独一个模板、风险标高。
EXECUTE_STEPS_PURGE: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "high", "tool": "trash_purge",
     "title": "彻底删除隔离区内容（不可恢复）", "input": "已确认的隔离区条目", "output": "实际释放的空间"},
]

# 相似图片 / 大文件：同走 clean 通道，但步骤标题要说清动的是什么
EXECUTE_STEPS_SIMILAR: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "medium", "tool": "similar_clean",
     "title": "分批清理相似图片（确认后不可恢复）", "input": "已确认的清单", "output": "实际处理结果"},
]

EXECUTE_STEPS_LARGE: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "medium", "tool": "large_clean",
     "title": "分批清理大文件（确认后不可恢复）", "input": "已确认的清单", "output": "实际处理结果"},
]

# 2026-10-14：就地整理 —— 只移动不删除，风险 medium（改结构不改数据），任务页可整体回滚
EXECUTE_STEPS_ORGANIZE: list[dict] = [
    {"id": "execute", "kind": "job", "risk": "medium", "tool": "virtual_organize",
     "title": "移动到分组子目录", "input": "已确认的整理清单", "output": "移动结果（可整体回滚）"},
]

_EXECUTE_TEMPLATES = {
    "migrate_execute": EXECUTE_STEPS_MIGRATE,
    "trash_purge": EXECUTE_STEPS_PURGE,
    "similar_clean": EXECUTE_STEPS_SIMILAR,
    "large_clean": EXECUTE_STEPS_LARGE,
    "virtual_organize": EXECUTE_STEPS_ORGANIZE,
}

# 汇报步骤：每条计划只加一次，放在所有执行步之后
_REPORT_STEP = {"id": "report", "kind": "report", "risk": "low", "tool": "",
                "title": "汇报结果", "input": "执行结果", "output": "总结与下一步"}


def execution_steps(intent: dict, proposal: dict) -> list[dict]:
    """用户确认后追加的执行步骤。

    注意：这些步骤调用的仍是既有的 clean / migrate / purge 服务——隔离区、分批、
    回滚都还是那一套，Agent 只是把它排进了计划树。

    2026-10-13「编排放开」：一条计划可以带**多个执行目标**（`intent["actions"]`），
    每个目标各出一段执行步，汇报步骤只加一次。
    """
    goals = [a for a in (intent.get("actions") or []) if a]
    if not goals:
        goals = [proposal.get("action") or intent.get("action") or ""]

    steps: list[dict] = []
    for action in goals:
        template = _EXECUTE_TEMPLATES.get(action, EXECUTE_STEPS)
        for item in template:
            if item["id"] == "report":
                continue                  # 汇报统一放最后，别每个目标都加一份
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
    steps.append(dict(_REPORT_STEP, index=0, status="pending", note="", digest="",
                      started_at=None, finished_at=None))
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
    "virtual_organize": "执行整理",
    # 2026-10-13「能力放开」新增
    "similar_scan": "查找相似图片",
    "similar_clean": "清理相似图片",
    "large_clean": "清理大文件",
    "large_scan": "查找大文件",
    "search_index": "建立全文索引",
    "trash_purge": "彻底删除隔离区内容",
}

# 「腾出空间」这类目标一旦成立，对外就按清理来表述——
# 但清单仍会先摊出来等确认，不存在「一句话直接删」的路径。
GOAL_UPGRADE = {
    "junk_scan": "junk_clean", "scan": "junk_clean", "": "junk_clean",
    # 2026-10-13：扫描类意图升级成对应的清理目标
    "similar_scan": "similar_clean", "duplicate_scan": "duplicate_clean",
}

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
    # 2026-10-13「能力放开」新增的三个来源
    similar = (step_results.get("similar") or {}).get("result") or {}
    trash = (step_results.get("trash") or {}).get("data") or {}
    # 2026-10-14：整理真执行的清单来源（预览步 id = preview）
    preview = (step_results.get("preview") or {}).get("result") or {}

    items: list[dict] = []
    freed = 0
    file_count = 0

    # 2026-10-13「编排放开」：一条计划可以带多个执行目标（自由编排），
    # 各分支改成**累加**而不是覆盖 —— 单目标时结果与原来完全一致。
    goals = [a for a in (intent.get("actions") or []) if a] or [action]

    if any(a in ("junk_clean", "junk_scan") for a in goals):
        # 2026-10-14「清理级别放宽」：安全级 + 建议确认级都进清单；
        # 谨慎级（可能是个人文件）仍然排除 —— include_caution 保持 False。
        # 无论哪一级，都要过确认卡逐条列清单，不存在"一句话直接删"。
        picked: list[dict] = []
        for level in junk.get("levels") or []:
            if level.get("level") not in ("safe", "confirm"):
                continue
            for item in level.get("items") or []:
                picked.append({
                    "path": item.get("path"), "is_dir": bool(item.get("is_dir")),
                    "size": item.get("size") or 0, "rule": item.get("rule"),
                    "level": level.get("level") or "safe",
                })
        items.extend(picked)
        freed += sum(item["size"] for item in picked)
        file_count += len(picked)

    if any(a in ("duplicate_clean", "duplicate_scan") for a in goals):
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
        freed += int(dup.get("reclaimable_size") or 0)
        file_count += sum(1 for i in items if i.get("rule") == "duplicate")

    if "migrate_execute" in goals:
        moved = [
            {"source": entry.get("source"), "target": entry.get("target"), "size": entry.get("size") or 0}
            for entry in (migrate.get("plan") or [])
        ]
        items.extend(moved)
        freed += sum(item.get("size") or 0 for item in moved)
        file_count += len(moved)

    # 2026-10-14：就地整理 —— 清单直接取自预览的 buckets（source → preview_target）。
    # 这是**移动**不是释放空间，所以不计入 freed；level 记 confirm（动的是你的文件）。
    if "virtual_organize" in goals:
        organized = [
            {"source": item.get("path"), "target": item.get("preview_target"),
             "size": item.get("size") or 0, "rule": "organize", "level": "confirm"}
            for bucket in preview.get("buckets") or []
            for item in bucket.get("items") or []
        ]
        items.extend(organized)
        freed += sum(i.get("size") or 0 for i in organized)   # 这里代表"移动体积"，不是释放
        file_count += len(organized)

    # ---- 2026-10-13「能力放开」新增 ----
    # 相似图片：每组保留 keep（或最新）那一张，其余列为候选。
    # ★ level 记 "confirm" 而不是 "safe"：这是用户数据、不是垃圾，
    #   必须让人在确认卡里逐张过一遍——"只清安全级"的谨慎取向在这里照样成立。
    if any(a in ("similar_clean", "similar_scan") for a in goals):
        keep = constraints.get("keep")
        for group in similar.get("groups") or []:
            files = list(group.get("files") or [])
            keep_path = group.get("keep")
            newest = None
            for file in files:
                if not newest or str(file.get("mtime") or "") > str(newest.get("mtime") or ""):
                    newest = file
            for file in files:
                if file.get("path") == keep_path:
                    continue
                if keep == "newest" and newest and file.get("path") == newest.get("path"):
                    continue
                items.append({
                    "path": file.get("path"), "is_dir": False,
                    "size": file.get("size") or 0, "rule": "similar", "level": "confirm",
                })
        freed += sum(i["size"] for i in items if i.get("rule") == "similar")
        file_count += sum(1 for i in items if i.get("rule") == "similar")

    # 大文件：清单本身就是重点，同样按用户数据处理（level=confirm）
    if any(a in ("large_clean", "large_scan") for a in goals):
        for row in large.get("rows") or []:
            items.append({
                "path": row.get("path"), "is_dir": False,
                "size": row.get("size") or 0, "rule": "large", "level": "confirm",
            })
        freed += sum(i["size"] for i in items if i.get("rule") == "large")
        file_count += sum(1 for i in items if i.get("rule") == "large")

    # 隔离区彻底删除：条目带 id（执行段按 id 删），level=danger
    if "trash_purge" in goals:
        for entry in trash.get("entries") or []:
            items.append({
                "id": entry.get("id"),
                "path": entry.get("path") or entry.get("name"),
                "is_dir": False, "size": entry.get("size") or 0,
                "rule": "trash_purge", "level": "danger",
            })
        freed += sum(i["size"] for i in items if i.get("rule") == "trash_purge")
        file_count += sum(1 for i in items if i.get("rule") == "trash_purge")

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
    if "junk_clean" in goals and not any(
        i.get("rule") and i.get("level") in ("safe", "confirm") for i in items
    ):
        missing.append("没有可清理项（安全级 / 建议确认级都为空）")
    if "migrate_execute" in goals and not items:
        missing.append("搬移清单为空")
    if any(a in ("similar_clean", "similar_scan") for a in goals) and not items:
        missing.append("没有可清理的相似图片（每组只保留一张，剩下的才是候选）")
    if any(a in ("large_clean", "large_scan") for a in goals) and not items:
        missing.append("没有达到体积阈值的大文件")
    if "trash_purge" in goals and not items:
        missing.append("隔离区是空的")
    if "virtual_organize" in goals and not items:
        missing.append("没有可整理的文件（预览为空）")

    executable = any(a in ("junk_clean", "junk_scan", "duplicate_clean",
                           "duplicate_scan", "migrate_execute",
                           # 2026-10-13「能力放开」新增
                           "similar_clean", "similar_scan", "large_clean", "large_scan",
                           "trash_purge",
                           # 2026-10-14：整理真执行
                           "virtual_organize") for a in goals) and bool(items)

    # 迁移 / 整理不是「释放空间」，是「换个地方放」——措辞跟着动作走，别让用户误会
    is_migrate = action == "migrate_execute"
    is_organize = action == "virtual_organize"

    return {
        "action": action or ("junk_clean" if items else ""),
        "label": ACTION_LABELS.get(action or "", "执行方案"),
        "executable": executable,
        "freed_label": "搬运体积" if is_migrate else ("整理体积" if is_organize else "预计释放"),
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

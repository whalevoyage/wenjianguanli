"""Agent 多步骤任务的执行体（第三阶段）。

一个 agent_task 任务把只读分析段跑完，然后**挂起**在「等待确认」：
不是跑完就结束，也不是直接开始删文件——而是把方案、预计数字、风险
一起摆出来，等用户决定。

设计上的两条硬线：
1. 这个 worker 里**没有任何写操作**。它只调用只读服务（scan / junk_scan /
   duplicate_scan / migrate dry_run）与本地查询工具。
2. 用户确认后的真正执行仍回到 clean / migrate 任务——隔离区、分批、回滚
   这些既有保障一条都不少。Agent 不新开写通道。
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import config, fsutil, store
from . import agent_intent, agent_planner
from .agent_tools import run_tool

# 每个子步骤最多占用整条流水线的进度比例由步骤数均分
PAUSE_STEP_IDS = {"confirm"}


class StepContext:
    """子步骤上下文：把服务的进度汇报折算进整条流水线的百分比。

    既有服务（junk / duplicate / migrate …）都只认 ctx.params 与那几个汇报方法，
    所以这里做一层薄包装即可复用它们，不必改动任何服务实现。
    """

    def __init__(self, parent, step: dict, params: dict, index: int, total: int):
        self._parent = parent
        self._step = step
        self._params = params
        self._index = max(0, index)
        self._total = max(1, total)

    @property
    def job_id(self) -> str:
        return self._parent.job_id

    @property
    def job(self) -> dict:
        return self._parent.job

    @property
    def params(self) -> dict:
        return self._params

    def cancelled(self) -> bool:
        return self._parent.cancelled()

    def check_cancel(self) -> None:
        self._parent.check_cancel()

    def _publish(self, message: str, fraction: float | None, path: str = "") -> None:
        """上报当前步的进度。

        `fraction` 为 `None` 表示「这一步没法量化」（query / helper 类步骤没有分母）。
        这时**不编一个半步的假比例**，改为按「已完成几步」报百分比，并打上
        `indeterminate` 让前端走不确定态（条子来回扫动 + 不显示死数字）——
        真正在动的信息由 message 里的实时条目数/秒数承担。
        有分母时才把真实比例折进当前步（`(已完成步 + 当前步比例) / 总步数`）。
        """
        title = self._step.get("title") or ""
        text = f"第 {self._index + 1}/{self._total} 步 · {title}｜{message}"
        if fraction is None:
            percent = int(self._index / max(self._total, 1) * 100)
            self._parent._manager.update(
                self.job_id,
                message=text,
                percent=min(percent, 99),
                current_path=path,
                indeterminate=True,
            )
            return
        within = max(0.0, min(fraction, 0.99))
        percent = int((self._index + within) / self._total * 100)
        self._parent._manager.update(
            self.job_id,
            message=text,
            percent=min(percent, 99),
            current_path=path,
            indeterminate=False,
        )

    def phase(self, index: int, message: str = "") -> None:
        # 子服务的 phase 只是文字提示；没有分母，别硬凑比例
        self._publish(message or "进行中", None)

    def progress(self, message: str, current: int, total: int | None = None, path: str = "") -> None:
        # 有 total 才是真实比例；没有就走不确定态（不写死 0.5 假装走到半步）
        fraction = None
        if total:
            fraction = min(current / max(total, 1), 0.95)
        self._publish(message, fraction, path)

    def tick(self, message: str, current: int, total: int | None = None, path: str = "") -> None:
        self.progress(message, current, total, path)

    def set_plan(self, plan: list[dict]) -> None:
        self._parent.set_plan(plan)

    def add_action(self, action: dict) -> None:
        self._parent.add_action(action)

    def add_actions(self, actions: list[dict]) -> None:
        self._parent.add_actions(actions)

    def waiting(self, message: str) -> None:
        self._publish(message, 0.95)

    def sleep(self, seconds: float = 0.02) -> None:
        fsutil.sleep_short(seconds)


# ---------------------------------------------------------------------------
# 单步执行
# ---------------------------------------------------------------------------

def _step_params(step: dict, intent: dict) -> dict:
    """按步骤要调用的服务组装参数。

    以 tool 为准而不是以 step id 为准——单步计划里 id 直接就是动作名，
    按 id 判会漏掉参数，退化成「路径不能为空」。
    """
    constraints = intent.get("constraints") or {}
    params = intent.get("params") or {}
    scope = (intent.get("scope") or {}).get("path") or ""
    target = (intent.get("scope") or {}).get("target") or ""
    excludes = list(dict.fromkeys(
        (constraints.get("exclude_names") or []) + list(config.IMPLICIT_EXCLUDES)
    ))
    tool = step.get("tool") or ""
    min_size = params.get("min_size_mb", 0) * 1024 * 1024 if params.get("min_size_mb") else 0

    if tool in ("junk_scan", "duplicate_scan", "scan"):
        return {
            "path": scope,
            "excludes": excludes,
            "rules": None,
            "include_caution": False,
            "stale_days": 30,
            "stale_min_size": 0,
        }
    if tool == "virtual_preview":
        return {
            "path": scope,
            "group_by": params.get("group_by") or "type",
            "min_size": min_size,
            "excludes": excludes,
        }
    if tool in ("migrate_plan", "migrate_execute"):
        return {
            "source_dir": scope,
            "target_dir": target,
            "excludes": excludes,
            "dry_run": tool == "migrate_plan",
            # move 会删源、copy 不会——用户在「修改方案」里选的就是这个值
            "mode": constraints.get("migrate_mode") or "move",
            "min_size": min_size,
            "include_ext": params.get("extensions") or [],
        }
    # ---- 2026-10-13「能力放开」新增 ----
    if tool == "similar_scan":
        # min_size 服务层按**字节**收；Agent 侧给 KB，更符合人的说法
        min_kb = params.get("min_size_kb")
        return {
            "path": scope,
            "threshold": max(0, min(int(params.get("threshold") or 10), 32)),
            "min_size": int(min_kb) * 1024 if min_kb else None,
            "rotations": bool(params.get("rotations")),
            "excludes": excludes,
        }
    if tool == "search_index":
        paths = params.get("paths") or ([scope] if scope else [])
        return {"paths": list(paths), "excludes": excludes, "max_depth": params.get("max_depth") or 0}
    if tool == "trash_purge":
        return {"ids": list(params.get("ids") or []), "all": bool(params.get("all"))}
    if tool in ("similar_clean", "large_clean"):
        # 两种清理都复用 /api/clean：isolate（默认，可还原）或 recycle（系统回收站）
        return {"mode": params.get("mode") or "isolate"}
    return {}


def _run_step(step: dict, intent: dict, parent, index: int, total: int, items: list[dict] | None = None) -> dict:
    """执行一步，返回 payload（结构由 kind 决定）。"""
    kind = step.get("kind")
    tool = step.get("tool") or ""
    constraints = intent.get("constraints") or {}
    scope = (intent.get("scope") or {}).get("path") or ""
    target = (intent.get("scope") or {}).get("target") or ""
    items = items or []

    if kind == "query":
        if tool == "large_files":
            return run_tool("large_files", {
                "path": scope,
                "min_mb": constraints.get("min_size_mb") or 100,
                "excludes": constraints.get("exclude_names"),
            })
        if tool == "drive_status":
            return run_tool("drive_status", {"path": scope})
        return run_tool(tool, {"path": scope, "excludes": constraints.get("exclude_names")})

    if kind == "helper":
        if tool == "check_disk_space":
            return run_tool("check_disk_space", {"path": target or scope})
        if tool == "check_path_protected":
            return run_tool("check_path_protected", {"path": scope})
        if tool == "check_target_conflict":
            return run_tool("check_target_conflict", {"source_dir": scope, "target_dir": target})
        if tool == "check_path_exists":
            return run_tool("check_path_exists", {"path": scope})
        return run_tool(tool, {})

    if kind == "report":
        return {}

    if kind == "job":
        from . import clean, duplicates, index, junk, migrate, scan, similar, virtual

        step_params = _step_params(step, intent)
        sub = StepContext(parent, step, step_params, index, total)

        if tool == "duplicate_scan":
            return {"result": duplicates.run_duplicate_scan(sub)}
        if tool == "junk_scan":
            return {"result": junk.run_junk_scan(sub)}
        if tool == "migrate_plan":
            return {"result": migrate.run_migrate(sub)}
        if tool == "scan":
            return {"result": scan.run_scan(sub)}
        if tool == "virtual_preview":
            return {"result": virtual.run_virtual(sub)}
        # ---- 2026-10-13「能力放开」新增 ----
        if tool == "similar_scan":
            return {"result": similar.run_similar_scan(sub)}
        if tool == "search_index":
            return {"result": index.run_index(sub)}

        # ---- 执行段：复用既有写通道，隔离区 / 分批 / 回滚一个不少 ----
        # similar_clean / large_clean 与 clean 同一条通道：差别只在清单来源（planner 已分摊）
        if tool in ("clean", "similar_clean", "large_clean"):
            if not items:
                return {"result": {"done": 0, "freed": 0, "errors": [], "skipped": "清单为空"}}
            sub._params = {"items": items, "mode": step_params.get("mode") or "isolate"}
            return {"result": clean.run_clean(sub)}
        if tool == "migrate_execute":
            if not items:
                return {"result": {"moved": 0, "errors": [], "skipped": "清单为空"}}
            sub._params = {
                **step_params,
                "dry_run": False,
                "mode": step_params.get("mode") or "move",
                "items": items,
                "excludes": step_params.get("excludes"),
            }
            return {"result": migrate.run_migrate(sub)}
        if tool == "trash_purge":
            # ★ 全项目唯一的物理删除口：不可回滚（job 的回滚帮不上），
            #   所以它走的是"确认卡逐条列出 + 最高风险等级"那道闸，而不是静默执行。
            # id 优先取方案清单里的（确认卡上列的就是那些），其次才是参数里显式给的。
            ids = [item.get("id") for item in items if item.get("id")]
            if not ids:
                ids = list(step_params.get("ids") or [])
            if step_params.get("all") and not ids:
                ids = [e.get("id") for e in (clean.list_trash().get("entries") or []) if e.get("id")]
            if not ids:
                return {"result": {"purged_count": 0, "freed_size_text": "0 B", "skipped": "隔离区是空的"}}
            return {"result": clean.purge_trash(ids)}

        raise ValueError(f"多步骤流水线暂不支持的任务：{tool}")

    if kind == "confirm":
        return {}

    return {}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

RECENT_PATHS_KEEP = 8


def _remember_path(intent: dict, conversation_id: str = "") -> None:
    """只读分析段跑完 = 用户确实在用这个目录，记进**本会话**的最近路径。

    刻意**不写全局 prefs**：那是"随手攒出来的使用痕迹"，写全局会让新对话
    "记得"你在别的对话里逛过哪些目录 —— 也就是「每个对话共用一个记忆」。
    用户手设的常用路径（偏好面板里加的）仍然留在全局 prefs 里，那是设置不是痕迹。

    这一步失败绝不能影响主任务——记忆只是锦上添花。
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return
    scope = str((intent.get("scope") or {}).get("path") or "").strip()
    # 盘符根没有记忆价值，反而会挤掉真正用到的目录（与 agent.add_common_path 同一条规则）
    if not scope or len(scope) <= 3:
        return
    try:
        if Path(scope).parent == Path(scope):
            return
    except (OSError, ValueError):
        return

    try:
        state = store.get_conversation_state(cid)
        items = [item for item in (state.get("recent_paths") or [])
                 if isinstance(item, dict) and item.get("path") != scope]
        items.insert(0, {
            "path": scope,
            "note": (intent.get("goal") or "")[:40],
            "at": fsutil.now_iso(),
        })
        store.save_conversation_state(cid, {"recent_paths": items[:RECENT_PATHS_KEEP]})
    except (OSError, ValueError):
        pass


def _remember_strategy(intent: dict, proposal: dict, summary: dict) -> None:
    """执行成功 → 把这条流程记成常用策略，之后能在偏好面板里一键重跑。"""
    try:
        from . import agent

        agent.add_clean_strategy(
            proposal.get("action") or intent.get("action") or "",
            (intent.get("scope") or {}).get("path") or "",
            intent.get("goal") or "",
        )
    except (OSError, ValueError):
        pass


def run_agent_task(ctx) -> dict:
    """跑一条流水线。

    mode="analyze"：跑只读分析段，最后停在「等待确认」（抛 JobPaused）。
    mode="execute"：沿用同一条任务，保留已完成的分析步骤，补上执行段。
                    「继续执行」因此不会另开一张任务卡，撤销也只需回滚同一个 job。
    """
    params = ctx.params
    # 前端传 {intent: plan.intent, plan_params: plan.params, steps, mode}
    intent = dict(params.get("intent") or {})
    intent["params"] = params.get("plan_params") or intent.get("params") or {}
    mode = params.get("mode") or "analyze"
    proposal = params.get("proposal") or ctx.job.get("proposal") or {}

    if mode == "execute":
        steps = [dict(item) for item in (ctx.job.get("steps") or [])]
        if not steps:
            steps = [dict(item) for item in (params.get("steps") or agent_planner.plan_steps(intent))]
        for step in steps:
            if step.get("kind") == "confirm":
                step["status"] = "done"
                step["digest"] = "已确认"
                step["finished_at"] = step.get("finished_at") or fsutil.now_iso()
        pending = agent_planner.execution_steps(intent, proposal)
        start_index = len(steps)
        for offset, step in enumerate(pending):
            step["index"] = start_index + offset
            steps.append(step)
    else:
        steps = [dict(item) for item in (params.get("steps") or agent_planner.plan_steps(intent))]

    if not steps:
        raise ValueError("这条指令没有可执行的多步骤方案")

    total = len(steps)
    scope = (intent.get("scope") or {}).get("path") or ""
    items = proposal.get("items") or []

    ctx._manager.update(
        ctx.job_id, steps=steps, intent=intent, step_total=total,
        message=f"{'继续执行' if mode == 'execute' else '开始执行'} {total} 步方案",
        force=True,
    )

    step_results: dict = {}
    started = time.time()

    for index, step in enumerate(steps):
        ctx.check_cancel()
        if step.get("status") in ("done", "skipped"):
            continue          # 续跑时跳过已完成的分析步骤

        if step.get("kind") == "confirm":
            if mode == "execute":
                step["status"] = "done"
                step["digest"] = "已确认"
                step["finished_at"] = fsutil.now_iso()
                ctx._manager.update(ctx.job_id, steps=steps, force=True)
                continue
            # 只读分析段跑完 → 先摊方案，再挂起等确认
            proposal = agent_planner.build_proposal(intent, step_results, mode)
            steps[index].update({
                "status": "waiting",
                "note": "等你决定后再动文件",
                "digest": f"预计释放 {proposal['freed_text']}",
                "started_at": fsutil.now_iso(),
            })
            # 回填「套用约束」与「汇总方案」两步的真实结论
            _backfill(step_results, steps, proposal, intent)
            _remember_path(intent, params.get("conversation_id") or "")
            ctx._manager.update(
                ctx.job_id,
                steps=steps, step_index=index, proposal=proposal,
                message="方案已生成，等待你确认", force=True,
            )
            raise _pause()

        step["status"] = "running"
        step["started_at"] = fsutil.now_iso()
        ctx._manager.update(
            ctx.job_id, steps=steps, step_index=index,
            message=f"第 {index + 1}/{total} 步 · {step['title']}", force=True,
        )

        try:
            payload = _run_step(step, intent, ctx, index, total, items)
            step_results[step["id"]] = payload
            step["status"] = "done"
            step["digest"] = agent_planner.digest_of(step["id"], payload or {})
        except Exception as exc:  # noqa: BLE001
            step["status"] = "failed"
            step["note"] = fsutil.human_error(exc)
            step["digest"] = step["note"]
            step["finished_at"] = fsutil.now_iso()
            ctx._manager.update(ctx.job_id, steps=steps, force=True)
            raise

        step["finished_at"] = fsutil.now_iso()
        ctx._manager.update(ctx.job_id, steps=steps, force=True)

    if mode == "execute":
        summary = _summarize_execution(step_results, proposal)
        if not summary.get("errors"):
            _remember_strategy(intent, proposal, summary)
        for step in steps:
            if step.get("id") == "report":
                step["digest"] = summary.get("message") or "已完成"
                step["finished_at"] = step.get("finished_at") or fsutil.now_iso()
        ctx._manager.update(ctx.job_id, steps=steps, force=True)
        return {
            "steps": steps,
            "intent": agent_intent.intent_view(intent),
            "proposal": proposal,
            "mode": mode,
            "scope": scope,
            "duration_seconds": round(time.time() - started, 2),
            "step_total": total,
            "completed": sum(1 for item in steps if item.get("status") == "done"),
            "summary": summary,
        }

    # 没有 confirm 步骤（单步只读动作）→ 直接给一个方案就结束
    proposal = agent_planner.build_proposal(intent, step_results, mode)
    return {
        "steps": steps,
        "intent": agent_intent.intent_view(intent),
        "proposal": proposal,
        "mode": mode,
        "scope": scope,
        "duration_seconds": round(time.time() - started, 2),
        "step_total": total,
        "completed": sum(1 for item in steps if item.get("status") == "done"),
    }


def _backfill(step_results: dict, steps: list[dict], proposal: dict, intent: dict) -> None:
    """把最后两步的结论补进计划树。

    「套用你的约束」「汇总方案」本身不产生数据（它们是本地折算），
    只有等到 proposal 出来才知道实际纳入范围与预计数字，所以在这里回填。
    """
    constraints = intent.get("constraints") or {}
    tags = constraints.get("exclude_tags") or []
    excludes = constraints.get("exclude_paths") or []

    for step in steps:
        if step.get("id") == "apply":
            bits = []
            if tags:
                bits.append("排除 " + "、".join(tags))
            if constraints.get("min_size_mb"):
                bits.append(f"仅 ≥ {constraints['min_size_mb']} MB")
            if constraints.get("extensions"):
                bits.append("仅 " + "/".join(constraints["extensions"][:6]))
            bits.append("只取安全级" if proposal.get("safe_only", True) else "含建议确认级")
            step["digest"] = " · ".join(bits)
            step["status"] = "done"
            step["finished_at"] = step.get("finished_at") or fsutil.now_iso()

        if step.get("id") == "propose":
            step["status"] = "done"
            step["digest"] = (f"{proposal.get('freed_label') or '预计释放'} {proposal['freed_text']} · "
                              f"{proposal['item_count']} 项 · {proposal['duration_text']}")
            step["finished_at"] = step.get("finished_at") or fsutil.now_iso()

    if excludes:
        proposal["exclude_note"] = "已排除 " + "、".join(excludes[:5])


def _summarize_execution(step_results: dict, proposal: dict) -> dict:
    """执行段的本地总结：处理了多少、成功多少、失败多少、下一步做什么。"""
    result = (step_results.get("execute") or {}).get("result") or {}
    is_migrate = (proposal.get("action") or "") == "migrate_execute"

    done = int(result.get("done") or result.get("cleaned_count")
               or result.get("copied_count") or result.get("moved") or 0)
    verified = int(result.get("verified_count") or 0)
    deleted = int(result.get("deleted_count") or 0)
    freed = int(result.get("freed") or result.get("freed_size")
                or result.get("total_size") or 0)
    errors = result.get("errors") or []

    facts = []
    if is_migrate:
        if done:
            facts.append(f"搬移 {done} 项")
        if verified:
            facts.append(f"校验通过 {verified} 项")
        if deleted:
            facts.append(f"已删源 {deleted} 项")
    elif done:
        facts.append(f"处理 {done} 项")
    if freed:
        facts.append(("搬运 " if is_migrate else "释放 ") + fsutil.format_size(freed))
    if errors:
        facts.append(f"{len(errors)} 条错误")

    next_steps = []
    if errors:
        next_steps.append("看一下错误明细，多半是文件被占用或权限不足")
    if is_migrate:
        next_steps.append("需要撤回就回滚这次任务——已复制的会被清掉，源文件会回到原位")
    else:
        next_steps.append("需要撤回就回滚这次任务，或去隔离区单独恢复")

    return {
        "message": ("搬运完成" if is_migrate else "执行完成") + ("：" + "，".join(facts) if facts else ""),
        "facts": facts,
        "done": done,
        "moved": done if is_migrate else 0,
        "freed": freed,
        "freed_text": fsutil.format_size(freed) if freed else "",
        "errors": errors[:20],
        "next_steps": next_steps,
        "planned_items": len(proposal.get("items") or []),
    }


def _pause():
    """构造 JobPaused；方案已写进 job["proposal"]，异常本身不带载荷。"""
    from ..jobs import JobPaused

    return JobPaused()


def resume_payload(intent: dict, steps: list[dict], proposal: dict, mode: str = "execute") -> dict:
    """用户点「继续执行」时，前端据此创建真正的执行任务。

    这里刻意不返回任何可写指令——只描述「该调哪个接口、用什么清单」，
    执行仍由前端的既有确认流（clean / migrate）发起。
    """
    return {
        "intent": intent,
        "steps": steps,
        "proposal": proposal,
        "mode": mode,
        "action": proposal.get("action"),
        "items": proposal.get("items") or [],
        "params": intent.get("params") or {},
        "safe_only": proposal.get("safe_only", True),
    }

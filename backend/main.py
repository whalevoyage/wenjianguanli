"""FilePilot Lite API。

路由层只做三件事：校验入参、创建任务、把请求交给 services。
所有耗时操作都返回一个 job，前端通过 /api/jobs/{id}/stream 订阅进度。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, diagnostics, fsutil, permissions, scheduler, store
from .jobs import JOB_TYPES, JobContext, manager
from .services import (
    agent, agent_task, backup, clean, duplicates, junk, migrate, overview, projects, scan, virtual,
)

app = FastAPI(title="FilePilot Lite API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
scheduler.start_runner()

WEB_DIR = config.APP_ROOT / "web"


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    path: str
    excludes: list[str] | None = None
    max_depth: int | None = Field(default=None, ge=0, le=50)
    stale_days: int = Field(default=30, ge=0, le=36500)
    stale_min_size: int = Field(default=0, ge=0)


class TreeRequest(BaseModel):
    path: str
    depth: int = Field(default=config.TREE_DEFAULT_DEPTH, ge=0, le=20)
    excludes: list[str] | None = None


class DuplicateRequest(BaseModel):
    path: str
    excludes: list[str] | None = None
    min_size: int = Field(default=1, ge=0)


class JunkRequest(BaseModel):
    path: str
    excludes: list[str] | None = None
    rules: list[str] | None = None
    include_caution: bool = False


class VirtualRequest(BaseModel):
    path: str
    group_by: str = "type"
    excludes: list[str] | None = None
    min_size: int = 0
    max_depth: int = 0
    limit: int = Field(default=config.VIRTUAL_ITEM_LIMIT, ge=1, le=100000)


class VirtualExportRequest(BaseModel):
    root: str = ""
    group_by: str = "type"
    items: list[dict[str, Any]] = Field(default_factory=list)


class ProjectRequest(BaseModel):
    path: str
    excludes: list[str] | None = None
    max_depth: int = Field(default=0, ge=0, le=50)
    zombie_days: int = Field(default=config.ZOMBIE_DAYS, ge=0, le=36500)
    limit: int = Field(default=config.PROJECT_LIMIT, ge=1, le=100000)


class MigrateRequest(BaseModel):
    source_dir: str
    target_dir: str
    excludes: list[str] | None = None
    dry_run: bool = True
    mode: str = "move"
    create_junction: bool = False
    min_size: int = 0
    max_items: int = Field(default=20000, ge=1, le=100000)
    rate_limit: float = Field(default=0.02, ge=0, le=10)
    items: list[dict[str, Any]] | None = None


class CleanItem(BaseModel):
    path: str
    is_dir: bool = False
    rule: str = ""
    size: int = Field(default=0, ge=0)


class CleanRequest(BaseModel):
    items: list[CleanItem] = Field(default_factory=list)
    mode: str = clean.MODE_ISOLATE


class BackupRequest(BaseModel):
    source_dir: str
    dest_dir: str = ""
    excludes: list[str] | None = None


class TrashRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class ConversationTitleRequest(BaseModel):
    title: str = Field(default="", max_length=60)


class CancelJobRequest(BaseModel):
    """取消任务时带上调用来源，写进审计日志，便于事后追溯。"""
    reason: str = ""


class SafetySettingsRequest(BaseModel):
    """切换权限档位。full 必须带 confirm=True。"""
    mode: str
    confirm: bool = False


class AgentSettingsRequest(BaseModel):
    """两个字段都可省略；传空串 = 清除该字段（回到环境变量/默认值）。"""
    api_key: str | None = None
    model: str | None = None


class RecipeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    action: dict[str, Any] = Field(default_factory=dict)


class ScheduleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(default=10080, ge=5, le=525600)
    next_run_at: str | None = None


class AgentPlanRequest(BaseModel):
    """计划预览：只拆步骤，不创建任务。"""
    message: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentTaskRequest(BaseModel):
    """启动多步骤 Agent 任务。

    intent  = plan.intent（统一意图结构）
    params  = plan.params（动作参数）
    steps   = 可省略；省略时由后端按 intent 现拆
    """
    intent: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] | None = None
    mode: str = "analyze"
    title: str = ""


class AgentPrefsRequest(BaseModel):
    prefs: dict[str, Any] = Field(default_factory=dict)


class AgentReviseRequest(BaseModel):
    """就地修改方案：只传改过的条件键，没传的沿用原值。"""
    constraints: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 公共
# ---------------------------------------------------------------------------

def _friendly(exc: BaseException) -> HTTPException:
    return HTTPException(status_code=400, detail=fsutil.human_error(exc))


def _launch(job_type: str, title: str, target: str, params: dict, worker: Callable[[JobContext], Any]) -> dict:
    # 「只读」档在入口就拒，不要等任务跑起来再逐项失败。
    # 例外：migrate 的 dry_run 只生成计划（清单 CSV 写在 .filepilot/reports），不动用户文件。
    if job_type in config.WRITE_JOB_TYPES and permissions.current() == "readonly":
        planning_only = job_type == "migrate" and bool(params.get("dry_run"))
        if not planning_only:
            raise HTTPException(status_code=400, detail="当前是「只读」档：只允许扫描、预览与生成计划")
    job = manager.create(job_type, title, target, params)
    manager.start(job["id"], worker)
    # 用户实际用过的目录记进 Agent 的常用路径（第五阶段）。
    # 这里是「副作用」，任何异常都不能影响任务本身。
    try:
        agent.add_common_path(target)
    except (OSError, ValueError, TypeError):
        pass
    return {"job_id": job["id"], "job": manager.get(job["id"])}


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "filepilot-lite", "version": "2.0.0", "time": fsutil.now_iso()}


@app.get("/api/diagnostics")
def get_diagnostics():
    return diagnostics.check()


@app.get("/api/config")
def get_config():
    return {
        "drives": config.drive_letters(),
        "excludes": config.DEFAULT_EXCLUDES,
        "junk_rules": [
            {"id": rule["id"], "name": rule["name"], "level": rule["level"], "desc": rule["desc"]}
            for rule in config.JUNK_RULES
        ],
        "levels": config.JUNK_LEVEL_META,
        "safety": {
            "protected": [str(path) for path in config.SOFT_PROTECTED_DIRS],
            "self_protected": [str(path) for path in config.SELF_PROTECTED_DIRS],
            "readonly": config.SYSTEM_READONLY_PREFIXES,
            "permission": permissions.describe(),
        },
        "trash_dir": str(config.TRASH_DIR),
        "large_file_threshold": config.LARGE_FILE_THRESHOLD,
        "virtual_modes": config.VIRTUAL_GROUP_MODES,
        "job_types": JOB_TYPES,
    }


@app.get("/api/overview")
def get_overview():
    return overview.get_overview()


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    return {"jobs": manager.list(max(1, min(limit, 200)))}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return manager.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在")


@app.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str, last_event_id: int = 0):
    try:
        manager.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在")

    return StreamingResponse(
        manager.stream(job_id, max(0, last_event_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, payload: CancelJobRequest | None = None):
    try:
        job = manager.cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 来源记在任务上，由任务收尾时统一写进审计日志——否则同一条取消会写两遍
    reason = (payload.reason if payload else "") or "未标注来源"
    manager.update(job_id, cancel_reason=reason)
    return manager.get(job_id)


@app.post("/api/jobs/{job_id}/rollback")
def rollback_job(job_id: str):
    try:
        return manager.rollback(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="任务不存在")


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

@app.post("/api/scan")
def start_scan(request: ScanRequest):
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("scan", f"扫描 {root}", str(root), request.model_dump(), scan.run_scan)


@app.post("/api/tree")
def start_tree(request: TreeRequest):
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("tree", f"空间地图 {root}", str(root), request.model_dump(), scan.run_tree)


@app.post("/api/duplicates/scan")
def start_duplicate_scan(request: DuplicateRequest):
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("duplicate", f"查找重复文件 {root}", str(root), request.model_dump(), duplicates.run_duplicate_scan)


@app.post("/api/junk/scan")
def start_junk_scan(request: JunkRequest):
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("junk", f"查找垃圾文件 {root}", str(root), request.model_dump(), junk.run_junk_scan)


@app.post("/api/virtual/organize")
def start_virtual(request: VirtualRequest):
    """整理预览：只生成「假想整理后」的结构，不动任何文件。"""
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    mode = next((item for item in config.VIRTUAL_GROUP_MODES if item["id"] == request.group_by), None)
    if mode is None:
        raise HTTPException(status_code=400, detail=f"不支持的分组方式：{request.group_by}")
    title = f"{mode['label']}预览 {root}"
    return _launch("virtual", title, str(root), request.model_dump(), virtual.run_virtual)


@app.post("/api/virtual/export")
def export_virtual(request: VirtualExportRequest):
    """把预览清单导出成 CSV（只写报表文件，不碰被扫描的文件）。"""
    try:
        return virtual.export_manifest(request.root, request.group_by, request.items)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)


@app.post("/api/projects/scan")
def start_project_scan(request: ProjectRequest):
    """项目雷达：识别项目目录，统计体积与活跃度，只读。"""
    try:
        root = fsutil.resolve_dir(request.path)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("projects", f"项目雷达 {root}", str(root), request.model_dump(), projects.run_project_scan)


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

@app.post("/api/migrate")
def start_migrate(request: MigrateRequest):
    try:
        source = fsutil.resolve_dir(request.source_dir)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    title = ("规划迁移 " if request.dry_run else "执行迁移 ") + f"{source} → {request.target_dir}"
    return _launch("migrate", title, str(source), request.model_dump(), migrate.run_migrate)


@app.post("/api/clean")
def start_clean(request: CleanRequest):
    if not request.items:
        raise HTTPException(status_code=400, detail="没有需要清理的项目")
    return _launch("clean", f"清理 {len(request.items)} 个项目", "", request.model_dump(), clean.run_clean)


@app.post("/api/backup")
def start_backup(request: BackupRequest):
    try:
        source = fsutil.resolve_dir(request.source_dir)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)
    return _launch("backup", f"备份 {source}", str(source), request.model_dump(), backup.run_backup)


# ---------------------------------------------------------------------------
# 回收站
# ---------------------------------------------------------------------------

@app.get("/api/trash")
def get_trash():
    return clean.list_trash()


@app.post("/api/trash/restore")
def restore_trash(request: TrashRequest):
    try:
        return clean.restore_trash(request.ids)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)


@app.post("/api/trash/purge")
def purge_trash(request: TrashRequest):
    try:
        return clean.purge_trash(request.ids)
    except (OSError, ValueError) as exc:
        raise _friendly(exc)


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

@app.get("/api/logs")
def get_logs():
    logs = store.read_logs()
    return {"logs": logs, "count": len(logs), "source": config.LOG_FILE.name}


@app.post("/api/logs/export")
def export_logs():
    logs = store.read_logs()
    folder = config.STATE_DIR / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"filepilot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = ["FilePilot Lite 操作日志", f"导出时间：{fsutil.now_iso()}", f"记录数：{len(logs)}", ""]
    for item in logs:
        lines.append(f"[{item.get('time')}] {item.get('type')} · {item.get('result')} · {item.get('path') or '—'}")
        if item.get("note"):
            lines.append(f"    {item['note']}")
    target.write_text("\n".join(lines), encoding="utf-8")
    size = target.stat().st_size
    return {
        "success": True, "path": str(target), "size": size,
        "size_text": fsutil.format_size(size), "count": len(logs),
    }


# ---------------------------------------------------------------------------
# Agent（唯一可能消耗 token 的部分）
# ---------------------------------------------------------------------------

@app.get("/api/agent/status")
def agent_status():
    return agent.status()


@app.post("/api/agent/chat")
def agent_chat(request: AgentRequest):
    try:
        return agent.chat(request.message, request.context)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/agent/query")
def agent_query(request: AgentRequest):
    """只读本地查询：不执行文件操作，也不调用模型。"""
    result = agent.query_local(request.message)
    if result is None:
        raise HTTPException(status_code=400, detail="暂时无法用本地规则理解这个查询")
    return result


@app.get("/api/agent/memory/{conversation_id}")
def get_agent_memory(conversation_id: str):
    return {"conversation_id": conversation_id, "messages": agent.load_memory(conversation_id)}


@app.delete("/api/agent/memory/{conversation_id}")
def clear_agent_memory(conversation_id: str):
    agent.clear_memory(conversation_id)
    return {"success": True, "conversation_id": conversation_id}


@app.get("/api/agent/conversations")
def list_agent_conversations():
    """历史会话列表（按最近活跃倒序）。"""
    return {"conversations": agent.list_conversations()}


@app.post("/api/agent/conversations/{conversation_id}/title")
def rename_agent_conversation(conversation_id: str, request: ConversationTitleRequest):
    """重命名会话；传空标题则退回按首条消息自动生成。"""
    return agent.set_conversation_title(conversation_id, request.title)


@app.post("/api/agent/chat/stream")
def agent_chat_stream(request: AgentRequest):
    """流式版：phase / delta / done / error 四种事件，前端逐块渲染。"""
    def event_source():
        try:
            for event in agent.stream_chat(request.message, request.context):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (ValueError, RuntimeError) as exc:
            payload = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/settings/safety")
def get_safety_settings():
    return permissions.describe()


@app.post("/api/settings/safety")
def update_safety_settings(request: SafetySettingsRequest):
    """切换权限档位。切到「完全访问」必须显式 confirm，否则拒绝。"""
    try:
        return permissions.set_mode(request.mode, request.confirm)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/settings/agent")
def get_agent_settings():
    """只回掩码与状态，绝不回传完整 key。"""
    return agent.settings_view()


@app.post("/api/settings/agent")
def update_agent_settings(request: AgentSettingsRequest):
    """保存/清除模型配置。api_key 或 model 传空串表示清除该字段。

    只写 .filepilot/settings.json —— 配方与任务记录都不受影响。
    """
    return agent.update_settings(request.api_key, request.model)


# ---------------------------------------------------------------------------
# Agent 工具层 / 偏好记忆 / 多步骤任务（第一、二、三、五、六阶段）
# ---------------------------------------------------------------------------

@app.get("/api/agent/tools")
def agent_tools():
    """三组工具清单（查询 12 / 执行 9 / 辅助 6）+ 风险档位说明。"""
    return agent.tools_view()


@app.get("/api/agent/context")
def agent_context():
    """当前状态快照：任务、权限档、模型配置、磁盘、确认卡、可回滚、隔离区。"""
    return agent.status_context()


@app.get("/api/agent/prefs")
def get_agent_prefs():
    return {"prefs": agent.read_prefs(), "tags": config.EXCLUDE_TAGS}


@app.post("/api/agent/prefs")
def update_agent_prefs(request: AgentPrefsRequest):
    try:
        return {"prefs": agent.update_prefs(request.prefs), "tags": config.EXCLUDE_TAGS}
    except (ValueError, TypeError) as exc:
        raise _friendly(exc)


@app.post("/api/agent/plan")
def agent_plan(request: AgentPlanRequest):
    """计划预览：把一句话拆成计划树，**不创建任何任务**。"""
    try:
        return agent.plan_preview(request.message, request.context)
    except (ValueError, TypeError) as exc:
        raise _friendly(exc)


@app.post("/api/agent/task")
def start_agent_task(request: AgentTaskRequest):
    """启动多步骤任务（只读分析段）。跑完停在「等待确认」。"""
    intent = request.intent or {}
    goal = request.title or intent.get("goal") or "Agent 任务"
    scope = (intent.get("scope") or {}).get("path") or ""
    params = {
        "intent": intent,
        "plan_params": request.params or {},
        "steps": request.steps,
        "mode": "analyze",
    }
    job = manager.create("agent_task", goal, scope, params)
    manager.start(job["id"], agent_task.run_agent_task)
    return {"job_id": job["id"], "job": manager.get(job["id"])}


@app.post("/api/agent/task/{job_id}/approve")
def approve_agent_task(job_id: str):
    """确认方案 → 在同一个任务里继续执行段。

    写权限在这里只拦一次：只读档直接拒绝，其余档位放行后仍由
    clean / migrate 服务自己的权限层与隔离区兜底。
    """
    if permissions.current() == "readonly":
        raise HTTPException(status_code=400, detail="当前是「只读」档：只允许分析，不能执行写操作")
    try:
        job = manager.get(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这个任务")
    if job.get("type") != "agent_task":
        raise HTTPException(status_code=400, detail="这个任务不是 Agent 多步骤方案")
    proposal = job.get("proposal") or {}
    if not proposal.get("action"):
        raise HTTPException(status_code=400, detail="方案里没有可执行的动作")
    if not proposal.get("items"):
        raise HTTPException(status_code=400, detail="清单为空，没有可执行的项目")

    params = {**(job.get("params") or {}), "mode": "execute", "proposal": proposal}
    manager.update(job_id, params=params, message="已确认，准备执行", force=True)
    manager.start(job_id, agent_task.run_agent_task)
    return {"job_id": job_id, "job": manager.get(job_id)}


@app.post("/api/agent/task/{job_id}/revise")
def revise_agent_task(job_id: str, request: AgentReviseRequest):
    """修改方案：换条件重算，零 token。仍停在「等待确认」，不会自动动文件。"""
    try:
        return agent.revise_task(job_id, request.constraints)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这个任务")
    except (ValueError, TypeError) as exc:
        raise _friendly(exc)


@app.get("/api/agent/observe/{job_id}")
def observe_agent_job(job_id: str):
    """任务结果观察：读成「人话总结 + 失败归因 + 下一步」。零 token。"""
    try:
        return agent.observe_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="找不到这个任务")


@app.get("/api/recipes")
def get_recipes():
    return {"recipes": store.read_recipes()}


@app.post("/api/recipes")
def create_recipe(request: RecipeRequest):
    recipes = store.read_recipes()
    recipes.append({
        "name": request.name,
        "message": request.message,
        "action": request.action,
        "created_at": fsutil.now_iso(),
    })
    store.save_recipes(recipes)
    return {"success": True, "count": len(recipes)}


@app.delete("/api/recipes/{index}")
def delete_recipe(index: int):
    recipes = store.read_recipes()
    if index < 0 or index >= len(recipes):
        raise HTTPException(status_code=404, detail="配方不存在")
    removed = recipes.pop(index)
    store.save_recipes(recipes)
    return {"success": True, "removed": removed, "count": len(recipes)}


# ---------------------------------------------------------------------------
# 本地定时维护
# ---------------------------------------------------------------------------

@app.get("/api/schedules")
def get_schedules():
    return {"schedules": scheduler.list_schedules()}


@app.post("/api/schedules")
def create_schedule(request: ScheduleRequest):
    try:
        return scheduler.new_schedule(request.name, request.kind, request.params, request.interval_minutes, request.next_run_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    if not any(item["id"] == schedule_id for item in scheduler.list_schedules()):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    scheduler.remove_schedule(schedule_id)
    return {"success": True, "id": schedule_id}


# 发行版由后端直接托管前端静态文件；开发环境没有 web/ 时不影响 API。
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)

"""Agent 智能助手 —— 全项目唯一可能消耗 token 的模块。

设计原则：
1. 先走「本地规则意图识别」，命中就零 token 出计划。
2. 本地认不出、且配置了 API Key，才调 DeepSeek（用标准库 urllib，不引第三方依赖）。
3. 没配 Key 就如实说明，绝不返回编造的建议。
4. Agent 只负责"把话翻译成计划"，真正执行必须由用户确认后走本地接口。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .. import config, database, fsutil, permissions, secrets, store
from .agent_intent import (
    SPACE_INTENT_RE,
    canonical,
    clarify_questions,
    intent_view,
    normalize,
    resolve_action_reference,
    resolve_reference,
    revise_payload,
    sanitize_constraints,
)
from .agent_tools import (
    ACTION_PARAMS,
    ALLOWED_ACTIONS,
    HELPER_TOOLS,
    PATH_PARAMS,
    TOOL_REGISTRY,
    WRITE_ACTIONS,
    registry_view,
)

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-flash"

# 界面上可选的模型（也允许自己填）
MODEL_OPTIONS = ["deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]

# 动作白名单、工具注册表、参数表与风险表统一定义在 services/agent_tools.py，
# 这里只做再导出，保证老调用方（main.py / 测试）继续可用。
__all__ = [
    "ALLOWED_ACTIONS", "TOOL_REGISTRY", "WRITE_ACTIONS", "ACTION_PARAMS", "PATH_PARAMS",
    "chat", "stream_chat", "query_local", "status", "status_context", "settings_view",
    "update_settings", "load_memory", "clear_memory", "list_conversations",
    "set_conversation_title", "summarize_job", "read_prefs", "update_prefs",
]

SYSTEM_PROMPT = """你是 Atlas 通用助手，优先帮助用户完成文件管理任务。遇到扫描、查重、清理、迁移、备份等需求时，必须转换为可执行 JSON 计划；其他问题正常回答，不要拒答，也不要强行生成文件操作。

只允许输出以下 action 之一；文件管理需求输出对应 action，普通问答使用 action 为 none，并在 reason 中直接回答用户：
- open_tab        打开 Atlas 的指定功能页
- overview        查询首页概况
- jobs_list       查询任务中心
- trash_list      查询回收站
- logs_list       查询操作日志
- scan            扫描目录，统计数量/体积/类型分布/大文件
- tree            生成目录占用树（空间可视化）
- virtual_preview 生成整理预览
- projects_scan   扫描项目雷达
- duplicate_scan  查找内容重复的文件
- junk_scan       查找垃圾文件（临时文件/缓存/日志/空目录等）
- migrate_plan    规划把文件从 A 目录迁移到 B 目录（只出清单，不动文件）
- duplicate_clean 清理重复文件（只删每组多余副本，进隔离区）
- junk_clean      清理垃圾文件（只清安全级，进隔离区）
- migrate_execute 真正执行搬移（复制+校验+删源）
- backup          把目录备份成压缩包
- trash_restore   把隔离区里的文件恢复回原位置
- job_cancel      取消当前正在运行的任务
- job_rollback    回滚最近一次可回滚的任务

输出格式严格为：
{"goal": "一句话目标", "action": "junk_clean", "reason": "一句话说明",
 "params": {"path": "C:\\\\", "min_size_mb": 1024, "extensions": ["mp4"], "keep": "newest"},
 "constraints": {"safe_only": true, "exclude": ["项目文件", "照片"]},
 "needs_confirm": true}

参数规则：
- path / source_dir / target_dir / dest_dir 必须来自用户提到的真实路径，不要编造；没提到就省略该键。
- min_size_mb：用户说了「大于 1GB」这类体积条件才给，单位 MB。
- extensions：用户说了文件类型才给（如「视频」给 ["mp4","mkv","avi","mov"]，「图片」给 ["jpg","jpeg","png","gif","webp"]）。
- keep：用户说「只保留最新」才给 "newest"；默认不传（保留最旧的为原份）。
- constraints.exclude：用户说「不要动 X」「排除 X」时，把对象原样放进数组，例如 ["项目文件", "照片"]、["D:\\\\素材"]。不要自己编排除项。
- constraints.safe_only：默认 true。用户明确说「连要确认的也清」才给 false。
- 用户说「腾出/至少/释放 20GB」这类空间目标时，加 constraints.min_free_space_mb（单位 MB）。
- 迁移/备份类必须同时给出来源与去向两个目录。
- 无法判断时输出 {"action": "none", "reason": "说明缺什么信息"}。
- 只输出 JSON，不要任何其他文字。"""

PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"'，,。；;、]*)")
DRIVE_CN_RE = re.compile(r"([A-Za-z])\s*盘")

# 中文紧贴在路径后面是很常见的写法：「把 D:\素材 腾出空间」「D:\素材清一下」。
# 正则本身分不清「路径里的中文」和「路径后的中文」，所以用存在性来判：
# 整段存在就用整段；否则从第一个中文字符处切开，切出来的前缀存在就用前缀。
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _trim_path(value: str) -> str:
    """把紧贴在路径尾部的普通中文词削掉，但保留本来就是中文的目录名。"""
    candidate = value.rstrip("\\/")
    if not candidate or os.path.exists(candidate):
        return candidate

    match = CJK_RE.search(candidate)
    if not match:
        return candidate
    head = candidate[:match.start()].rstrip("\\/")
    if len(head) > 3 and os.path.exists(head):
        return head
    return candidate

# 流式模式下让模型「先说话、再给计划」：正文可以边生成边显示，
# 计划仍锁在 ```json 代码块里，解析契约不变。
STREAM_SYSTEM_PROMPT = """你是本地文件管理工具的指令翻译器。
先用一句中文说明你的判断（不超过 40 个字），然后输出一个 JSON 代码块。

文件管理需求生成计划；普通问题直接回答，JSON 使用 action=none：
- open_tab        打开指定功能页
- overview        查询首页概况
- jobs_list       查询任务中心
- trash_list      查询回收站
- logs_list       查询操作日志
- scan            扫描目录
- tree            目录占用树
- virtual_preview 整理预览
- projects_scan   项目雷达
- duplicate_scan  查找重复文件
- junk_scan       查找垃圾文件
- migrate_plan    规划迁移（只出清单）
- duplicate_clean 清理重复文件（进隔离区）
- junk_clean      清理垃圾文件（只清安全级，进隔离区）
- migrate_execute 真正执行搬移
- backup          备份成压缩包

JSON 结构：
{"action": "junk_clean", "reason": "一句话说明", "params": {"path": "C:\\\\"}, "needs_confirm": true}

参数规则：
- 路径必须来自用户原话，不要编造；迁移/备份要同时给 source_dir 与 target_dir（或 dest_dir）。
- 用户说了体积条件给 min_size_mb（MB），说了文件类型给 extensions 数组，说「只保留最新」给 keep="newest"。
- 判断不了时用 {"action": "none", "reason": "还缺什么信息"}。

输出格式（严格照做）：
一句中文说明
```json
{"action": "...", "reason": "...", "params": {}, "needs_confirm": true}
```"""


# ---------------------------------------------------------------------------
# 模型配置的解析与持久化
#
# 优先级：设置文件（界面里填的） → 环境变量（向后兼容 setx / start_backend.bat）
# 改设置文件**不需要重启服务**，所以界面改了立刻生效。
# ---------------------------------------------------------------------------

def resolve_key() -> str:
    stored = secrets.unprotect((store.read_agent_settings().get("api_key") or "").strip())
    return stored or (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def resolve_model() -> str:
    stored = (store.read_agent_settings().get("model") or "").strip()
    return stored or DEFAULT_MODEL


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * 8
    return f"{key[:5]}…{key[-4:]}"


def settings_view() -> dict:
    """给前端的视图：**绝不回传完整 key**，只回掩码。"""
    stored = store.read_agent_settings()
    stored_key = secrets.unprotect((stored.get("api_key") or "").strip())
    env_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    key = stored_key or env_key

    return {
        "configured": bool(key),
        "masked": _mask_key(key),
        "model": resolve_model(),
        "custom_model": bool((stored.get("model") or "").strip()),
        "source": "settings" if stored_key else ("env" if env_key else "none"),
        "model_options": MODEL_OPTIONS,
        "checked_at": fsutil.now_iso(),
    }


def update_settings(api_key: str | None = None, model: str | None = None) -> dict:
    """写设置。api_key 传空串表示清除；model 传空串表示回到默认。

    只动 settings.json —— 配方（recipes.json）与所有任务记录都不受影响。
    """
    agent_settings = store.read_agent_settings()

    if api_key is not None:
        value = api_key.strip()
        if value:
            agent_settings["api_key"] = secrets.protect(value)
        else:
            agent_settings.pop("api_key", None)

    if model is not None:
        value = model.strip()
        if value:
            agent_settings["model"] = value
        else:
            agent_settings.pop("model", None)

    store.save_agent_settings(agent_settings)
    return settings_view()


def _conversation_id(context: dict | None) -> str:
    value = str((context or {}).get("conversation_id") or "default").strip()
    return value[:100] or "default"


# 每个会话在库里保留的条数（够你翻历史）。真正喂给模型的上下文由 build_context_memory 再按字符预算压缩。
MEMORY_KEEP = 400


# 落进对话记忆的字段白名单：只留「重建这条回复长什么样」需要的东西。
# 存整份 payload 会把状态快照、磁盘列表这类一次性数据也冻进历史——
# 那些数字第二天就是错的，还原出来反倒是误导。
MEMORY_PAYLOAD_KEYS = (
    "type", "source", "tokens",
    "plan", "intent", "steps", "needs_path", "hint",
    "question", "missing", "suggestions", "options", "fields",
)


def _memory_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload[key] for key in MEMORY_PAYLOAD_KEYS
        if payload.get(key) not in (None, "", [], {})
    }


def save_memory(conversation_id: str, role: str, content: str, payload: dict | None = None) -> None:
    if role not in {"user", "assistant", "system"} or not content:
        return
    database.append_memory(
        config.DATABASE_FILE, conversation_id, role, content[:8000], fsutil.now_iso(),
        limit=MEMORY_KEEP, payload=_memory_payload(payload),
    )


def load_memory(conversation_id: str, limit: int = MEMORY_KEEP) -> list[dict]:
    return database.read_memory(config.DATABASE_FILE, conversation_id, max(1, min(limit, MEMORY_KEEP)))


def list_conversations(limit: int = 50) -> list[dict]:
    return database.list_conversations(config.DATABASE_FILE, limit)


def set_conversation_title(conversation_id: str, title: str) -> dict:
    value = (title or "").strip()[:60]
    database.set_conversation_title(config.DATABASE_FILE, conversation_id, value, fsutil.now_iso())
    return {"conversation_id": conversation_id, "title": value}


def clear_memory(conversation_id: str) -> None:
    database.clear_memory(config.DATABASE_FILE, conversation_id)
    store.clear_conversation_state(conversation_id)


# ---------------------------------------------------------------------------
# 用户偏好记忆（第五阶段）
#
# 与对话记忆分开：对话记忆是逐条历史，prefs 是提炼后的长期结论，
# 用户在 Agent 页可直接查看、修改、单条删除。
# ---------------------------------------------------------------------------

def read_prefs() -> dict:
    return store.read_prefs()


def update_prefs(patch: dict) -> dict:
    """局部更新偏好。只接受白名单键，值一律做类型与长度收敛。"""
    prefs = store.read_prefs()

    if "common_paths" in patch:
        prefs["common_paths"] = _clean_list(patch.get("common_paths"), 40, 400)
    if "exclude_tags" in patch:
        allowed = set(config.EXCLUDE_TAGS)
        prefs["exclude_tags"] = [str(item) for item in _clean_list(patch.get("exclude_tags"), 20, 40)
                                 if str(item) in allowed]
    if "exclude_paths" in patch:
        prefs["exclude_paths"] = _clean_list(patch.get("exclude_paths"), 40, 400)
    if "hard_rules" in patch:
        rules = []
        for item in (patch.get("hard_rules") or [])[:20]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()[:80]
            path = str(item.get("path") or "").strip()[:400]
            if not text and not path:
                continue
            rules.append({"text": text, "path": path, "action": str(item.get("action") or "*")[:40]})
        prefs["hard_rules"] = rules
    if "clean_strategies" in patch:
        strategies = []
        for item in (patch.get("clean_strategies") or [])[:12]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "").strip()
            if action not in ALLOWED_ACTIONS:
                continue
            strategies.append({
                "action": action,
                "path": str(item.get("path") or "")[:400],
                "message": str(item.get("message") or "")[:200],
                "at": str(item.get("at") or "")[:40],
            })
        prefs["clean_strategies"] = strategies

    prefs["updated_at"] = fsutil.now_iso()
    store.save_prefs(prefs)
    return prefs


def _clean_list(value, limit: int, max_length: int) -> list:
    """去重、截断、限长——偏好记忆是用户可编辑的，写入前必须收敛。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list = []
    for item in value[:limit]:
        if isinstance(item, str):
            text = item.strip()[:max_length]
            if text and text not in result:
                result.append(text)
        elif isinstance(item, dict):
            cleaned = {str(key)[:40]: str(val)[:max_length] for key, val in item.items()}
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


# add_common_path 已移除（2026-10-08）：
# 它把"用过的目录"写进全局 prefs.common_paths，是所有对话共用一份记忆的来源。
# 现在自动记录改走 agent_task._remember_path → 会话状态里的 recent_paths；
# 全局 prefs.common_paths 不再被任何代码写入（界面也没有手动添加入口）。
# 字段本身留着不影响什么，清空偏好时前端仍会带上它。


def add_clean_strategy(action: str, path: str, message: str) -> None:
    """把一次成功执行的流程记成「常用清理策略」，之后能一键重跑。"""
    if not action:
        return
    prefs = store.read_prefs()
    items = [item for item in prefs.get("clean_strategies") or [] if isinstance(item, dict)]
    entry = {
        "action": action,
        "path": (path or "")[:400],
        "message": (message or "")[:200],
        "at": fsutil.now_iso(),
    }
    # 同一个动作 + 同一个目录只留最新一条
    items = [item for item in items
             if not (item.get("action") == action and item.get("path") == entry["path"])]
    items.insert(0, entry)
    prefs["clean_strategies"] = items[:12]
    prefs["updated_at"] = fsutil.now_iso()
    store.save_prefs(prefs)


# ---------------------------------------------------------------------------
# 主动状态感知（第六阶段）
#
# Agent 回答前先知道自己现在处于什么状态：有没有任务在跑、当前权限档、
# 模型配没配、磁盘还剩多少、最近一次任务结果、有没有未处理的确认卡、
# 有没有可回滚的任务、隔离区占多少。全部本地读取，零 token。
# ---------------------------------------------------------------------------

PHASE_RUNNING = {"running", "waiting", "pending"}
# 「正在干活」与「停着等人」要分开报：待确认的任务不该被算成在跑，
# 否则状态条会一直显示「有任务在跑」，用户以为机器在忙其实只是在等他点确认。
PHASE_ACTIVE = {"running", "pending"}


def _job_digest(job: dict | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job.get("id"),
        "type": job.get("type"),
        "type_label": job.get("type_label") or job.get("type"),
        "title": job.get("title"),
        "target": job.get("target"),
        "status": job.get("status"),
        "phase": job.get("phase"),
        "percent": job.get("percent"),
        "message": job.get("message"),
        "error": job.get("error"),
        "finished_at": job.get("finished_at"),
        "rollbackable": bool(job.get("rollbackable")),
    }


def status_context() -> dict:
    """把 Atlas 当前状态压成一个小字典，既给本地规则用，也喂给模型。"""
    from ..jobs import manager
    from . import clean

    try:
        jobs = manager.list(10)
    except (OSError, ValueError):
        jobs = []

    running = [job for job in jobs if job.get("status") in PHASE_ACTIVE]
    waiting = [job for job in jobs if job.get("status") == "waiting"]
    rollbackable = [job for job in jobs if job.get("rollbackable")]

    try:
        disks = fsutil.disk_stats()
    except OSError:
        disks = []

    try:
        trash = clean.list_trash()
    except (OSError, ValueError):
        trash = {}

    info = permissions.describe()
    configured = bool(resolve_key())

    return {
        "busy": bool(running),
        "running": [_job_digest(job) for job in running],
        "running_count": len(running),
        "waiting_confirm": [{"id": job.get("id"), "title": job.get("title")} for job in waiting],
        "waiting_count": len(waiting),
        "rollbackable": [{"id": job.get("id"), "title": job.get("title")} for job in rollbackable],
        "rollback_count": len(rollbackable),
        "last_job": _job_digest(jobs[0]) if jobs else None,
        "permission": {
            "mode": info.get("mode"),
            "label": info.get("label"),
            "allows_write": info.get("allows_write"),
        },
        "model_configured": configured,
        "model": resolve_model() if configured else None,
        "disks": [
            {"root": disk.get("root"), "free_text": disk.get("free_text"),
             "percent": disk.get("percent"), "level": disk.get("level")}
            for disk in disks
        ],
        "trash": {"count": trash.get("count", 0), "size_text": trash.get("total_size_text", "0 B")},
        "checked_at": fsutil.now_iso(),
    }


def _runtime_context(conversation_id: str, context: dict | None = None) -> dict:
    """拼出喂给本地规则与模型的完整上下文：偏好 + 会话快照 + 状态 + 排除目录。

    这里顺带修掉一个历史断链：前端一直把全局 excludes 传过来，
    但早期版本从未读过它，导致「排除目录」对 Agent 完全无效。
    """
    context = dict(context or {})
    # 「修改方案」的补丁只能由 /revise 路由注入，不能从对话上下文里伪造进来——
    # 否则一次普通对话就能绕过模型侧「不许改排除项」的限制。
    context.pop("revise", None)
    prefs = store.read_prefs()
    state = store.get_conversation_state(conversation_id)

    front_excludes = [str(item) for item in (context.get("excludes") or []) if str(item).strip()]
    saved_paths = [str(item) for item in (prefs.get("exclude_paths") or [])]
    merged = list(dict.fromkeys(front_excludes + saved_paths))

    return {
        **context,
        "conversation_id": conversation_id,
        # 喂给模型的 prefs 去掉 common_paths：那是"逛过哪些目录"的使用痕迹，
        # 带上它等于让模型跨对话记住路径。本会话用过的目录在下面的 state 里，
        # 那是跟着会话走的，模型照样能用来消解「就刚才那个目录」。
        "prefs": {k: v for k, v in prefs.items() if k != "common_paths"},
        "state": state,
        "excludes": merged,
        "status": status_context(),
        "memory": build_context_memory(conversation_id),
    }


def _remember_intent(conversation_id: str, plan: dict, message: str) -> None:
    """把本轮意图落成快照，供下一轮「就刚才那个目录」复用。"""
    scope = (plan.get("scope") or {}).get("path") or ""
    if not scope:
        scope = (plan.get("params") or {}).get("path") or (plan.get("params") or {}).get("source_dir") or ""
    store.save_conversation_state(conversation_id, {
        "last_scope": scope,
        "last_target": (plan.get("scope") or {}).get("target") or "",
        "last_action": plan.get("action") or "",
        "last_goal": plan.get("goal") or "",
        "last_message": (message or "")[:200],
        "updated_at": fsutil.now_iso(),
    })


# ---------------------------------------------------------------------------
# 任务结果观察（第三 / 第七阶段）
#
# 任务能启动还不够——Agent 必须读得懂结果：成功了释放了多少、失败了为什么、
# 下一步该干什么。这里全部用本地模板拼装，零 token。
# ---------------------------------------------------------------------------

def summarize_job(job: dict | None) -> dict:
    """把一条任务读成「人话总结 + 失败归因 + 下一步建议」。"""
    if not job:
        return {"ok": False, "status": "missing", "message": "没有可观察的任务", "next_steps": []}

    title = job.get("title") or job.get("type_label") or "任务"
    status = job.get("status")
    result = job.get("result") or {}

    if status in PHASE_RUNNING:
        percent = job.get("percent") or 0
        return {
            "ok": True, "status": status, "title": title,
            "message": f"「{title}」正在跑，{job.get('phase') or ''} {percent}%",
            "detail": job.get("message") or "",
            "percent": percent, "next_steps": [],
        }

    if status == "failed":
        reason = job.get("error") or "未知错误"
        steps = ["按错误信息处理——多数是路径不可写或权限不足"]
        if "权限" in str(reason) or "拒绝" in str(reason):
            steps.append("把权限档切到「标准」或更高，再重试")
        else:
            steps.append("换一个目录重试，或先把目录挪出受保护位置")
        return {"ok": False, "status": status, "title": title,
                "message": f"「{title}」失败了：{reason}", "detail": str(reason),
                "next_steps": steps}

    if status == "cancelled":
        return {"ok": False, "status": status, "title": title,
                "message": f"「{title}」已取消，已完成的动作仍留在记录里",
                "next_steps": ["需要继续的话重新下一遍命令；要撤回已完成的动作可以回滚"]}

    # ---- 已完成：按类型把关键数字读成人话 ----
    kind = job.get("type")
    facts: list[str] = []
    freed = 0
    next_steps: list[str] = []

    if kind == "junk":
        freed = int(result.get("safe_size") or 0)
        facts.append(f"扫到 {result.get('total_count', 0)} 项，其中安全级 {result.get('safe_count', 0)} 项"
                     f"（{result.get('safe_size_text', '0 B')}）")
        if result.get("caution_included"):
            facts.append("本次已包含谨慎级")
        else:
            facts.append("谨慎级未纳入，需要的话去垃圾文件页手动挑")
        next_steps.append("确认清单后执行清理，文件会先进隔离区")
    elif kind == "duplicate":
        freed = int(result.get("reclaimable_size") or 0)
        facts.append(f"{result.get('group_count', 0)} 组重复，多余副本 {result.get('redundant_file_count', 0)} 个")
        next_steps.append("可释放 " + str(result.get("reclaimable_size_text") or "0 B"))
    elif kind == "scan":
        facts.append(f"{result.get('total_files', 0)} 个文件 / {result.get('total_dirs', 0)} 个目录，"
                     f"{result.get('total_size_text', '0 B')}")
        if result.get("large_file_count"):
            facts.append(f"大文件 {result['large_file_count']} 个")
        next_steps.append("可以接着查重或清垃圾")
    elif kind == "migrate":
        if result.get("dry_run"):
            facts.append(f"演练完成，待搬移 {len(result.get('plan') or [])} 项")
            next_steps.append("确认清单后再执行真正的搬移")
        else:
            facts.append(f"已搬移 {result.get('moved', len(result.get('copied') or []))} 项，"
                         f"校验通过 {result.get('verified', 0)} 项")
            if result.get("errors"):
                facts.append(f"有 {len(result['errors'])} 条错误")
            next_steps.append("需要撤回的话可以回滚这次任务")
    elif kind == "clean":
        freed = int(result.get("freed_size") or result.get("total_size") or 0)
        facts.append(f"已清理 {result.get('cleaned_count', result.get('count', 0))} 项")
        next_steps.append("误删的话去隔离区恢复")
    elif kind == "backup":
        facts.append(f"备份 {result.get('file_count', 0)} 个文件，包体 {result.get('size_text', '0 B')}")
        if result.get("partial"):
            facts.append("部分文件跳过，见任务详情")
        next_steps.append(f"备份包在 {result.get('zip_path', '')}".strip())
    elif kind == "virtual":
        facts.append(f"扫了 {result.get('scanned_files', 0)} 个文件，聚成 {result.get('bucket_count', 0)} 组")
        next_steps.append("满意的话可以导出清单或去整理预览页继续")
    elif kind == "projects":
        facts.append(f"识别到 {result.get('project_count', 0)} 个项目，"
                     f"其中僵尸项目 {result.get('zombie_count', 0)} 个"
                     f"（{result.get('zombie_size_text', '0 B')}）")
    elif kind == "tree":
        tree = result.get("tree") or {}
        facts.append(f"占用合计 {tree.get('size_text', '0 B')}")
    elif kind == "agent_task":
        summary = result.get("summary") or {}
        message = summary.get("message") or f"「{title}」已完成"
        return {
            "ok": True, "status": "done", "title": title, "kind": kind,
            "message": message,
            "facts": summary.get("facts") or [],
            "released_bytes": int(summary.get("freed") or 0),
            "released_text": summary.get("freed_text") or "",
            "next_steps": summary.get("next_steps") or [],
            "job_id": job.get("id"),
        }

    message = f"「{title}」已完成"
    if facts:
        message += "：" + "；".join(facts)

    return {
        "ok": True, "status": "done", "title": title, "kind": kind,
        "message": message, "facts": facts, "released_bytes": freed,
        "released_text": fsutil.format_size(freed) if freed else "",
        "next_steps": next_steps,
        "job_id": job.get("id"),
    }


def observe_job(job_id: str) -> dict:
    """按 id 观察一条任务，并给出下一步建议。"""
    from ..jobs import manager

    job = manager.get(job_id)
    if not job:
        return {"ok": False, "message": f"找不到任务 {job_id}", "next_steps": []}
    return {**summarize_job(job), "job": _job_digest(job)}


def revise_task(job_id: str, patch: dict) -> dict:
    """「修改方案」：只换条件重算，零 token，跑完仍停在等待确认。

    关键点：**沿用同一个 job_id**。重算不是新任务，是同一个方案换一组条件——
    任务卡不该因此分裂成两张，撤销时也只用回滚一个。
    """
    from ..jobs import manager
    from . import agent_planner, agent_task

    job = manager.get(job_id)
    if job.get("type") != "agent_task":
        raise ValueError("这个任务不是 Agent 多步骤方案")
    if job.get("status") != "waiting":
        raise ValueError("只有停在「等待确认」的方案才能改条件")

    intent = dict(job.get("intent") or {})
    if not intent.get("action"):
        raise ValueError("这个任务没有可修改的意图")

    clean = sanitize_constraints(patch)
    runtime = {"prefs": store.read_prefs(), "revise": clean}
    plan = normalize(revise_payload(intent, clean), "", runtime)
    if plan.get("action") == "none":
        raise ValueError("按新条件算下来没有可执行的方案了")

    steps = agent_planner.plan_steps(plan)
    params = {
        **(job.get("params") or {}),
        "intent": plan["intent"],
        "plan_params": plan.get("params") or {},
        "steps": None,          # 让 worker 按新意图重新拆步骤
        "mode": "analyze",
        "proposal": None,
        "revised_at": fsutil.now_iso(),
        "revise_count": int((job.get("params") or {}).get("revise_count") or 0) + 1,
    }
    manager.update(
        job_id, params=params, proposal=None, steps=steps,
        message="已按新条件重算", force=True,
    )
    manager.start(job_id, agent_task.run_agent_task)
    return {"job_id": job_id, "job": manager.get(job_id)}


def tools_view() -> dict:
    """给前端的工具清单：三组工具 + 动作白名单 + 风险档位说明。"""
    return {**registry_view(), "risk_levels": config.AGENT_RISK_META}


def plan_preview(message: str, context: dict | None = None) -> dict:
    """零副作用的计划预览：只看会拆成哪些步骤，不创建任何任务。

    前端用它渲染「计划树」的第一屏——用户还没点执行就能看到全程。
    """
    from . import agent_planner

    text = (message or "").strip()
    if not text:
        raise ValueError("请输入内容")
    conversation_id = _conversation_id(context)
    runtime = _runtime_context(conversation_id, context)

    local = parse_local(text, runtime)
    if local and local.get("type") == "clarify":
        return _local_payload(local, text, runtime)

    if local:
        plan = normalize(local, text, runtime)
    elif resolve_key():
        result = _call_deepseek(text, runtime)
        plan = sanitize_plan(result["plan"], text, runtime)
    else:
        return fallback_clarify(text)

    if plan.get("action") == "none":
        # 模型没给出可执行计划时，同样追问而不是干巴巴一句「没识别出意图」
        return none_payload(plan, text, "local" if local else "deepseek")

    steps = agent_planner.plan_steps(plan)
    question = None
    if plan.get("missing"):
        question = clarify_questions(plan.get("action"), text, plan.get("params") or {}, plan.get("constraints") or {})

    return {
        "source": plan.get("source") or "local",
        "tokens": 0,
        "message": plan.get("reason") or plan.get("goal") or "已生成计划",
        "plan": plan,
        "intent": intent_view(plan.get("intent") or {}),
        "steps": steps,
        "pipeline": agent_planner.pick_pipeline(plan) or "single",
        "clarify": question,
        "needs_path": plan.get("needs_path", False),
        "hint": "预览不创建任务；确认后才会真正开始。",
    }


def revise_plan_preview(intent: dict, patch: dict, context: dict | None = None) -> dict:
    """草稿阶段的「修改方案」：方案还没变成任务，意图由前端带回来重算。

    与 `revise_task()` 的分工：那个针对已经挂起在 `waiting` 的任务，这个针对
    还没点「开始分析」的草稿。共同点是**零 token、零副作用**——不建任务、
    不落记忆、不调模型，只把同一份意图按新条件重新摊一遍。

    runtime 刻意与 `revise_task()` 保持逐字一致（只给 prefs 与 revise 两个键）。
    两处若喂不同的上下文，同一个补丁会算出不同的约束——用户会看到「任务里的
    修改方案」和「草稿里的修改方案」给出不一样的结果，这比不支持就地编辑更糟。
    补丁也只在服务端注入：`_runtime_context()` 会主动 pop 掉上下文里的 revise，
    否则一次普通对话就能伪造条件绕过模型侧「不许改排除项」的限制。

    不带 `clarify`：草稿已经把计划树渲染出来了，重算只换条件不产生澄清卡，
    凭空造一句追问反而是误导。
    """
    from . import agent_planner

    base = dict(intent or {})
    if not base.get("action"):
        raise ValueError("这份方案没有可修改的意图")

    clean = sanitize_constraints(patch or {})
    if not clean:
        raise ValueError("没有需要修改的条件")

    runtime = {"prefs": store.read_prefs(), "revise": clean}
    plan = normalize(revise_payload(base, clean), "", runtime)
    if plan.get("action") == "none":
        raise ValueError("按新条件算下来没有可执行的方案了")

    return {
        "tokens": 0,
        "revised": True,
        "message": plan.get("reason") or plan.get("goal") or "已按新条件重算",
        "plan": plan,
        "intent": intent_view(plan.get("intent") or {}),
        "steps": agent_planner.plan_steps(plan),
        "pipeline": agent_planner.pick_pipeline(plan) or "single",
        "needs_path": bool(plan.get("missing")),
        "hint": "只换了条件重算，仍然是只读预览；确认后才会真正开始。",
    }


# 喂给模型的上下文记忆上限（字符）。这是**零 token 的"压缩"**：不做摘要，
# 只保证上下文不无限膨胀——从最近往前累积，超预算就丢更早的，并顺手去掉相邻重复。
MEMORY_CHAR_BUDGET = 6000


def build_context_memory(conversation_id: str, budget: int = MEMORY_CHAR_BUDGET) -> list[dict]:
    items = load_memory(conversation_id)
    kept: list[dict] = []
    used = 0
    for item in reversed(items):
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if kept and kept[-1]["role"] == item.get("role") and kept[-1]["content"] == content:
            continue
        if kept and used + len(content) > budget:
            break
        kept.append({"role": item.get("role"), "content": content})
        used += len(content)
    kept.reverse()
    return kept


def _extract_paths(text: str) -> list[str]:
    found: list[str] = []
    for match in PATH_RE.finditer(text):
        value = _trim_path(match.group(1))
        if value and value not in found:
            found.append(value)
    for match in DRIVE_CN_RE.finditer(text):
        value = f"{match.group(1).upper()}:\\"
        if value not in found:
            # 「C 盘」若已经匹配到更具体的 C:\... 就不要再插一条盘根
            if not any(item.upper().startswith(f"{value[0].upper()}:") for item in found):
                found.append(value)
    return found


# ---------------------------------------------------------------------------
# 参数抽取（零 token）：体积阈值 / 文件类型 / 保留策略
# 本地规则与 DeepSeek 共用同一套键：min_size_mb / extensions / keep
# ---------------------------------------------------------------------------

SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GB|G|MB|M|gb|mb)(?![_A-Za-z0-9])")

TYPE_KEYWORDS = {
    "视频": ["mp4", "mkv", "avi", "mov", "wmv", "flv"],
    "图片": ["jpg", "jpeg", "png", "gif", "webp", "bmp"],
    "音频": ["mp3", "wav", "flac", "aac", "ogg"],
    "文档": ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz"],
    "安装包": ["exe", "msi"],
}

KEEP_RE = re.compile(r"(只留最新|只保留最新|保留最新|最新一份|最新的那份)")

# 动作动词：query_local 用它判断「用户在问状态」还是「用户在派活」
ACTION_HINT_RE = re.compile(
    r"(清理|清掉|删掉|清除|删除|清空|扫描|扫一下|扫扫|查重|去重|整理|归置|收拾"
    r"|迁移|搬到|挪到|移动到|搬走|备份|腾出|释放|清出|省出|空出|挤出|分析|体检|预演)"
)


def _extract_filters(text: str) -> dict:
    """从原话里抽体积阈值 / 类型扩展名 / 保留策略。抽不到就不给键。"""
    filters: dict = {}
    for match in SIZE_RE.finditer(text):
        value = float(match.group(1))
        if match.group(2).lower().startswith("g"):
            value *= 1024
        filters["min_size_mb"] = max(1, min(int(value), 1_000_000))
        break

    extensions: list[str] = []
    for keyword, exts in TYPE_KEYWORDS.items():
        if keyword in text:
            for ext in exts:
                if ext not in extensions:
                    extensions.append(ext)
    if extensions:
        filters["extensions"] = extensions

    if KEEP_RE.search(text):
        filters["keep"] = "newest"

    return filters


def _sanitize_extensions(value) -> list[str] | None:
    """扩展名只留短字母数字，防模型吐奇怪内容。"""
    if not isinstance(value, list):
        return None
    cleaned: list[str] = []
    for item in value[:12]:
        text = str(item).lower().lstrip(".").strip()
        if re.fullmatch(r"[a-z0-9]{1,10}", text) and text not in cleaned:
            cleaned.append(text)
    return cleaned or None


def sanitize_plan(plan: dict, message: str = "", context: dict | None = None) -> dict:
    """模型产出的计划过一道白名单：action 必须在表内、参数逐键过滤、数值 clamp、
    源路径必须真实存在（目标目录除外）。

    过滤完立刻交给 agent_intent.normalize() 收敛成统一意图结构——这样本地规则
    与模型两条路产出的是**同一个形状**，前端与编排层只需认一种协议。
    """
    if not isinstance(plan, dict):
        return normalize({"action": "none", "reason": "模型没有给出可执行计划"}, message, context)

    action = plan.get("action")
    if action not in ALLOWED_ACTIONS:
        return normalize(
            {"action": "none", "reason": plan.get("reason") or "这个动作不在我能做的清单里"},
            message, context,
        )

    raw_params = plan.get("params") if isinstance(plan.get("params"), dict) else {}
    params: dict = {}
    for key in ACTION_PARAMS[action]:
        if key not in raw_params or raw_params.get(key) in (None, ""):
            continue
        value = raw_params[key]
        if key == "min_size_mb":
            try:
                params[key] = max(1, min(int(float(value)), 1_000_000))
            except (TypeError, ValueError):
                continue
        elif key == "extensions":
            cleaned = _sanitize_extensions(value)
            if cleaned:
                params[key] = cleaned
        elif key == "keep":
            if value in ("newest", "original"):
                params[key] = value
        else:
            params[key] = str(value).strip().strip('"').rstrip("\\/") or None
            if not params[key]:
                params.pop(key)

    # 源路径必须真实存在：防模型编造路径，也把「路径不存在」提前到计划阶段暴露
    missing = [
        key for key in PATH_PARAMS.get(action, [])
        if not params.get(key) or not os.path.exists(params[key])
    ]
    reason = plan.get("reason") or "已生成计划"
    if missing and action != "backup":
        reason = f"{reason}（{'、'.join(missing)} 路径不存在或没提到）"

    payload = {
        "action": action,
        "reason": reason,
        "params": params,
        "goal": plan.get("goal"),
        "constraints": plan.get("constraints") if isinstance(plan.get("constraints"), dict) else {},
        "needs_confirm": True,
        "confidence": 0.7,
    }
    return normalize(payload, message, context)


def parse_local(message: str, context: dict | None = None) -> dict | None:
    """本地规则意图识别：命中返回计划，不消耗任何 token。

    与早期版本的三处不同：
    1. 先做**同义归一**（canonical）与**上下文指代还原**（resolve_reference），
       「腾空间」「就刚才那个目录」因此能落到同一套正则上。
    2. 澄清不再写死文案，统一交给 agent_intent.clarify_questions()——
       整理类给选项、迁移类给字段清单。
    3. 默认先扫描再清理：用户说「清理垃圾」不能跳过清单预览，
       只有明确说「立即/直接/执行」时才生成写操作计划。
    """
    context = context or {}
    raw = (message or "").strip()
    if not raw:
        return None

    reference = resolve_reference(raw, context.get("state") or {})
    text = canonical(reference["text"])
    paths = _extract_paths(text)
    for item in reversed(reference["resolved"]):
        if item not in paths:
            paths.insert(0, item)
    first = paths[0] if paths else ""
    filters = _extract_filters(text)

    # ---- 任务控制：对「正在跑的东西」和「误删的东西」直接下命令 ----
    if re.search(r"(取消|停掉|停止|中断|终止)", text) and re.search(r"(任务|迁移|清理|扫描|拷贝|备份)", text):
        return {"action": "job_cancel", "label": "取消任务",
                "reason": "命中本地规则：取消正在运行的任务",
                "params": {}, "needs_path": False}

    if re.search(r"(回滚|撤销|撤回|复原)", text) and re.search(r"(任务|迁移|清理|操作)", text):
        return {"action": "job_rollback", "label": "回滚任务",
                "reason": "命中本地规则：回滚最近一次任务",
                "params": {}, "needs_path": False}

    if re.search(r"(恢复|还原|找回来|救回来|捞回来)", text) and re.search(r"(隔离区|回收站|误删|删错)", text):
        return {"action": "trash_restore", "label": "恢复隔离区文件",
                "reason": "命中本地规则：从隔离区恢复",
                "params": {"path": first}, "needs_path": not first}

    # ---- 备份：缺路径才追问（第四阶段） ----
    if re.search(r"(备份)", text):
        backup_params = {"source_dir": first, "dest_dir": paths[1] if len(paths) > 1 else ""}
        question = clarify_questions("backup", raw, backup_params, {})
        if question and (not first or len(paths) < 2):
            return question
        return {
            "action": "backup",
            "label": "备份目录",
            "reason": "命中本地规则：目录备份",
            "params": backup_params,
            "needs_path": len(paths) < 2,
        }

    tab_routes = {
        "任务中心": "/jobs", "回收站": "/trash", "操作日志": "/logs",
        "空间地图": "/space", "整理预览": "/virtual", "项目雷达": "/projects",
        "扫描分析": "/scan", "重复文件": "/duplicates", "垃圾文件": "/junk",
        "跨盘迁移": "/migrate", "项目备份": "/backup", "首页": "/",
    }
    # 这些页接受"带着目录过去"（前端 useHandoffTarget）——只有它们才附带 path，
    # 其余（任务中心 / 回收站 / 日志 / 空间地图 / 首页）带了也没处用。
    focus_routes = {"/scan", "/duplicates", "/junk", "/virtual", "/projects", "/migrate", "/search", "/backup"}
    for label, route in tab_routes.items():
        if re.search(rf"(打开|进入|切到|查看){label}", text):
            params: dict = {"route": route}
            if paths and route in focus_routes:
                params["path"] = paths[0]
            return {"action": "open_tab", "label": f"打开{label}", "reason": f"打开{label}",
                    "params": params, "needs_path": False}

    # ---- 迁移：来源/去向不全就追问；给齐了仍先出只读计划 ----
    if re.search(r"(搬到|挪到|移动到|迁移|移过去|搬走)", text):
        migrate_params = {
            "source_dir": paths[0] if paths else "",
            "target_dir": paths[1] if len(paths) > 1 else "",
            **filters,
        }
        if len(paths) < 2:
            question = clarify_questions("migrate_plan", raw, migrate_params, {})
            if question:
                return question
        if re.search(r"(直接搬|开始搬|执行迁移|确认搬移|真的搬)", text):
            return {"action": "migrate_execute", "label": "执行跨盘迁移",
                    "reason": "命中本地规则：执行迁移（复制+校验+删源）",
                    "params": migrate_params, "needs_path": False}
        return {"action": "migrate_plan", "label": "规划跨盘迁移",
                "reason": "命中本地规则：跨盘迁移（只出清单，不动文件）",
                "params": migrate_params, "needs_path": False}

    # ---- 写类（执行前仍需用户在计划卡上确认） ----
    if re.search(r"(清理|清掉|删掉|清除|删除|清空)", text) and re.search(r"(垃圾|临时文件|缓存)", text):
        if not re.search(r"(立即|直接|确认|执行|删除掉|彻底)", text):
            return {
                "action": "junk_scan",
                "label": "查找垃圾文件",
                "reason": "先扫描并展示清单，确认后再进入隔离区",
                "params": {"path": first, **filters},
                "needs_path": not first,
            }
        return {
            "action": "junk_clean",
            "label": "清理垃圾文件",
            "reason": "命中本地规则：垃圾清理（只清安全级，先进隔离区）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(删掉|清除|清理|去掉|删了|只留|只保留)", text) and re.search(r"重复", text):
        return {
            "action": "duplicate_clean",
            "label": "清理重复文件",
            "reason": "命中本地规则：重复文件清理（只删多余副本，先进隔离区）",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(直接搬|开始搬|执行迁移|确认搬移|真的搬)", text) and len(paths) >= 2:
        return {
            "action": "migrate_execute",
            "label": "执行跨盘迁移",
            "reason": "命中本地规则：执行迁移（复制+校验+删源）",
            "params": {"source_dir": paths[0], "target_dir": paths[1]},
            "needs_path": False,
        }

    # ---- 只读 ----
    if re.search(r"(垃圾|清一下|清清|清垃圾|临时文件|缓存)", text):
        return {
            "action": "junk_scan",
            "label": "查找垃圾文件",
            "reason": "命中本地规则：垃圾文件扫描",
            "params": {"path": first},
            "needs_path": not first,
        }

    if re.search(r"(重复文件|重复的|查重|重复)", text):
        return {
            "action": "duplicate_scan",
            "label": "查找重复文件",
            "reason": "命中本地规则：重复文件检测",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    if re.search(r"(空间|占用|占多少|地图|可视化|谁在占)", text):
        return {
            "action": "tree",
            "label": "生成空间地图",
            "reason": "命中本地规则：目录占用可视化",
            "params": {"path": first},
            "needs_path": not first,
        }

    # ---- 整理：方式不明先把选项摆出来；方式已定就只问路径（第二阶段追问的收尾）----
    if re.search(r"(整理|归置|收拾|按类型|按时间|按月份|按体积|按项目|整理预览)", text):
        group_by = ""
        for keyword, mode in (("按月份", "time"), ("按时间", "time"), ("按类型", "type"),
                              ("按体积", "size"), ("按项目", "project")):
            if keyword in text:
                group_by = mode
                break
        # 只有「整理预览」这类笼统说法时，默认按类型——不必为一个默认值多问一轮
        if not group_by and re.search(r"(只生成预览|整理预览|预览)", text):
            group_by = "type"

        if not group_by:
            question = clarify_questions("virtual_preview", raw, {"path": first}, {})
            if question:
                if not first:
                    question["missing"] = ["path"]
                    question["fields"] = [{"id": "path", "question": "要整理哪个目录？", "kind": "path"}]
                return question

        if not first:
            # 方式已经定了，就别再把那四个选项摆一遍——只问路径，
            # 顺手把常用路径当建议推过去，避免用户在同一个追问里打转。
            common = [item.get("path") if isinstance(item, dict) else str(item)
                      for item in ((context.get("prefs") or {}).get("common_paths") or [])][:4]
            return {
                "type": "clarify",
                "reason": f"整理方式记下了（{'按月份' if group_by == 'time' else '按类型' if group_by == 'type' else '按体积' if group_by == 'size' else '按项目'}），还差一个目录。",
                "question": "要整理哪个目录？",
                "missing": ["path"],
                "suggestions": common or ["例如：D:\\下载", "例如：C:\\Users\\faker\\Downloads"],
                "options": [],
                "fields": [{"id": "path", "question": "要整理的目录", "kind": "path"}],
            }

        return {"action": "virtual_preview", "label": "生成整理预览",
                "reason": "命中本地规则：整理预览",
                "params": {"path": first, "group_by": group_by},
                "needs_path": False}

    if re.search(r"(项目雷达|找项目|扫描项目|僵尸项目)", text):
        return {"action": "projects_scan", "label": "扫描项目", "reason": "命中本地规则：项目雷达",
                "params": {"path": first}, "needs_path": not first}

    if re.search(r"(扫描|扫一下|扫扫|统计|看看有什么|体检)", text):
        return {
            "action": "scan",
            "label": "扫描目录",
            "reason": "命中本地规则：目录扫描",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    return None


# 垃圾规则 + 预编译正则（与 junk 服务共用 config 里那张表）。
# 这里不跳过任何规则：不靠模式匹配的几条（空目录/零字节/超大）本来就没有 pattern，
# matcher 为 None，行为与原来"遍历 0 个模式、`any()` 恒 False"一致。
_JUNK_MATCHERS = config.junk_matchers()


def _rule_explanation(path_text: str) -> dict:
    """Explain junk classification locally without reading or modifying the file."""
    path = path_text.strip().strip('"')
    name = os.path.basename(path).lower()
    for rule, matcher in _JUNK_MATCHERS:
        if matcher is not None and matcher.match(name):
            return {
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "level": rule["level"],
                "reason": rule["desc"],
                "matched": True,
                "path": path,
            }
    return {
        "rule_id": None,
        "rule_name": None,
        "level": None,
        "reason": "当前规则库没有根据文件名匹配到垃圾规则，可能需要查看文件所在目录或手动确认。",
        "matched": False,
        "path": path,
    }


def _fallback_options(path: str) -> list[dict]:
    """没认出意图时给出的候选动作。label 本身就是一条完整指令，点一下就能发出去。"""
    if path:
        return [
            {"id": "scan", "label": f"扫描 {path}", "note": "统计数量 / 体积 / 类型分布"},
            {"id": "dup", "label": f"找出 {path} 里的重复文件", "note": "按内容比对，只出清单"},
            {"id": "junk", "label": f"找出 {path} 里的垃圾文件", "note": "临时文件 / 缓存 / 空目录"},
            {"id": "space", "label": f"看 {path} 里谁在占空间", "note": "目录占用树"},
        ]

    letters = config.drive_letters()[:2]
    options = [{"id": f"scan-{letter}", "label": f"扫描 {letter} 盘", "note": "统计数量 / 体积"}
               for letter in letters]
    if letters:
        first = letters[0]
        options.append({"id": "dup", "label": f"找出 {first} 盘的重复文件", "note": "只出清单"})
        options.append({"id": "junk", "label": f"清理 {first} 盘垃圾", "note": "先进隔离区，可恢复"})
    return options[:4]


def fallback_clarify(message: str) -> dict:
    """本地规则没认出、又没有模型可用时：**追问，而不是通报「没看懂」**。

    第四阶段的硬要求——没识别清楚就主动问，不要猜。
    带路径的就把动作选项摆出来；不带路径的先问要处理哪里。

    **必须带上 `plan.action == "none"`**：三条调用路径（本地+无 Key、流式、兜底）
    都直接返回这个 dict，不走 `none_payload`。少了这个标记，"认不出"这件事在数据里
    就是缺失的 —— 前端只能靠「plan 不存在」去猜，接口契约也不自洽。
    这里补上，三条路一次对齐。source 仍是 local（界面要显示「本地规则」）。
    """
    paths = _extract_paths(message)
    options = _fallback_options(paths[0] if paths else "")
    none_plan = {"action": "none", "reason": ""}

    if paths:
        return {
            "source": "local",
            "tokens": 0,
            "type": "clarify",
            "message": f"我知道你指的是 {paths[0]}，但不确定要对它做什么。",
            "question": "你想让我做哪件事？",
            "missing": [],
            "suggestions": [],
            "options": options,
            "fields": [],
            "plan": dict(none_plan),
        }

    return {
        "source": "local",
        "tokens": 0,
        "type": "clarify",
        "message": "这句话我没认出明确的意图——直接给我一个目录，或者从下面挑一个也行。",
        "question": "要处理哪里、做什么？",
        "missing": ["path"],
        "suggestions": [],
        "options": options,
        "fields": [
            {"id": "path", "question": "要处理的目录（也可以直接打一个盘符，如 D:\\）", "kind": "path"},
        ],
        "plan": dict(none_plan),
    }


def none_payload(plan: dict, message: str, source: str, tokens: int = 0) -> dict:
    """意图落空时的统一出口：把「我听不懂」升级成「给你几个能点的」。

    无论这句话是本地规则没认出，还是模型回了 action=none，用户拿到的
    都不该是一句「无法执行」的死胡同——第四阶段要的就是主动追问。
    模型给的 reason 更有针对性，所以保留它当正文，选项照样附上。
    """
    base = fallback_clarify(message)
    reason = (plan or {}).get("reason") or ""
    return {
        **base,
        "source": source,
        "tokens": tokens,
        "message": reason or base["message"],
        "plan": plan if plan else {"action": "none", "reason": reason},
        "steps": [],
    }


def _looks_like_action(text: str) -> bool:
    """这段话是在「要我做一件事」，还是在「问我一个状态」？

    有真实路径、有空间目标、或带动作动词，都算前者。
    query_local 靠它让步——否则「把 D:\\x 腾出空间」会被总览分支抢答成
    「已读取本机当前状态」，用户下的命令就丢了。
    """
    if _extract_paths(text):
        return True
    if SPACE_INTENT_RE.search(text or ""):
        return True
    return bool(ACTION_HINT_RE.search(text or ""))


def query_local(message: str, context: dict | None = None) -> dict | None:
    """零 token 回答状态、身份与解释类问题。

    第六阶段的关键改动：这里不再只回答「用户显式问的那一项」，
    而是先看 status_context——所以「刚才的迁移怎么样了」能直接拿到真实进度，
    而不是让模型去猜。
    """
    context = context or {}
    text = (message or "").strip()
    if not text:
        return None
    status = context.get("status") or {}

    # 「刚才的迁移怎么样了」——先做上下文指代解析
    reference = resolve_action_reference(text, context.get("state") or {})
    if reference.get("matched"):
        job_id = reference.get("last_job_id")
        digest = status.get("last_job")
        if job_id:
            observed = observe_job(job_id)
            if observed.get("ok") or observed.get("status") != "missing":
                return {"source": "local", "tokens": 0, "type": "observe",
                        "message": observed.get("message") or "已读取最近一次任务",
                        "observation": observed}
        if digest:
            return {"source": "local", "tokens": 0, "type": "observe",
                    "message": f"最近一次任务是「{digest.get('title')}」，状态 {digest.get('status')}"
                               f"（{digest.get('phase') or ''} {digest.get('percent') or 0}%）",
                    "observation": {"ok": True, "status": digest.get("status"),
                                    "message": digest.get("message") or "",
                                    "next_steps": []}}

    if re.search(r"(任务在跑|正在跑|忙不忙|现在在忙|当前任务|在进行什么)", text):
        running = status.get("running") or []
        if not running:
            tail = "现在没有任务在跑"
            if status.get("rollbackable"):
                tail += f"；有 {status.get('rollback_count')} 个任务可以回滚"
            return {"source": "local", "tokens": 0, "type": "status",
                    "message": tail + "。", "data": status}
        lines = [f"{item.get('title')}（{item.get('phase') or ''} {item.get('percent') or 0}%）" for item in running]
        return {"source": "local", "tokens": 0, "type": "status",
                "message": f"当前有 {len(running)} 个任务在跑：" + "、".join(lines), "data": status}

    if re.search(r"(为什么|为何|原因).*(垃圾|清理|判定|规则)", text):
        paths = _extract_paths(text)
        return {
            "source": "local", "tokens": 0, "type": "explain",
            "message": "已根据本地规则解释文件判定原因。",
            "explanation": _rule_explanation(paths[0] if paths else text),
        }

    # 不能单独用“怎么样”作为本地总览意图；例如“今天天气怎么样”应交给
    # 未知意图处理，而不是误读本机磁盘状态。总览必须出现明确的本地状态词；
    # 而且只在这句话**不是**在下命令时才回答（见 _looks_like_action）。
    if re.search(r"(磁盘|空间|总体|概况|状态|还剩|剩余)", text) and not _looks_like_action(text):
        from . import overview

        data = overview.get_overview()
        return {
            "source": "local", "tokens": 0, "type": "overview",
            "message": "已读取本机当前状态。", "data": {**data, "status": status},
        }

    if re.search(r"(任务中心|任务列表|最近任务|有哪些任务|有什么任务)", text):
        from ..jobs import manager

        jobs = manager.list(10)
        return {
            "source": "local", "tokens": 0, "type": "jobs",
            "message": f"当前共有 {len(jobs)} 条最近任务记录。",
            "data": {"jobs": jobs, "status": status},
        }

    if re.search(r"(回收站|隔离区|可恢复)", text):
        from . import clean

        data = clean.list_trash()
        return {
            "source": "local", "tokens": 0, "type": "trash",
            "message": f"隔离区当前有 {data.get('count', 0)} 项，"
                       f"可恢复空间约 {data.get('total_size_text', '0 B')}。",
            "data": data,
        }

    if re.search(r"(操作日志|审计日志|最近操作)", text):
        logs = store.read_logs()
        return {
            "source": "local", "tokens": 0, "type": "logs",
            "message": f"当前共有 {len(logs)} 条操作日志。",
            "data": {"logs": logs[-20:], "count": len(logs)},
        }
    return None


def _call_deepseek(message: str, context: dict) -> dict:
    key = resolve_key()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    body = json.dumps({
        "model": resolve_model(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                {"message": message, "context": context}, ensure_ascii=False
            )},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": 400,
    }).encode("utf-8")

    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"DeepSeek 调用失败（{exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 DeepSeek：{exc.reason}") from exc

    content = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    try:
        plan = json.loads(content)
    except json.JSONDecodeError:
        plan = {"action": "none", "reason": "模型返回的不是合法 JSON"}

    usage = payload.get("usage") or {}
    return {"plan": plan, "tokens": int(usage.get("total_tokens") or 0)}


def status() -> dict:
    configured = bool(resolve_key())
    return {
        "configured": configured,
        "provider": "DeepSeek" if configured else "本地规则（零 token）",
        "model": resolve_model() if configured else None,
        "modes": [
            {"id": "local", "label": "本地规则", "cost": "0 token", "note": "关键词识别，可离线"},
            {"id": "deepseek", "label": "DeepSeek", "cost": "按量计费",
             "note": "本地规则认不出时启用" if configured else "未配置 API Key，当前不可用"},
        ],
        "actions": ALLOWED_ACTIONS,
        "tools": TOOL_REGISTRY,
        "tool_groups": registry_view().get("groups"),
        "query_tools": registry_view().get("query"),
        "helper_tools": HELPER_TOOLS,
        "risk_levels": config.AGENT_RISK_META,
        "runtime": status_context(),
        "checked_at": fsutil.now_iso(),
    }


def chat(message: str, context: dict | None = None) -> dict:
    context = context or {}
    text = (message or "").strip()
    if not text:
        raise ValueError("请输入内容")
    conversation_id = _conversation_id(context)
    save_memory(conversation_id, "user", text)
    context = _runtime_context(conversation_id, context)

    def finish(payload: dict) -> dict:
        payload = {**payload, "conversation_id": conversation_id, "memory_count": len(load_memory(conversation_id))}
        save_memory(conversation_id, "assistant", payload.get("message") or json.dumps(payload, ensure_ascii=False), payload=payload)
        return payload

    query = query_local(text, context)
    if query:
        return finish(query)

    local = parse_local(text, context)
    if local:
        payload = _local_payload(local, text, context)
        if payload.get("plan"):
            _remember_intent(conversation_id, payload["plan"], text)
        return finish(payload)

    if resolve_key():
        result = _call_deepseek(text, context)
        plan = sanitize_plan(result["plan"], text, context)
        if plan.get("action") == "none":
            # 模型也没给出可执行计划 → 一样给选项，别只丢一句话
            return finish(none_payload(plan, text, "deepseek", result["tokens"]))
        _remember_intent(conversation_id, plan, text)
        return finish({
            "source": "deepseek",
            "tokens": result["tokens"],
            "message": plan.get("reason") or "已生成计划",
            "plan": plan,
            "intent": intent_view(plan.get("intent") or {}),
            "steps": steps_for(plan),
            "needs_path": plan.get("needs_path", False),
            "hint": "本回答消耗了 token；确认后执行由本地完成。",
        })

    return finish(fallback_clarify(text))


# ---------------------------------------------------------------------------
# 流式对话：边生成边显示，最后仍落回同一个「计划」契约
# ---------------------------------------------------------------------------

def _chunks(text: str, size: int = 24):
    """把一段说明拆成几帧推给前端。

    **刻意不加 sleep**：以前是每 3 个字符睡 15ms，一条 200 字的追问要硬等 1 秒 ——
    而本地规则算出这句话只要 18ms。界面本来就是逐帧渲染 delta，
    拆帧只是让前端少拼一次长字符串，不该是节流阀。
    """
    for index in range(0, len(text), size):
        yield text[index:index + size]


def _iter_deepseek_stream(message: str, context: dict, usage: dict):
    """逐块读 DeepSeek 的 SSE 流，把增量正文 yield 出去，token 用量写进 usage。"""
    key = resolve_key()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    body = json.dumps({
        "model": resolve_model(),
        "messages": [
            {"role": "system", "content": STREAM_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(
                {"message": message, "context": context}, ensure_ascii=False
            )},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0,
        "max_tokens": 500,
    }).encode("utf-8")

    request = urllib.request.Request(
        DEEPSEEK_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw in response:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                usage_payload = chunk.get("usage") or {}
                if usage_payload.get("total_tokens"):
                    usage["tokens"] = int(usage_payload["total_tokens"])
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:200]
        raise RuntimeError(f"DeepSeek 调用失败（{exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 DeepSeek：{exc.reason}") from exc


def _find_json_objects(text: str) -> list[str]:
    """按大括号配平扫出全文里的 JSON 对象（跳过字符串内的括号）。"""
    found: list[str] = []
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                found.append(text[start:index + 1])
                start = None
    return found


def extract_plan(text: str) -> dict | None:
    """从流式全文里取出计划：优先 ```json 代码块，其次全文里最后一个合法 JSON。"""
    candidates: list[str] = []
    for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", text or ""):
        candidates.extend(_find_json_objects(block))
    candidates.extend(_find_json_objects(text or ""))

    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    return None


def _local_payload(local: dict, message: str = "", context: dict | None = None) -> dict:
    if local.get("type") == "clarify":
        return {
            "source": "local",
            "tokens": 0,
            "type": "clarify",
            "message": local.get("reason") or "需要补充一点信息",
            "question": local.get("question") or "",
            "missing": local.get("missing") or [],
            "suggestions": local.get("suggestions") or [],
            "options": local.get("options") or [],
            "fields": local.get("fields") or [],
        }
    plan = normalize(local, message, context)
    return {
        "source": "local",
        "tokens": 0,
        "message": plan.get("reason") or plan.get("goal") or "已生成计划",
        "plan": plan,
        "intent": intent_view(plan.get("intent") or {}),
        "steps": steps_for(plan),
        "needs_path": plan.get("needs_path", False),
        "hint": "执行前需要你确认；实际动作全部由本地接口完成，不再消耗 token。",
    }


def steps_for(plan: dict) -> list[dict]:
    """把计划拆成步骤列表随响应一起下发。

    多步的走计划树，单步的给一条——前端按长度决定渲染密度，
    不必再多发一次请求（那会在模型路径上白白多花一次 token）。
    """
    if not plan or plan.get("action") == "none":
        return []
    from . import agent_planner

    return agent_planner.plan_steps(plan)


def stream_chat(message: str, context: dict | None = None):
    """流式事件序列：phase（阶段）/ delta（增量正文）/ done（最终结果）/ error。

    与 chat() 共用同一套判定顺序：本地规则 → DeepSeek → 如实说明。
    本地规则命中时零 token，逐字吐出说明，让零 token 与「有反应」不矛盾。
    """
    context = context or {}
    text = (message or "").strip()
    if not text:
        yield {"type": "error", "message": "请输入内容"}
        return
    conversation_id = _conversation_id(context)
    save_memory(conversation_id, "user", text)
    context = _runtime_context(conversation_id, context)

    def finalize(payload: dict) -> dict:
        """把助手回复落库，并回带会话 id 与记忆条数——所有分支统一走这里，避免漏存。"""
        save_memory(conversation_id, "assistant", payload.get("message") or json.dumps(payload, ensure_ascii=False), payload=payload)
        return {**payload, "conversation_id": conversation_id, "memory_count": len(load_memory(conversation_id))}

    query = query_local(text, context)
    if query:
        yield {"type": "phase", "text": "读取本地状态"}
        yield {"type": "done", "payload": finalize(query)}
        return

    local = parse_local(text, context)
    if local:
        yield {"type": "phase", "text": "命中本地规则"}
        for piece in _chunks(local.get("reason") or local.get("question") or ""):
            yield {"type": "delta", "text": piece}
        payload = _local_payload(local, text, context)
        if payload.get("plan"):
            _remember_intent(conversation_id, payload["plan"], text)
        yield {"type": "done", "payload": finalize(payload)}
        return

    if not resolve_key():
        payload = fallback_clarify(text)
        for piece in _chunks(payload["message"]):
            yield {"type": "delta", "text": piece}
        yield {"type": "done", "payload": finalize(payload)}
        return

    yield {"type": "phase", "text": "查询 DeepSeek"}
    usage = {"tokens": 0}
    buffer = ""
    shown = 0
    for piece in _iter_deepseek_stream(text, context, usage):
        buffer += piece
        # 正文只取第一个代码围栏之前的部分：计划 JSON 不进气泡
        cut = buffer.find("```")
        body = buffer if cut < 0 else buffer[:cut]
        if len(body) > shown:
            yield {"type": "delta", "text": body[shown:]}
            shown = len(body)

    plan = extract_plan(buffer)
    body_text = (buffer if buffer.find("```") < 0 else buffer[:buffer.find("```")]).strip()
    if plan:
        plan = sanitize_plan(plan, text, context)
    if not plan or plan.get("action") == "none":
        plan = normalize(
            {"action": "none", "reason": (plan or {}).get("reason") or "模型没有给出可执行计划"},
            text, context,
        )
        payload = none_payload(plan, text, "deepseek", usage["tokens"])
        payload["message"] = body_text or payload["message"]
        yield {"type": "done", "payload": finalize(payload)}
        return

    _remember_intent(conversation_id, plan, text)

    yield {"type": "done", "payload": finalize({
        "source": "deepseek",
        "tokens": usage["tokens"],
        "message": body_text or plan.get("reason") or "已生成计划",
        "plan": plan,
        "intent": intent_view(plan.get("intent") or {}),
        "steps": steps_for(plan),
        "needs_path": plan.get("needs_path", False),
        "hint": "本回答消耗了 token；确认后执行由本地完成。",
    })}



"""Agent 智能助手 —— 全项目唯一可能消耗 token 的模块。

设计原则：
1. 先走「本地规则意图识别」，命中就零 token 出计划。
2. 本地认不出、且配置了 API Key，才调 DeepSeek（用标准库 urllib，不引第三方依赖）。
3. 没配 Key 就如实说明，绝不返回编造的建议。
4. Agent 只负责"把话翻译成计划"，真正执行必须由用户确认后走本地接口。
"""
from __future__ import annotations

import json
import fnmatch
import os
import re
import time
import urllib.error
import urllib.request

from .. import config, database, fsutil, secrets, store

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-v4.1-flash"

# 界面上可选的模型（也允许自己填）
MODEL_OPTIONS = ["deepseek-v4.1-flash", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"]

ALLOWED_ACTIONS = [
    "scan", "tree", "duplicate_scan", "junk_scan", "migrate_plan",
    "duplicate_clean", "junk_clean", "migrate_execute", "backup",
]

# 写类动作：会真正动到用户的文件。Agent 侧只负责「出计划」，执行前必须再过一道确认；
# 前端拿到 write=True 会换警告样式，readonly 档直接拒发。
WRITE_ACTIONS = {"duplicate_clean", "junk_clean", "migrate_execute", "backup"}

# 每个动作允许的参数键：模型多吐的字段一律丢弃
ACTION_PARAMS = {
    "scan": {"path", "min_size_mb", "extensions"},
    "tree": {"path", "min_size_mb", "extensions"},
    "duplicate_scan": {"path", "min_size_mb", "extensions"},
    "junk_scan": {"path"},
    "migrate_plan": {"source_dir", "target_dir", "min_size_mb", "extensions"},
    "duplicate_clean": {"path", "keep", "min_size_mb", "extensions"},
    "junk_clean": {"path"},
    "migrate_execute": {"source_dir", "target_dir", "min_size_mb", "extensions"},
    "backup": {"source_dir", "dest_dir"},
}

# 需要真实存在的目录（目标目录除外——迁移目标允许尚不存在，由 migrate 服务创建）
PATH_PARAMS = {
    "scan": ["path"], "tree": ["path"],
    "duplicate_scan": ["path"], "junk_scan": ["path"],
    "duplicate_clean": ["path"], "junk_clean": ["path"],
    "migrate_plan": ["source_dir"], "migrate_execute": ["source_dir"],
    "backup": ["source_dir"],
}

SYSTEM_PROMPT = """你是本地文件管理工具的指令翻译器。把用户的中文需求翻译成一个 JSON 计划。

只允许输出以下 action 之一（只处理与文件/磁盘相关的需求，无关需求用 none）：
- scan            扫描目录，统计数量/体积/类型分布/大文件
- tree            生成目录占用树（空间可视化）
- duplicate_scan  查找内容重复的文件
- junk_scan       查找垃圾文件（临时文件/缓存/日志/空目录等）
- migrate_plan    规划把文件从 A 目录迁移到 B 目录（只出清单，不动文件）
- duplicate_clean 清理重复文件（只删每组多余副本，进隔离区）
- junk_clean      清理垃圾文件（只清安全级，进隔离区）
- migrate_execute 真正执行搬移（复制+校验+删源）
- backup          把目录备份成压缩包

输出格式严格为：
{"action": "junk_clean", "reason": "一句话说明",
 "params": {"path": "C:\\\\", "min_size_mb": 1024, "extensions": ["mp4"], "keep": "newest"}, "needs_confirm": true}

参数规则：
- path / source_dir / target_dir / dest_dir 必须来自用户提到的真实路径，不要编造；没提到就省略该键。
- min_size_mb：用户说了「大于 1GB」这类体积条件才给，单位 MB。
- extensions：用户说了文件类型才给（如「视频」给 ["mp4","mkv","avi","mov"]，「图片」给 ["jpg","jpeg","png","gif","webp"]）。
- keep：用户说「只保留最新」才给 "newest"；默认不传（保留最旧的为原份）。
- 迁移/备份类必须同时给出来源与去向两个目录。
- 无法判断时输出 {"action": "none", "reason": "说明缺什么信息"}。
- 只输出 JSON，不要任何其他文字。"""

PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\s\"'，,。；;、]*)")
DRIVE_CN_RE = re.compile(r"([A-Za-z])\s*盘")

# 流式模式下让模型「先说话、再给计划」：正文可以边生成边显示，
# 计划仍锁在 ```json 代码块里，解析契约不变。
STREAM_SYSTEM_PROMPT = """你是本地文件管理工具的指令翻译器。
先用一句中文说明你的判断（不超过 40 个字），然后输出一个 JSON 代码块。

只允许以下 action 之一（只处理文件/磁盘相关需求，无关需求用 none）：
- scan            扫描目录
- tree            目录占用树
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


def save_memory(conversation_id: str, role: str, content: str) -> None:
    if role not in {"user", "assistant", "system"} or not content:
        return
    database.append_memory(
        config.DATABASE_FILE, conversation_id, role, content[:8000], fsutil.now_iso(), limit=MEMORY_KEEP
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
        value = match.group(1).rstrip("\\/")
        if value and value not in found:
            found.append(value)
    for match in DRIVE_CN_RE.finditer(text):
        value = f"{match.group(1).upper()}:\\"
        if value not in found:
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


def sanitize_plan(plan: dict) -> dict:
    """模型产出的计划过一道白名单：action 必须在表内、参数逐键过滤、数值 clamp、
    源路径必须真实存在（目标目录除外）。返回新的 plan，不修改入参。"""
    if not isinstance(plan, dict):
        return {"action": "none", "reason": "模型没有给出可执行计划"}
    action = plan.get("action")
    if action not in ALLOWED_ACTIONS:
        return {"action": "none", "reason": plan.get("reason") or "这个动作不在我能做的清单里"}

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
    needs_path = bool(missing)
    if missing and action != "backup":
        reason = plan.get("reason") or "请补充路径"
        plan = {**plan, "reason": f"{reason}（{'、'.join(missing)} 路径不存在或没提到）"}

    return {
        "action": action,
        "reason": plan.get("reason") or "已生成计划",
        "params": params,
        "needs_confirm": True,
        "write": action in WRITE_ACTIONS,
        "needs_path": needs_path,
    }


def parse_local(message: str) -> dict | None:
    """本地规则意图识别：命中返回计划，不消耗任何 token。

    默认先扫描再清理：用户说“清理垃圾”通常是在请求处理流程，不能跳过
    清单预览；只有明确说“立即删除/直接清理/执行清理”时才生成写操作计划。
    这样可避免自然语言中的“清理”绕过安全确认流程。"""
    text = (message or "").strip()
    if not text:
        return None
    paths = _extract_paths(text)
    first = paths[0] if paths else ""
    filters = _extract_filters(text)

    # ---- 写类（执行前仍需用户在计划卡上确认） ----
    if re.search(r"(备份|拷个备份)", text) and paths:
        return {
            "action": "backup",
            "label": "备份目录",
            "reason": "命中本地规则：目录备份",
            "params": {"source_dir": first, "dest_dir": paths[1] if len(paths) > 1 else ""},
            "needs_path": len(paths) < 2,
        }

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

    if re.search(r"(搬到|挪到|移动到|迁移|移过去)", text) and len(paths) >= 2:
        return {
            "action": "migrate_plan",
            "label": "规划跨盘迁移",
            "reason": "命中本地规则：跨盘迁移",
            "params": {"source_dir": paths[0], "target_dir": paths[1], **filters},
            "needs_path": False,
        }

    if re.search(r"(空间|占用|占多少|地图|可视化|谁在占)", text):
        return {
            "action": "tree",
            "label": "生成空间地图",
            "reason": "命中本地规则：目录占用可视化",
            "params": {"path": first},
            "needs_path": not first,
        }

    if re.search(r"(扫描|扫一下|扫扫|统计|看看有什么|体检)", text):
        return {
            "action": "scan",
            "label": "扫描目录",
            "reason": "命中本地规则：目录扫描",
            "params": {"path": first, **filters},
            "needs_path": not first,
        }

    return None


def _rule_explanation(path_text: str) -> dict:
    """Explain junk classification locally without reading or modifying the file."""
    path = path_text.strip().strip('"')
    name = os.path.basename(path).lower()
    for rule in config.JUNK_RULES:
        patterns = rule.get("patterns") or []
        if any(fnmatch.fnmatch(name, pattern.lower()) for pattern in patterns):
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


def query_local(message: str) -> dict | None:
    """Answer read-only status and explanation questions with zero tokens."""
    text = (message or "").strip()
    if not text:
        return None

    if re.search(r"(为什么|为何|原因).*(垃圾|清理|判定|规则)", text):
        paths = _extract_paths(text)
        return {
            "source": "local", "tokens": 0, "type": "explain",
            "message": "已根据本地规则解释文件判定原因。",
            "explanation": _rule_explanation(paths[0] if paths else text),
        }

    # 不能单独用“怎么样”作为本地总览意图；例如“今天天气怎么样”应交给
    # 未知意图处理，而不是误读本机磁盘状态。总览必须出现明确的本地状态词。
    if re.search(r"(磁盘|空间|任务|回收站|总体|概况|状态)", text):
        from . import overview

        data = overview.get_overview()
        return {
            "source": "local", "tokens": 0, "type": "overview",
            "message": "已读取本机当前状态。", "data": data,
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
        "checked_at": fsutil.now_iso(),
    }


def chat(message: str, context: dict | None = None) -> dict:
    context = context or {}
    text = (message or "").strip()
    if not text:
        raise ValueError("请输入内容")
    conversation_id = _conversation_id(context)
    save_memory(conversation_id, "user", text)
    context = {**context, "conversation_id": conversation_id, "memory": build_context_memory(conversation_id)}

    def finish(payload: dict) -> dict:
        payload = {**payload, "conversation_id": conversation_id, "memory_count": len(load_memory(conversation_id))}
        save_memory(conversation_id, "assistant", payload.get("message") or json.dumps(payload, ensure_ascii=False))
        return payload

    query = query_local(text)
    if query:
        return finish(query)

    local = parse_local(text)
    if local:
        return finish({
            "source": "local",
            "tokens": 0,
            "message": local["reason"],
            "plan": {
                "action": local["action"],
                "label": local["label"],
                "params": local["params"],
                "needs_confirm": True,
                "write": local["action"] in WRITE_ACTIONS,
            },
            "needs_path": local.get("needs_path", False),
            "hint": "执行前需要你确认；实际动作全部由本地接口完成，不再消耗 token。",
        })

    if resolve_key():
        result = _call_deepseek(text, context)
        plan = sanitize_plan(result["plan"])
        return finish({
            "source": "deepseek",
            "tokens": result["tokens"],
            "message": plan.get("reason") or "已生成计划",
            "plan": plan,
            "needs_path": plan.get("needs_path", False),
            "hint": "本回答消耗了 token；确认后执行由本地完成。",
        })

    return finish({
        "source": "none",
        "tokens": 0,
        "message": "本地规则没看懂这句话，而且没有配置 DEEPSEEK_API_KEY。",
        "plan": {"action": "none", "reason": "缺少可识别的意图"},
        "hint": "可以直接说：扫描 D 盘 / 查找 D 盘重复文件 / 清理 C 盘垃圾 / 把 D:\\大文件 搬到 E:\\归档",
    })


# ---------------------------------------------------------------------------
# 流式对话：边生成边显示，最后仍落回同一个「计划」契约
# ---------------------------------------------------------------------------

def _chunks(text: str, size: int = 3):
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


def _local_payload(local: dict) -> dict:
    return {
        "source": "local",
        "tokens": 0,
        "message": local["reason"],
        "plan": {
            "action": local["action"],
            "label": local["label"],
            "params": local["params"],
            "needs_confirm": True,
            "write": local["action"] in WRITE_ACTIONS,
        },
        "needs_path": local.get("needs_path", False),
        "hint": "执行前需要你确认；实际动作全部由本地接口完成，不再消耗 token。",
    }


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
    context = {**context, "conversation_id": conversation_id, "memory": build_context_memory(conversation_id)}

    def finalize(payload: dict) -> dict:
        """把助手回复落库，并回带会话 id 与记忆条数——所有分支统一走这里，避免漏存。"""
        save_memory(conversation_id, "assistant", payload.get("message") or json.dumps(payload, ensure_ascii=False))
        return {**payload, "conversation_id": conversation_id, "memory_count": len(load_memory(conversation_id))}

    query = query_local(text)
    if query:
        yield {"type": "phase", "text": "读取本地状态"}
        yield {"type": "done", "payload": finalize(query)}
        return

    local = parse_local(text)
    if local:
        yield {"type": "phase", "text": "命中本地规则"}
        for piece in _chunks(local["reason"]):
            yield {"type": "delta", "text": piece}
            time.sleep(0.015)
        yield {"type": "done", "payload": finalize(_local_payload(local))}
        return

    if not resolve_key():
        payload = {
            "source": "none",
            "tokens": 0,
            "message": "本地规则没看懂这句话，而且没有配置 DEEPSEEK_API_KEY。",
            "plan": {"action": "none", "reason": "缺少可识别的意图"},
            "hint": "可以直接说：扫描 D 盘 / 查找 D 盘重复文件 / 清理 C 盘垃圾 / 把 D://大文件 搬到 E://归档",
        }
        for piece in _chunks(payload["message"]):
            yield {"type": "delta", "text": piece}
            time.sleep(0.015)
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
        plan = sanitize_plan(plan)
    if not plan or plan.get("action") == "none":
        plan = {"action": "none", "reason": (plan or {}).get("reason") or "模型没有给出可执行计划"}

    yield {"type": "done", "payload": finalize({
        "source": "deepseek",
        "tokens": usage["tokens"],
        "message": body_text or plan.get("reason") or "已生成计划",
        "plan": plan,
        "needs_path": plan.get("needs_path", False),
        "hint": "本回答消耗了 token；确认后执行由本地完成。",
    })}

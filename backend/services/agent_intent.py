"""统一的 Agent 意图结构（AgentIntent）。

本地规则与模型输出都在这里**收敛成同一个协议**，此后全链路只有一种形状：

    {
      "goal": "释放 C 盘空间",
      "action": "junk_clean",
      "scope": {"path": "C:\\", "label": "C 盘"},
      "constraints": {
        "safe_only": true,
        "min_size_mb": null, "extensions": [], "keep": null,
        "min_free_space_mb": null,
        "exclude_tags": ["项目文件", "照片"],
        "exclude_names": ["node_modules", "Pictures"],
        "exclude_paths": ["D:\\Pictures"],
        "overwrite": null, "keep_structure": null, "delete_source": null
      },
      "risk_level": "medium",
      "needs_confirmation": true,
      "missing": [],
      "confidence": 0.8
    }

三条原则：
1. 意图结构只描述「要做什么」，绝不负责执行。
2. risk_level / needs_confirmation 一律本地确定性计算，模型给的值不采信。
3. 语义排除（"不要动项目和照片"）在这里解析成目录名剪枝 + 真实路径前缀过滤。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .. import config, fsutil
from .agent_tools import ACTION_PARAMS, PATH_PARAMS, WRITE_ACTIONS

# ---------------------------------------------------------------------------
# 一、同义表达归一（第一阶段：不再只依赖关键词）
#
# 用一张替换表把口语收敛到 canonical 说法，之后所有正则只需匹配一种写法。
# 顺序有意义：先长后短，避免「清理空间」被「清理」先吃掉。
# ---------------------------------------------------------------------------

SYNONYMS: list[tuple[str, str]] = [
    ("腾出空间", "清理垃圾"), ("腾空间", "清理垃圾"), ("释放空间", "清理垃圾"),
    ("瘦身", "清理垃圾"), ("清清盘", "清理垃圾"), ("清下盘", "清理垃圾"),
    ("盘满了", "清理垃圾"), ("磁盘满了", "清理垃圾"), ("空间不够", "清理垃圾"),
    ("占地方", "空间"), ("占地", "空间"), ("吃空间", "空间"),
    ("查重", "重复文件"), ("去重", "重复文件"), ("找重复", "重复文件"),
    ("重复的", "重复文件"),
    ("搬走", "迁移"), ("挪走", "迁移"), ("移走", "迁移"), ("搬过去", "迁移"),
    ("移过去", "迁移"), ("挪过去", "迁移"), ("搬一下", "迁移"),
    ("拷个备份", "备份"), ("存个备份", "备份"), ("做个备份", "备份"),
    ("清一下", "清理垃圾"), ("清清", "清理垃圾"), ("清垃圾", "清理垃圾"),
    ("垃圾文件", "垃圾"), ("临时文件", "垃圾"), ("缓存文件", "垃圾"),
    ("大文件", "大文件"), ("巨型文件", "大文件"),
    ("整理一下", "整理"), ("归置一下", "整理"), ("收拾一下", "整理"),
    ("体检", "扫描"), ("看看有什么", "扫描"), ("查一下", "扫描"),
    ("清空回收站", "清空回收站"),
]

# 负向约束：「不要动 X」「别碰 X」——这是第一阶段要求的「用户修改条件」
NEGATION_RE = re.compile(r"(不要动|别动|不许动|不能动|别碰|不要碰|不准动|保持不动|永远不要动|都不要动)")
NEGATION_SCOPE_RE = re.compile(
    r"(?:不要动|别动|不许动|不能动|别碰|不要碰|不准动|保持不动|永远不要动|都不要动)\s*"
    r"([^\s，,。；;、]{1,20})"
)

# 「只清理安全项」「只动安全的」——safe_only 显式开启
SAFE_ONLY_RE = re.compile(r"(只清(理)?安全|只动安全|只处理安全|仅安全级|只清安全的)")
# 「连建议确认的也清」——放开 confirm 级（caution 级永不放）
INCLUDE_CONFIRM_RE = re.compile(r"(建议确认的?也|连确认级|包括要确认的|谨慎的也清)")

# 体积下限："空间不够了""腾出 20G"
MIN_FREE_RE = re.compile(r"(?:至少|腾出|释放|留出|空出)\s*(\d+(?:\.\d+)?)\s*(GB|G|MB|M)", re.I)

# 上下文指代：第一阶段要求的「就刚才那个目录」
CONTEXT_REF_RE = re.compile(r"(就)?(刚才|刚刚|上次|之前|上一个|那个)(说的)?(那个)?(目录|文件夹|路径|盘|地方)")
CONTEXT_REF_ACTION_RE = re.compile(r"(刚才|刚刚|上次|之前|上一个)(那个|的)?(任务|操作|迁移|清理|扫描)")

# 口语盘符：「C 盘」「d 盘根目录」
DRIVE_CN_RE = re.compile(r"([A-Za-z])\s*盘")

# 排除标签的口语别名 → EXCLUDE_TAGS 的正式键
TAG_ALIASES: dict[str, str] = {
    "项目": "项目文件", "项目文件": "项目文件", "项目目录": "项目文件",
    "代码": "项目文件", "仓库": "项目文件", "工程": "项目文件",
    "照片": "照片", "相片": "照片", "图片库": "照片", "相册": "照片",
    "视频": "视频", "影片": "视频",
    "文档": "文档", "资料": "文档", "文件资料": "文档",
    "桌面": "桌面",
    "下载": "下载", "下载目录": "下载",
}


# 空间类口语带数字：「腾出 20G 空间」「释放 30 GB 容量」——先转成 canonical
SPACE_GOAL_RE = re.compile(
    r"(腾出|腾|释放|清出|省出|空出|挤出)\s*[\d.]*\s*(?:TB|GB|G|MB|M)?\s*(?:的)?\s*(空间|容量|地方)",
    re.I,
)

# 空间目标（可以不带数字）：「帮我腾出点空间」「C 盘满了」。
# 它决定这次要不要走多步骤的「释放空间」流水线，而不是单跑一次扫描。
SPACE_INTENT_RE = re.compile(
    r"(腾出|腾|释放|清出|省出|空出|挤出)\s*[\d.]*\s*(?:TB|GB|G|MB|M)?\s*(?:的)?\s*(空间|容量|地方)"
    r"|(空间|容量|盘)\s*(不够|满了|吃紧|见底)"
    r"|(磁盘|硬盘|C盘|D盘|盘)\s*(满了|爆了|红了)",
    re.I,
)


def canonical(text: str) -> str:
    """同义归一。返回归一后的文本；原文在调用方另行保留。

    先过一遍带数字的空间表达（正则），再过固定词组替换表。
    """
    value = text or ""
    value = SPACE_GOAL_RE.sub("清理垃圾", value)
    for source, target in SYNONYMS:
        if source != target and source in value:
            value = value.replace(source, target)
    return value


def extract_exclude_tags(text: str) -> list[str]:
    """抽取「不要动 / 排除 / 除了」后面的对象，映射成排除标签。"""
    value = text or ""
    tags: list[str] = []

    # 「不要动项目和照片」可能一次带多个，按分隔符切开逐个映射
    for match in NEGATION_SCOPE_RE.finditer(value):
        chunk = match.group(1)
        for piece in re.split(r"[和与跟、及]|以及", chunk):
            name = piece.strip()
            if not name:
                continue
            for alias, tag in TAG_ALIASES.items():
                if alias in name and tag not in tags:
                    tags.append(tag)

    # 显式排除表达：「排除照片」「除了项目」「不动 D:\素材」
    for match in re.finditer(r"(?:排除|除了|不含|跳过)\s*([^\s，,。；;、]{1,20})", value):
        name = match.group(1)
        for alias, tag in TAG_ALIASES.items():
            if alias in name and tag not in tags:
                tags.append(tag)

    return tags


def extract_exclude_paths(text: str) -> list[str]:
    """「不要动 D:\\素材」这类带真实路径的负向约束。"""
    found: list[str] = []
    for match in NEGATION_SCOPE_RE.finditer(text or ""):
        chunk = match.group(1)
        for piece in re.split(r"[和与跟、及]", chunk):
            candidate = piece.strip().strip('"')
            if re.match(r"^[A-Za-z]:[\\/]", candidate) and candidate not in found:
                found.append(candidate)
    return found


def resolve_excludes(tags: list[str], extra_paths: list[str] | None = None, prefs: dict | None = None) -> dict:
    """把语义标签解析成：目录名剪枝 + 真实路径前缀。

    - names 交给 fsutil.iter_files 做剪枝（按目录名匹配）
    - paths 用于事后过滤清单（按路径前缀匹配），两者互补
    """
    names: list[str] = []
    paths: list[str] = []

    for tag in tags or []:
        spec = config.EXCLUDE_TAGS.get(tag)
        if not spec:
            continue
        for name in spec.get("names") or []:
            if name not in names:
                names.append(name)

    # 标签里的「常见真实路径」按当前用户目录展开
    home = Path(os.path.expanduser("~"))
    for tag in tags or []:
        spec = config.EXCLUDE_TAGS.get(tag)
        for relative in (spec or {}).get("paths") or []:
            candidate = home / relative
            try:
                if candidate.exists() and str(candidate) not in paths:
                    paths.append(str(candidate))
            except OSError:
                continue

    for candidate in extra_paths or []:
        if candidate and candidate not in paths:
            paths.append(candidate)

    # 偏好记忆里长期生效的排除项
    saved = (prefs or {}).get("exclude_paths") or []
    for candidate in saved:
        if candidate and candidate not in paths:
            paths.append(candidate)
    for tag in (prefs or {}).get("exclude_tags") or []:
        spec = config.EXCLUDE_TAGS.get(tag)
        for name in (spec or {}).get("names") or []:
            if name not in names:
                names.append(name)

    return {"names": names, "paths": paths}


# ---------------------------------------------------------------------------
# 二、上下文指代解析（第一阶段：「就刚才那个目录」）
# ---------------------------------------------------------------------------

def resolve_reference(text: str, state: dict | None = None) -> dict:
    """把「刚才那个目录」替换成上一轮真实路径。

    返回 {"text": 替换后的文本, "resolved": [路径], "matched": bool}。
    解析不到就原样返回——由上层走追问分支，绝不猜一个路径出来。
    """
    state = state or {}
    value = text or ""
    last_scope = (state.get("last_scope") or "").strip()
    last_target = (state.get("last_target") or "").strip()

    if not CONTEXT_REF_RE.search(value):
        return {"text": value, "resolved": [], "matched": False}

    resolved: list[str] = []
    if last_scope:
        # 把指代短语整体换成真实路径，后续正则就能照常抽取
        value = CONTEXT_REF_RE.sub(last_scope, value, count=1)
        resolved.append(last_scope)
    elif last_target:
        value = CONTEXT_REF_RE.sub(last_target, value, count=1)
        resolved.append(last_target)

    return {"text": value, "resolved": resolved, "matched": bool(resolved)}


def resolve_action_reference(text: str, state: dict | None = None) -> dict:
    """「刚才的迁移怎么样了」——识别出在问某个已完成任务的进度。"""
    state = state or {}
    match = CONTEXT_REF_ACTION_RE.search(text or "")
    if not match:
        return {"matched": False}
    return {
        "matched": True,
        "keyword": match.group(3) or "",
        "last_action": state.get("last_action") or "",
        "last_job_id": state.get("last_job_id") or "",
        "last_job_title": state.get("last_job_title") or "",
    }


# ---------------------------------------------------------------------------
# 三、目标、约束、缺失与风险
# ---------------------------------------------------------------------------

ACTION_GOALS = {
    "scan": "看清一个目录里有什么",
    "tree": "找出谁在占空间",
    "virtual_preview": "预演一次整理",
    "projects_scan": "找出本机的项目",
    "duplicate_scan": "找出重复文件",
    "junk_scan": "找出可清理的垃圾文件",
    "junk_clean": "释放空间（清理垃圾）",
    "duplicate_clean": "释放空间（清理重复副本）",
    "migrate_plan": "规划一次跨盘迁移",
    "migrate_execute": "把文件搬到目标盘",
    "backup": "给目录留一份备份",
    "trash_restore": "把隔离区里的文件恢复回来",
    "job_cancel": "停掉正在跑的任务",
    "job_rollback": "撤销上一次任务",
    "open_tab": "打开对应功能页",
    "overview": "看本机当前状态",
    "jobs_list": "看任务中心",
    "trash_list": "看隔离区",
    "logs_list": "看操作日志",
}


def derive_goal(action: str, params: dict, message: str = "") -> str:
    base = ACTION_GOALS.get(action) or action
    path = params.get("path") or params.get("source_dir") or ""
    if path:
        return f"{base}：{path}"
    drive = DRIVE_CN_RE.search(message or "")
    if drive:
        return f"{base}：{drive.group(1).upper()} 盘"
    return base


def _parse_size_mb(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(1, min(int(float(value)), 1_000_000))
    except (TypeError, ValueError):
        return None


def build_constraints(
    plan_params: dict,
    message: str,
    prefs: dict | None = None,
    plan_constraints: dict | None = None,
    user_patch: dict | None = None,
) -> dict:
    """把「参数 + 原话 + 模型给的约束 + 用户就地修改 + 偏好」合成完整约束集。

    优先级从低到高：模型 → 原话 → **用户就地修改** → 永久约束（最后压回来）。

    为什么用户就地修改要单独一个通道：模型给的 `plan_constraints` 绝不允许
    改动排除项（否则模型一句话就能把「不要动项目」取消掉）；而「修改方案」
    是用户本人点出来的，本人当然可以调整自己的条件。两者不能共用一个入口。
    """
    given = plan_constraints or {}
    patch = user_patch or {}

    tags = extract_exclude_tags(message)
    explicit_paths = extract_exclude_paths(message)

    # 模型给的 exclude 里，能映射成标签的进 tags，是真实路径的进 explicit_paths
    for item in given.get("exclude") or []:
        name = str(item).strip()
        if not name:
            continue
        if re.match(r"^[A-Za-z]:[\\/]", name):
            if name not in explicit_paths:
                explicit_paths.append(name)
            continue
        for alias, tag in TAG_ALIASES.items():
            if alias in name and tag not in tags:
                tags.append(tag)

    # 用户就地修改：整组替换（不是追加），否则用户永远关不掉一条自己不想要的排除项
    if "exclude_tags" in patch:
        tags = [str(item) for item in (patch.get("exclude_tags") or []) if str(item) in config.EXCLUDE_TAGS][:20]
    if "exclude_paths" in patch:
        explicit_paths = [str(item).strip() for item in (patch.get("exclude_paths") or []) if str(item).strip()][:40]

    excludes = resolve_excludes(tags, explicit_paths, prefs)

    safe_only = True if given.get("safe_only") is None else bool(given.get("safe_only"))
    if INCLUDE_CONFIRM_RE.search(message):
        safe_only = False          # 用户显式要求把「建议确认」级也纳入；caution 级永不放
    if "safe_only" in patch:
        safe_only = bool(patch.get("safe_only"))

    min_free = None
    match = MIN_FREE_RE.search(message or "")
    if match:
        amount = float(match.group(1))
        if match.group(2).upper().startswith("G"):
            amount *= 1024
        min_free = max(1, int(amount))
    if min_free is None:
        min_free = _parse_size_mb(given.get("min_free_space_mb"))

    min_size = _parse_size_mb(plan_params.get("min_size_mb"))
    if "min_size_mb" in patch:
        min_size = _parse_size_mb(patch.get("min_size_mb"))

    extensions = list(plan_params.get("extensions") or [])
    if "extensions" in patch:
        extensions = [str(item).strip().lstrip(".")[:12] for item in (patch.get("extensions") or []) if str(item).strip()][:20]

    keep = plan_params.get("keep")
    if patch.get("keep") in ("newest", "original"):
        keep = patch["keep"]
    elif "keep" in patch and not patch.get("keep"):
        keep = None

    def pick(key):
        """用户改过就用用户的，没改过沿用模型给的。"""
        return patch[key] if key in patch else given.get(key)

    return {
        "safe_only": safe_only,
        "min_size_mb": min_size,
        "extensions": extensions,
        "keep": keep,
        "min_free_space_mb": min_free,
        "space_goal": bool(SPACE_INTENT_RE.search(message or "")),
        "exclude_tags": tags,
        "exclude_names": excludes["names"],
        "exclude_paths": excludes["paths"],
        "overwrite": pick("overwrite"),
        "keep_structure": pick("keep_structure"),
        "delete_source": pick("delete_source"),
        "migrate_mode": patch.get("migrate_mode") or (given.get("migrate_mode") if "migrate_mode" in given else None) or "move",
        "blocked_paths": [],
    }


# ---------------------------------------------------------------------------
# 二·五、就地修改方案（第七阶段：「修改方案」按钮）
#
# 只换条件重算，**不重新问模型、也不重新解析原话**——所以是零 token 且结果可预期。
# 用户改的是条件，不是意图；范围（scope）和动作（action）保持原样。
# ---------------------------------------------------------------------------

def sanitize_constraints(patch: dict) -> dict:
    """收敛用户在「修改方案」里改的条件。只认白名单键，其余一律丢弃。"""
    if not isinstance(patch, dict):
        return {}
    clean: dict = {}

    if "min_size_mb" in patch:
        clean["min_size_mb"] = _parse_size_mb(patch.get("min_size_mb"))
    if "extensions" in patch:
        raw = patch.get("extensions")
        if isinstance(raw, str):
            raw = re.split(r"[,\s、，;；]+", raw)
        clean["extensions"] = [
            str(item).strip().lstrip(".")[:12]
            for item in (raw or []) if str(item).strip()
        ][:20]
    if "keep" in patch:
        clean["keep"] = patch.get("keep") if patch.get("keep") in ("newest", "original") else None
    if "safe_only" in patch:
        clean["safe_only"] = bool(patch.get("safe_only"))
    if "exclude_tags" in patch:
        # 标签必须来自规则库：自由文本会变成「看起来排除了其实没排除」，比不排除更危险
        clean["exclude_tags"] = [
            str(item) for item in (patch.get("exclude_tags") or [])
            if str(item) in config.EXCLUDE_TAGS
        ][:20]
    if "exclude_paths" in patch:
        clean["exclude_paths"] = [
            str(item).strip()[:400] for item in (patch.get("exclude_paths") or [])
            if str(item).strip()
        ][:40]
    if "migrate_mode" in patch:
        # 只认后端真的会消费的两种：move 会删源、copy 不会
        clean["migrate_mode"] = patch.get("migrate_mode") if patch.get("migrate_mode") in ("move", "copy") else "move"
    for key in ("overwrite", "keep_structure", "delete_source"):
        if key in patch:
            clean[key] = None if patch.get(key) is None else bool(patch.get(key))

    return clean


def revise_payload(intent: dict, patch: dict) -> dict:
    """把原来的意图重新摊成一份 plan payload，条件部分交给 user_patch 通道覆盖。

    刻意不把 patch 并进 `constraints`：那样会跟「模型给的约束」混在一条路上，
    而这两者必须走不同通道（模型无权改排除项，用户有权）。
    """
    scope = intent.get("scope") or {}
    params = {}
    path = (scope.get("path") or "").strip()
    target = (scope.get("target") or "").strip()
    if path:
        params["path"] = path
    if target:
        params["target_dir"] = target

    return {
        "goal": intent.get("goal") or "",
        "action": intent.get("action") or "",
        "params": params,
        "constraints": dict(intent.get("constraints") or {}),
        "needs_confirm": True,
        "confidence": intent.get("confidence") or 0.9,
    }


def apply_hard_rules(constraints: dict, prefs: dict | None = None) -> dict:
    """偏好记忆里的「永久约束」在这里强制生效，模型、本地规则、用户就地修改都绕不过。

    被强制加回来的路径要记进 `blocked_paths`——用户在「修改方案」里删掉了它，
    界面上必须看得出「这条删不掉，是永久约束」，而不是删完没反应、以为是 bug。
    """
    constraints.setdefault("blocked_paths", [])
    for rule in (prefs or {}).get("hard_rules") or []:
        path = (rule or {}).get("path")
        if not path:
            continue
        if path not in constraints["exclude_paths"]:
            constraints["exclude_paths"].append(path)
            if path not in constraints["blocked_paths"]:
                constraints["blocked_paths"].append(path)
    return constraints


def derive_missing(action: str, params: dict) -> list[str]:
    """缺参推导：与 sanitize_plan 的存在性检查同源，但给出人话键名。"""
    missing: list[str] = []
    for key in PATH_PARAMS.get(action, []):
        value = params.get(key)
        if not value:
            missing.append(key)
            continue
        # 列表型参数（paths / ids）逐项查，别把 list 直接喂给 os.path.exists
        candidates = value if isinstance(value, (list, tuple)) else [value]
        if not all(os.path.exists(item) for item in candidates):
            missing.append(key)
    return missing


def _sensitive(path: str) -> str | None:
    """判断一个路径是否属于「敏感位置」：盘符根、只读区、受保护目录。"""
    if not path:
        return None
    try:
        target = Path(str(path).strip().strip('"'))
    except (OSError, ValueError):
        return None
    try:
        if target.parent == target:
            return "盘符根目录"
        if fsutil.is_readonly_zone(target):
            return "系统只读区"
        for protected in config.SAFETY_BLOCKLIST:
            try:
                if target == protected or protected in target.parents:
                    return "受保护目录"
            except (OSError, ValueError):
                continue
    except OSError:
        return None
    return None


def compute_risk(action: str, params: dict, constraints: dict, estimate: dict | None = None) -> tuple[str, str]:
    """风险等级**本地确定性计算**，不采信模型给的 risk_level。

    low    只读；或从隔离区恢复（不会删任何东西）
    medium 会移动或删除文件（可恢复与否由具体动作决定，见下方各分支）
    high   涉及盘符根、系统只读区、受保护目录，或批量对象超阈值
    """
    if action not in WRITE_ACTIONS:
        return "low", "只读操作，不会改动任何文件"

    if action in ("job_cancel", "job_rollback"):
        return "medium", "会中断或回滚正在执行的任务，已产生的记录会保留"

    if action == "trash_restore":
        return "low", "从隔离区往回恢复，不会删除任何文件"

    # 2026-10-14：就地整理只移动、不删除，且任务页可整体回滚 —— 风险按 low
    if action == "virtual_organize":
        return "low", "只把文件移动到分组子目录，不删除任何东西；任务页可整体回滚"

    # 2026-10-13「能力放开」：隔离区彻底删除 = 全项目唯一的物理删除口，**不可回滚**
    if action == "trash_purge":
        count = int((estimate or {}).get("file_count") or 0)
        return "high", (f"将物理删除隔离区 {count} 项，**不可恢复**（隔离区是最后一道缓冲，删了就没了）"
                        if count else "将物理删除隔离区内容，**不可恢复**")

    for key in ("path", "source_dir", "target_dir", "dest_dir"):
        value = params.get(key)
        if not value:
            continue
        hit = _sensitive(str(value))
        if hit:
            return "high", f"{value} 是{hit}，一旦误判影响面较大"

    count = int((estimate or {}).get("file_count") or 0)
    if count and count >= config.AGENT_HIGH_RISK_ITEMS:
        return "high", f"预计涉及 {count} 项，超过 {config.AGENT_HIGH_RISK_ITEMS} 项阈值，建议分批"

    if action == "migrate_execute":
        return "medium", "会复制并校验后删除源文件，全程有隔离区兜底"

    # 相似图 / 大文件：动的是用户数据（不是垃圾），提醒语要说清"删的是你的文件"。
    # 清理类现在是「确认后即永久删」（进隔离区 + 立刻清空同批），不再写"可恢复"。
    if action in ("similar_clean", "large_clean"):
        return "medium", "会永久删除你选中的文件，不可恢复；这些不是垃圾，删前请看清清单"

    # 备份是纯产出：只写一份 ZIP，不动任何原文件
    if action == "backup":
        return "low", "只会生成一份 ZIP 备份，不动任何原文件"

    mib = constraints.get("min_size_mb")
    hint = f"（仅处理 ≥ {mib} MB 的对象）" if mib else ""
    return "medium", f"确认后即永久删除，不可恢复{hint}"


def build_estimate(action: str, params: dict, estimate: dict | None = None) -> dict:
    """预计释放空间 / 文件数 / 耗时。

    规划阶段拿不到真实数据时 known=False —— 前端据此显示「待分析」，
    绝不用编造的数字糊弄。真实值在多步骤流水线跑完分析步后回填。
    """
    data = dict(estimate or {})
    if data.get("file_count") is not None:
        data.setdefault("known", True)
        data.setdefault("batches", max(1, (int(data["file_count"]) + config.AGENT_BATCH_SIZE - 1)
                                       // config.AGENT_BATCH_SIZE))
    else:
        data = {"known": False, "file_count": None, "bytes": None,
                "free_text": None, "seconds": None, "duration_text": None, "batches": None}
    data.setdefault("batch_size", config.AGENT_BATCH_SIZE)
    return data


# ---------------------------------------------------------------------------
# 四、主动追问（第四阶段）
# ---------------------------------------------------------------------------

ORGANIZE_QUESTION = {
    "id": "organize_mode",
    "question": "你希望按哪种方式整理？",
    "options": [
        {"id": "type", "label": "按类型", "note": "图片 / 视频 / 文档 / 压缩包…"},
        {"id": "time", "label": "按月份", "note": "本月 / 近三个月 / 今年 / 更早"},
        {"id": "project", "label": "按项目", "note": "按项目标记聚拢"},
        {"id": "preview", "label": "只生成预览，不移动文件", "note": "最安全，只出清单"},
    ],
}

MIGRATE_QUESTIONS = [
    {"id": "source_dir", "question": "来源目录是哪一个？", "kind": "path"},
    {"id": "target_dir", "question": "D 盘下的目标目录是什么？", "kind": "path"},
    {"id": "overwrite", "question": "是否允许覆盖同名文件？", "kind": "bool", "default": False},
    {"id": "keep_structure", "question": "是否保留原目录结构？", "kind": "bool", "default": True},
    {"id": "delete_source", "question": "是否需要完成后删除源文件？", "kind": "bool", "default": False},
]


def clarify_questions(action: str, message: str, params: dict, constraints: dict) -> dict | None:
    """返回一个追问结构；不需要追问时返回 None。

    两种形态：
    - 选项式（options）：把选择权摆在用户面前，点一下就能继续
    - 缺参式（missing + fields）：列出还差哪些信息
    """
    text = message or ""

    if action == "virtual_preview":
        # 「整理下载文件夹」——不直接执行，先问按什么方式整理
        return {
            "type": "clarify",
            "reason": "整理方式会直接影响结果，我先问清楚再生成方案。",
            "question": ORGANIZE_QUESTION["question"],
            "missing": [],
            "suggestions": ["例如：按月份整理", "例如：只生成预览"],
            "options": ORGANIZE_QUESTION["options"],
            "fields": [],
        }

    if action in ("migrate_plan", "migrate_execute"):
        missing = [key for key in ("source_dir", "target_dir") if not params.get(key)]
        if missing or re.search(r"(搬到|挪到|移动|迁移)", text):
            labels = {"source_dir": "来源目录", "target_dir": "目标目录"}
            return {
                "type": "clarify",
                "reason": "迁移会动到真实文件，我要把来源、去向和覆盖策略都确认清楚。",
                "question": "这次迁移还差几条信息：",
                "missing": missing,
                "suggestions": ["例如：把 D:\\资料搬到 E:\\归档，保留目录结构，完成后删除源文件"],
                "options": [],
                "fields": [
                    {**item, "label": labels.get(item["id"], item["question"])}
                    for item in MIGRATE_QUESTIONS
                    if item["id"] in missing or (not missing and item["kind"] == "bool")
                ],
            }

    if action == "backup":
        missing = [key for key in ("source_dir", "dest_dir") if not params.get(key)]
        if missing:
            return {
                "type": "clarify",
                "reason": "我可以帮你备份目录，但还需要知道源目录和备份目标位置。",
                "question": "要备份哪个目录？ZIP 文件保存到哪里？",
                "missing": missing,
                "suggestions": ["例如：把 D:\\项目备份到 E:\\备份"],
                "options": [],
                "fields": [
                    {"id": key, "question": "来源目录" if key == "source_dir" else "备份到", "kind": "path"}
                    for key in missing
                ],
            }

    if action == "trash_restore":
        if not params.get("path"):
            entries = []
            try:
                from . import clean
                entries = (clean.list_trash().get("entries") or [])[:5]
            except (OSError, ValueError):
                entries = []
            return {
                "type": "clarify",
                "reason": "隔离区里可能有多个同名的东西，我需要你指明是哪一个。",
                "question": "要恢复隔离区里的哪一项？",
                "missing": ["path"],
                "suggestions": [item.get("original_path") or item.get("path") or "" for item in entries],
                "options": [],
                "fields": [{"id": "path", "question": "要恢复的原始路径", "kind": "path"}],
            }

    return None


# ---------------------------------------------------------------------------
# 五、统一入口
# ---------------------------------------------------------------------------

def normalize(plan: dict, message: str = "", context: dict | None = None) -> dict:
    """把任意来源的计划（本地规则 / 模型 JSON）收敛成统一结构。

    返回的字典**兼容旧契约**（保留 action/reason/params/label/write/needs_path），
    同时挂上新的 intent / risk_level / constraints / estimate / missing 字段，
    因此前端可以逐段迁移，不会一次性崩掉。
    """
    context = context or {}
    prefs = context.get("prefs") or {}
    action = plan.get("action") or "none"
    params = dict(plan.get("params") or {})

    constraints = build_constraints(
        params, message, prefs, plan.get("constraints"), (context or {}).get("revise")
    )
    constraints = apply_hard_rules(constraints, prefs)

    missing = derive_missing(action, params) if action in ACTION_PARAMS else []
    # 被永久约束挡掉的路径，直接算作「缺参数」——让用户在计划阶段就看到
    blocked = [
        value for key in ("path", "source_dir", "target_dir", "dest_dir")
        if (value := params.get(key))
        and any(str(value).lower().startswith(str(rule).lower())
                for rule in constraints["exclude_paths"])
    ]
    constraints["blocked_paths"] = blocked

    estimate = build_estimate(action, params, plan.get("estimate"))
    risk_level, risk_reason = compute_risk(action, params, constraints, estimate)

    scope_path = params.get("path") or params.get("source_dir") or ""
    intent = {
        "goal": plan.get("goal") or derive_goal(action, params, message),
        "action": action,
        "scope": {
            "path": scope_path,
            "target": params.get("target_dir") or params.get("dest_dir") or "",
            "label": _scope_label(scope_path, message),
        },
        "constraints": constraints,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "needs_confirmation": action in WRITE_ACTIONS or bool(plan.get("needs_confirm", False)),
        "missing": missing,
        "confidence": float(plan.get("confidence") or (0.9 if plan.get("source") == "local" else 0.7)),
    }

    return {
        **plan,
        "action": action,
        "params": params,
        "label": plan.get("label") or ACTION_GOALS.get(action) or "计划",
        "goal": intent["goal"],
        "intent": intent,
        "scope": intent["scope"],
        "constraints": constraints,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "estimate": estimate,
        "missing": missing,
        "needs_path": bool(missing),
        "write": action in WRITE_ACTIONS,
        "needs_confirm": intent["needs_confirmation"],
    }


def _scope_label(path: str, message: str) -> str:
    if path:
        drive = Path(str(path)).drive
        if drive and str(path).rstrip("\\/") == drive.rstrip("\\/"):
            return f"{drive[0].upper()} 盘"
        return Path(str(path)).name or str(path)
    drive = DRIVE_CN_RE.search(message or "")
    return f"{drive.group(1).upper()} 盘" if drive else "未指定"


def intent_view(intent: dict) -> dict:
    """给前端的精简视图：只暴露展示需要的字段，不泄露内部键。"""
    constraints = intent.get("constraints") or {}
    return {
        "goal": intent.get("goal"),
        "scope": intent.get("scope"),
        "risk_level": intent.get("risk_level"),
        "risk_reason": intent.get("risk_reason"),
        "needs_confirmation": intent.get("needs_confirmation"),
        "missing": intent.get("missing") or [],
        "confidence": intent.get("confidence"),
        "constraints": {
            "safe_only": constraints.get("safe_only"),
            "min_size_mb": constraints.get("min_size_mb"),
            "extensions": constraints.get("extensions") or [],
            "keep": constraints.get("keep"),
            "min_free_space_mb": constraints.get("min_free_space_mb"),
            "space_goal": bool(constraints.get("space_goal")),
            "exclude_tags": constraints.get("exclude_tags") or [],
            "exclude_paths": constraints.get("exclude_paths") or [],
            "overwrite": constraints.get("overwrite"),
            "keep_structure": constraints.get("keep_structure"),
            "delete_source": constraints.get("delete_source"),
            "migrate_mode": constraints.get("migrate_mode") or "move",
            "blocked_paths": constraints.get("blocked_paths") or [],
        },
    }

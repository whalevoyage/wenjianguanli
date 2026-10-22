"""全局配置与规则库。

所有"什么能碰、什么不能碰、什么算垃圾"的判断都集中在这里，
其他模块只引用，不自己写死规则。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import string
import sys
from functools import lru_cache
from pathlib import Path

# 全项目唯一版本号：/api/health、FastAPI 元信息、MCP handshake 都读这里。
# 以前散在 3 处硬编码，改漏了就会出现「界面说 2.1.0、health 说 2.0.0」。
APP_VERSION = "2.1.1"

APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def _state_dir() -> Path:
    configured = os.getenv("FILEPILOT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        local_app_data = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local_app_data) / "Atlas"
    return APP_ROOT / ".filepilot"


STATE_DIR = _state_dir()
STATE_DIR.mkdir(exist_ok=True)


def _safe_home() -> Path:
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return Path("C:/Users")


JOBS_FILE = STATE_DIR / "jobs.json"
LOG_FILE = STATE_DIR / "logs.json"
RECIPES_FILE = STATE_DIR / "recipes.json"
CONFIG_FILE = STATE_DIR / "config.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
TRASH_MANIFEST = STATE_DIR / "trash.json"
SNAPSHOT_FILE = STATE_DIR / "snapshots.json"
DATABASE_FILE = STATE_DIR / "filepilot.db"

# 隔离区：普通手动清理默认先把文件暂存于此；永久清理会走单独的确认流程。
# 实际使用时优先落到「源文件所在盘」的同盘隔离区，避免跨盘移动（慢且易失败），
# 这里的 TRASH_DIR 只作为兜底位置。
TRASH_DIR = Path(os.getenv("FILEPILOT_TRASH") or (_safe_home() / "FilePilotTrash"))
TRASH_DIR_NAME = "_filepilot_trash"

MAX_LOG_ENTRIES = 500
MAX_JOBS_KEPT = 200

# 大文件阈值（字节）
LARGE_FILE_THRESHOLD = 100 * 1024 * 1024

# 目录树默认展开层数
TREE_DEFAULT_DEPTH = 3

# 扫描限流：单次遍历最多处理的条目数，超出则截断并提示
SCAN_ENTRY_LIMIT = int(os.getenv("FILEPILOT_SCAN_LIMIT") or 400000)

# ---------------------------------------------------------------------------
# 文件分类
# ---------------------------------------------------------------------------

FILE_CATEGORIES: dict[str, set[str]] = {
    "图片": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico", ".heic", ".raw"},
    "视频": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"},
    "音频": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma"},
    "文档": {".doc", ".docx", ".pdf", ".txt", ".md", ".rtf", ".odt", ".epub"},
    "表格": {".xls", ".xlsx", ".csv", ".tsv", ".ods"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "代码": {".py", ".js", ".ts", ".vue", ".html", ".css", ".json", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".cs"},
    "程序": {".exe", ".msi", ".dll", ".so", ".dylib", ".apk", ".appx", ".deb", ".rpm"},
}
CATEGORY_ORDER = list(FILE_CATEGORIES.keys()) + ["其他"]

# 扩展名 → 分类 的反向表，导入时建一次。
# 先出现的分类赢 —— 与原来"按 FILE_CATEGORIES 顺序找第一个命中"完全一致。
_EXT_CATEGORY: dict[str, str] = {}
for _cat, _exts in FILE_CATEGORIES.items():
    for _ext in _exts:
        _EXT_CATEGORY.setdefault(_ext, _cat)


def category_for(path: Path | str) -> str:
    """按扩展名归类。

    原来每次 `Path(path).suffix` —— pathlib 造对象 + `_parse_path` 逐文件都不便宜
    （实测 scan 里占 20%）。改成 `os.path.splitext` + 一次字典查找。
    语义与原实现一致：目录名里的点不算（splitext 只看最后一个分隔符之后）、
    `.gitignore` 这类前导点文件仍然没有扩展名。
    """
    ext = os.path.splitext(path if isinstance(path, str) else str(path))[1].lower()
    return _EXT_CATEGORY.get(ext) or "其他"


# ---------------------------------------------------------------------------
# glob 匹配：**预编译成一条正则**，别再逐次 fnmatch
# ---------------------------------------------------------------------------

@lru_cache(maxsize=256)
def glob_matcher(patterns: tuple[str, ...]) -> re.Pattern | None:
    """把一组 glob 预编译成一条正则，用 `.match(name)` 匹配**已 lower** 的名字。

    等价于 `any(fnmatch.fnmatchcase(name, p) for p in patterns)`（名字与模式都先 lower），
    但只编译一次。

    **为什么要换掉 `fnmatch.fnmatch`**：它对**名字和模式各调一次** `os.path.normcase`，
    在 Windows 上那是 `LCMapStringEx` 系统调用。逐文件 × 逐模式地调用时纯是开销 ——
    实测占垃圾扫描一半时间、虚拟整理三成。规则表在导入时编译，之后每个文件只花一次正则。
    """
    pats = tuple(p.lower() for p in patterns if p)
    if not pats:
        return None
    return re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in pats))


# ---------------------------------------------------------------------------
# 默认排除项（用户可在界面上增减，但"安全黑名单"不可绕过）
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDES: list[str] = [
    "node_modules",
    ".git",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
    ".cache",
]

# 隐藏但始终生效的排除（不展示给用户，避免误关）
IMPLICIT_EXCLUDES: list[str] = [
    ".filepilot",
    ".filepilot_trash",
    "_filepilot_trash",
    "FilePilotTrash",
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
    "Windows.old",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
]


# ---------------------------------------------------------------------------
# 写保护分层与权限档位
#
# 软保护：日常不该动的目录，「完全访问」档放开（桌面）。
# 自保护：任何档位都不放开——工具的运行状态、自身的仓库、以及隔离区账本，
#         一旦被自己清理掉，隔离区与任务记录就散了。
# ---------------------------------------------------------------------------
SOFT_PROTECTED_DIRS: list[Path] = [
    _safe_home() / "Desktop",
    _safe_home() / "桌面",
]

SELF_PROTECTED_DIRS: list[Path] = [
    _safe_home() / ".workbuddy",
    APP_ROOT,
    STATE_DIR,
    TRASH_DIR,
]

# 软保护 + 自保护的合集。
# **只用于「写」语义**（风险评级：目标落在这里就该抬高风险等级）。
# 读取遍历不要用它 —— 软保护（桌面）只限制写，读必须放行，
# 否则「扫描 C 盘」会静默漏掉桌面。读剪枝请用 fsutil.is_self_protected()。
SAFETY_BLOCKLIST: list[Path] = SOFT_PROTECTED_DIRS + SELF_PROTECTED_DIRS

# 权限档位：readonly / standard / full
# 「完全访问」仍受底限约束：手动普通清理默认隔离、自保护目录不写、切换需显式确认
PERMISSION_MODES: list[dict] = [
    {
        "id": "readonly",
        "label": "只读",
        "tone": "locked",
        "desc": "只允许扫描、预览与生成计划；任何写操作在入口就被拒绝",
    },
    {
        "id": "standard",
        "label": "标准",
        "tone": "quiet",
        "desc": "桌面与系统目录受保护，其余目录可正常清理与迁移",
    },
    {
        "id": "full",
        "label": "完全访问",
        "tone": "warning",
        "desc": "允许改动桌面与系统目录；手动普通清理默认进隔离区，自保护目录仍不可写",
    },
]

DEFAULT_PERMISSION_MODE = "standard"
PERMISSION_MODE_IDS: list[str] = [item["id"] for item in PERMISSION_MODES]

# 会写文件的任务类型（「只读」档要拦的入口）
WRITE_JOB_TYPES: set[str] = {"clean", "migrate", "backup"}

# 这些系统目录允许"读"，但禁止任何写/删/移动（「完全访问」档放开）
SYSTEM_READONLY_PREFIXES: list[str] = [
    "c:\\windows",
    "c:\\program files",
    "c:\\program files (x86)",
    "c:\\programdata",
    "c:\\$recycle.bin",
    "c:\\system volume information",
]


# ---------------------------------------------------------------------------
# 垃圾文件规则库：三级安全分级
# ---------------------------------------------------------------------------

JUNK_RULES: list[dict] = [
    # ---- 安全（默认勾选）----
    {
        "id": "temp",
        "name": "临时文件",
        "level": "safe",
        "desc": "系统与软件产生的 .tmp / .temp / ~$ 临时文件",
        "patterns": ["*.tmp", "*.temp", "~$*", "*.part", "*.crdownload"],
    },
    {
        "id": "log",
        "name": "日志文件",
        "level": "safe",
        "desc": ".log 日志文件，通常可安全删除",
        "patterns": ["*.log", "*.log.1", "*.log.old"],
    },
    {
        "id": "bak",
        "name": "备份残留",
        "level": "safe",
        "desc": ".bak / .old / .orig 等编辑器与工具残留",
        "patterns": ["*.bak", "*.old", "*.orig", "*.rej"],
    },
    {
        "id": "empty_dir",
        "name": "空文件夹",
        "level": "safe",
        "desc": "不含任何文件与子目录的空文件夹",
        "patterns": [],
    },
    {
        "id": "zero_byte",
        "name": "0 字节文件",
        "level": "safe",
        "desc": "大小为 0 的文件，通常是中断的下载或写入",
        "patterns": [],
    },
    {
        "id": "thumb_cache",
        "name": "缩略图缓存",
        "level": "safe",
        "desc": "系统缩略图与图片缓存",
        "patterns": ["thumbcache_*.db", "*.thumbcache", "Thumbs.db"],
    },
    # ---- 确认（默认不勾选）----
    {
        "id": "installer",
        "name": "安装包",
        "level": "confirm",
        "desc": "下载目录里的安装程序，装完通常不再需要",
        "patterns": ["*.exe", "*.msi", "*.dmg", "*.pkg", "*.deb", "*.rpm"],
    },
    {
        "id": "archive",
        "name": "压缩包",
        "level": "confirm",
        "desc": "压缩包，解压后通常可删，请先确认",
        "patterns": ["*.zip", "*.rar", "*.7z", "*.tar.gz"],
    },
    {
        "id": "browser_cache",
        "name": "浏览器缓存",
        "level": "confirm",
        "desc": "浏览器缓存目录，删除后首次打开网页会变慢",
        "patterns": [],
    },
    {
        "id": "crash_dump",
        "name": "崩溃转储",
        "level": "confirm",
        "desc": ".dmp 崩溃转储文件，除非在排查问题否则可删",
        "patterns": ["*.dmp", "*.mdmp", "*.hdmp"],
    },
    # ---- 谨慎（默认隐藏）----
    {
        "id": "huge_file",
        "name": "超大文件",
        "level": "caution",
        "desc": "大于 1GB 的文件，极可能是个人资料，务必逐个确认",
        "patterns": [],
    },
]

JUNK_LEVEL_META = {
    "safe": {"label": "安全", "note": "默认勾选，可放心清理"},
    "confirm": {"label": "建议确认", "note": "默认不勾选，需要你逐个确认"},
    "caution": {"label": "谨慎", "note": "默认隐藏，可能是个人文件"},
}

HUGE_FILE_BYTES = 1024 ** 3


# ---------------------------------------------------------------------------
# 规则库的「用户覆盖」
# ---------------------------------------------------------------------------
#
# ★ 用户不需要为了加一条规则去改代码。
#   把 `junk_rules.json` 放到数据目录（STATE_DIR）里就能**覆盖同 id 的规则**或**追加新规则**：
#
#       [
#         {"id": "log", "name": "日志文件", "level": "confirm",
#          "desc": "改成默认不勾选了", "patterns": ["*.log"], "reason": "我常用日志排查问题"},
#         {"id": "my_cache", "name": "我的缓存目录", "level": "safe",
#          "desc": "XX 软件重建得出来的缓存", "patterns": ["*\\XXCache\\*"]}
#       ]
#
# 三条设计约束：
#   1. **内置默认永远在**：文件不存在 / 读不动 / 整份 JSON 坏了 → 用内置表照常跑。
#   2. **逐条校验**：坏的那条跳过并记进 `JUNK_RULE_ISSUES`，**一条坏规则不能把整个扫描拖垮**。
#   3. **只做覆盖与追加，不做删除**：想停用某条内置规则用 `"disabled": true`，而不是靠删不掉它。
#      （这条是跟 MangoDisk 学的：规则可以改，但不给自己留"悄悄少了一条安全规则"的口子。）

JUNK_RULES_FILE = STATE_DIR / "junk_rules.json"

# 校验不过的规则（给诊断页和用户看的），形如 ["第 2 条 my_cache：level 只能是 safe/confirm/caution"]
JUNK_RULE_ISSUES: list[str] = []

_REQUIRED_RULE_FIELDS = ("id", "name", "level", "desc", "patterns")
_VALID_LEVELS = {"safe", "confirm", "caution"}


def validate_junk_rule(rule: object, index: int) -> str | None:
    """校验一条规则。通过返回 None，否则返回**人话写的原因**（要能直接显示给用户）。"""
    if not isinstance(rule, dict):
        return f"第 {index} 条：不是对象"
    rule_id = rule.get("id")
    label = rule_id if isinstance(rule_id, str) and rule_id else f"第 {index} 条"

    # 只想停用某条内置规则时，写 {"id": "...", "disabled": true} 就够了 ——
    # 逼用户把整条规则抄一遍是没道理的（而且抄错一个字段整条就失效）。
    if rule.get("disabled") is True and set(rule) <= {"id", "disabled"}:
        if not isinstance(rule_id, str) or not rule_id.strip():
            return f"第 {index} 条：只写 disabled 时必须给 id"
        return None

    for field in _REQUIRED_RULE_FIELDS:
        if field not in rule:
            return f"{label}：缺少字段 {field}"
    for field in ("id", "name", "desc"):
        if not isinstance(rule[field], str) or not rule[field].strip():
            return f"{label}：{field} 必须是非空字符串"
    if rule["level"] not in _VALID_LEVELS:
        return f"{label}：level 只能是 {'/'.join(sorted(_VALID_LEVELS))}，收到 {rule['level']!r}"
    patterns = rule["patterns"]
    if not isinstance(patterns, list) or any(not isinstance(item, str) for item in patterns):
        return f"{label}：patterns 必须是字符串数组"
    if rule.get("disabled") is not None and not isinstance(rule["disabled"], bool):
        return f"{label}：disabled 只能是 true/false"
    return None


def _load_user_junk_rules() -> list[dict]:
    """读数据目录里的用户规则。任何一步出错都退回空列表（用内置表跑）。"""
    try:
        raw = JUNK_RULES_FILE.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return []
    try:
        data = json.loads(raw)
    except ValueError as exc:
        JUNK_RULE_ISSUES.append(f"junk_rules.json 不是合法 JSON：{exc}")
        return []
    if not isinstance(data, list):
        JUNK_RULE_ISSUES.append("junk_rules.json 的顶层必须是数组")
        return []

    valid: list[dict] = []
    for index, rule in enumerate(data, 1):
        problem = validate_junk_rule(rule, index)
        if problem:
            JUNK_RULE_ISSUES.append(problem)
            continue
        valid.append(rule)
    return valid


def merge_junk_rules(builtin: list[dict], user: list[dict]) -> list[dict]:
    """同 id 覆盖（保序），新 id 追加；`disabled: true` 的从结果里去掉。"""
    merged: list[dict] = []
    by_id: dict[str, int] = {}
    for rule in builtin:
        by_id[rule["id"]] = len(merged)
        merged.append(dict(rule))
    for rule in user:
        slot = by_id.get(rule["id"])
        if slot is None:
            by_id[rule["id"]] = len(merged)
            merged.append(dict(rule))
        else:
            merged[slot] = {**merged[slot], **rule}
    return [rule for rule in merged if not rule.get("disabled")]


def reload_junk_rules() -> list[str]:
    """重新读一次用户规则并重建 JUNK_RULES。返回这次遇到的问题（空表示全好）。

    改完 `junk_rules.json` 想立刻生效时调用；正常情况下模块导入时已经加载过一次。
    """
    global JUNK_RULES
    JUNK_RULE_ISSUES.clear()
    JUNK_RULES = merge_junk_rules(JUNK_RULES_BUILTIN, _load_user_junk_rules())
    return list(JUNK_RULE_ISSUES)


# 内置表的原名保留一份 —— 用户规则是「覆盖」它，不是替代它
JUNK_RULES_BUILTIN = JUNK_RULES
JUNK_RULES = merge_junk_rules(JUNK_RULES_BUILTIN, _load_user_junk_rules())


# 查重：「读文件头」与「算完整哈希」合并成一次打开的文件大小上限。
#
# 一次 open 在 Windows 上约 0.22ms，而多读 16KB 只约 0.016ms —— 所以对这么小的文件，
# 与其「开一次读 4KB 算头哈希、需要时再开一次读全量」，不如**一次读完、两个哈希一起算**。
# 盈亏平衡点：只要 7% 的候选真的会进入完整哈希阶段就稳赚；最坏情况（全部被头哈希剪掉）
# 每个文件也只多读 16KB，**损失有界且很小**。
# 阈值不能开大：实测 1MB 时有利场景反而掉到 1.34×、2MB 掉到 0.88×（读全量的代价反超）。
DUP_SMALL_FILE_BYTES = 16 * 1024


# 这些格式本身已是压缩编码，再 deflate 一次几乎不省体积、却慢好几倍：
# 实测 72MB 的 mp4 —— deflate 6 花了 3.13s、产物 72.0MB；直接 STORED 只要 **0.43s**。
# 只列「确定压不动」的媒体与压缩容器；**exe / dll / pdf 不在此列**（它们常能压 20~50%），
# bmp / wav 这类无损原始数据也保留压缩（能压 30~70%）。
NON_COMPRESSIBLE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".raw",
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v",
    ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".opus", ".aac",
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".zst", ".iso", ".dmg",
    ".woff", ".woff2",
})


# **同一时刻最多几个读盘**（进程级预算，`fsutil._io_slot` 的信号量按它建）。
# 两层哈希剩下的唯一大头是**打开文件**，而 Windows 的 CreateFile /
# readinto / hashlib.update(>2KB) 都释放 GIL —— 实测 8 路下整文件哈希 2.2×。
# **封顶 8**：机械盘上并发读会因寻道互相拖累，所以也留了环境变量给人调小。
# 注意它是**全局**的：任务少时能跑满 8 路，两个查重同时跑就各分一半 ——
# 否则 8×2 = 16 路并发读盘反而更慢（实测每个慢 2.3×、总吞吐低 20%）。
HASH_WORKERS = max(1, min(int(os.getenv("FILEPILOT_HASH_WORKERS") or 8), os.cpu_count() or 4))


@lru_cache(maxsize=8)
def junk_matchers(skip: frozenset[str] = frozenset()) -> tuple[tuple[dict, "re.Pattern | None"], ...]:
    """JUNK_RULES 的「(规则, 预编译正则)」有序表，`skip` 里的规则不参与模式匹配。

    垃圾扫描、Agent 的垃圾查询、垃圾解释三处共用同一张表 —— 规则仍只写在 JUNK_RULES，
    只是把 glob 编译一次（见 `glob_matcher` 的注释：逐个 fnmatch 是纯开销）。
    三处的 skip 集合略有不同（谁单独处理零字节/空目录），所以做成参数。
    """
    return tuple(
        (rule, glob_matcher(tuple(rule.get("patterns") or ())))
        for rule in JUNK_RULES
        if rule["id"] not in skip
    )


# ---------------------------------------------------------------------------
# 虚拟整理预览（只读）：把文件按维度聚成「假想整理后」的结构，不动任何文件
# ---------------------------------------------------------------------------

VIRTUAL_ITEM_LIMIT = int(os.getenv("FILEPILOT_VIRTUAL_LIMIT") or 3000)

VIRTUAL_GROUP_MODES: list[dict] = [
    {"id": "type", "label": "按类型", "note": "图片 / 视频 / 文档 / 压缩包…", "group_dir": "按类型"},
    {"id": "time", "label": "按时间", "note": "本月 / 近三个月 / 今年 / 去年 / 更早", "group_dir": "按时间"},
    {"id": "size", "label": "按体积", "note": "小于 1MB / 1-100MB / 100MB-1GB / 大于 1GB", "group_dir": "按体积"},
    {"id": "project", "label": "按项目", "note": "按项目标记聚拢，不属于任何项目的归入散落文件", "group_dir": "按项目"},
]

# 时间分档：从上到下匹配，days=None 表示兜底
TIME_BUCKETS: list[dict] = [
    {"id": "month", "label": "本月", "days": 30},
    {"id": "quarter", "label": "近三个月", "days": 90},
    {"id": "year", "label": "今年", "days": 365},
    {"id": "last_year", "label": "去年", "days": 730},
    {"id": "older", "label": "更早", "days": None},
]

# 体积分档：max=None 表示兜底
SIZE_BUCKETS: list[dict] = [
    {"id": "tiny", "label": "小于 1 MB", "max": 1024 ** 2},
    {"id": "small", "label": "1 - 100 MB", "max": 100 * 1024 ** 2},
    {"id": "large", "label": "100 MB - 1 GB", "max": 1024 ** 3},
    {"id": "huge", "label": "大于 1 GB", "max": None},
]

# 项目标记：目录里出现任一标记文件，就认为它（含子目录）是一个项目
PROJECT_MARKERS: list[dict] = [
    {"file": ".git", "label": "Git 仓库"},
    {"file": "package.json", "label": "Node"},
    {"file": "pyproject.toml", "label": "Python"},
    {"file": "requirements.txt", "label": "Python"},
    {"file": "go.mod", "label": "Go"},
    {"file": "Cargo.toml", "label": "Rust"},
    {"file": "pom.xml", "label": "Java"},
    {"file": "build.gradle", "label": "Java/Gradle"},
    {"file": "*.sln", "label": "C#/.NET"},
    {"file": "*.csproj", "label": "C#/.NET"},
    {"file": "*.uproject", "label": "Unity"},
    {"file": "CMakeLists.txt", "label": "C/C++"},
    {"file": "composer.json", "label": "PHP"},
    {"file": "Gemfile", "label": "Ruby"},
    {"file": "pubspec.yaml", "label": "Flutter"},
]

# 标记模式在导入时编译一次。原来是 15 个标记 × 目录里每个名字各调一次 fnmatch
# （每次还带两次 normcase）—— 虚拟整理里这占了约三成时间。
_MARKER_MATCHERS: tuple[tuple[str, re.Pattern | None], ...] = tuple(
    (marker["label"], glob_matcher((marker["file"],))) for marker in PROJECT_MARKERS
)

LOOSE_PROJECT_KEY = "__loose__"

# 项目雷达：超过这么多天没动过，判定为僵尸项目
ZOMBIE_DAYS = int(os.getenv("FILEPILOT_ZOMBIE_DAYS") or 180)
PROJECT_LIMIT = int(os.getenv("FILEPILOT_PROJECT_LIMIT") or 2000)

# 预览清单导出：单次最多导出多少行
VIRTUAL_EXPORT_LIMIT = int(os.getenv("FILEPILOT_EXPORT_LIMIT") or 20000)


# ---------------------------------------------------------------------------
# 全文检索（B1）
#
# **白名单**而不是黑名单：只索引"明确是文本"的扩展名。
# 宁可漏掉一个改了扩展名的文本文件，也不能把一个 2GB 的二进制塞进索引。
# PDF / docx / xlsx 刻意不在列 —— 抽它们要新增依赖，而本项目的硬约束是
# 「可选依赖只有两个」（pywebview / pillow）。
# ---------------------------------------------------------------------------

SEARCH_TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".jsonl", ".yaml", ".yml", ".xml", ".ini", ".toml", ".cfg",
    ".conf", ".properties", ".editorconfig", ".gitignore",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".vue",
    ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".html", ".htm", ".css", ".scss", ".less", ".svg",
    ".r", ".m", ".lua", ".php", ".rb", ".pl", ".swift", ".scala",
})

# 单文件正文抽取上限：一个 2GB 的日志就能把索引撑爆
SEARCH_MAX_FILE_BYTES = int(os.getenv("FILEPILOT_SEARCH_MAX_BYTES") or 2 * 1024 * 1024)
# 一次索引最多新处理多少个文件（超出如实标 truncated，不假装全部建完）
SEARCH_FILE_LIMIT = int(os.getenv("FILEPILOT_SEARCH_LIMIT") or 200000)
# 单次检索最多返回多少条
SEARCH_RESULT_LIMIT = int(os.getenv("FILEPILOT_SEARCH_RESULT_LIMIT") or 200)
# 索引超过这么多天就提示"可能已过期"。**只提示，不自动重建** ——
# 自用项目不该在用户没同意的情况下自己跑一个可能很久的任务。
SEARCH_STALE_DAYS = int(os.getenv("FILEPILOT_SEARCH_STALE_DAYS") or 7)


# ---------------------------------------------------------------------------
# Agent 偏好记忆、语义排除与风险分级
#
# prefs.json 是 Agent 的「长期记忆」，与对话记忆（filepilot.db 的 memory 表）分开：
# 对话记忆是逐条历史，prefs 是提炼后的结论，用户在 Agent 页可直接查看与编辑。
# ---------------------------------------------------------------------------

PREFS_FILE = STATE_DIR / "prefs.json"

# 每个会话的「最近一次意图」快照，供「就刚才那个目录」这类上下文指代复用。
# 只存结论（最近路径 / 最近动作），不存原文；原文在 conversation memory 里。
AGENT_STATE_FILE = STATE_DIR / "agent_state.json"

# prefs.json 的骨架：首次读取即按此结构补齐缺键，避免前端到处判空
PREFS_DEFAULT: dict = {
    "common_paths": [],       # 常用路径 [{"path": "D:\\素材", "note": "自填"}]
    "exclude_tags": [],       # 常用排除标签，取自 EXCLUDE_TAGS 的键
    "exclude_paths": [],      # 常用排除的真实路径
    "hard_rules": [],         # 永久约束 [{"text": "项目都不要动", "action": "*", "path": "D:\\项目"}]
    "clean_strategies": [],   # 常用清理策略 [{"action": "junk_clean", "then": "duplicate_scan"}]
    "updated_at": None,
}

# 语义排除标签 → 目录名剪枝（交给 fsutil.iter_files）+ 常见真实路径（用于事后过滤清单）
EXCLUDE_TAGS: dict[str, dict] = {
    "项目文件": {
        "names": ["node_modules", ".git", ".svn", "__pycache__", ".venv", "venv",
                  "dist", "build", "target", "vendor", "obj"],
        "paths": [],
        "note": "项目目录与依赖目录（配合项目雷达识别出的项目根）",
    },
    "照片": {
        "names": ["Pictures", "图片", "DCIM", "Photos", "相册", "Camera", "Camera Roll"],
        "paths": ["Pictures", "图片", "DCIM", "Photos"],
        "note": "个人照片目录",
    },
    "视频": {
        "names": ["Videos", "视频", "Movies", "影片"],
        "paths": ["Videos", "视频", "Movies"],
        "note": "个人视频目录",
    },
    "文档": {
        "names": ["Documents", "文档", "我的文档"],
        "paths": ["Documents", "文档"],
        "note": "个人文档目录",
    },
    "桌面": {
        "names": ["Desktop", "桌面"],
        "paths": ["Desktop", "桌面"],
        "note": "桌面（软保护目录，只读扫描时一并跳过）",
    },
    "下载": {
        "names": ["Downloads", "下载"],
        "paths": ["Downloads", "下载"],
        "note": "下载目录",
    },
}

# 风险分级：本地确定性计算，不交给模型。见 services/agent_intent.py: compute_risk()
AGENT_RISK_META: dict[str, dict] = {
    "low": {"label": "低风险", "note": "只读操作，或结果可完全回滚"},
    # ★ 不要在这里承诺「可恢复」：清理类（junk/duplicate/similar/large）确认后
    #   走的是「隔离 + 立刻清空同批」，实际不可恢复。具体语义由 compute_risk()
    #   按动作给出，这一层只说明「会动文件」。
    "medium": {"label": "中风险", "note": "会改动或删除文件，请看清清单再确认"},
    "high": {"label": "高风险", "note": "涉及系统盘根目录、系统目录或大批量对象"},
}

# 高风险判定阈值
AGENT_HIGH_RISK_ITEMS = int(os.getenv("FILEPILOT_AGENT_HIGH_RISK_ITEMS") or 500)

# 多步骤任务的分批执行粒度（每批处理多少项后落一次进度）
AGENT_BATCH_SIZE = int(os.getenv("FILEPILOT_AGENT_BATCH") or 200)

# 辅助工具「估算耗时」用的经验吞吐（字节/秒），只用于给数量级，不追求精确
AGENT_ESTIMATE_BYTES_PER_SEC = float(os.getenv("FILEPILOT_AGENT_THROUGHPUT") or (80 * 1024 * 1024))

# 轻量只读查询的上限：Agent 问「这个目录多大」时不能变成一次全盘重扫
AGENT_QUERY_MAX_ENTRIES = int(os.getenv("FILEPILOT_AGENT_QUERY_MAX") or 120000)
AGENT_QUERY_MAX_SECONDS = float(os.getenv("FILEPILOT_AGENT_QUERY_SECONDS") or 6.0)


def project_marker_for(names) -> str | None:
    """目录里出现任一项目标记就返回项目类型标签，否则 None。

    规则只写在这里：整理预览的「按项目」与项目雷达共用同一套判断。
    匹配走 `_MARKER_MATCHERS` 里预编译好的正则（见上方注释）。
    """
    lowered = [str(name).lower() for name in names]
    for label, matcher in _MARKER_MATCHERS:
        if matcher is None:
            continue
        for name in lowered:
            if matcher.match(name):
                return label
    return None


def drive_letters() -> list[str]:
    """返回本机存在的盘符列表，如 ['C', 'D']。"""
    found = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                found.append(letter)
        except OSError:
            continue
    return found

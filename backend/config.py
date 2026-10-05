"""全局配置与规则库。

所有"什么能碰、什么不能碰、什么算垃圾"的判断都集中在这里，
其他模块只引用，不自己写死规则。
"""
from __future__ import annotations

import fnmatch
import os
import string
import sys
from pathlib import Path

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
DATABASE_FILE = STATE_DIR / "filepilot.db"

# 隔离区：所有"删除"默认先落到这里，而不是物理删除。
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


def category_for(path: Path | str) -> str:
    ext = Path(path).suffix.lower()
    for name, extensions in FILE_CATEGORIES.items():
        if ext in extensions:
            return name
    return "其他"


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

# 读取时剪枝用的完整黑名单（软 + 自），行为与档位无关
SAFETY_BLOCKLIST: list[Path] = SOFT_PROTECTED_DIRS + SELF_PROTECTED_DIRS

# 权限档位：readonly / standard / full
# 「完全访问」仍受三条底限约束：删除必进隔离区、自保护目录不写、切换需显式确认
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
        "desc": "允许改动桌面与系统目录；删除仍先进隔离区，自保护目录仍不可写",
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

LOOSE_PROJECT_KEY = "__loose__"

# 项目雷达：超过这么多天没动过，判定为僵尸项目
ZOMBIE_DAYS = int(os.getenv("FILEPILOT_ZOMBIE_DAYS") or 180)
PROJECT_LIMIT = int(os.getenv("FILEPILOT_PROJECT_LIMIT") or 2000)

# 预览清单导出：单次最多导出多少行
VIRTUAL_EXPORT_LIMIT = int(os.getenv("FILEPILOT_EXPORT_LIMIT") or 20000)


def project_marker_for(names) -> str | None:
    """目录里出现任一项目标记就返回项目类型标签，否则 None。

    规则只写在这里：整理预览的「按项目」与项目雷达共用同一套判断。
    """
    lowered = {str(name).lower() for name in names}
    for marker in PROJECT_MARKERS:
        pattern = marker["file"].lower()
        if any(fnmatch.fnmatch(name, pattern) for name in lowered):
            return marker["label"]
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

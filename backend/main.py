from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".filepilot"
STATE_DIR.mkdir(exist_ok=True)
LOG_FILE = STATE_DIR / "logs.json"
UNDO_FILE = STATE_DIR / "last_organization.json"

app = FastAPI(title="FilePilot Lite API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PathRequest(BaseModel):
    path: str


class BackupRequest(BaseModel):
    source_dir: str
    dest_dir: str


class DuplicateMoveRequest(BaseModel):
    root: str
    files: list[dict[str, Any]] = Field(default_factory=list)


class OrganizeRequest(BaseModel):
    source_dir: str
    target_dir: str


class AdviceRequest(BaseModel):
    question: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_size(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def safe_path(value: str, *, must_exist: bool = False) -> Path:
    path = Path(value).expanduser()
    if must_exist and not path.exists():
        raise HTTPException(status_code=400, detail=f"路径不存在: {value}")
    return path


def read_logs() -> list[dict[str, Any]]:
    try:
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_log(kind: str, result: str, path: str = "", note: str = "") -> None:
    logs = read_logs()
    logs.append({"time": now_iso(), "type": kind, "result": result, "path": path, "note": note})
    LOG_FILE.write_text(json.dumps(logs[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def iter_files(root: Path):
    try:
        for entry in root.rglob("*"):
            if entry.is_file() and ".filepilot" not in entry.parts:
                yield entry
    except OSError:
        return


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    groups = {
        "图片": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"},
        "视频": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
        "音频": {".mp3", ".wav", ".flac", ".aac", ".m4a"},
        "文档": {".doc", ".docx", ".pdf", ".txt", ".md", ".rtf"},
        "表格": {".xls", ".xlsx", ".csv", ".tsv"},
        "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz"},
        "代码": {".py", ".js", ".ts", ".vue", ".html", ".css", ".json", ".java", ".go"},
    }
    for name, extensions in groups.items():
        if ext in extensions:
            return name
    return "其他"


def scan_path(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail=f"目录不存在: {root}")
    total_files = total_size = 0
    dirs = 0
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "size": 0})
    large_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    for current, subdirs, files in os.walk(root):
        dirs += len(subdirs)
        for name in files:
            path = Path(current) / name
            try:
                size = path.stat().st_size
            except OSError as exc:
                warnings.append(f"无法读取 {path}: {exc}")
                continue
            total_files += 1
            total_size += size
            category = category_for(path)
            categories[category]["count"] += 1
            categories[category]["size"] += size
            if size >= 100 * 1024 * 1024:
                large_files.append({"name": name, "path": str(path), "size": size, "size_text": format_size(size), "category": category})
    large_files.sort(key=lambda item: item["size"], reverse=True)
    category_stats = [
        {"category": key, "name": key, "count": value["count"], "size": value["size"], "size_text": format_size(value["size"])}
        for key, value in sorted(categories.items(), key=lambda item: item[1]["size"], reverse=True)
    ]
    result = {
        "root": str(root), "scan_time": now_iso(), "duration_seconds": round(time.perf_counter() - started, 3),
        "total_files": total_files, "total_dirs": dirs, "total_size": total_size, "total_size_text": format_size(total_size),
        "category_stats": category_stats, "large_files": large_files[:100], "largest_file": large_files[0] if large_files else None,
        "warnings": warnings[:100],
    }
    write_log("scan", "成功", str(root), f"扫描 {total_files} 个文件")
    return result


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "filepilot-lite", "time": now_iso()}


@app.get("/api/ai/status")
def ai_status():
    configured = bool(os.getenv("DEEPSEEK_API_KEY"))
    return {"configured": configured, "provider": "DeepSeek" if configured else "本地规则建议"}


@app.post("/api/scan")
def scan(request: PathRequest):
    return scan_path(safe_path(request.path, must_exist=True))


@app.get("/api/logs")
def logs():
    return read_logs()


@app.post("/api/backup")
def backup(request: BackupRequest):
    source = safe_path(request.source_dir, must_exist=True)
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="源路径必须是目录")
    dest = safe_path(request.dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / f"{source.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    skipped = {".git", "node_modules", ".filepilot"}
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source.rglob("*"):
            if path.is_file() and not any(part in skipped for part in path.relative_to(source).parts):
                zf.write(path, path.relative_to(source.parent))
    size = archive.stat().st_size
    result = {"success": True, "source": str(source), "zip_path": str(archive), "size": size, "size_text": format_size(size), "backup_time": now_iso()}
    write_log("backup", "成功", str(source), str(archive))
    return result


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@app.post("/api/duplicates/scan")
def duplicate_scan(request: PathRequest):
    root = safe_path(request.path, must_exist=True)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="路径必须是目录")
    buckets: dict[tuple[int, str], list[Path]] = defaultdict(list)
    errors: list[str] = []
    scanned = 0
    for path in iter_files(root):
        try:
            stat = path.stat()
            scanned += 1
            if stat.st_size > 0:
                buckets[(stat.st_size, digest(path))].append(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    groups = []
    for (size, checksum), paths in buckets.items():
        if len(paths) < 2:
            continue
        items = [{"path": str(path), "name": path.name, "size": size, "size_text": format_size(size)} for path in paths]
        groups.append({"sha256": checksum, "size": size, "size_text": format_size(size), "count": len(items), "original": items[0], "redundant_files": items[1:], "files": items})
    groups.sort(key=lambda item: item["size"] * (item["count"] - 1), reverse=True)
    redundant = sum(len(item["redundant_files"]) for item in groups)
    result = {"root": str(root), "scan_time": now_iso(), "duration_seconds": 0, "scanned_files": scanned, "group_count": len(groups), "redundant_file_count": redundant, "reclaimable_size": sum(item["size"] * len(item["redundant_files"]) for item in groups), "reclaimable_size_text": format_size(sum(item["size"] * len(item["redundant_files"]) for item in groups)), "groups": groups, "errors": errors[:100]}
    write_log("duplicates", "成功", str(root), f"发现 {len(groups)} 组重复文件")
    return result


@app.post("/api/duplicates/move")
def move_duplicates(request: DuplicateMoveRequest):
    root = safe_path(request.root, must_exist=True)
    recovery = root / ".filepilot_recovery"
    recovery.mkdir(exist_ok=True)
    moved = []
    for item in request.files:
        source = safe_path(str(item.get("path", "")))
        if not source.is_file():
            continue
        target = recovery / source.name
        index = 1
        while target.exists():
            target = recovery / f"{source.stem}_{index}{source.suffix}"
            index += 1
        shutil.move(str(source), str(target))
        moved.append({"from": str(source), "to": str(target)})
    write_log("duplicates", "成功", str(root), f"移动 {len(moved)} 个重复副本")
    return {"success": True, "moved_count": len(moved), "moved": moved, "recovery_dir": str(recovery)}


def organization_items(source: Path, target: Path) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in iter_files(source):
        try:
            relative = path.relative_to(source)
            category = category_for(path)
            destination = target / category / path.name
            items.append({"source": str(path), "path": str(path), "target": str(destination), "name": path.name, "category": category, "size": path.stat().st_size, "size_text": format_size(path.stat().st_size), "relative_path": str(relative)})
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return items, errors


@app.post("/api/organize/preview")
def organize_preview(request: OrganizeRequest):
    source, target = safe_path(request.source_dir, must_exist=True), safe_path(request.target_dir)
    if not source.is_dir():
        raise HTTPException(status_code=400, detail="源路径必须是目录")
    items, errors = organization_items(source, target)
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "size": 0})
    for item in items:
        stats[item["category"]]["count"] += 1
        stats[item["category"]]["size"] += item["size"]
    category_stats = [{"category": key, "name": key, "count": value["count"], "size": value["size"], "size_text": format_size(value["size"])} for key, value in sorted(stats.items())]
    total_size = sum(item["size"] for item in items)
    return {"source_dir": str(source), "target_dir": str(target), "created_at": now_iso(), "item_count": len(items), "total_size": total_size, "total_size_text": format_size(total_size), "category_stats": category_stats, "items": items[:500], "errors": errors[:100]}


@app.post("/api/organize/execute")
def organize_execute(request: OrganizeRequest):
    source, target = safe_path(request.source_dir, must_exist=True), safe_path(request.target_dir)
    items, errors = organization_items(source, target)
    moved: list[dict[str, str]] = []
    for item in items:
        old, new = Path(item["source"]), Path(item["target"])
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            if new.exists():
                new = new.with_name(f"{new.stem}_{int(time.time() * 1000)}{new.suffix}")
            shutil.move(str(old), str(new))
            moved.append({"from": str(old), "to": str(new)})
        except OSError as exc:
            errors.append(f"{old}: {exc}")
    UNDO_FILE.write_text(json.dumps(moved, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(Path(item["from"]).stat().st_size if Path(item["from"]).exists() else 0 for item in moved)
    result = {"success": not errors, "source_dir": str(source), "target_dir": str(target), "moved_count": len(moved), "moved_size": total, "moved_size_text": format_size(total), "finished_at": now_iso(), "errors": errors[:100]}
    write_log("organize", "成功" if not errors else "部分完成", str(source), f"移动 {len(moved)} 个文件")
    return result


@app.post("/api/organize/undo")
def organize_undo():
    try:
        moved = json.loads(UNDO_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"success": False, "moved_count": 0, "errors": ["没有可撤销的整理记录"]}
    restored = 0
    errors = []
    for item in reversed(moved):
        source, target = Path(item["from"]), Path(item["to"])
        try:
            if target.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(source))
                restored += 1
        except OSError as exc:
            errors.append(str(exc))
    return {"success": not errors, "moved_count": restored, "errors": errors}


@app.post("/api/export-report")
def export_report(request: PathRequest):
    root = safe_path(request.path, must_exist=True)
    result = scan_path(root)
    report_dir = root / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"filepilot_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = ["FilePilot Lite 扫描报告", f"目录: {result['root']}", f"生成时间: {result['scan_time']}", f"文件总数: {result['total_files']}", f"目录总数: {result['total_dirs']}", f"总大小: {result['total_size_text']}", "", "文件分类:"]
    lines.extend(f"- {item['name']}: {item['count']} 个, {item['size_text']}" for item in result["category_stats"])
    lines.append("\n大文件:")
    lines.extend(f"- {item['path']} ({item['size_text']})" for item in result["large_files"])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    size = report_path.stat().st_size
    write_log("report", "成功", str(root), str(report_path))
    return {"success": True, "report_path": str(report_path), "size": size, "size_text": format_size(size), "created_at": now_iso()}


@app.post("/api/ai/scan-advice")
def scan_advice(request: AdviceRequest):
    summary = request.summary or {}
    total = int(summary.get("total_files") or 0)
    large = len(summary.get("large_files") or [])
    overview = f"本次扫描发现 {total} 个文件。"
    actions = ["先处理重复文件，再整理大文件。", "备份重要目录后再执行移动操作。"]
    if large:
        actions.insert(0, f"发现 {large} 个大文件，建议优先确认是否需要长期保留。")
    return {"overview": overview, "priority_actions": actions, "risks": ["移动前请确认目标目录有足够空间。"], "next_steps": ["导出报告留档", "完成备份后再整理"], "generated_at": now_iso(), "usage": {"total_tokens": 0}, "question": request.question}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)

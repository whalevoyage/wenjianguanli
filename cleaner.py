"""清理工具：只清理空目录，不触碰文件。"""
from pathlib import Path


def remove_empty_dirs(root: str) -> int:
    removed = 0
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
                removed += 1
            except OSError:
                pass
    return removed

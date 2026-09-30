"""备份兼容入口。"""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def backup_directory(source: str, destination: str) -> str:
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    destination_path.mkdir(parents=True, exist_ok=True)
    archive = destination_path / f"{source_path.name}.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as stream:
        for path in source_path.rglob("*"):
            if path.is_file() and "node_modules" not in path.parts and ".git" not in path.parts:
                stream.write(path, path.relative_to(source_path.parent))
    return str(archive)

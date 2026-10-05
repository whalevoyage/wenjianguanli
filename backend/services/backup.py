"""项目备份：把目录打成 ZIP 快照。

排除规则统一走 config.DEFAULT_EXCLUDES + IMPLICIT_EXCLUDES，
（旧版界面宣称排除 10 项、后端实际只排除 3 项的 bug 在此修掉）
"""
from __future__ import annotations

import zipfile
import os
from datetime import datetime
from pathlib import Path

from .. import config, fsutil, permissions
from ..jobs import JobContext


def run_backup(ctx: JobContext) -> dict:
    params = ctx.params
    source = fsutil.resolve_dir(params.get("source_dir", ""))
    dest_raw = params.get("dest_dir") or str(config.STATE_DIR / "backups")

    destination = fsutil.normalize(dest_raw)
    fsutil.assert_writable(destination, permissions.current())
    destination.mkdir(parents=True, exist_ok=True)

    ctx.phase(0, f"正在清点 {source} 下的文件")
    files: list[Path] = []
    total_bytes = 0
    for path in fsutil.iter_files(source, excludes=params.get("excludes"), progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue
        files.append(path)

    free = fsutil.disk_free(destination)
    # 压缩后一般远小于原体积，留 20% 余量做保守估计
    if total_bytes * 0.2 > free:
        raise RuntimeError(
            f"目标盘空间可能不足：待压缩 {fsutil.format_size(total_bytes)}，可用 {fsutil.format_size(free)}"
        )

    archive = destination / f"{source.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    partial = archive.with_suffix(".partial.zip")
    ctx.phase(4, "正在写入压缩包")

    errors: list[str] = []
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for index, path in enumerate(files, start=1):
                ctx.check_cancel()
                ctx.progress(f"正在压缩 {path.name}", index, len(files), str(path))
                try:
                    zf.write(path, path.relative_to(source.parent))
                except OSError as exc:
                    errors.append(f"{path}：{fsutil.human_error(exc)}")
        with zipfile.ZipFile(partial) as check:
            bad = check.testzip()
            if bad:
                raise RuntimeError(f"压缩包校验失败：{bad}")
        os.replace(partial, archive)
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    ctx.phase(6, "备份完成")
    try:
        size = archive.stat().st_size
    except OSError:
        size = 0

    return {
        "source": str(source),
        "zip_path": str(archive),
        "file_count": len(files),
        "source_size": total_bytes,
        "source_size_text": fsutil.format_size(total_bytes),
        "size": size,
        "size_text": fsutil.format_size(size),
        "excludes": list(params.get("excludes") or config.DEFAULT_EXCLUDES),
        "backup_time": fsutil.now_iso(),
        "errors": errors[:100],
        "partial": bool(errors),
    }

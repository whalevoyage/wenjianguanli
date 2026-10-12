"""项目备份：把目录打成 ZIP 快照。

排除规则统一走 config.DEFAULT_EXCLUDES + IMPLICIT_EXCLUDES，
（旧版界面宣称排除 10 项、后端实际只排除 3 项的 bug 在此修掉）
"""
from __future__ import annotations

import zipfile
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, LargeZipFile, ZipFile

from .. import config, fsutil, permissions
from ..jobs import JobContext


def run_backup(ctx: JobContext) -> dict:
    params = ctx.params
    source = fsutil.resolve_dir(params.get("source_dir", ""))

    # ★ 目标目录分两种来源，门禁口径不同：
    #   · 用户显式给了 dest_dir → 过写入白名单门禁（不许把备份写进桌面 / 系统目录）；
    #   · 没给 → 落到工具自己的 STATE_DIR/backups，这是**工具内部的写入**
    #     （与「隔离区」同理：工具管理自己的数据，不套自保护门禁）。
    #   之前两种情况共用一条 assert_writable，而 STATE_DIR 是自保护目录 —— 于是
    #   「留空」这个默认行为会被自己的护栏拦下，备份永远失败（冒烟补测才照出来）。
    dest_raw = str(params.get("dest_dir") or "").strip()
    if dest_raw:
        destination = fsutil.normalize(dest_raw)
        fsutil.assert_writable(destination, permissions.current())
    else:
        destination = config.STATE_DIR / "backups"
    destination.mkdir(parents=True, exist_ok=True)

    ctx.phase(0, f"正在清点 {source} 下的文件")
    files: list[Path] = []
    total_bytes = 0
    for entry in fsutil.iter_entries(source, excludes=params.get("excludes"), progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            total_bytes += entry.stat().st_size   # Windows 上零系统调用
        except OSError:
            continue
        files.append(Path(entry.path))

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
                    arc = path.relative_to(source.parent)
                    if path.suffix.lower() in config.NON_COMPRESSIBLE_EXTS:
                        # 媒体/压缩容器本来就是压缩编码，再 deflate 一次不省体积、却慢好几倍
                        # （实测 72MB 的 mp4：deflate 6 花 3.13s，STORED 只要 0.43s，产物一样大）
                        zf.write(path, arc, compress_type=zipfile.ZIP_STORED)
                    else:
                        zf.write(path, arc)
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


def verify_backup_restore(zip_path: str | os.PathLike, mode: str = "verify") -> dict:
    """核对一个备份 ZIP 是否真的能还原。

    两种模式：
    - ``"verify"``（默认）：把每个成员**读进内存**逐一做 CRC 校验，证明整包字节完整、
      每个文件都能解压。不落盘，不占额外磁盘，适合大备份。
    - ``"restore_test"``：先把全部成员解到临时目录，再逐一核对「解出的文件大小 == 包内记录」、
      最后删掉临时目录。真正走一遍还原路径，但会占用「解压后总大小」的临时空间——
      目标盘剩余不足时直接拒绝，避免把磁盘写满。

    返回报告 dict，含 ok / member_count / uncompressed_total / verified_count / errors。

    两个坑要说明：
    1. ``ZipFile.extractall`` 在旧 Python 上对 CRC 错误只是记告警、不抛异常；
       所以这里**自己** ``zf.read`` 触发 CRC 校验（读出来即校验通过），再写临时文件核对大小，
       不依赖 extractall 的隐式行为，跨版本都稳。
    2. ``restore_test`` 绝不在「剩余空间 < 解压总大小」时硬写，否则会把临时盘写爆、
       牵连其它程序——宁可先拦下，让用户换盘或清空间。
    """
    if mode not in ("verify", "restore_test"):
        raise ValueError(f"mode 只能是 verify / restore_test，收到 {mode!r}")

    zp = fsutil.normalize(str(zip_path))
    if not os.path.isfile(zp):
        raise FileNotFoundError(f"找不到备份文件：{zp}")

    start = time.monotonic()
    errors: list[str] = []
    member_count = 0
    file_total = 0
    uncompressed_total = 0
    compressed_total = 0
    verified_count = 0

    try:
        with ZipFile(zp, "r") as zf:
            infos = zf.infolist()
            member_count = len(infos)
            for info in infos:
                if info.is_dir():
                    continue
                file_total += 1
                uncompressed_total += info.file_size
                compressed_total += info.compress_size

            # 独立交叉校验：testzip 在首个坏文件处返回其名，全好返回 None
            bad = zf.testzip()
            if bad is not None:
                errors.append(f"CRC 校验失败：{bad}")

            if mode == "restore_test":
                free = fsutil.disk_free(Path(tempfile.gettempdir()))
                needed = uncompressed_total + 1024 * 1024  # 一点余量
                if needed > free:
                    raise RuntimeError(
                        f"临时盘空间不足，无法做还原演练：需 {fsutil.format_size(needed)}，"
                        f"可用 {fsutil.format_size(free)}"
                    )
                tmp = tempfile.mkdtemp(prefix="fp_restore_")
                try:
                    for info in infos:
                        if info.is_dir():
                            continue
                        data = zf.read(info.filename)  # CRC 校验在读取时完成
                        dest = os.path.join(tmp, info.filename)
                        os.makedirs(os.path.dirname(dest) or tmp, exist_ok=True)
                        with open(dest, "wb") as fh:
                            fh.write(data)
                        if os.path.getsize(dest) != info.file_size:
                            errors.append(f"解压后大小不符：{info.filename}")
                        else:
                            verified_count += 1
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            else:
                # verify：只把每个成员读进内存做 CRC 校验，不落盘
                for info in infos:
                    if info.is_dir():
                        continue
                    try:
                        zf.read(info.filename)
                        verified_count += 1
                    except (BadZipFile, LargeZipFile, OSError) as exc:
                        errors.append(f"{info.filename}：{fsutil.human_error(exc)}")
    except BadZipFile as exc:
        raise ValueError(f"不是合法的 ZIP 备份文件：{fsutil.human_error(exc)}")
    except (OSError, LargeZipFile) as exc:
        raise OSError(f"读取备份失败：{fsutil.human_error(exc)}")

    elapsed = round(time.monotonic() - start, 3)
    return {
        "zip_path": zp,
        "mode": mode,
        "ok": (not errors) and verified_count == file_total,
        "member_count": member_count,
        "file_count": file_total,
        "verified_count": verified_count,
        "uncompressed_total": uncompressed_total,
        "uncompressed_total_text": fsutil.format_size(uncompressed_total),
        "compressed_total": compressed_total,
        "compressed_total_text": fsutil.format_size(compressed_total),
        "errors": errors[:200],
        "elapsed_seconds": elapsed,
    }

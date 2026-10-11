"""清理执行与隔离区（回收站）。

一切"删除"默认先进隔离区，可还原到原位；也可以选择送进系统回收站。
空文件夹是唯一直接删除的对象（因为它本来就没有内容）。
"""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from .. import config, fsutil, permissions, store
from ..jobs import JobContext

MODE_ISOLATE = "isolate"
MODE_RECYCLE = "recycle"


def _trash_root_for(path: Path) -> Path:
    """优先用「源文件所在盘」的同盘隔离区。

    跨盘移动在 Windows 上只能退化成"复制+删除"，大文件又慢又容易中途失败，
    所以隔离区跟着源文件所在的盘走，还原时就是一次同盘改名。
    """
    anchor = path.anchor
    if anchor:
        candidate = Path(anchor) / config.TRASH_DIR_NAME
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            pass
    config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    return config.TRASH_DIR


def list_trash() -> dict:
    entries = store.read_trash()
    entries.sort(key=lambda item: item.get("time", ""), reverse=True)
    total = sum(int(item.get("size") or 0) for item in entries)
    roots = sorted({
        str(Path(entry["trash_path"]).parent.parent)
        for entry in entries
        if entry.get("trash_path")
    })
    return {
        "entries": entries,
        "count": len(entries),
        "total_size": total,
        "total_size_text": fsutil.format_size(total),
        "trash_dir": roots[0] if roots else str(config.TRASH_DIR),
        "trash_roots": roots,
    }


def run_clean(ctx: JobContext) -> dict:
    params = ctx.params
    items = params.get("items") or []
    mode = params.get("mode") or MODE_ISOLATE
    if not items:
        raise ValueError("没有需要清理的项目")

    ctx.phase(4, f"正在清理 {len(items)} 个项目")
    done = 0
    freed = 0
    errors: list[str] = []
    actions: list[dict] = []
    entries = store.read_trash()

    try:
        permission = permissions.current()

        for index, item in enumerate(items, start=1):
            ctx.check_cancel()
            path = Path(item.get("path") or "")
            ctx.progress(f"正在处理 {path.name}", index, len(items), str(path))

            if not path.exists():
                continue

            try:
                fsutil.assert_writable(path, permission)

                if item.get("is_dir") and item.get("rule") == "empty_dir":
                    path.rmdir()
                    actions.append({"op": "rmdir", "to": str(path)})
                    done += 1

                elif mode == MODE_RECYCLE:
                    size = _size_of(path)
                    if fsutil.send_to_recycle_bin(path):
                        actions.append({"op": "recycle", "to": str(path)})
                        done += 1
                        freed += size
                    else:
                        errors.append(f"{path}：送回收站失败，已跳过")

                else:
                    size = _size_of(path)
                    bucket = _trash_root_for(path) / uuid.uuid4().hex[:8]
                    bucket.mkdir(parents=True, exist_ok=True)
                    destination = fsutil.ensure_unique(bucket / path.name)
                    shutil.move(str(path), str(destination))
                    record = {
                        "id": uuid.uuid4().hex[:12],
                        "original": str(path),
                        "trash_path": str(destination),
                        "name": path.name,
                        "size": size,
                        "size_text": fsutil.format_size(size),
                        "is_dir": bool(item.get("is_dir")),
                        "time": fsutil.now_iso(),
                        "job_id": ctx.job_id,
                    }
                    entries.append(record)
                    actions.append({"op": "trash", "from": str(path), "to": str(destination)})
                    done += 1
                    freed += size

            except (OSError, ValueError) as exc:
                errors.append(f"{path}：{fsutil.human_error(exc)}")

            # 账本分批落盘：任务中途被杀时，已进隔离区的文件不至于变成孤儿
            if index % 20 == 0:
                store.save_trash(entries)

            ctx.sleep(0.02)
    finally:
        # 无论正常结束、取消还是异常，账本都要和隔离区里的实际文件对得上
        store.save_trash(entries)

    ctx.add_actions(actions)

    ctx.phase(6, "清理完成")
    return {
        "mode": mode,
        "requested_count": len(items),
        "cleaned_count": done,
        "freed_size": freed,
        "freed_size_text": fsutil.format_size(freed),
        "trash_dir": str(config.TRASH_DIR),
        "errors": errors[:100],
    }


def restore_trash(ids: list[str]) -> dict:
    if not ids:
        raise ValueError("请选择要还原的项目")
    permissions.require_write("还原隔离区里的文件")
    targets = set(ids)
    entries = store.read_trash()
    restored: list[dict] = []
    errors: list[str] = []
    keep: list[dict] = []

    for entry in entries:
        if entry.get("id") not in targets:
            keep.append(entry)
            continue
        try:
            source = Path(entry["trash_path"])
            original = Path(entry["original"])
            if not source.exists():
                errors.append(f"{entry['name']}：隔离区里已找不到")
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            final = fsutil.ensure_unique(original) if original.exists() else original
            shutil.move(str(source), str(final))
            restored.append({"name": entry["name"], "path": str(final)})
        except OSError as exc:
            errors.append(f"{entry.get('name')}：{fsutil.human_error(exc)}")
            keep.append(entry)

    store.save_trash(keep)
    # 还原后原桶会空掉，得和 purge_trash 一样回收 —— 少了这一步，每「清理→还原」一轮
    # 就在隔离区留一个空目录。实测用户机器上 136 个桶里 134 个是空的。
    _prune_empty_buckets()
    return {"restored_count": len(restored), "restored": restored, "errors": errors}


def purge_trash(ids: list[str]) -> dict:
    """彻底删除隔离区里的条目。**不可恢复**，所以门禁也拦最狠的一档。"""
    if not ids:
        raise ValueError("请选择要彻底删除的项目")
    permissions.require_write("永久删除隔离区里的文件（这一步不可恢复）")
    targets = set(ids)
    entries = store.read_trash()
    purged = 0
    freed = 0
    errors: list[str] = []
    keep: list[dict] = []

    for entry in entries:
        if entry.get("id") not in targets:
            keep.append(entry)
            continue
        try:
            path = Path(entry["trash_path"])
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            elif path.exists():
                path.unlink()
            purged += 1
            freed += int(entry.get("size") or 0)
        except OSError as exc:
            errors.append(f"{entry.get('name')}：{fsutil.human_error(exc)}")
            keep.append(entry)

    store.save_trash(keep)
    _prune_empty_buckets()
    return {
        "purged_count": purged,
        "freed_size": freed,
        "freed_size_text": fsutil.format_size(freed),
        "errors": errors,
    }


def _prune_empty_buckets() -> None:
    roots = {config.TRASH_DIR}
    for entry in store.read_trash():
        raw = entry.get("trash_path")
        if raw:
            roots.add(Path(raw).parent.parent)

    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            try:
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
            except OSError:
                continue


def _size_of(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as iterator:
                entries = list(iterator)
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                total += entry.stat().st_size      # Windows 上目录列表已带回属性，零系统调用
            except OSError:
                continue
    return total

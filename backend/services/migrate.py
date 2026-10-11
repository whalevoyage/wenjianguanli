"""跨盘迁移：复制 → 校验 → 删源 → 可选建目录联接。

每一步都可中断、可续跑；只有逐文件校验全部通过才会删除源文件。
"""
from __future__ import annotations

from pathlib import Path

from .. import config, fsutil, permissions
from ..jobs import JobContext

# 目标盘至少要留这么多余量，防止刚好塞满
SPACE_MARGIN = 200 * 1024 * 1024


def _plan_items(ctx: JobContext, source: Path, target: Path, excludes, min_size: int, max_items: int,
                include_ext=None) -> list[dict]:
    ctx.phase(0, f"正在列举 {source} 下待迁移的文件")
    exts = {e.lower().lstrip(".") for e in (include_ext or []) if e}
    plan: list[dict] = []
    for entry in fsutil.iter_entries(source, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            stat = entry.stat()          # Windows 上目录列表已带回属性，零系统调用
        except OSError:
            continue
        if stat.st_size < min_size:
            continue
        path = Path(entry.path)
        if exts and path.suffix.lower().lstrip(".") not in exts:
            continue
        relative = path.relative_to(source)
        destination = target / relative
        plan.append({
            "source": str(path),
            "target": str(destination),
            "name": path.name,
            "relative": str(relative),
            "size": stat.st_size,
            "size_text": fsutil.format_size(stat.st_size),
        })
        if len(plan) >= max_items:
            break
    plan.sort(key=lambda item: item["size"], reverse=True)
    return plan


def run_migrate(ctx: JobContext) -> dict:
    params = ctx.params
    source = fsutil.resolve_dir(params.get("source_dir", ""))
    target_raw = params.get("target_dir", "")
    excludes = params.get("excludes")
    dry_run = bool(params.get("dry_run", True))
    mode = params.get("mode") or "move"
    create_junction = bool(params.get("create_junction", False))
    min_size = int(params.get("min_size") or 0)
    max_items = int(params.get("max_items") or 20000)
    rate_limit = float(params.get("rate_limit") or 0.02)

    if not target_raw:
        raise ValueError("请填写目标目录")
    target = fsutil.normalize(target_raw)
    permission = permissions.current()
    fsutil.assert_writable(target, permission)
    source, target = fsutil.assert_migration_paths(source, target)

    # ---- 生成计划 ----
    explicit = params.get("items")
    if explicit:
        plan = []
        for item in explicit[:max_items]:
            item_source = fsutil.assert_within(item["source"], source)
            item_target = fsutil.assert_within(item["target"], target)
            if not item_source.is_file():
                continue
            actual_size = item_source.stat().st_size
            plan.append({
                "source": str(item_source),
                "target": str(item_target),
                "name": item_source.name,
                "size": actual_size,
                "size_text": fsutil.format_size(actual_size),
            })
    else:
        plan = _plan_items(ctx, source, target, excludes, min_size, max_items,
                           include_ext=params.get("include_ext"))

    free = fsutil.disk_free(target)

    # 桌面 / .workbuddy / 项目自身目录，以及系统只读区：
    # 「移动」会删掉源文件，所以这些路径一律剔除，绝不触碰（只读功能可以照常看它们）。
    protected: list[str] = []
    if mode == "move":
        kept_plan = []
        for item in plan:
            source = Path(item["source"])
            if fsutil.write_block_reason(source, permission) or fsutil.is_readonly_zone(source):
                protected.append(str(source))
            else:
                kept_plan.append(item)
        plan = kept_plan

    # 剔除之后重新算总数，保证清单、体积、冲突三者一致
    total_size = sum(item["size"] for item in plan)
    conflicts = sum(1 for item in plan if Path(item["target"]).exists())

    ctx.set_plan(plan[:500])

    summary = {
        "source_dir": str(source),
        "target_dir": str(target),
        "mode": mode,
        "item_count": len(plan),
        "total_size": total_size,
        "total_size_text": fsutil.format_size(total_size),
        "target_free": free,
        "target_free_text": fsutil.format_size(free),
        "conflicts": conflicts,
        "create_junction": create_junction,
        "skipped_protected": len(protected),
        "protected_paths": protected[:20],
    }

    if total_size + SPACE_MARGIN > free:
        raise RuntimeError(
            f"目标盘空间不足：需要 {fsutil.format_size(total_size)}，可用 {fsutil.format_size(free)}"
        )

    if dry_run:
        ctx.phase(2, f"已生成 {len(plan)} 条迁移计划")
        return {**summary, "dry_run": True, "plan": plan[:500]}

    # ---- 执行：复制 → 校验 → 删源 ----
    ctx.phase(4, "正在复制文件")
    copied: list[dict] = []
    errors: list[str] = []
    pending: list[dict] = []

    def _flush_actions() -> None:
        """把累积的动作写进 job（分批持久化）。

        必须边做边记：迁移中途取消或崩溃时，已经复制到目标盘的文件
        依然能通过回滚接口清理掉，不留半成品。
        """
        if pending:
            ctx.add_actions(list(pending))
            pending.clear()

    def _record_move(src: Path, dst: Path) -> None:
        """源文件删除后，回滚语义升级：删拷贝 → 搬回原位。

        优先原地替换 pending 里的 copy 记录（每个文件始终一条动作）；
        若那条 copy 已随分批落盘写进了 job，就追加 move（回滚按倒序
        先搬回、后遇到 copy 记录时目标已不在，自动跳过，结果仍正确）。
        """
        move = {"op": "move", "from": str(src), "to": str(dst)}
        for i in range(len(pending) - 1, -1, -1):
            if pending[i].get("op") == "copy" and pending[i].get("to") == str(dst):
                pending[i] = move
                return
        pending.append(move)

    try:
        for index, item in enumerate(plan, start=1):
            ctx.check_cancel()
            src = Path(item["source"])
            dst = Path(item["target"])
            ctx.progress(f"正在复制 {src.name}", index, len(plan), str(src))

            try:
                if not src.exists():
                    continue
                if dst.exists():
                    # 目标已存在：必须逐字节校验内容一致才能免复制，
                    # 只比大小就删源违背「校验通过才删」的承诺。
                    if fsutil.verify_copy(src, dst):
                        if mode == "move":
                            src.unlink()
                            pending.append({"op": "move", "from": str(src), "to": str(dst)})
                        continue
                    dst = fsutil.ensure_unique(dst)

                fsutil.copy_with_progress(src, dst, cancel=ctx.cancelled)
                # 复制成功先记 copy：此时还没删源，回滚 = 删掉这份拷贝
                pending.append({"op": "copy", "from": str(src), "to": str(dst)})
                copied.append({"source": src, "target": dst, "size": item["size"]})
                if len(pending) >= 50:
                    _flush_actions()
                ctx.sleep(rate_limit)
            except InterruptedError:
                raise
            except OSError as exc:
                errors.append(f"{src}：{fsutil.human_error(exc)}")

        ctx.phase(5, "正在逐文件校验")
        verified = 0
        deletable: set[str] = set()
        for index, record in enumerate(copied, start=1):
            ctx.check_cancel()
            ctx.progress(f"正在校验 {record['target'].name}", index, len(copied), str(record["target"]))
            if fsutil.verify_copy(record["source"], record["target"]):
                verified += 1
                deletable.add(str(record["source"]))
            else:
                errors.append(f"校验不一致，已保留源文件：{record['source']}")

        # 只有校验通过的那些才允许删源
        deleted = 0
        if mode == "move":
            ctx.phase(5, "正在清理源文件")
            for record in copied:
                ctx.check_cancel()
                src, dst = record["source"], record["target"]
                if str(src) not in deletable:
                    continue
                try:
                    src.unlink()
                    deleted += 1
                    _record_move(src, dst)
                    if len(pending) >= 50:
                        _flush_actions()
                except OSError as exc:
                    errors.append(f"{src}：{fsutil.human_error(exc)}")
    finally:
        # 无论正常结束、取消还是异常，已发生的动作都要可回滚
        _flush_actions()

    junction_path = None
    if create_junction and mode == "move":
        if fsutil.create_junction(source, target):
            junction_path = str(source)

    ctx.phase(6, "迁移完成")
    store_result = {
        **summary,
        "dry_run": False,
        "copied_count": len(copied),
        "verified_count": verified,
        "deleted_count": deleted,
        "junction": junction_path,
        "errors": errors[:100],
    }
    _export_manifest(ctx, plan)
    return store_result


def _export_manifest(ctx: JobContext, plan: list[dict]) -> None:
    """把迁移清单落一份 CSV，方便搬完对账。"""
    if not plan:
        return
    try:
        path = config.STATE_DIR / f"migrate_{ctx.job_id}.csv"
        lines = ["source,target,size"]
        for item in plan:
            lines.append(f'"{item["source"]}","{item["target"]}",{item["size"]}')
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass

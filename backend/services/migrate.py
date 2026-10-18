"""跨盘迁移：复制 → 校验 → 删源 → 可选建目录联接。

每一步都可中断、可续跑；只有逐文件校验全部通过才会删除源文件。
"""
from __future__ import annotations

from pathlib import Path

from .. import config, execution, fsutil, permissions
from ..jobs import JobContext

# 目标盘至少要留这么多余量，防止刚好塞满
SPACE_MARGIN = 200 * 1024 * 1024


def _plan_items(ctx: JobContext, source: Path, dest_root: Path, excludes, min_size: int, max_items: int,
                include_ext=None) -> list[dict]:
    """列举 source 下待迁移的文件，目标为 `dest_root/<相对路径>`。

    `dest_root` 由调用方决定：单来源时就是目标目录本身；多来源时是 `目标/<来源名>`，
    这样两个来源里的同名文件各落在各自的顶层目录下，不会互相覆盖。
    """
    ctx.phase(0, f"正在列举 {source} 下待迁移的文件")
    exts = {e.lower().lstrip(".") for e in (include_ext or []) if e}
    plan: list[dict] = []
    for entry in fsutil.iter_entries(source, excludes=excludes, progress=ctx.tick, cancelled=ctx.cancelled):
        ctx.check_cancel()
        try:
            stat = entry.stat()          # Windows 目录列表已带回属性，零系统调用
        except OSError:
            continue
        if stat.st_size < min_size:
            continue
        path = Path(entry.path)
        if exts and path.suffix.lower().lstrip(".") not in exts:
            continue
        relative = path.relative_to(source)
        destination = dest_root / relative
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


def _owning_root(path: Path, roots: list[Path]) -> Path | None:
    """这条显式指定的条目属于哪个来源（用于逐个校验越界）。"""
    for root in roots:
        if fsutil._path_contains(root, path):
            return root
    return None


def run_migrate(ctx: JobContext) -> dict:
    params = ctx.params
    # 多来源：`source_dirs`（列表）优先，回退到单个 `source_dir`（兼容旧调用方 / Agent / CLI）。
    roots, notes = fsutil.resolve_sources(
        {"paths": params.get("source_dirs") or [], "source_dir": params.get("source_dir", "")}
    )
    multi = len(roots) > 1
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
    # ★ 服务层门禁：以前迁移只靠路由层 `_launch()` 拦，Agent / scheduler / 直接调用
    #   都能绕过 —— 只读档下真的会搬文件。预览（dry_run）不改任何东西，必须放行。
    if not dry_run:
        permissions.require_write("跨盘迁移（会复制并在校验后删除源文件）")
    target = fsutil.normalize(target_raw)
    permission = permissions.current()
    fsutil.assert_writable(target, permission)
    # 每个来源都要过「不与目标重叠」检查（目标不能等于或落在任一来源内）
    for root in roots:
        fsutil.assert_migration_paths(root, target)

    # 目录联接是「一个源 ↔ 一个目标」的语义，多来源下说不清，直接关掉并说明
    if multi and create_junction:
        create_junction = False
        notes.append("多来源不做目录联接（联接是「一个源 ↔ 一个目标」的语义）")

    prefixes = fsutil.source_prefixes(roots) if multi else [""] * len(roots)
    source = roots[0]  # 兼容字段：标题/联接/摘要仍以首个来源为主

    # ---- 生成计划 ----
    explicit = params.get("items")
    if explicit:
        plan = []
        for item in explicit[:max_items]:
            raw_source = Path(item["source"])
            root = _owning_root(raw_source, roots)
            if root is None:
                continue
            item_source = fsutil.assert_within(raw_source, root)
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
                "source_root": str(root),
            })
    else:
        plan = []
        for root, prefix in zip(roots, prefixes):
            dest_root = target / prefix if prefix else target
            plan.extend(_plan_items(ctx, root, dest_root, excludes, min_size,
                                    max_items - len(plan), include_ext=params.get("include_ext")))
            if len(plan) >= max_items:
                break

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
        "source_dirs": [str(root) for root in roots],
        "source_count": len(roots),
        "notes": notes,
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
                            # 删源同样过 execution：权限档与写保护一条不少
                            execution.remove(src, action="删除源文件（已校验一致）", mode=permission)
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
                    # check_symlink=False：源来自不跟随链接的遍历，且根已过
                    # assert_migration_paths；逐个再爬一遍父链在几千文件上很贵。
                    execution.remove(
                        src,
                        action="删除源文件（已校验一致）",
                        mode=permission,
                        check_symlink=False,
                    )
                    deleted += 1
                    _record_move(src, dst)
                    if len(pending) >= 50:
                        _flush_actions()
                except (OSError, ValueError) as exc:
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

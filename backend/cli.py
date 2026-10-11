"""Atlas 命令行：把已有的分析能力搬到终端里。

    python -m backend.cli overview
    python -m backend.cli scan D:\\Downloads
    python -m backend.cli duplicates D:\\Downloads --json
    python -m backend.cli similar D:\\Photos
    python -m backend.cli junk D:\\Downloads
    python -m backend.cli tree D:\\Downloads --depth 2
    python -m backend.cli reclaimed

## ★ 本 CLI 刻意只做只读分析

**不提供任何删除、移动、清理命令。** 原因不是偷懒：

- 写操作在这个项目里有一条完整的链路 —— 权限门禁（服务层）→ 先进隔离区 → 任务可整体回滚。
  一个 `atlas clean --yes` 会**绕过"先出清单、等用户确认"那一步**，
  而这正是这个项目最要紧的安全前提。
- 想批量写入时，走**界面**（有确认页和回滚按钮）或 **Agent**（计划 → 确认 → 执行 → 可回滚），
  这两条路都会留下任务记录，出问题能整体撤销。

所以这里的能力边界和 `backend/mcp_server.py` 完全一致：**只看不动**。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend import fsutil, store  # noqa: E402
from backend.services import duplicates, junk, overview, scan, similar  # noqa: E402
from backend.services.headless import HeadlessContext  # noqa: E402


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _print(payload: dict, as_json: bool, title: str = "") -> None:
    """默认给人看；`--json` 时原样吐 JSON（管道给别的工具用）。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if title:
        print(f"\n{title}")
    for key, value in payload.items():
        if isinstance(value, (list, dict)):
            continue
        print(f"  {key}: {value}")


def _rows(items: list[dict], columns: list[tuple[str, str]], limit: int) -> None:
    """极简表格：不引第三方库，够看就行。"""
    if not items:
        print("  （无）")
        return
    widths = []
    for key, label in columns:
        longest = max([len(label)] + [len(str(item.get(key, ""))) for item in items[:limit]])
        widths.append(min(longest, 60))
    head = "  ".join(label.ljust(width) for (_k, label), width in zip(columns, widths))
    print("  " + head)
    print("  " + "-" * len(head))
    for item in items[:limit]:
        cells = []
        for (key, _label), width in zip(columns, widths):
            text = str(item.get(key, ""))
            cells.append((text[: width - 1] + "…" if len(text) > width else text).ljust(width))
        print("  " + "  ".join(cells))
    if len(items) > limit:
        print(f"  …（还有 {len(items) - limit} 条，用 --limit 调整）")


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_overview(args) -> int:
    data = overview.get_overview()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print("\n盘符")
    _rows(data.get("disks") or [], [
        ("name", "盘符"), ("total_text", "总容量"), ("free_text", "可用"), ("used_percent", "已用%"),
    ], args.limit)
    print(f"\n  可释放空间（可清理范围）: {data.get('reclaimable_size_text')}")
    print(f"  隔离区: {data.get('trash', {}).get('count', 0)} 项")
    return 0


def cmd_scan(args) -> int:
    result = scan.run_scan(HeadlessContext({"path": args.path}))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"\n扫描 {result['root']}")
    print(f"  文件 {result['total_files']} 个 · 目录 {result['total_dirs']} 个 · 合计 {result['total_size_text']}")
    print(f"  耗时 {result['duration_seconds']}s · 可清理 {result.get('reclaimable_size_text')}")
    print("\n按类型")
    _rows(result.get("category_stats") or [], [
        ("label", "类型"), ("count", "数量"), ("size_text", "体积"), ("percentage", "占比%"),
    ], args.limit)
    print("\n最大的文件")
    _rows(result.get("large_files") or [], [("path", "路径"), ("size_text", "体积")], args.limit)
    return 0


def cmd_duplicates(args) -> int:
    result = duplicates.run_duplicate_scan(HeadlessContext({"path": args.path, "min_size": args.min_size}))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"\n查重 {result['root']}")
    print(f"  扫描 {result['scanned_files']} 个 · 重复组 {result['group_count']} 组 · "
          f"可清理 {result.get('reclaimable_size_text')}")
    for index, group in enumerate((result.get("groups") or [])[:args.limit], 1):
        print(f"\n  第 {index} 组（{group['count']} 个，可省 {group.get('size_text')}）")
        for item in group.get("files") or []:
            print(f"    {item.get('path')}")
    return 0


def cmd_similar(args) -> int:
    result = similar.find_similar(HeadlessContext({
        "path": args.path, "threshold": args.threshold, "min_size": args.min_size,
    }))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not result.get("available"):
        print(f"\n  {result.get('reason')}")
        return 1
    print(f"\n相似图片 {result.get('root')}（阈值 {result.get('threshold')}）")
    print(f"  参与比对 {result.get('scanned')} 张 · 分 {result.get('group_count')} 组 · "
          f"可省 {result.get('redundant_text')}")
    print(f"  ⚠️ {result.get('note')}")
    for index, group in enumerate((result.get("groups") or [])[:args.limit], 1):
        print(f"\n  第 {index} 组（{group['count']} 张，可省 {group['redundant_text']}）")
        for item in group.get("files") or []:
            mark = "★ 建议保留" if item["path"] == group.get("keep") else "  候选可删"
            print(f"    {mark}  {item.get('path')}")
    return 0


def cmd_junk(args) -> int:
    result = junk.run_junk_scan(HeadlessContext({"path": args.path}))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"\n垃圾分析 {result['root']}")
    print(f"  发现 {result['total_count']} 项 · {result['total_size_text']}"
          f"（其中安全项 {result.get('safe_count')} 项 · {result.get('safe_size_text')}）")
    for level in result.get("levels") or []:
        print(f"\n  【{level.get('label')}】{level.get('count')} 项 · {level.get('size_text')}")
        for item in (level.get("items") or [])[:args.limit]:
            print(f"    {item.get('size_text'):>10}  {item.get('path')}")
    return 0


def cmd_tree(args) -> int:
    result = scan.run_tree(HeadlessContext({"path": args.path, "depth": args.depth}))
    tree = result.get("tree") or {}

    def walk(node: dict, depth: int) -> None:
        children = node.get("children") or []
        for item in children:
            if depth > args.depth:
                return
            if not item.get("size"):
                continue  # 尺寸为 0 的是「未展开」，人看表格时没意义
            print(f"  {'  ' * depth}{fsutil.format_size(item.get('size')):>10}  {item.get('name')}")
            if item.get("is_dir"):
                walk(item, depth + 1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"\n空间地图 {result.get('root')}（{result.get('depth')} 层）")
    print(f"  合计 {tree.get('size_text')}")
    if tree.get("skipped"):
        names = "、".join(item["name"] for item in tree["skipped"])
        print(f"  已跳过: {names}")
    walk(tree, 0)
    return 0


def cmd_reclaimed(args) -> int:
    data = store.reclaimed_stats()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"\n累计腾出 {data['total_text']}（{data['count']} 次清理/迁移 · 今天 {data['today_text']}）")
    print(f"  ⚠️ {data['note']}")
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """`--json` / `--limit` 放在子命令**前面或后面都能用**。

    只定义在主 parser 上的话，`cli duplicates D:\\x --json` 会报错 ——
    而人几乎总是把这类开关写在最后。所以子命令上也挂一份；
    但子命令那份的 default 必须是 `SUPPRESS`，否则它会**把主 parser 上已经解析好的值覆盖掉**。
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出 JSON（给别的工具用）")
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="每个列表最多显示多少条（默认 20）")

    parser = argparse.ArgumentParser(
        prog="python -m backend.cli",
        description="Atlas 命令行（只读：只做分析，不做任何删除/移动）",
        epilog="写操作请走界面或 Agent —— 那两条路有确认页和整体回滚。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（给别的工具用）")
    parser.add_argument("--limit", type=int, default=20, help="每个列表最多显示多少条（默认 20）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("overview", parents=[common], help="盘符容量与可释放空间").set_defaults(func=cmd_overview)

    p = sub.add_parser("scan", parents=[common], help="扫描目录")
    p.add_argument("path")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("duplicates", parents=[common], help="找内容完全相同的文件")
    p.add_argument("path")
    p.add_argument("--min-size", type=int, default=1, help="小于这个字节数的文件忽略")
    p.set_defaults(func=cmd_duplicates)

    p = sub.add_parser("similar", parents=[common], help="找画面相似的图片（需要 pillow）")
    p.add_argument("path")
    p.add_argument("--threshold", type=int, default=similar.DEFAULT_THRESHOLD, help="相似阈值，越大越宽松")
    p.add_argument("--min-size", type=int, default=similar.DEFAULT_MIN_SIZE)
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("junk", parents=[common], help="分析可清理内容（只出清单）")
    p.add_argument("path")
    p.set_defaults(func=cmd_junk)

    p = sub.add_parser("tree", parents=[common], help="看目录里每项占多少空间")
    p.add_argument("path")
    p.add_argument("--depth", type=int, default=2, help="展开层数（1-4）")
    p.set_defaults(func=cmd_tree)

    sub.add_parser("reclaimed", parents=[common], help="累计腾出多少空间").set_defaults(func=cmd_reclaimed)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 终端默认可能是 GBK，中文路径/输出会乱 —— 统一 UTF-8
    # （同一个函数 MCP server 也在用：两处都要跟外部程序对话，编码必须一致）
    from backend.mcp_server import force_utf8

    force_utf8()

    args = build_parser().parse_args(argv)
    args.path = str(Path(args.path).expanduser()) if getattr(args, "path", None) else None
    args.depth = max(1, min(getattr(args, "depth", 2) or 2, 4))
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"出错：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

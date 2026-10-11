"""Atlas 的 MCP server（stdio + JSON-RPC 2.0）。

让 Claude Code / Codex / Cursor 这类客户端**只看你能指定的那几个目录**，
用上 Atlas 已有的扫描、查重、垃圾分析、空间地图能力。

## 为什么手写而不用官方 SDK

MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」，自己实现只有一百多行；
而引入 `mcp` 包会给这个"双击就能用"的自用工具多背一整套依赖链
（任何一台电脑双击就能用，是它一直守着的东西）。
**零新增运行时依赖**，所以手写。

## 安全模型（和项目既有的三条线一条不破）

| 约束 | 做法 |
|---|---|
| 看得见什么 | **默认一个目录都看不见**，必须 `--allow <目录>` 显式放行；只放行该目录子树 |
| 能干什么 | **只暴露只读工具**：没有任何删除 / 移动 / 写入 / 回滚工具 |
| 返回多大 | 每个工具的清单都有条目上限，超了如实标 `truncated` |
| 出错 | 错误写进 JSON-RPC 的 `error` 字段，正文写 stderr（**stdout 只放协议消息**） |

## 用法

    Atlas.exe --mcp --allow D:\\Downloads --allow D:\\Docs

客户端配置（Claude Desktop / Cursor 等）：

    {
      "mcpServers": {
        "atlas": { "command": "D:\\\\tools\\\\Atlas\\\\Atlas.exe",
                 "args": ["--mcp", "--allow", "D:\\\\Downloads"] }
      }
    }
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 允许 `python backend/mcp_server.py` 直接跑（PyInstaller 打包后不需要）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend import fsutil  # noqa: E402
from backend.services import duplicates, explain, junk, overview, scan, similar  # noqa: E402
from backend.services.headless import HeadlessContext as Headless  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "atlas"
SERVER_VERSION = "2.0.0"

# 单次返回的清单上限 —— MCP 的返回是塞进模型上下文的，不能无限长
MAX_ITEMS = 200


def _log(message: str) -> None:
    """诊断信息一律走 stderr。**stdout 只能出现协议消息**，混一行就废了整个连接。"""
    print(f"[atlas-mcp] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 目录白名单
# ---------------------------------------------------------------------------

class AllowList:
    """`--allow` 放行的目录。默认空 = 什么都看不见（不是"默认全开"）。"""

    def __init__(self, roots: list[str]) -> None:
        self.roots: list[Path] = []
        for raw in roots:
            try:
                self.roots.append(Path(raw).resolve())
            except (OSError, ValueError):
                _log(f"忽略无效的 --allow 路径：{raw}")

    def empty(self) -> bool:
        return not self.roots

    def check(self, raw: str) -> Path:
        """把客户端给的路径解析成绝对路径，并确认它在白名单里。"""
        if not raw:
            raise ValueError("缺少 path 参数")
        try:
            target = Path(raw).resolve()
        except (OSError, ValueError) as exc:
            raise ValueError(f"路径无法解析：{raw}（{exc}）") from exc

        if not self.roots:
            raise ValueError("没有放行任何目录。启动时加 --allow <目录> 才能访问。")

        for root in self.roots:
            if target == root or root in target.parents:
                if not target.is_dir():
                    raise ValueError(f"不是文件夹：{target}")
                return target
        allowed = "、".join(str(root) for root in self.roots)
        raise ValueError(f"路径不在放行范围内。当前只允许：{allowed}")


def _trim(items: list, limit: int = MAX_ITEMS) -> tuple[list, bool]:
    if len(items) <= limit:
        return items, False
    return items[:limit], True


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def tool_overview(_args: dict, _allow: AllowList) -> dict:
    """盘符容量与可释放空间 —— 不需要参数，也不受白名单限制（只读系统信息）。"""
    data = overview.get_overview()
    return data


def tool_scan(args: dict, allow: AllowList) -> dict:
    root = allow.check(args.get("path", ""))
    result = scan.run_scan(Headless({"path": str(root)}))
    big = result.get("large_files") or []
    trimmed, cut = _trim(big, 30)
    return {
        "root": str(root),
        "total_files": result.get("total_files"),
        "total_dirs": result.get("total_dirs"),
        "total_size_text": result.get("total_size_text"),
        "categories": result.get("category_stats"),
        "largest_files": [
            {"path": item.get("path"), "size_text": item.get("size_text")} for item in trimmed
        ],
        "truncated": cut,
    }


def tool_space_tree(args: dict, allow: AllowList) -> dict:
    root = allow.check(args.get("path", ""))
    depth = int(args.get("depth") or 2)
    depth = max(1, min(depth, 4))  # 深度封顶：再深就是整盘递归，别让一次调用把磁盘打满
    result = scan.run_tree(Headless({"path": str(root), "depth": depth}))
    tree = result.get("tree") or {}
    children = tree.get("children") or []
    trimmed, cut = _trim(children, 50)
    return {
        "root": result.get("root"),
        "depth": result.get("depth"),
        "total_size_text": tree.get("size_text"),
        "skipped": tree.get("skipped"),
        "children": [
            {"name": item.get("name"), "size_text": fsutil.format_size(item.get("size") or 0),
             "is_dir": item.get("is_dir"), "collapsed": item.get("collapsed", False)}
            for item in trimmed
        ],
        "truncated": cut,
    }


def tool_duplicates(args: dict, allow: AllowList) -> dict:
    root = allow.check(args.get("path", ""))
    params = {"path": str(root)}
    if args.get("min_size"):
        params["min_size"] = int(args["min_size"])
    result = duplicates.run_duplicate_scan(Headless(params))
    groups = result.get("groups") or []
    trimmed, cut = _trim(groups, 30)
    return {
        "root": str(root),
        "scanned_files": result.get("scanned_files"),
        "group_count": result.get("group_count"),
        "redundant_file_count": result.get("redundant_file_count"),
        "reclaimable_size_text": result.get("reclaimable_size_text"),
        "groups": [
            {"count": group.get("count"), "size_text": group.get("size_text"),
             "files": [item.get("path") for item in (group.get("files") or [])]}
            for group in trimmed
        ],
        "truncated": cut,
    }


def tool_junk(args: dict, allow: AllowList) -> dict:
    """垃圾文件分析 —— 只出清单，不做任何删除。"""
    root = allow.check(args.get("path", ""))
    result = junk.run_junk_scan(Headless({"path": str(root)}))
    levels = result.get("levels") or []
    out = []
    for level in levels:
        items = level.get("items") or []
        trimmed, cut = _trim(items, 50)
        out.append({
            "level": level.get("label") or level.get("key"),
            "count": level.get("count"),
            "size_text": level.get("size_text"),
            "items": [{"path": item.get("path"), "size_text": item.get("size_text"),
                       "reason": item.get("reason")} for item in trimmed],
            "truncated": cut,
        })
    return {
        "root": str(root),
        "total_size_text": result.get("total_size_text"),
        "levels": out,
        "note": "只出清单，不会删除任何东西。",
    }


def tool_similar_images(args: dict, allow: AllowList) -> dict:
    root = allow.check(args.get("path", ""))
    params = {"path": str(root)}
    if args.get("threshold"):
        params["threshold"] = int(args["threshold"])
    result = similar.find_similar(Headless(params))
    return result


def tool_explain(args: dict, _allow: AllowList) -> dict:
    """用模型解释一个待清理项是什么 —— 会联网、会花 token，由调用方自己决定要不要用。"""
    return explain.explain(args.get("kind") or "file", args.get("item") or {})


TOOLS = [
    {
        "name": "atlas_overview",
        "description": "看这台电脑的盘符容量、各盘剩余空间与可释放空间。不需要参数。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_overview,
        "needs_allow": False,
    },
    {
        "name": "atlas_scan",
        "description": "扫描一个目录：文件数、目录数、总体积、按类型分布、最大的若干文件。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要扫描的目录绝对路径"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": tool_scan,
        "needs_allow": True,
    },
    {
        "name": "atlas_space_tree",
        "description": "看一个目录下面每项占多少空间（空间地图的数据）。depth 默认 2，最大 4。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录绝对路径"},
                "depth": {"type": "integer", "description": "展开几层，1-4，默认 2"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": tool_space_tree,
        "needs_allow": True,
    },
    {
        "name": "atlas_duplicates",
        "description": "在目录里找内容完全相同的重复文件，给出分组与可释放空间。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录绝对路径"},
                "min_size": {"type": "integer", "description": "小于这个字节数的文件忽略，默认 1"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": tool_duplicates,
        "needs_allow": True,
    },
    {
        "name": "atlas_junk",
        "description": "分析一个目录里的可清理内容（缓存、临时文件、构建产物等），分档给出清单。只出清单，不删除。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录绝对路径"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": tool_junk,
        "needs_allow": True,
    },
    {
        "name": "atlas_similar_images",
        "description": "找画面相似（不要求字节相同）的图片，比如被压缩或缩放过的那一份。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录绝对路径"},
                "threshold": {"type": "integer", "description": "相似阈值 0-16，越大越宽松，默认 6"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "handler": tool_similar_images,
        "needs_allow": True,
    },
    {
        "name": "atlas_explain_item",
        "description": "让模型用一句话解释一个待清理项是什么、删了会怎样。⚠️ 这个工具会联网并消耗 token。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "junk / duplicate / similar / file"},
                "item": {"type": "object", "description": "至少包含 name；给 path/size_text/reason 更准"},
            },
            "required": ["item"],
            "additionalProperties": False,
        },
        "handler": tool_explain,
        "needs_allow": False,
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------

def _reply(request_id, result=None, error=None) -> None:
    message: dict = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(request: dict, allow: AllowList) -> None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    # 通知（没有 id）不需要回复
    if request_id is None and method and method.startswith("notifications/"):
        return

    if method == "initialize":
        client_version = params.get("protocolVersion")
        _reply(request_id, {
            "protocolVersion": client_version or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if method == "ping":
        _reply(request_id, {})
        return

    if method == "tools/list":
        _reply(request_id, {
            "tools": [
                {key: tool[key] for key in ("name", "description", "inputSchema")}
                for tool in TOOLS
            ],
        })
        return

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = next((item for item in TOOLS if item["name"] == name), None)
        if tool is None:
            _reply(request_id, error={"code": -32602, "message": f"没有这个工具：{name}"})
            return
        try:
            payload = tool["handler"](arguments, allow)
        except ValueError as exc:
            # 白名单/参数问题：作为工具结果返回，让模型自己看懂并纠正
            _reply(request_id, {
                "content": [{"type": "text", "text": f"参数有问题：{exc}"}],
                "isError": True,
            })
            return
        except Exception as exc:  # noqa: BLE001
            _log(f"{name} 执行出错：{type(exc).__name__}: {exc}")
            _reply(request_id, {
                "content": [{"type": "text", "text": f"执行失败：{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
            return
        _reply(request_id, {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": False,
        })
        return

    _reply(request_id, error={"code": -32601, "message": f"不支持的方法：{method}"})


def parse_args(argv: list[str]) -> tuple[list[str], bool]:
    """从命令行里取 --allow，返回 (放行目录, 是否请求了帮助)。"""
    roots: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--allow" and index + 1 < len(argv):
            roots.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("--allow="):
            roots.append(token.split("=", 1)[1])
        index += 1
    return roots, ("--help" in argv or "-h" in argv)


def force_utf8() -> None:
    """把 stdio 强制成 UTF-8，并把 GUI 子系统下缺失的标准流接回来。

    两个 Windows 专属的坑，都会让 MCP 客户端收到**乱码或直接连不上**：

    1. **编码**：Python 在 Windows 上默认用控制台代码页（简体中文是 GBK）编码标准流。
       而 MCP 协议规定 UTF-8 —— 工具描述里有中文，按 GBK 发出去、客户端按 UTF-8 解，
       结果就是一堆乱码。所以这里显式改成 UTF-8，并把换行统一成 `\\n`（协议是按行分帧的）。
    2. **GUI 子系统的 stdio**：打包时用了 `--noconsole`，Python 启动时会把
       `sys.stdin/stdout/stderr` 置成 **None**。这时把继承来的文件描述符 0/1/2 重新包起来。
    """
    import io

    for index, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
        stream = getattr(sys, name, None)
        if stream is None:
            mode = "rb" if index == 0 else "wb"
            try:
                handle = io.open(index, mode, closefd=False)
            except OSError:
                continue
            stream = io.TextIOWrapper(handle, encoding="utf-8", errors="replace", newline="")
            setattr(sys, name, stream)
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", newline="" if index == 0 else "\n")
        except (AttributeError, ValueError, OSError):
            pass


def serve(argv: list[str]) -> int:
    force_utf8()
    roots, wants_help = parse_args(argv)
    if wants_help:
        print(__doc__)
        return 0
    allow = AllowList(roots)
    if allow.empty():
        _log("没有 --allow，任何目录都访问不了。示例：Atlas.exe --mcp --allow D:\\Downloads")
    else:
        _log("放行目录：" + "、".join(str(root) for root in allow.roots))
    _log(f"就绪，{len(TOOLS)} 个只读工具")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _log(f"收到非 JSON 的一行，已忽略：{line[:120]}")
            continue
        if isinstance(request, list):
            for item in request:
                if isinstance(item, dict):
                    _handle(item, allow)
            continue
        if isinstance(request, dict):
            _handle(request, allow)
    return 0


def serve_main() -> int:
    """console_scripts 入口（`atlas-mcp`）。参数从 argv 取，用法同 `Atlas.exe --mcp`。"""
    return serve(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(serve(sys.argv[1:]))

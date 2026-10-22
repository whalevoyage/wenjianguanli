"""Atlas Windows 启动器：本地 API + 内置前端界面。

这是打包后的入口。为了做到「任何一台电脑双击就能用」，它要处理好四件事：

1. **端口**：偏好 8000，被占用就自动换一个空闲端口。
   不能让用户看到「黑窗口闪一下就没了」——那是无提示的失败。
2. **已有实例**：本机已经在跑 Atlas 时，不再起第二个进程
   （两个进程共用一个数据目录会互相打架），直接把浏览器指过去。
3. **开浏览器要等服务器真的起来**：提前开会撞上一个还没监听的端口，
   用户先看到「无法访问此网站」，然后服务才起来。
4. **出错必须看得见**：打包成无控制台窗口时（console=False），异常没有地方显示，
   所以同时写启动日志（state 目录下的 atlas.log）并弹一个消息框。

环境变量：
  FILEPILOT_PORT          指定端口（被占用时自动换空闲端口）
  FILEPILOT_OPEN_BROWSER  设为 0 / false 则不自动开浏览器（当后台服务跑时用）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from importlib.util import find_spec as _find_spec
from logging.handlers import RotatingFileHandler

import uvicorn

# 支持直接 `python packaging/launcher.py` 运行：脚本模式下 sys.path[0] 是 packaging/，
# 找不到 backend 包。PyInstaller 打包后不需要（已经收进 PYZ 里了）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend import config  # noqa: E402
from backend.main import app  # noqa: E402

PREFERRED_PORT = 8000
HEALTH_PATH = "/api/health"
SERVICE_ID = "filepilot-lite"
BOOT_LOG = config.STATE_DIR / "atlas.log"


# ---------------------------------------------------------------------------
# 日志：打包后没有控制台窗口，这是唯一能留下线索的地方
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(RotatingFileHandler(BOOT_LOG, maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    # 开发环境（有控制台）也打一份到屏幕；打包成窗口程序时 stderr 是 None，跳过
    if sys.stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    if not handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for h in handlers:
        h.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)


def _fatal(message: str) -> None:
    """打包后没有控制台，必须弹窗 —— 否则用户只看到「什么都没发生」。"""
    logging.getLogger("atlas").error("启动失败：%s", message)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Atlas 启动失败：\n\n{message}\n\n日志：{BOOT_LOG}",
            "Atlas",
            0x10,  # MB_ICONERROR
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 端口
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """让系统随便给一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _bind_socket(preferred: int) -> tuple[socket.socket, int]:
    """把端口真的绑下来，返回 (socket, 实际端口)。

    **先绑再交给 uvicorn**，而不是"先猜一个端口、再让 uvicorn 去绑"：
    后者在预检和 bind 之间留了一个窗口（端口可能刚好被别人抢走），
    结果就是「黑窗口闪一下就没了」。这里绑成功 = 端口确实归我们，
    顺带也让浏览器知道该指向哪个地址。
    """
    log = logging.getLogger("atlas")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", preferred))
        return sock, preferred
    except OSError:
        sock.close()
    free = _free_port()
    log.warning("端口 %s 已被其它程序占用，改用 %s", preferred, free)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", free))
    return sock, free


def _health_ok(port: int) -> bool:
    """这个端口上跑的是不是 Atlas —— 只看端口被占，会把别的程序误判成我们自己。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2) as resp:
            return json.loads(resp.read().decode("utf-8")).get("service") == SERVICE_ID
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def _serves_ui(port: int) -> bool:
    """这个端口上是不是一个**能用**的 Atlas：健康接口在，而且根路径真的给出界面。

    **只查健康接口是不够的。** 开发模式（`uvicorn backend.main:app`）跑的是同一个应用，
    同样会答 /api/health，但它没有 web/ 目录，根路径是一个裸的 JSON 404。
    凭健康接口就认定"已有实例在跑"，结果是：双击启动器 → 浏览器被指向那个没有界面的服务
    → 用户只看到 `{"detail":"Not Found"}`，而真正带界面的那份根本没启动。
    """
    if not _health_ok(port):
        return False
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(request, timeout=3) as resp:
            status = resp.status
            ctype = (resp.headers.get("Content-Type") or "").lower()
            head = resp.read(160).lstrip().lower()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False
    if status != 200 or "text/html" not in ctype:
        return False
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _preferred_port() -> int:
    raw = (os.getenv("FILEPILOT_PORT") or "").strip()
    return int(raw) if raw.isdigit() else PREFERRED_PORT


# ---------------------------------------------------------------------------
# 打开浏览器
# ---------------------------------------------------------------------------

def _open_browser_when_ready(port: int, timeout: float = 30.0) -> None:
    """轮询健康接口，服务真的起来了再开 —— 提前开只会撞上「无法访问此网站」。"""

    def worker() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _serves_ui(port):
                try:
                    webbrowser.open(f"http://127.0.0.1:{port}/")
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger("atlas").warning("打开浏览器失败：%s", exc)
                return
            time.sleep(0.25)
        # 起不来就别让用户对着空白等：弹框 + 指向日志
        _fatal(f"服务在 {timeout:.0f} 秒内没能就绪，界面没有打开。\n\n请查看日志确认原因。")

    threading.Thread(target=worker, name="atlas-open-browser", daemon=True).start()


def _browser_enabled() -> bool:
    value = (os.getenv("FILEPILOT_OPEN_BROWSER") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# 呈现界面：优先原生窗口，退而求其次才用浏览器
# ---------------------------------------------------------------------------

WINDOW_TITLE = "Atlas"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN = (1024, 680)


def _window_available() -> bool:
    """有没有 pywebview（Windows 上走 WebView2）。

    没装就安静地退回浏览器 —— 宁可形态差一点，也不能打不开。
    环境变量 FILEPILOT_WINDOW=0 可以强制走浏览器（排障用）。
    """
    if (os.getenv("FILEPILOT_WINDOW") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    try:
        if _find_spec("webview") is None:
            raise ImportError("找不到 webview 模块")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("atlas").info("未安装 pywebview（%s），改用浏览器打开", exc)
        return False
    return True


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _serves_ui(port):
            return True
        time.sleep(0.25)
    return False


def _open_window(port: int) -> bool:
    """开原生窗口，**阻塞到窗口被关掉**才返回。返回是否成功开出来。

    窗口用的是 WebView2（Win10/11 自带运行时），所以看起来就是个普通桌面应用：
    没有地址栏、没有标签页、任务栏是独立的项，关掉窗口 = 程序退出。
    """
    import webview

    webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{port}/",
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN,
        text_select=True,
    )
    # storage_path 指到自己的数据目录：不写临时目录，也不会在退出时报"删不掉用户数据"。
    # private_mode=False 是为了让 localStorage 落盘 —— Agent 页靠它记住"当前是哪个会话"。
    webview.start(storage_path=str(config.STATE_DIR / "webview"), private_mode=False)
    return True


# ---------------------------------------------------------------------------

def _run_mcp(argv: list[str]) -> int:
    """`Atlas.exe --mcp --allow <目录>`：把打包好的这个 exe 直接当成 MCP server 用。

    这样就不用再单独打包一个可执行文件 —— 同一个 exe，带 --mcp 走 stdio 协议，
    不带就起图形界面。
    """
    from backend.mcp_server import serve

    return serve(argv)


def _run_cli(argv: list[str]) -> int:
    """`Atlas.exe --cli scan D://x`：把同一个 exe 当命令行工具用。

    从源码跑时用 `python -m backend.cli ...`，效果完全一样 —— 这里只是让
    打包版（GUI 子系统、没有自己的控制台）在**被终端调用**时也能正常输出。
    """
    from backend.cli import main as cli_main
    from backend.mcp_server import force_utf8

    force_utf8()
    return cli_main([item for item in argv if item != "--cli"])


def _meta_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="启动 Atlas 桌面界面")
    parser.add_argument(
        "--version", action="version", version=f"Atlas {config.APP_VERSION}",
        help="显示版本并退出",
    )
    return parser


def main() -> None:
    # --mcp / --cli 要在配置日志之前分流：它们的 stdout 是**给别人用的**
    # （协议消息 / JSON），而 _configure_logging 会往文件与 stderr 写 —— 绝不碰 stdout。
    argv = sys.argv[1:]
    if "--mcp" in argv:
        raise SystemExit(_run_mcp(argv))
    if "--cli" in argv:
        raise SystemExit(_run_cli(argv))
    if any(item in {"--help", "-h", "--version"} for item in argv):
        _meta_parser().parse_args(argv)
    _configure_logging()
    log = logging.getLogger("atlas")

    preferred = _preferred_port()

    # 已经在跑一个**带界面**的实例：不要再起一个（两个进程写同一个数据目录会打架），指过去就行
    if _serves_ui(preferred):
        url = f"http://127.0.0.1:{preferred}/"
        log.info("检测到 Atlas 已在 %s 运行，直接打开界面", url)
        if _browser_enabled():
            try:
                webbrowser.open(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("打开浏览器失败：%s", exc)
        return

    # 端口上蹲着一个活着的 Atlas，却给不出界面 —— 典型是开发模式的后端（没有 web/ 目录）。
    # 这种情况**不能复用**，否则用户只会看到 {"detail":"Not Found"}。换个端口起带界面的自己。
    if _health_ok(preferred):
        log.warning("端口 %s 上的 Atlas 没有前端界面（多为开发模式后端），改用其它端口启动", preferred)

    # 先把端口绑下来 —— 绑成功才知道最终地址，浏览器也就不会指错地方
    try:
        sock, port = _bind_socket(preferred)
    except OSError as exc:
        _fatal(f"无法监听 127.0.0.1:{preferred}（{exc}）")
        return

    url = f"http://127.0.0.1:{port}/"
    log.info("Atlas 启动中，界面地址 %s", url)
    log.info("数据目录：%s", config.STATE_DIR)

    # 用自己绑好的 socket 起服务：不做二次 bind，也就没有"预检通过、真 bind 失败"的竞态。
    # log_config=None：沿用上面配好的日志，别让 uvicorn 覆盖掉文件日志。
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", log_config=None))

    if _window_available():
        # pywebview 的窗口循环必须跑在主线程，所以服务放后台线程。
        def serve() -> None:
            # 非主线程装不了信号处理器，不屏蔽掉会直接抛 ValueError
            server.install_signal_handlers = lambda: None
            server.run(sockets=[sock])

        threading.Thread(target=serve, name="atlas-server", daemon=True).start()

        if not _wait_ready(port):
            log.error("服务没能在 30 秒内就绪，仍未打开窗口")
            _fatal("本地服务没能启动，窗口没有打开。\n\n请查看日志确认原因。")
            return

        try:
            _open_window(port)
            log.info("窗口已关闭，退出")
            return
        except Exception as exc:  # noqa: BLE001
            # 窗口开不出来不是致命问题 —— 退回浏览器，功能照旧
            log.warning("原生窗口打开失败（%s），改用浏览器", exc)

    # 兜底：浏览器
    if _browser_enabled():
        _open_browser_when_ready(port)

    try:
        server.run(sockets=[sock])
    except OSError as exc:
        _fatal(f"无法在 {url} 提供服务（{exc}）")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001  窗口模式下没有控制台，兜住一切并弹窗
        _fatal(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1)

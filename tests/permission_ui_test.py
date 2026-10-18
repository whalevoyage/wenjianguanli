"""权限档位切换的真点击回归测试（Playwright 驱动真实 Chromium）。

要回答的问题：Agent 页输入框左侧那颗权限胶囊，**点开后能不能真的切档**。
无头 shell（--dump-dom）测不了点击，所以这里用 Playwright。

跑法：
    python tests/permission_ui_test.py            # 需先起 8000 后端 + 5173 前端
    PLAYWRIGHT_CHROMIUM=<chrome-headless-shell.exe> python tests/permission_ui_test.py
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

FRONTEND = os.getenv("FRONTEND_URL", "http://127.0.0.1:5173")
API = os.getenv("FILEPILOT_API", "http://127.0.0.1:8000/api")

PASS, FAIL = "  [OK]", "  [!!]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def current_mode() -> str:
    import json
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{API}/settings/safety", timeout=8) as resp:
        return json.loads(resp.read())["mode"]


def set_mode(mode: str) -> None:
    import json
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{API}/settings/safety",
        data=json.dumps({"mode": mode, "confirm": mode == "full"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener.open(req, timeout=8).read()


def open_popover(page, tries: int = 3):
    """点胶囊把权限弹层打开。EP 偶尔会因为上一次的关闭动画而吃掉第一次点击，
    所以最多重试几次。返回可见的 popover locator。"""
    for _ in range(tries):
        page.locator(".perm-pill").click()
        page.wait_for_timeout(700)
        pop = page.locator(".el-popper:has-text('权限档位')").filter(
            has=page.locator(".panel__option")
        )
        for i in range(pop.count()):
            item = pop.nth(i)
            if item.evaluate("el => getComputedStyle(el).display !== 'none'"):
                return item
    return page.locator(".el-popper:visible", has_text="权限档位").first


def run() -> int:
    exe = os.getenv("PLAYWRIGHT_CHROMIUM")
    launch: dict = {"args": ["--no-sandbox", "--disable-gpu"]}
    if exe:
        launch["executable_path"] = exe

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1300, "height": 720})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{FRONTEND}/#/agent", wait_until="networkidle")
        page.wait_for_timeout(1200)

        print("1) 胶囊存在且可点")
        pill = page.locator(".perm-pill")
        check("胶囊已渲染", pill.count() == 1, f"count={pill.count()}")
        if pill.count() != 1:
            browser.close()
            return 1
        check("胶囊有文字", bool(pill.inner_text().strip()), repr(pill.inner_text()))

        print("\n2) 点击胶囊 → 弹层应可见")
        pop = open_popover(page)
        shown = pop.count() >= 1 and pop.evaluate("el => getComputedStyle(el).display !== 'none'")
        check("弹层可见", shown)
        if not shown:
            browser.close()
            return 1

        opacity = pop.evaluate("el => getComputedStyle(el).opacity")
        check("弹层不透明（opacity=1）", opacity == "1", f"opacity={opacity}")
        bg = pop.evaluate("el => getComputedStyle(el).backgroundColor")
        check("弹层有不透明底色", bg != "rgba(0, 0, 0, 0)", bg)

        print("\n3) 弹层内三档齐全")
        options = pop.locator(".panel__option")
        labels = [options.nth(i).inner_text().strip() for i in range(options.count())]
        check("三档都在", options.count() == 3, f"选项={labels}")
        page.screenshot(path="permission_popover.png")
        print("     已截图 permission_popover.png（弹层打开时的样子）")

        print("\n4) 命中测试：选项中心点上最顶层就是它自己")
        for i in range(options.count()):
            hit = options.nth(i).evaluate(
                "el => { const r = el.getBoundingClientRect();"
                " const t = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);"
                " return t === el || el.contains(t) }"
            )
            check(f"「{labels[i]}」可点（未被遮挡）", hit is True)

        print("\n5) 真实点击切到「只读」→ 后端变更、胶囊更新、弹层自动收起")
        set_mode("standard")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        pop = open_popover(page)
        pop.locator(".panel__option", has_text="只读").first.click()
        page.wait_for_timeout(1000)
        check("后端已切到 readonly", current_mode() == "readonly", f"当前={current_mode()}")
        label = page.locator(".perm-pill").inner_text().strip()
        check("胶囊文案已更新", "只读" in label, repr(label))
        still_open = pop.evaluate("el => getComputedStyle(el).display !== 'none'")
        check("选完档位弹层自动收起（本轮修复）", still_open is False, "display 仍为 block" if still_open else "")

        print("\n6) 切档后能再次打开，并可切到「完全访问」")
        pop2 = open_popover(page)
        check("切档后弹层能重新打开", pop2.count() >= 1
              and pop2.evaluate("el => getComputedStyle(el).display !== 'none'"))
        pop2.locator(".panel__option", has_text="完全访问").first.click()
        page.wait_for_timeout(900)
        page.screenshot(path="permission_confirm.png")
        print("     已截图 permission_confirm.png（确认框出现时的样子）")
        box = page.locator(".el-message-box")
        check("出现二次确认框", box.count() == 1 and box.is_visible())
        if box.count() == 1:
            page.locator(".el-message-box__btns .el-button--primary").click()
            page.wait_for_timeout(1000)
            check("确认后切到 full", current_mode() == "full", f"当前={current_mode()}")

        print("\n7) 点当前档位 → 应有反馈而不是毫无反应")
        pop3 = open_popover(page)
        pop3.locator(".panel__option", has_text="完全访问").first.click()
        page.wait_for_timeout(800)
        pop4 = open_popover(page)
        pop4.locator(".panel__option", has_text="完全访问").first.click()
        page.wait_for_timeout(800)
        msg = page.locator(".el-message")
        check("点当前档位有提示", msg.count() >= 1,
              (msg.first.inner_text().strip()[:30] if msg.count() else "无提示"))
        check("点当前档位后弹层收起",
              pop4.evaluate("el => getComputedStyle(el).display !== 'none'") is False)

        print("\n8) 还原为标准档")
        set_mode("standard")

        if errors:
            print("\n页面 JS 异常：")
            for e in errors:
                print("   ", e[:160])
        check("无未捕获 JS 异常", not errors, f"{len(errors)} 条")

        browser.close()

    print("\n" + "=" * 52)
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())

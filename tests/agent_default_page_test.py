# -*- coding: utf-8 -*-
"""校验 Agent 助手页的「默认页」行为（用户 2026-10-07 要求，详见 docs/UI_REVAMP.md v26）。

规则：每次启动后的**首次**进入 = 「新任务」欢迎页；同一次运行内切走再回来沿用当前会话。

场景：
  A 本次启动首访（新标签页 => sessionStorage 为空），localStorage 里却存着一条有内容的历史会话
    -> 期望显示欢迎页（.agent-hero 可见），而不是恢复那条会话
  B 同一次运行内切走再回来（当前会话还是空的）-> 期望仍是欢迎页
  C 非首访 + localStorage 指向有内容的旧会话 -> 期望恢复正常（hero 不可见）

前置：后端 :8000 与前端 dev :5173 已启动。
本机 ms-playwright 里的 chromium 已被磁盘清理带走，故改用系统 Edge（channel="msedge"）。
"""
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path(__file__).resolve().parent.parent          # 截图落在项目根（与其它 *_check.png 一致）


def pick_conversation():
    """挑一条「确实有内容」的历史会话；挑不到就返回空串（A/C 会退化成空态）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/agent/conversations", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("conversations") or []:
            if item.get("count"):
                return item["conversation_id"]
    except Exception as exc:                                    # noqa: BLE001
        print("  (会话列表取不到：%s)" % exc)
    return ""


def main():
    conv = pick_conversation()
    lines = []

    def log(s):
        print(s)
        lines.append(str(s))

    log("用于对照的历史会话 = %s" % (conv or "(无)"))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        # 每次页面加载前预置 localStorage —— 模拟「上次用的就是这条会话」
        if conv:
            ctx.add_init_script(
                "try{localStorage.setItem('filepilot.agent.conversationId','%s')}catch(e){}" % conv
            )
        page = ctx.new_page()

        def hero_visible():
            loc = page.locator(".agent-hero")
            return loc.count() > 0 and loc.first.is_visible()

        def hero_title():
            try:
                return page.locator(".agent-hero__title").first.inner_text().strip()
            except Exception:                                   # noqa: BLE001
                return "(无)"

        # ---------------- A：本次启动首访 ----------------
        page.goto(BASE + "/#/agent", wait_until="load")
        page.wait_for_timeout(3000)
        a_ok = hero_visible()
        log("")
        log("[A] 本次启动后首次进入（新标签页 => sessionStorage 为空）")
        log("    localStorage 里预先存着 = %s" % conv)
        log("    hero 可见 = %s  <-- 期望 True" % a_ok)
        log("    hero 标题 = %s" % hero_title())
        log("    sessionStorage 标记 = %s" % page.evaluate("sessionStorage.getItem('filepilot.agent.booted')"))
        page.screenshot(path=str(OUT / "agent_default_page_check.png"))

        # ---------------- B：同一次运行内切走再回来 ----------------
        page.goto(BASE + "/#/duplicates", wait_until="load")
        page.wait_for_timeout(1500)
        page.goto(BASE + "/#/agent", wait_until="load")
        page.wait_for_timeout(2500)
        b_ok = hero_visible()
        log("")
        log("[B] 同一次运行内切走再回来（当前会话还是空的）")
        log("    hero 可见 = %s  <-- 期望 True" % b_ok)

        # ---------------- C：非首访 + 指向有内容的会话 ----------------
        if conv:
            page.evaluate("localStorage.setItem('filepilot.agent.conversationId','%s')" % conv)
            page.goto(BASE + "/#/junk", wait_until="load")
            page.wait_for_timeout(1500)
            page.goto(BASE + "/#/agent", wait_until="load")
            page.wait_for_timeout(3200)
            c_ok = not hero_visible()
            log("")
            log("[C] 非首访 + localStorage 指向有内容的旧会话")
            log("    hero 不可见（已恢复对话）= %s  <-- 期望 True" % c_ok)
            page.screenshot(path=str(OUT / "agent_default_resume_check.png"))
        else:
            c_ok = True
            log("")
            log("[C] 跳过（后端没有可用作对照的历史会话）")

        browser.close()

    log("")
    log("=== 结论：A=%s B=%s C=%s ===" % (a_ok, b_ok, c_ok))
    return 0 if (a_ok and b_ok and c_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

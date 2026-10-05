"""Agent 输入框工具行优化后的结构/样式核验（真渲染 + 计算样式，不依赖读图）。

只验证前端渲染结果：控件高度是否统一、标准态胶囊是否透明底+细边、
模型触发器是否更淡、流式状态是否移到了右侧、权限弹层宽度与每档说明是否落地。
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

EXE = os.getenv("PLAYWRIGHT_CHROMIUM") or r"C:\Users\faker\AppData\Local\ms-playwright\chromium_headless_shell-1234\chrome-headless-shell-win64\chrome-headless-shell.exe"
URL = "http://127.0.0.1:5173/#/agent"


def box(page, sel):
    el = page.query_selector(sel)
    if not el:
        return None
    b = el.bounding_box()
    return b


def main():
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EXE, args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        msgs = []
        page.on("console", lambda m: msgs.append(m.text))
        page.on("pageerror", lambda e: msgs.append("PAGEERROR: " + str(e)))
        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector(".composer", timeout=8000)

        # 1) 工具行控件高度
        heights = {}
        for sel in [".composer__icon", ".perm-pill", ".model-trigger", ".send-button", ".composer__row"]:
            b = box(page, sel)
            heights[sel] = round(b["height"], 1) if b else None
        results["heights"] = heights

        # 2) 权限胶囊（标准态）样式：透明底 + 细边
        pill = page.query_selector(".perm-pill")
        if pill:
            cs = pill.evaluate("el => ({ bg: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderTopColor, h: getComputedStyle(el).height })")
            results["perm_pill_standard"] = cs

        # 3) 模型触发器颜色（应偏淡 ink-4）
        mt = page.query_selector(".model-trigger")
        if mt:
            results["model_trigger_color"] = mt.evaluate("el => getComputedStyle(el).color")
            results["model_dot_present"] = mt.query_selector(".model-dot") is not None

        # 4) composer__phase 是否在右侧（x 大于 spacer 中点）
        phase = page.query_selector(".composer__phase")
        spacer = page.query_selector(".composer__spacer")
        if phase and spacer:
            pb = phase.bounding_box()
            sb = spacer.bounding_box()
            results["phase_right_of_spacer"] = bool(pb and sb and pb["x"] >= sb["x"])
            results["phase_text"] = phase.inner_text()
        else:
            results["phase_right_of_spacer"] = None
            results["phase_text"] = None

        # 5) 权限弹层：点开，看宽度与每档说明
        pill.click()
        page.wait_for_selector(".perm-popper .panel__option", timeout=4000)
        opts = page.query_selector_all(".perm-popper .panel__option")
        results["perm_option_count"] = len(opts)
        descs = []
        for o in opts:
            d = o.query_selector(".panel__option-desc")
            descs.append(d.inner_text() if d else None)
        results["perm_option_descs"] = descs
        pop = page.query_selector(".perm-popper")
        if pop:
            results["perm_popover_width"] = round(pop.bounding_box()["width"], 1)
        # 选中态高亮
        active = page.query_selector(".perm-popper .panel__option.is-active")
        results["perm_active_present"] = active is not None

        results["console_errors"] = [m for m in msgs if "PAGEERROR" in m or "error" in m.lower()][:5]
        browser.close()

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FAILED:", repr(e), file=sys.stderr)
        sys.exit(1)

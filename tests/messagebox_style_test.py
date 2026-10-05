"""验证 ElMessageBox / ElMessage 的样式是否真的加载了。

背景：这两个是**命令式调用**的，模板里没有标签，
unplugin-vue-components 的 ElementPlusResolver 扫模板扫不到 → 样式不注入
→ 表现为「确认框裸着跑到左上角、吐司没底色」。

跑法：python tests/messagebox_style_test.py
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

FRONTEND = os.getenv("FRONTEND_URL", "http://127.0.0.1:5173")
exe = os.getenv("PLAYWRIGHT_CHROMIUM", "")

PASS, FAIL = "  [OK]", "  [!!]"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


launch: dict = {"args": ["--no-sandbox", "--disable-gpu"]}
if exe:
    launch["executable_path"] = exe

with sync_playwright() as p:
    b = p.chromium.launch(**launch)
    page = b.new_page(viewport={"width": 1300, "height": 720})
    page.goto(f"{FRONTEND}/#/agent", wait_until="networkidle")
    page.wait_for_timeout(1500)

    print("1) 样式是否真的生效")
    # Vite dev 下样式是内联注入的 <style>，没有独立 href，
    # 所以不能查样式表文件名，要查「规则是否存在于文档里」
    rules = page.evaluate("""() => {
        let text = ''
        for (const s of document.styleSheets) {
            try { for (const r of s.cssRules) text += r.cssText + '\\n' } catch (e) {}
        }
        return {
            box: /\\.el-message-box[\\s{]/.test(text),
            msg: /\\.el-message[\\s{]/.test(text),
            overlay: /\\.el-overlay[\\s{]/.test(text),
        }
    }""")
    print("     ", rules)
    check("el-message-box 规则已注入", rules["box"])
    check("el-message 规则已注入", rules["msg"])
    check("el-overlay 规则已注入", rules["overlay"])

    print("\n2) ElMessageBox 对话框的样式与定位")
    # Agent 页切「完全访问」会走 ElMessageBox.confirm，用它当入口
    page.locator(".perm-pill").click()
    page.wait_for_timeout(700)
    page.locator(".panel__option", has_text="完全访问").first.click()
    page.wait_for_timeout(900)

    box = page.locator(".el-message-box")
    check("对话框已渲染", box.count() == 1)
    if box.count() == 1:
        info = box.evaluate("""el => {
            const cs = getComputedStyle(el)
            const r = el.getBoundingClientRect()
            return {
                bg: cs.backgroundColor,
                radius: cs.borderRadius,
                shadow: cs.boxShadow.slice(0, 30),
                pos: cs.position,
                w: Math.round(r.width), h: Math.round(r.height),
                left: Math.round(r.left), top: Math.round(r.top),
            }
        }""")
        print("     ", info)
        check("有白底（不是裸 DOM）", info["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), info["bg"])
        check("有圆角", info["radius"] not in ("0px",), info["radius"])
        check("有投影", info["shadow"] not in ("none", ""), info["shadow"])
        check("水平居中", abs(info["left"] - (1300 - info["w"]) / 2) < 40,
              f"left={info['left']}, 期望≈{(1300 - info['w']) // 2}")
        check("宽度合理（不是撑满/贴边）", 200 < info["w"] < 700, f"{info['w']}px")

        # 按钮应当有 EP 按钮样式（有圆角、有背景）
        btns = box.locator(".el-message-box__btns button")
        check("按钮有 2 个", btns.count() == 2, f"count={btns.count()}")
        if btns.count() == 2:
            bs = btns.first.evaluate(
                "el => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();"
                " return { r: cs.borderRadius, h: Math.round(r.height) } }"
            )
            check("按钮有圆角与高度", bs["r"] not in ("0px",) and bs["h"] > 20, str(bs))

        page.screenshot(path="message_box_check.png")
        print("     已截图 message_box_check.png")

        page.locator(".el-message-box__btns .el-button").first.click()  # 取消
        page.wait_for_timeout(600)

    print("\n3) ElMessage 吐司的样式")
    # 故意点当前档位，触发 ElMessage.info（那条「当前已经是…」）
    page.locator(".perm-pill").click()
    page.wait_for_timeout(700)
    page.locator(".panel__option", has_text="标准").first.click()
    page.wait_for_timeout(700)
    msg = page.locator(".el-message")
    if msg.count() == 0:
        # 切档成功后没有 info，只有切到同一档才有；再点一次
        page.locator(".perm-pill").click()
        page.wait_for_timeout(700)
        page.locator(".panel__option", has_text="标准").first.click()
        page.wait_for_timeout(800)
        msg = page.locator(".el-message")
    check("吐司已出现", msg.count() >= 1)
    if msg.count() >= 1:
        ms = msg.first.evaluate("""el => {
            const cs = getComputedStyle(el)
            const r = el.getBoundingClientRect()
            return { bg: cs.backgroundColor, r: cs.borderRadius,
                     top: Math.round(r.top), left: Math.round(r.left) }
        }""")
        print("     ", ms)
        check("吐司有底色", ms["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), ms["bg"])
        check("吐司有圆角", ms["r"] not in ("0px",), ms["r"])
        check("吐司在顶部居中（不是左上角）", ms["top"] < 200 and ms["left"] > 1300 / 4,
              f"top={ms['top']}, left={ms['left']}")
        page.screenshot(path="message_toast_check.png")
        print("     已截图 message_toast_check.png")

    b.close()

print("\n" + "=" * 52)
if failures:
    print(f"失败 {len(failures)} 项：")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过")

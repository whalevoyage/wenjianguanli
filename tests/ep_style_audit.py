"""核对每个页面用到的 EP 组件，其样式是否真的注入了。

背景：`unplugin-vue-components` + `ElementPlusResolver` 靠**扫描模板**注入样式。
模板里没用到的组件不会有样式 —— 命令式调用（ElMessage / ElMessageBox）就是这种情况，
已手动在 main.js 引入了。本脚本防的是「又漏了一个」。

跑法：python tests/ep_style_audit.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FRONTEND = os.getenv("FRONTEND_URL", "http://127.0.0.1:5173")
SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
exe = os.getenv("PLAYWRIGHT_CHROMIUM", "")

# 组件名 → 它依赖的样式文件（EP 拆包后大致对应，取主包名）
ROUTES = {
    "/": "Home", "/agent": "Agent", "/space": "SpaceMap", "/virtual": "Virtual",
    "/projects": "Projects", "/scan": "Scan", "/duplicates": "Duplicates",
    "/junk": "Junk", "/migrate": "Migrate", "/backup": "Backup",
    "/trash": "Trash", "/logs": "Logs", "/jobs": "Jobs",
}

# 这些子组件在 EP 里**没有独立的 .el-xxx 样式类**（由父组件的样式负责渲染），
# 审计时要跳过，否则误报。已核实：el-option 由 el-select 的 .el-select-dropdown__item 渲染。
NO_OWN_STYLE = {
    "el-option",
    # Element Plus 的下拉项没有独立的 .el-dropdown-item 样式类，
    # 实际 DOM 使用 .el-dropdown-menu__item，由 el-dropdown 样式统一覆盖。
    "el-dropdown-item",
}


def used_components() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in list(SRC.glob("views/*.vue")) + list(SRC.glob("components/*.vue")):
        text = f.read_text(encoding="utf-8")
        # 只取 <template> 段，避免把注释里的也算进来
        m = re.search(r"<template>(.*)</template>", text, re.S)
        body = m.group(1) if m else text
        out[f.stem] = set(re.findall(r"<(el-[a-z-]+)", body))
    return out


launch: dict = {"args": ["--no-sandbox", "--disable-gpu"]}
if exe:
    launch["executable_path"] = exe

comps_by_view = used_components()
problems: list[str] = []

with sync_playwright() as p:
    b = p.chromium.launch(**launch)
    page = b.new_page(viewport={"width": 1400, "height": 800})

    for route, view in ROUTES.items():
        page.goto(f"{FRONTEND}/#{route}", wait_until="networkidle")
        page.wait_for_timeout(900)
        rules = page.evaluate("""() => {
            let t = ''
            for (const s of document.styleSheets) {
                try { for (const r of s.cssRules) t += r.cssText + '\\n' } catch (e) {}
            }
            return t
        }""")
        used = comps_by_view.get(view, set()) - NO_OWN_STYLE
        missing = []
        for c in sorted(used):
            cls = "." + c  # el-table -> .el-table
            if not re.search(re.escape(cls) + r"(?![\w-])", rules):
                missing.append(c)
        if missing:
            problems.append(f"{route} ({view}): 缺 {', '.join(missing)}")
            print(f"  [!!] {route:12} {view:10} 缺样式 → {', '.join(missing)}")
        else:
            print(f"  [OK] {route:12} {view:10} {len(used)} 个组件样式齐全")

    b.close()

print("\n" + "=" * 52)
if problems:
    print(f"发现 {len(problems)} 处缺失：")
    for x in problems:
        print("  -", x)
    sys.exit(1)
print("所有页面组件样式齐全")

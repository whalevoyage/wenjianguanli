# -*- coding: utf-8 -*-
"""回归：切到「操作日志」不该白屏 / 不该有长任务阻塞。

背景（2026-10-07 实测定位）：
  464 条日志原本一次性全渲染进 el-table（464 行 × 5 列，4 列挂自定义 slot），
  主线程阻塞 1251ms（单次最长 646ms）；而离场页的 opacity 淡出跑在合成器线程上、
  不受阻塞影响会一路跑到全透明 —— 新页面图层还没提交绘制，屏幕上只剩空白底色。
  修法：① 表格分页（每页 50）；② 灌数据前先让出一帧，把「骨架 + 遮罩」画出来。

判定：
  - 无「露底色」帧（.workspace-content 子元素 maxOp 恒 > 0.05）
  - 累计 longtask 显著低于修复前的 1251ms
  - 首屏渲染行数 == pageSize

前置：后端 :8000、前端 dev :5173 已启动；用系统 Edge（ms-playwright 的 chromium 已被清理）。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path(__file__).resolve().parent.parent

PAGE_SIZE = 50
BLOCK_BUDGET_MS = 400          # 修复前 1251ms

SAMPLER = r"""
window.__probe = { frames: [], longtasks: [] };
try {
  new PerformanceObserver(function(list){
    list.getEntries().forEach(function(e){
      window.__probe.longtasks.push({ s: Math.round(e.startTime), d: Math.round(e.duration) });
    });
  }).observe({ entryTypes: ['longtask'] });
} catch(e) {}
(function(){
  function tick(){
    var el = document.querySelector('.workspace-content');
    var kids = el ? Array.prototype.slice.call(el.children) : [];
    var ops = kids.map(function(c){ return parseFloat(getComputedStyle(c).opacity); });
    window.__probe.frames.push({
      t: Math.round(performance.now()),
      maxOp: ops.length ? Math.round(Math.max.apply(null, ops) * 100) / 100 : -1,
      rows: document.querySelectorAll('.el-table__row').length
    });
    if (window.__probe.frames.length < 300) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
})();
"""


def main():
    lines = []

    def log(s):
        print(s)
        lines.append(str(s))

    ok = True

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.goto(BASE + "/#/home", wait_until="load")
        page.wait_for_timeout(3000)

        page.evaluate(SAMPLER)
        page.evaluate("window.__probe.longtasks = []")
        page.click('a[href="#/logs"]')
        page.wait_for_timeout(4000)

        probe = page.evaluate("window.__probe")
        lt = probe["longtasks"]
        frames = probe["frames"]
        total_block = sum(e["d"] for e in lt)

        blanks = [f for f in frames if 0 <= f["maxOp"] < 0.05]
        rows_first_page = page.evaluate("document.querySelectorAll('.el-table__row').length")
        try:
            total_text = page.locator(".logs-pager__total").inner_text().strip()
        except Exception:
            total_text = "(未找到分页条)"

        log("longtask 数 = %d，累计阻塞 = %dms   （修复前：5 个 / 1251ms）" % (len(lt), total_block))
        for e in lt[:10]:
            log("    start=%sms duration=%dms" % (e["s"], e["d"]))
        log("露底色帧数 = %d   （期望 0）" % len(blanks))
        log("首屏渲染行数 = %d   （期望 %d）" % (rows_first_page, PAGE_SIZE))
        log("分页条文案 = %s" % total_text)

        # 截图：滚到分页条处，顺带把它一起框进画面
        try:
            page.locator(".logs-pager").scroll_into_view_if_needed()
            page.wait_for_timeout(500)
        except Exception:                              # noqa: BLE001
            pass
        page.screenshot(path=str(OUT / "logs_tab_after_check.png"))

        # 翻到第 2 页，确认分页真的生效
        try:
            page.locator(".el-pager li", has_text="2").first.click()
            page.wait_for_timeout(900)
            rows_p2 = page.evaluate("document.querySelectorAll('.el-table__row').length")
            log("第 2 页渲染行数 = %d" % rows_p2)
            if rows_p2 == 0:
                ok = False
                log("  !! 第 2 页没有行")
        except Exception as exc:                       # noqa: BLE001
            ok = False
            log("翻页失败：%s" % exc)

        if blanks:
            ok = False
        if total_block > BLOCK_BUDGET_MS:
            ok = False
            log("  !! 累计阻塞 %dms 超出预算 %dms" % (total_block, BLOCK_BUDGET_MS))
        if rows_first_page != PAGE_SIZE:
            ok = False

        browser.close()

    log("")
    log("=== 结论：%s ===" % ("通过" if ok else "未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

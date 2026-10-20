// 为 README 出 3 张演示截图（用当前 2.1.0 代码 + 干净的演示目录，不拿旧图凑）。
// 目录是现造的：文档/合同/项目/媒体 + 几个可清理的 log/tmp/bak。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = 'D:\\FilePilotLite\\docs\\images'
const DEMO = 'C:/Users/faker/Documents/资料库'
fs.mkdirSync(OUT, { recursive: true })

const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name), fullPage: false })
  console.log('  截图:', name)
}

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_readme_shots'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,             // 2x，README 里放大不糊
  })
  const page = ctx.pages()[0] || (await ctx.newPage())
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  // ---------- 1. 首页 ----------
  await page.goto('http://127.0.0.1:5173/#/', { waitUntil: 'load' })
  await page.waitForTimeout(4500)
  await shot(page, 'overview.png')

  // ---------- 2. 垃圾清理预检 ----------
  await page.goto('http://127.0.0.1:5173/#/junk', { waitUntil: 'load' })
  await page.waitForTimeout(2500)

  const input = page.locator('.target-picker input').first()
  await input.click()
  await input.fill(DEMO)
  await input.press('Enter')
  await page.waitForTimeout(1200)

  // 开始预检：按钮文案可能是「开始检查」「开始扫描」之类，按 ite 兜底
  const startBtn = page.locator('button', { hasText: /开始|检查|扫描/ }).first()
  if (await startBtn.count()) {
    await startBtn.click()
    await page.waitForTimeout(6000)
  }
  await shot(page, 'junk-scan.png')

  // ---------- 3. 执行清理（生成一条可撤销记录）----------
  const isolateBtn = page.locator('button', { hasText: /隔离|清理|送到隔离区/ }).first()
  if (await isolateBtn.count()) {
    await isolateBtn.click()
    await page.waitForTimeout(2500)
    // 可能的二次确认
    const ok = page.locator('.el-message-box__btns .el-button--primary')
    if (await ok.count()) {
      await ok.first().click()
      await page.waitForTimeout(4000)
    }
  }

  // ---------- 4. 任务中心：最近操作 ----------
  await page.goto('http://127.0.0.1:5173/#/jobs', { waitUntil: 'load' })
  await page.waitForTimeout(3500)
  await shot(page, 'journal-undo.png')

  console.log('pageerrors =', errors.length ? errors : 'none')
  await ctx.close()
})()

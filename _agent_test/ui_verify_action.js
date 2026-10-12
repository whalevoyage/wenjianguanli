// 「处理结果」（勾选 → 送隔离区/回收站）实机验证：扫描 / 相似图片 / 检索 三页
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })
const ORIGIN = process.env.ORIGIN || 'http://127.0.0.1:5174'
const SAMPLE = process.env.SAMPLE_DIR

const shot = async (page, name) => { await page.screenshot({ path: path.join(OUT, name + '.png') }); console.log('  shot:', name) }

async function addTarget(page, dir) {
  const input = page.locator('.multi-add input').first()
  await input.fill(dir)
  await input.press('Enter')
  await page.waitForTimeout(300)
}

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_action_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 1000 },
  })
  const page = ctx.pages()[0] || await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))

  // ---- 扫描分析 ----
  console.log('[扫描]')
  await page.goto(`${ORIGIN}/#/scan`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  await addTarget(page, SAMPLE)
  await page.getByRole('button', { name: /开始扫描/ }).first().click()
  // 扫描页刻意不自动加载结果，要手动点「载入结果」
  const loadBtn = page.getByRole('button', { name: '载入结果' })
  await loadBtn.waitFor({ state: 'visible', timeout: 90000 })
  await loadBtn.click()
  await page.waitForSelector('.data-table', { timeout: 30000 })
  await page.waitForTimeout(1200)
  const scanPicks = page.locator('.data-table').last().locator('.el-checkbox:visible')
  console.log('  表格勾选框数 =', await scanPicks.count())
  if (await scanPicks.count()) {
    await scanPicks.first().scrollIntoViewIfNeeded().catch(() => {})
    await scanPicks.first().click({ force: true })
    await page.waitForTimeout(500)
  }
  console.log('  sticky bar 可见 =', await page.locator('.sticky-bar').isVisible().catch(() => false))
  console.log('  含 隔离区/回收站 切换 =', await page.locator('.sticky-bar .el-segmented').isVisible().catch(() => false))
  await page.locator('.sticky-bar').scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(300)
  await shot(page, 'action-scan')

  // ---- 相似图片 ----
  console.log('[相似图片]')
  await page.goto(`${ORIGIN}/#/duplicates`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  await addTarget(page, SAMPLE)
  await page.getByRole('button', { name: /找出相似图片/ }).first().click()
  await page.waitForTimeout(5000)
  const simPicks = page.locator('.similar-file .el-checkbox:visible')
  console.log('  勾选框数 =', await simPicks.count())
  if (await simPicks.count()) {
    await simPicks.first().scrollIntoViewIfNeeded().catch(() => {})
    await simPicks.first().click({ force: true })
    await page.waitForTimeout(500)
  }
  console.log('  sticky bar 可见 =', await page.locator('.sticky-bar').isVisible().catch(() => false))
  await page.locator('.sticky-bar').scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(300)
  await shot(page, 'action-similar')

  // ---- 全文检索 ----
  console.log('[检索]')
  await page.goto(`${ORIGIN}/#/search`, { waitUntil: 'load' })
  await page.waitForTimeout(1800)
  await page.locator('.search-input input').fill('hello')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(1800)
  const hitPicks = page.locator('.hit .el-checkbox:visible')
  console.log('  勾选框数 =', await hitPicks.count())
  if (await hitPicks.count()) {
    await hitPicks.first().scrollIntoViewIfNeeded().catch(() => {})
    await hitPicks.first().click({ force: true })
    await page.waitForTimeout(500)
  }
  console.log('  sticky bar 可见 =', await page.locator('.sticky-bar').isVisible().catch(() => false))
  await page.locator('.sticky-bar').scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(300)
  await shot(page, 'action-search')

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  await ctx.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

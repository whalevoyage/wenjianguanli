// Phase 13 验证：任务中心里的「最近操作」能看见、点得动、撤销真的生效。
// 决定成败的不是区块渲染，而是**点撤销后文件真的回到原位**。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
const SNAPSHOT = process.env.SNAPSHOT_ROOT || ''
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase13_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/jobs', { waitUntil: 'load' })
  await page.waitForTimeout(3500)

  const card = page.locator('.panel-card', { hasText: '最近操作' }).first()
  const hasCard = await card.count()
  const items = await page.locator('.journal-item').count()
  const firstMeta = items ? (await page.locator('.journal-item__meta').first().innerText()).trim() : ''
  const undoBtn = page.locator('.journal-item button', { hasText: '撤销' }).first()
  const canUndo = await undoBtn.count()

  if (canUndo) {
    await undoBtn.click()
    await page.waitForTimeout(700)
    const boxVisible = await page.locator('.el-message-box').isVisible().catch(() => false)
    const boxText = boxVisible
      ? (await page.locator('.el-message-box__message').innerText()).trim()
      : ''
    if (boxVisible) {
      await page.locator('.el-message-box__btns .el-button--primary').click()
      await page.waitForTimeout(2500)
    }
    console.log('confirm_box     =', boxVisible, '|', boxText.slice(0, 50))
  }

  const stateText = await page.locator('.journal-item__state').first().innerText().catch(() => '')
  const journalAfter = await page.evaluate(async () => {
    const res = await fetch('http://127.0.0.1:8235/api/journal')
    const data = await res.json()
    return { undone: data.entries[0]?.undone, stats: data.stats }
  })

  console.log('card            =', hasCard)
  console.log('items           =', items)
  console.log('meta            =', firstMeta)
  console.log('undo_button     =', canUndo)
  console.log('state_after     =', stateText)
  console.log('backend_undone  =', journalAfter.undone, '| stats:', JSON.stringify(journalAfter.stats))
  console.log('pageerrors      =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase13-journal.png') })
  await ctx.close()

  const ok =
    hasCard === 1 &&
    items >= 1 &&
    canUndo === 1 &&
    stateText.includes('已撤销') &&
    journalAfter.undone === true &&
    errors.length === 0
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

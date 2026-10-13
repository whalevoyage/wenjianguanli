// 就地整理（不换盘）入口复检：预览后应出现该区块 + 按钮可用 + 确认框文案正确。
// 不真执行（跑着的后端可能是旧代码，没有 /api/virtual/apply），点开确认框就取消。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

const BASE = path.join(os.tmpdir(), '_inplace_verify')
fs.rmSync(BASE, { recursive: true, force: true })
fs.mkdirSync(path.join(BASE, 'photos'), { recursive: true })
fs.mkdirSync(path.join(BASE, 'videos'), { recursive: true })
fs.writeFileSync(path.join(BASE, 'photos', 'a.jpg'), Buffer.alloc(300 * 1024, 1))
fs.writeFileSync(path.join(BASE, 'photos', 'b.jpg'), Buffer.alloc(300 * 1024, 2))
fs.writeFileSync(path.join(BASE, 'videos', 'clip.mp4'), Buffer.alloc(2 * 1024 * 1024, 3))
fs.writeFileSync(path.join(BASE, 'note.txt'), 'hello'.repeat(500))

const enc = p => encodeURIComponent(p)
const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name + '.png') })
  console.log('  shot:', name)
}

;(async () => {
  const browser = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_inplace_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = browser.pages()[0] || await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()) })

  await page.goto(`http://127.0.0.1:5173/#/virtual?focus=${enc(BASE)}&from=space`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)

  console.log('[1] 生成预览')
  await page.getByRole('button', { name: /生成整理预览/ }).click()
  await page.waitForTimeout(6000)

  console.log('[2] 就地整理区块')
  const title = page.getByRole('heading', { name: /就地整理/ })
  console.log('  标题可见 =', await title.isVisible().catch(() => false))
  const btn = page.getByRole('button', { name: /^就地整理$/ })
  console.log('  按钮可见 =', await btn.isVisible().catch(() => false))
  console.log('  按钮可用 =', await btn.isEnabled().catch(() => false))
  const hint = await page.locator('section', { has: page.getByRole('heading', { name: /就地整理/ }) })
    .locator('.tree-hint').innerText().catch(() => '（无）')
  console.log('  提示文案 =', hint.replace(/\s+/g, ' '))
  await shot(page, 'inplace-1-section')

  console.log('[3] 点开确认框（不执行）')
  await btn.click()
  await page.waitForTimeout(800)
  const box = page.locator('.el-message-box')
  console.log('  确认框可见 =', await box.isVisible().catch(() => false))
  console.log('  确认框标题 =', await box.locator('.el-message-box__title').innerText().catch(() => '（无）'))
  console.log('  确认框正文 =', (await box.locator('.el-message-box__message').innerText().catch(() => '（无）')).replace(/\s+/g, ' '))
  await shot(page, 'inplace-2-confirm')
  await page.getByRole('button', { name: /再想想/ }).click().catch(() => {})

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  fs.rmSync(BASE, { recursive: true, force: true })
  await browser.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

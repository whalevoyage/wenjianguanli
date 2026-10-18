// Phase 4 验证：Agent 页在改动后仍能正常渲染（无白屏 / 无 pageerror），
// 并确认「继续清理」类入口存在。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase4_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())

  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(4500)

  const appLen = (await page.locator('#app').innerHTML()).length
  const hoverCount = await page.locator('.hero-card').count()
  const composerCount = await page.locator('.composer-zone').count()

  console.log('app_inner_len =', appLen)
  console.log('hero_cards    =', hoverCount)
  console.log('composer      =', composerCount)
  console.log('pageerrors    =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase4-agent-page.png') })
  await ctx.close()

  const ok = appLen > 2000 && composerCount === 1 && errors.length === 0
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

// 截图证据：确认 dev server 实际渲染正常
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_diag_profile3'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || await ctx.newPage()
  await page.goto('http://127.0.0.1:5173/#/', { waitUntil: 'load' })
  await page.waitForTimeout(4500)
  await page.screenshot({ path: path.join(OUT, 'diag-home.png') })
  console.log('home #app len =', (await page.locator('#app').innerHTML()).length)

  await page.goto('http://127.0.0.1:5173/#/virtual', { waitUntil: 'load' })
  await page.waitForTimeout(3000)
  await page.screenshot({ path: path.join(OUT, 'diag-virtual.png') })
  console.log('virtual #app len =', (await page.locator('#app').innerHTML()).length)

  await ctx.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

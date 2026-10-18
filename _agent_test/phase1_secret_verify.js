// Phase 1 证据：Agent 设置弹层在「存量明文」状态下必须显示警告
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase1_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())

  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(4500)

  // 打开「切换模型」弹层（API Key 段在同一个 panel 里）
  await page.locator('.model-trigger').click()
  await page.waitForTimeout(900)

  const warn = page.locator('.panel__warn')
  const count = await warn.count()
  const text = count ? (await warn.first().innerText()).trim() : '(未渲染)'
  const mask = await page.locator('.panel__mask').innerText().catch(() => '(无掩码)')

  console.log('warn_count =', count)
  console.log('warn_text  =', text)
  console.log('mask       =', mask)
  console.log('pageerrors =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase1-secret-warning.png') })
  await ctx.close()

  const ok = count === 1 && text.includes('明文') && !mask.includes('legacy-plaintext-demo')
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

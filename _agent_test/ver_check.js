const { chromium } = require('playwright-core')
const os = require('os')
const path = require('path')

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_ver_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  })
  const page = ctx.pages()[0] || (await ctx.newPage())
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/', { waitUntil: 'load' })
  await page.waitForTimeout(4000)

  const ver = page.locator('.foot-version')
  const count = await ver.count()
  const text = count ? (await ver.innerText()).trim() : ''

  console.log('foot_version_count =', count)
  console.log('foot_version_text  =', text)
  console.log('pageerrors         =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(__dirname, 'shots', 'version-in-sidebar.png') })
  await ctx.close()
  console.log('RESULT:', count === 1 && text === 'v2.1.0' && errors.length === 0 ? 'PASS' : 'FAIL')
})()

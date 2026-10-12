// 相似图片「旋转容忍」开关：实机验证渲染与默认态（只读，不跑任务）
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })
const ORIGIN = process.env.ORIGIN || 'http://127.0.0.1:5173'

const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name + '.png') })
  console.log('  shot:', name)
}

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_simrot_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = ctx.pages()[0] || await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))

  await page.goto(`${ORIGIN}/#/duplicates`, { waitUntil: 'load' })
  await page.waitForTimeout(2000)

  // 找动作行里的开关（顺带确认阈值下拉还在同一行）
  const cb = page.locator('.similar-rotations')
  console.log('[1] 动作行')
  console.log('  阈值下拉可见 =', await page.locator('.similar-threshold').isVisible().catch(() => false))
  console.log('  旋转容忍开关可见 =', await cb.isVisible().catch(() => false))
  console.log('  开关文案 =', (await cb.innerText().catch(() => '（无）')).trim())
  const box = cb.locator('input[type=checkbox]')
  console.log('  默认是否勾选 =', await box.isChecked().catch(() => '?'))
  await shot(page, 'simrot-0-default')

  console.log('[2] 点击开关')
  await cb.click()
  await page.waitForTimeout(400)
  console.log('  点击后是否勾选 =', await box.isChecked().catch(() => '?'))
  await shot(page, 'simrot-1-checked')

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  await ctx.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

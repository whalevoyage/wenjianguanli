// 执行类多来源（迁移 / 备份）+ 检索「自动增量更新」开关：实机渲染验证（只读）
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
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_multiexec_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = ctx.pages()[0] || await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))

  for (const route of ['migrate', 'backup']) {
    console.log(`[${route}]`)
    await page.goto(`${ORIGIN}/#/${route}`, { waitUntil: 'load' })
    await page.waitForTimeout(1800)
    console.log('  多源选择器可见 =', await page.locator('.multi-add').isVisible().catch(() => false))
    console.log('  「添加来源」按钮 =', await page.getByRole('button', { name: '添加来源' }).isVisible().catch(() => false))
    await shot(page, `multiexec-${route}`)
  }

  console.log('[search]')
  await page.goto(`${ORIGIN}/#/search`, { waitUntil: 'load' })
  await page.waitForTimeout(2000)
  const row = page.locator('.auto-refresh-row')
  console.log('  自动更新开关行可见 =', await row.isVisible().catch(() => false))
  if (await row.isVisible().catch(() => false)) {
    console.log('  开关文案 =', (await row.innerText()).replace(/\n/g, ' '))
    console.log('  默认勾选 =', await row.locator('input[type=checkbox]').isChecked().catch(() => '?'))
    // Element Plus 把原生 input 隐藏了，点它的 label（.el-checkbox）
    await row.locator('.el-checkbox').click()
    await page.waitForTimeout(400)
    console.log('  点击后勾选 =', await row.locator('input[type=checkbox]').isChecked().catch(() => '?'))
  }
  await shot(page, 'multiexec-search')

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  await ctx.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

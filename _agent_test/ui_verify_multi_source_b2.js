// B8 第二批：全文检索 / 项目雷达 的多来源选择器是否就位（只读，不跑任务）
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
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_msb2_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = ctx.pages()[0] || await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))

  for (const [route, name] of [['search', '多来源-检索'], ['projects', '多来源-项目雷达']]) {
    console.log(`[${route}]`)
    await page.goto(`${ORIGIN}/#/${route}`, { waitUntil: 'load' })
    await page.waitForTimeout(1800)
    const multi = page.locator('.multi-add')
    console.log('  多源选择器可见 =', await multi.isVisible().catch(() => false))
    console.log('  「添加来源」按钮 =', await page.getByRole('button', { name: '添加来源' }).isVisible().catch(() => false))
    await shot(page, `msb2-${route}`)
  }

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  await ctx.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

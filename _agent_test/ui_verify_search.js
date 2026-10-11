// B1 全文检索：实机验证「建索引 → 搜索 → 命中高亮 → 接力出口 → 移除索引」
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

// 默认打 dev server；验证**打包版**时传 ORIGIN=http://127.0.0.1:<打包版端口>
// （打包版同源托管前端与 API，所以直接当普通站点访问即可）。
const ORIGIN = process.env.ORIGIN || 'http://127.0.0.1:5173'

// ★ 样本树**不能**建在数据目录下：数据目录是自保护目录，
//   iter_entries 会剪掉它下面的子目录（根层文件不受影响）→ 子目录整层消失。
const BASE = path.join(os.tmpdir(), '_b1_ui_tree')
fs.rmSync(BASE, { recursive: true, force: true })
fs.mkdirSync(path.join(BASE, 'docs'), { recursive: true })
fs.writeFileSync(path.join(BASE, '玉米表型测量报告.md'),
  '这是一份关于玉米表型测量的说明文档，包含 budget 与 report。', { encoding: 'utf8' })
fs.writeFileSync(path.join(BASE, 'docs', 'notes.txt'),
  '玉米 表型 数据 汇总 report budget', { encoding: 'utf8' })
fs.writeFileSync(path.join(BASE, 'docs', 'blob.bin'), Buffer.alloc(4096, 0))

const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name + '.png') })
  console.log('  shot:', name)
}

;(async () => {
  const browser = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_b1_ui_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = browser.pages()[0] || await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()) })

  await page.goto(`${ORIGIN}/#/search`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)

  console.log('[0] 页面加载')
  console.log('  标题 =', await page.locator('.panel-title').first().innerText().catch(() => '（无）'))
  console.log('  建索引按钮禁用的原因 =', await page.locator('.row-actions .tree-hint').first().innerText().catch(() => '（无）'))
  await shot(page, 'b1-0-empty')

  // ---- 1) 建索引 ----
  console.log('[1] 填目录 → 建立索引')
  await page.locator('.target-picker .el-input__inner').first().fill(BASE)
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: /更新索引/ }).first().click()
  await page.waitForSelector('.root-item', { timeout: 60000 })
  await page.waitForTimeout(800)
  console.log('  已索引目录 =', (await page.locator('.root-item__meta').allInnerTexts()).join(' | '))
  console.log('  索引摘要 =', (await page.locator('.index-summary').innerText().catch(() => '（无）')).replace(/\n/g, ' '))
  // 陈旧提示：本次跑时后端带着 FILEPILOT_SEARCH_STALE_DAYS=0，所以刚建的索引也算"过期"
  console.log('  陈旧提示 =', (await page.getByText(/可能已经过期/).first().innerText().catch(() => '（无）')))
  await shot(page, 'b1-1-indexed')

  // ---- 2) 中文检索 ----
  console.log('[2] 搜「表型」')
  await page.locator('.search-input input').fill('表型')
  await page.keyboard.press('Enter')
  await page.waitForSelector('.hit', { timeout: 20000 })
  await page.waitForTimeout(500)
  console.log('  命中数 =', await page.locator('.hit').count())
  const names = await page.locator('.hit__name').allInnerTexts()
  console.log('  命中文件 =', names.join(' | '))
  console.log('  命中片段 =', (await page.locator('.hit__snippet').first().innerText().catch(() => '（无）')))
  console.log('  高亮片段数（.is-hit）=', await page.locator('.is-hit').count())
  console.log('  副标题 =', await page.locator('.panel-subtitle').last().innerText().catch(() => '（无）'))
  console.log('  「在资源管理器打开」按钮数 =', await page.locator('.hit__reveal').count())
  await page.locator('.hit').first().scrollIntoViewIfNeeded()
  await page.waitForTimeout(400)
  await shot(page, 'b1-2-hit')

  // ---- 3) 英文 + 无结果 ----
  console.log('[3] 搜「report」/「zzzz」')
  await page.locator('.search-input input').fill('report')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(1200)
  console.log('  report 命中 =', await page.locator('.hit').count())
  await page.locator('.search-input input').fill('zzzzzz')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(1200)
  console.log('  zzzzzz 提示 =', await page.locator('.row-actions .tree-hint').last().innerText().catch(() => '（无）'))
  await shot(page, 'b1-3-empty')

  // ---- 4) 接力出口 ----
  await page.locator('.search-input input').fill('表型')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(1200)
  console.log('[4] 接力出口')
  console.log('  .next-steps 可见 =', await page.locator('.next-steps').isVisible().catch(() => false))
  console.log('  .next-steps 文案 =', (await page.locator('.next-steps').innerText().catch(() => '（无）')).replace(/\n/g, ' '))

  // ---- 5) 移除索引（顺手清理，别在用户真实库里留样本根）----
  console.log('[5] 移除索引')
  await page.getByRole('button', { name: '移除索引' }).first().click()
  await page.waitForTimeout(1500)
  console.log('  剩余目录条目 =', await page.locator('.root-item').count())

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  fs.rmSync(BASE, { recursive: true, force: true })
  await browser.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

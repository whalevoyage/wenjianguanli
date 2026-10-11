// v36「结果 → 执行」契约复检：步骤指示 / HandoffNote / NextSteps / planHint 三态
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

const BASE = path.join(os.tmpdir(), '_v36_verify')
const PROJ = path.join(BASE, 'projects')
const MEDIA = path.join(BASE, 'media')
fs.rmSync(BASE, { recursive: true, force: true })
fs.mkdirSync(path.join(PROJ, 'demo-app', '.git'), { recursive: true })
fs.mkdirSync(path.join(MEDIA, 'photos'), { recursive: true })
fs.mkdirSync(path.join(MEDIA, 'videos'), { recursive: true })
fs.writeFileSync(path.join(PROJ, 'demo-app', 'package.json'), '{"name":"demo"}')
fs.writeFileSync(path.join(PROJ, 'demo-app', 'src.js'), 'x'.repeat(4000))
fs.writeFileSync(path.join(MEDIA, 'photos', 'a.jpg'), Buffer.alloc(300 * 1024, 1))
fs.writeFileSync(path.join(MEDIA, 'photos', 'b.jpg'), Buffer.alloc(300 * 1024, 2))
fs.writeFileSync(path.join(MEDIA, 'videos', 'clip.mp4'), Buffer.alloc(2 * 1024 * 1024, 3))
fs.writeFileSync(path.join(MEDIA, 'note.txt'), 'hello'.repeat(500))

const enc = p => encodeURIComponent(p)
const shot = async (page, name) => {
  await page.screenshot({ path: path.join(OUT, name + '.png') })
  console.log('  shot:', name)
}

;(async () => {
  const browser = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_v36_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1440, height: 900 },
  })
  const page = browser.pages()[0] || await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()) })

  // ---- A) 迁移页：接力带目录进来 → NextSteps 应因 source 有值而出现 ----
  console.log('[A] /#/migrate?focus=  → NextSteps')
  await page.goto(`http://127.0.0.1:5173/#/migrate?focus=${enc(MEDIA)}&from=virtual`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  console.log('  .next-steps 可见 =', await page.locator('.next-steps').isVisible().catch(() => false))
  console.log('  .next-steps 文案 =', (await page.locator('.next-steps').innerText().catch(() => '（无）')).replace(/\n/g, ' '))
  console.log('  接力框已填 =', await page.locator('.target-picker .el-input__inner').first().inputValue().catch(() => '（无）'))
  await shot(page, 'v36-A-migrate-nextsteps')

  // ---- B) 整理预览：HandoffNote + 三步指示 + planHint 三态 ----
  console.log('[B] /#/virtual?focus=  → HandoffNote / steps / planHint')
  await page.goto(`http://127.0.0.1:5173/#/virtual?focus=${enc(MEDIA)}&from=space`, { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  console.log('  .handoff 可见 =', await page.locator('.handoff').isVisible().catch(() => false))
  const stepItems = await page.locator('.steps__item').allInnerTexts()
  console.log('  步骤数 =', stepItems.length, '→', stepItems.map(t => t.replace(/\s+/g, '')).join(' | '))
  console.log('  当前步 =', await page.locator('.steps__item.is-current .steps__label').innerText().catch(() => '（无）'))
  console.log('  planHint(未预览) =', (await page.locator('.row-actions .tree-hint').innerText().catch(() => '（无）')))
  await shot(page, 'v36-B1-virtual-handoff')

  // 生成预览
  await page.getByRole('button', { name: /生成整理预览/ }).click()
  await page.waitForTimeout(6000)
  const hintAfter = await page.locator('.row-actions .tree-hint').innerText().catch(() => '（无）')
  console.log('  planHint(未选目标) =', hintAfter.replace(/\n/g, ' '))
  console.log('  .next-steps 可见 =', await page.locator('.next-steps').isVisible().catch(() => false))
  await shot(page, 'v36-B2-virtual-preview')

  // 填目标根目录 → planHint 应换成"清单里有 N 个文件"
  const targetInput = page.locator('.target-picker').nth(1).locator('.el-input__inner')
  await targetInput.fill(path.join(os.tmpdir(), '_v36_verify_out'))
  await page.waitForTimeout(600)
  console.log('  planHint(已选目标) =', (await page.locator('.row-actions .tree-hint').innerText().catch(() => '（无）')).replace(/\n/g, ' '))
  await shot(page, 'v36-B3-virtual-targetset')

  // ---- C) 项目雷达：扫描后 NextSteps 应出现 ----
  console.log('[C] /#/projects  → 扫描后 NextSteps')
  await page.goto('http://127.0.0.1:5173/#/projects', { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  await page.locator('.target-picker .el-input__inner').first().fill(PROJ)
  await page.waitForTimeout(400)
  await page.getByRole('button', { name: /扫描项目/ }).click()
  await page.waitForTimeout(8000)
  console.log('  .next-steps 可见 =', await page.locator('.next-steps').isVisible().catch(() => false))
  console.log('  .next-steps 文案 =', (await page.locator('.next-steps').innerText().catch(() => '（无）')).replace(/\n/g, ' '))
  await shot(page, 'v36-C-projects-nextsteps')

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  fs.rmSync(BASE, { recursive: true, force: true })
  await browser.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

// 复检界面：迁移计划树（含搬运体积措辞 + 继续执行）+ 取消任务计划卡
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

const BASE = path.join(os.tmpdir(), '_agent_ui_mig')
const SRC = path.join(BASE, 'src')
const DST = path.join(BASE, 'dst')
fs.rmSync(BASE, { recursive: true, force: true })
fs.mkdirSync(path.join(SRC, 'docs'), { recursive: true })
fs.mkdirSync(DST, { recursive: true })
fs.writeFileSync(path.join(SRC, 'big1.bin'), Buffer.alloc(2 * 1024 * 1024, 1))
fs.writeFileSync(path.join(SRC, 'big2.bin'), Buffer.alloc(1024 * 1024, 2))
fs.writeFileSync(path.join(SRC, 'note.md'), 'hello'.repeat(200))

const shot = async (page, name) => {
  await page.evaluate(() => {
    const el = document.querySelector('.chat-scroll')
    if (el) el.scrollTop = el.scrollHeight
  })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, name + '.png') })
  console.log('shot:', name)
}

;(async () => {
  const browser = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_agent_verify_profile'), {
    executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    args: ['--disable-web-security'],
    viewport: { width: 1440, height: 900 },
  })
  const page = browser.pages()[0] || await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push('pageerror: ' + e.message))

  await page.goto('http://127.0.0.1:5174/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(1500)
  await page.locator('.task-new').click()
  await page.waitForTimeout(700)
  const input = page.locator('.composer-zone textarea').first()

  // ---- 1) 迁移计划树 ----
  await input.fill(`把 ${SRC} 搬到 ${DST}`)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(3000)
  await shot(page, '20-migrate-draft')
  console.log('草稿步骤数 =', await page.locator('.plan-draft .plan__step').count())

  await page.locator('.plan-draft__actions button').first().click()
  await page.waitForSelector('.plan__step.is-waiting', { timeout: 60000 })
  await page.waitForTimeout(800)
  await shot(page, '21-migrate-confirm')
  const numbers = await page.locator('.job-card.is-plan .plan__number').allInnerTexts()
  console.log('预计数字 =', numbers.map(t => t.replace(/\s+/g, ' ')).join(' | '))
  const btns = await page.locator('.job-card.is-plan .plan__actions button').allInnerTexts()
  console.log('决策按钮 =', btns.join(' / '))

  // 真的执行（沙箱在 %TEMP%，安全）
  await page.locator('.job-card.is-plan .plan__actions button').first().click()
  await page.waitForTimeout(700)
  await page.locator('.el-message-box__btns button').last().click()
  await page.waitForTimeout(6000)
  await shot(page, '22-migrate-done')
  console.log('执行后 src =', fs.readdirSync(SRC).join(','))
  console.log('执行后 dst =', fs.readdirSync(DST).join(','))

  // ---- 2) 取消任务：计划卡按钮措辞 + 点下去的落点 ----
  await page.locator('.task-new').click()
  await page.waitForTimeout(600)
  await input.fill('取消现在跑的任务')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2500)
  await shot(page, '23-cancel-plan')
  const cancelBtn = await page.locator('.plan-actions button').first().innerText().catch(() => '（无）')
  console.log('取消计划卡按钮 =', cancelBtn.replace(/\n/g, ' '))
  await page.locator('.plan-actions button').first().click()
  await page.waitForTimeout(1500)
  await shot(page, '24-cancel-result')
  const toast = await page.locator('.el-message').last().innerText().catch(() => '（无提示）')
  console.log('点击后提示 =', toast.replace(/\n/g, ' '))

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  fs.rmSync(BASE, { recursive: true, force: true })
  await browser.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

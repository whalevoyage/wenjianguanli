// 补做部分的自查：兜底追问选项 + 偏好面板新增两块
const { chromium } = require('playwright-core')
const fs = require('fs')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

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
  const browser = await chromium.launchPersistentContext(path.join(require('os').tmpdir(), '_agent_verify_profile'), {
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

  // 1) 认不出意图、也没有模型 → 应该追问 + 给选项，而不是「没看懂」
  await input.fill('那个东西你看着办')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2500)
  await shot(page, '10-fallback-clarify')
  const fallbackButtons = await page.locator('.agent-clarify__options button').count()
  console.log('兜底追问按钮数 =', fallbackButtons)

  // 2) 带路径但没动作 → 选项应带上真实路径
  await input.fill('把 D:\\FilePilotLite 那个啥弄一下')
  await page.keyboard.press('Enter')
  await page.waitForTimeout(2500)
  await shot(page, '11-fallback-with-path')
  const labels = await page.locator('.agent-clarify__options button').last().innerText().catch(() => '')
  console.log('末条选项 =', labels.replace(/\n/g, ' / '))

  // 3) 点一个选项，应该能直接跑起来
  const first = page.locator('.agent-clarify__options button').first()
  if (await first.count()) {
    console.log('首个选项 =', (await first.innerText()).replace(/\n/g, ' / '))
  }

  // 4) 偏好面板：常用路径 / 常用清理策略
  await page.locator('.agent-head__icon[title="偏好记忆"]').click()
  await page.waitForTimeout(800)
  await shot(page, '12-prefs-full')
  const sections = await page.locator('.prefs__label').allInnerTexts()
  console.log('偏好面板分区 =', sections.join(' | '))
  const chips = await page.locator('.prefs__chips button, .prefs__list li').count()
  console.log('可用条目数 =', chips)

  console.log('\n=== 页面错误 ===')
  console.log(errors.length ? errors.join('\n') : '（无）')
  await browser.close()
})().catch(e => { console.error('FAILED:', e); process.exit(1) })

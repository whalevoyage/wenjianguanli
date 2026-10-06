// Agent 页真实渲染验证：空态 → 计划树 → 开始分析 → 等待确认 → 执行
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

const SANDBOX = path.join(os.tmpdir(), '_agent_sandbox')
fs.mkdirSync(path.join(SANDBOX, 'cache'), { recursive: true })
for (const name of ['a.tmp', 'b.tmp', 'c.log', 'd.bak']) {
  fs.writeFileSync(path.join(SANDBOX, name), 'junk'.repeat(300))
}

const shot = async (page, name) => {
  await page.evaluate(() => {
    const el = document.querySelector('.chat-scroll')
    if (el) el.scrollTop = el.scrollHeight
  })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false })
  console.log('shot:', name)
}

;(async () => {
  // 验证用的前端跑在 5174，而后端 CORS 白名单只有 5173 —— 这里用独立 profile
  // 关掉 web security 来绕开跨域，避免为了截图去改项目的 CORS 配置。
  const browser = await chromium.launchPersistentContext(
    path.join(os.tmpdir(), '_agent_verify_profile'),
    {
      executablePath: 'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
      args: ['--disable-web-security'],
      viewport: { width: 1440, height: 900 },
    },
  )
  const page = browser.pages()[0] || await browser.newPage()
  const errors = []
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()) })
  page.on('pageerror', err => errors.push(`pageerror: ${err.message}`))
  const badResponses = []
  page.on('response', r => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()) })

  await page.goto('http://127.0.0.1:5174/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(1500)

  // 新开一条任务，别把验证内容写进用户已有的对话记忆
  const newTask = page.locator('.task-new')
  if (await newTask.count()) {
    await newTask.click()
    await page.waitForTimeout(800)
  }
  await shot(page, '01-empty')

  // 状态条 + 偏好记忆面板
  const gear = page.locator('.agent-head__icon[title="偏好记忆"]')
  if (await gear.count()) {
    await gear.click()
    await page.waitForTimeout(600)
    await shot(page, '02-prefs')
    if (await page.locator('.prefs__tag').count()) {
      await page.locator('.prefs__tag').first().click()
      await page.waitForTimeout(800)
      await shot(page, '03-prefs-tag-on')
    }
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  } else {
    console.log('WARN: 找不到偏好记忆入口')
  }

  // 发一条「腾出空间」——本地规则应拆成多步
  const input = page.locator('.composer-zone textarea, .composer-zone input').first()
  await input.click()
  await input.fill(`把 ${SANDBOX} 腾出空间，不要动项目文件`)
  await page.keyboard.press('Enter')
  await page.waitForTimeout(3000)
  await shot(page, '04-plan-tree')

  const steps = await page.locator('.plan-draft .plan__step').count()
  console.log('计划树步数 =', steps)

  // 开始分析
  const startBtn = page.locator('.plan-draft__actions button').first()
  if (await startBtn.count()) {
    await startBtn.click()
    await page.waitForTimeout(400)
    await shot(page, '05-analyzing')
    // 等挂起到等待确认
    await page.waitForSelector('.plan__step.is-waiting', { timeout: 60000 })
    await page.waitForTimeout(800)
    await shot(page, '06-waiting-confirm')
    const approve = page.locator('.job-card.is-plan .plan__actions button').first()
    console.log('确认按钮：', await approve.innerText().catch(() => '无'))
  } else {
    console.log('WARN: 找不到「开始分析」按钮')
  }

  // 确认执行（弹窗里点确认）
  const confirmBtn = page.locator('.job-card.is-plan .plan__actions button').first()
  if (await confirmBtn.count()) {
    await confirmBtn.click()
    await page.waitForTimeout(700)
    await shot(page, '07-confirm-dialog')
    const ok = page.locator('.el-message-box__btns button').last()
    if (await ok.count()) {
      await ok.click()
      await page.waitForTimeout(2500)
      await shot(page, '08-executing')
      await page.waitForTimeout(4000)
      await shot(page, '09-done')
    }
  }

  console.log('\n=== 控制台错误 ===')
  console.log(errors.length ? errors.slice(0, 12).join('\n') : '（无）')
  console.log('\n=== 4xx/5xx 请求 ===')
  console.log(badResponses.length ? badResponses.join('\n') : '（无）')
  await browser.close()
})().catch(err => {
  console.error('FAILED:', err)
  process.exit(1)
})

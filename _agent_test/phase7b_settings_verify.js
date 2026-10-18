// Phase 7 第二批验证：设置逻辑抽成 useAgentSettings 后，
// 模型弹层、API Key 面板、权限弹层都还能正常打开与生效。
// 抽取 composable 最容易犯的错是"模板引用了不存在的名字"——build 不会报，只有运行时炸。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase7b_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())

  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(3500)

  const modelTrigger = page.locator('.model-trigger')
  const modelBefore = (await modelTrigger.innerText()).trim().split('\n')[0]

  // 打开模型弹层
  await modelTrigger.click()
  await page.waitForTimeout(800)
  const modelOptions = await page.locator('.panel--model .panel__option').count()
  const hasKeySection = await page.locator('.panel__keyrow').count()

  // 切一个不同的模型
  const target = modelBefore === 'deepseek-chat' ? 'deepseek-flash' : 'deepseek-chat'
  await page.locator('.panel--model .panel__option', { hasText: target }).first().click()
  await page.waitForTimeout(1500)
  const modelAfter = (await modelTrigger.innerText()).trim().split('\n')[0]

  const backendModel = await page.evaluate(async () => {
    const res = await fetch('http://127.0.0.1:8233/api/settings/agent')
    return (await res.json()).model
  })

  // 权限弹层
  await page.locator('.perm-pill').click()
  await page.waitForTimeout(800)
  const permOptions = await page.locator('.panel .panel__option').count()
  const guards = await page.locator('.panel__guards li').count()
  await page.keyboard.press('Escape')
  await page.waitForTimeout(400)

  console.log('model_before      =', modelBefore)
  console.log('model_options     =', modelOptions)
  console.log('key_section       =', hasKeySection)
  console.log('model_after       =', modelAfter)
  console.log('backend_model     =', backendModel)
  console.log('permission_options=', permOptions)
  console.log('permission_guards =', guards)
  console.log('pageerrors        =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase7b-settings.png') })
  await ctx.close()

  const ok =
    modelOptions >= 4 &&
    hasKeySection === 1 &&
    modelAfter === target &&
    backendModel === target &&
    permOptions >= 3 &&
    guards >= 1 &&
    errors.length === 0
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

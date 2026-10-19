// Phase 12 验证：服务商选择能看见、能切换、切到自定义会出地址框和风险提示。
// 重点验证"切换会清 key"这条安全行为在 UI 上被提前告知（确认框）。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase12_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(2500)

  // 先把服务商重置回 DeepSeek —— 否则上一轮跑完留下的 custom 会让"切换"这一步无从触发
  await page.evaluate(async () => {
    await fetch('http://127.0.0.1:8234/api/settings/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'deepseek' }),
    })
  })
  await page.reload({ waitUntil: 'load' })
  await page.waitForTimeout(3000)

  // 打开模型/服务商弹层
  await page.locator('.model-trigger').click()
  await page.waitForTimeout(800)

  const panel = page.locator('.panel--model')
  const hasProviderTitle = await panel.locator('.panel__title', { hasText: '服务商' }).count()
  const selectText = (await panel.locator('.el-select').innerText()).trim()

  // 切到「自定义」
  await panel.locator('.el-select').click()
  await page.waitForTimeout(500)
  await page.locator('.el-select-dropdown__item', { hasText: '自定义' }).first().click()
  await page.waitForTimeout(700)

  // 应该先弹确认框说明「会清掉 Key」
  const confirmVisible = await page.locator('.el-message-box').isVisible().catch(() => false)
  const confirmText = confirmVisible
    ? (await page.locator('.el-message-box__message').innerText()).trim()
    : ''
  if (confirmVisible) {
    await page.locator('.el-message-box__btns .el-button--primary').click()
    await page.waitForTimeout(1500)
  }

  // 重新打开弹层（确认后弹层会收起）
  if (!(await panel.isVisible().catch(() => false))) {
    await page.locator('.model-trigger').click()
    await page.waitForTimeout(800)
  }

  const baseUrlInput = await panel.locator('input[placeholder*="网关"]').count()
  const riskWarn = await panel.locator('.panel__warn', { hasText: '可信' }).count()
  const backendProvider = await page.evaluate(async () => {
    const res = await fetch('http://127.0.0.1:8234/api/settings/agent')
    return (await res.json()).provider
  })

  console.log('provider_title  =', hasProviderTitle)
  console.log('select_text     =', selectText)
  console.log('confirm_box     =', confirmVisible, '|', confirmText.slice(0, 40))
  console.log('base_url_input  =', baseUrlInput)
  console.log('risk_warning    =', riskWarn)
  console.log('backend_provider=', backendProvider)
  console.log('pageerrors      =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase12-provider.png') })
  await ctx.close()

  const ok =
    hasProviderTitle === 1 &&
    selectText.includes('DeepSeek') &&
    confirmVisible &&
    confirmText.includes('API Key') &&
    baseUrlInput === 1 &&
    riskWarn === 1 &&
    backendProvider === 'custom' &&
    errors.length === 0
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

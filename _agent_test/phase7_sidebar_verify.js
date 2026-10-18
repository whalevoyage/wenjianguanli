// Phase 7 验证：侧栏拆成 AgentTaskPanel 后，渲染、props、emit 都要真跑通。
// 重点不是"能构建"，而是"点了有反应" —— 白屏和事件断链都不会让 build 失败。
const { chromium } = require('playwright-core')
const fs = require('fs')
const os = require('os')
const path = require('path')

const OUT = path.join(__dirname, 'shots')
fs.mkdirSync(OUT, { recursive: true })

;(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(os.tmpdir(), '_phase7_profile'), {
    executablePath:
      'C:\\Users\\faker\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe',
    viewport: { width: 1600, height: 900 },
  })
  const page = ctx.pages()[0] || (await ctx.newPage())

  const errors = []
  page.on('pageerror', e => errors.push(String(e)))

  await page.goto('http://127.0.0.1:5173/#/agent', { waitUntil: 'load' })
  await page.waitForTimeout(4000)

  const panel = page.locator('.task-panel')
  const panelCount = await panel.count()
  const visible = panelCount ? await panel.isVisible() : false
  const width = panelCount ? await panel.evaluate(el => el.getBoundingClientRect().width) : 0

  // 注意：点「新建任务」不会立刻往左栏塞一项 —— 新会话要等下次加载列表才出现（既有设计）。
  // 所以验证 emit 有没有通，看**后端有没有真的落库**，而不是看 DOM 里的项数。
  const countConversations = () => page.evaluate(async () => {
    const res = await fetch('http://127.0.0.1:8233/api/agent/conversations')
    return (await res.json()).conversations.length
  })
  const before = await countConversations()
  const itemsBefore = await page.locator('.task-item').count()

  await page.locator('.task-new').click()
  await page.waitForTimeout(2000)
  const after = await countConversations()

  // emit: toggle（收起）
  await page.locator('.task-panel__collapse').click()
  await page.waitForTimeout(600)
  const hiddenAfterToggle = !(await panel.isVisible())

  // 再展开
  const expandBtn = page.locator('.agent-head__icon')
  if (await expandBtn.count()) {
    await expandBtn.first().click()
    await page.waitForTimeout(600)
  }
  const visibleAgain = await panel.isVisible()

  console.log('panel_count      =', panelCount)
  console.log('panel_visible    =', visible)
  console.log('panel_width      =', width)
  console.log('items in sidebar =', itemsBefore)
  console.log('backend convs    =', before, '->', after)
  console.log('hidden after toggle =', hiddenAfterToggle)
  console.log('visible again    =', visibleAgain)
  console.log('pageerrors       =', errors.length ? errors : 'none')

  await page.screenshot({ path: path.join(OUT, 'phase7-sidebar.png') })
  await ctx.close()

  const ok =
    panelCount === 1 &&
    visible &&
    Math.round(width) === 248 &&
    after > before &&
    hiddenAfterToggle &&
    visibleAgain &&
    errors.length === 0
  console.log('RESULT:', ok ? 'PASS' : 'FAIL')
  process.exit(ok ? 0 : 1)
})()

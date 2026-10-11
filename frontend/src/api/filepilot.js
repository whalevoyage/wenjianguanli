import axios from 'axios'

// 开发环境：直连本地后端 8000（vite.config.js 里另配了 /api 代理，两条路都通）。
// 打包环境：走**相对路径**，跟着页面自己的来源走。
//
// 为什么打包后绝不能写死端口：发行版由后端同源托管前端，而启动器会在
// 8000 被占用时挑一个空闲端口 —— 写死了端口，界面就再也找不到后端
// （表现是「无法连接 FilePilot 后端」+ 首页一片空）。
//
// 注意别把 dev 那条也改成相对路径：那样就**依赖 vite 代理必须生效**，
// 而改 vite.config.js 需要重启开发服务器，别人的服务器没重启就整个用不了。
// 用 import.meta.env.DEV 分支则只改源码即可，HMR 立刻生效。
const DEV_API = 'http://127.0.0.1:8000/api'
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? DEV_API : '/api'),
  timeout: 0,
  headers: { 'Content-Type': 'application/json' },
})

api.interceptors.response.use(
  response => response,
  error => {
    const detail = error.response?.data?.detail
    let message = typeof detail === 'string' ? detail : ''
    if (!message && Array.isArray(detail)) message = '请求参数不完整或格式不正确'
    if (!message && error.code === 'ERR_NETWORK') message = '无法连接 FilePilot 后端，请确认本地服务已启动'
    if (!message && ['ECONNABORTED', 'ETIMEDOUT'].includes(error.code)) message = '请求超时，请稍后重试'
    if (!message) message = '请求失败，请检查后端服务后重试'
    return Promise.reject(new Error(message))
  },
)

const unwrap = async promise => (await promise).data

/* ---------------- 基础 ---------------- */
export const healthCheck = () => unwrap(api.get('/health'))
export const getConfig = () => unwrap(api.get('/config'))
export const getOverview = () => unwrap(api.get('/overview'))

/* ---------------- 任务 ---------------- */
export const listJobs = (limit = 50) => unwrap(api.get('/jobs', { params: { limit } }))
export const getJob = jobId => unwrap(api.get(`/jobs/${jobId}`))

/** 累计腾出多少空间（首页 / 日志页的正反馈） */
export const getReclaimedStats = () => unwrap(api.get('/stats/reclaimed'))

/** 找画面相似的图片（只读；结果里 available=false 表示缺 Pillow 依赖） */
export const startSimilarScan = payload => unwrap(api.post('/similar/scan', payload))

/** 让模型解释一个待清理项是什么 —— ⚠️ 会联网花 token，只在用户主动点时才调 */
export const explainItem = payload => unwrap(api.post('/agent/explain', payload))
/* 取消任务时带上调用栈：后端会把它写进审计日志，
   事后一眼就能看出这次取消是「用户点了按钮」还是「某处代码自动调的」。 */
export function cancelJob(jobId, reason = '') {
  const frames = (new Error().stack || '')
    .split('\n')
    .slice(2, 5)
    .map(line => line.trim().replace(/^at\s+/, ''))
    .join(' ← ')
  return unwrap(api.post(`/jobs/${jobId}/cancel`, { reason: reason ? `${reason} @ ${frames}` : frames }))
}
export const rollbackJob = jobId => unwrap(api.post(`/jobs/${jobId}/rollback`))

export function subscribeJob(jobId, onUpdate) {
  const base = api.defaults.baseURL
  const source = new EventSource(`${base}/jobs/${jobId}/stream`)
  source.onmessage = event => {
    try {
      onUpdate(JSON.parse(event.data))
    } catch {
      /* 忽略无法解析的心跳帧 */
    }
  }
  source.onerror = () => {
    source.close()
    onUpdate(null)
  }
  return () => source.close()
}

/* ---------------- 分析 ---------------- */
export const startScan = payload => unwrap(api.post('/scan', payload))
export const startTree = payload => unwrap(api.post('/tree', payload))
export const startDuplicateScan = payload => unwrap(api.post('/duplicates/scan', payload))
export const startJunkScan = payload => unwrap(api.post('/junk/scan', payload))
export const startVirtual = payload => unwrap(api.post('/virtual/organize', payload))
export const exportVirtual = payload => unwrap(api.post('/virtual/export', payload))
export const startProjectScan = payload => unwrap(api.post('/projects/scan', payload))

/* ---------------- 执行 ---------------- */
export const startMigrate = payload => unwrap(api.post('/migrate', payload))
export const startClean = payload => unwrap(api.post('/clean', payload))
export const startBackup = payload => unwrap(api.post('/backup', payload))

/* ---------------- 全文检索（B1）---------------- */
// 建索引是 Job（耗时长、要进度、可中断）；检索是即时 GET —— 敲完就要出结果，
// 套一层 Job + SSE 只会平白加延迟。
export const startSearchIndex = payload => unwrap(api.post('/search/index', payload))
export const searchFiles = (keyword, limit = 0) =>
  unwrap(api.get('/search', { params: { q: keyword, limit } }))
export const getSearchStatus = () => unwrap(api.get('/search/status'))
export const removeSearchRoot = path => unwrap(api.post('/search/remove', { path }))
export const revealFile = path => unwrap(api.post('/search/reveal', { path }))

/* ---------------- 回收站 ---------------- */
export const getTrash = () => unwrap(api.get('/trash'))
export const restoreTrash = ids => unwrap(api.post('/trash/restore', { ids }))
export const purgeTrash = ids => unwrap(api.post('/trash/purge', { ids }))

/* ---------------- 日志 ---------------- */
export const getLogs = () => unwrap(api.get('/logs'))
export const exportLogs = () => unwrap(api.post('/logs/export'))

/* ---------------- Agent ---------------- */
export const getAgentStatus = () => unwrap(api.get('/agent/status'))
export const agentChat = (message, context = {}) => unwrap(api.post('/agent/chat', { message, context }))

/** 读取某个会话的上下文记忆（用于重开页面后恢复对话）。 */
export const getAgentMemory = conversationId =>
  unwrap(api.get(`/agent/memory/${encodeURIComponent(conversationId)}`))

/** 清空某个会话的上下文记忆（删除历史会话时调用）。 */
export const clearAgentMemory = conversationId =>
  unwrap(api.delete(`/agent/memory/${encodeURIComponent(conversationId)}`))

/** 历史会话列表（标题=首条用户消息，按最近活跃倒序）。 */
export const listAgentConversations = () => unwrap(api.get('/agent/conversations'))

/** 重命名会话；传空标题则退回按首条消息自动生成。 */
export const renameAgentConversation = (conversationId, title) =>
  unwrap(api.post(`/agent/conversations/${encodeURIComponent(conversationId)}/title`, { title }))

/**
 * 流式对话：逐块回调，返回一个「停止」函数。
 *
 *   const stop = agentChatStream(msg, ctx, { onDelta, onPhase, onDone, onError, onClose })
 *
 * 用 fetch + ReadableStream 而不是 EventSource——EventSource 只能发 GET，
 * 装不下 message 与 context 这两个入参。
 */
export function agentChatStream(message, context = {}, handlers = {}) {
  const controller = new AbortController()
  const baseURL = api.defaults.baseURL

  const run = async () => {
    try {
      const response = await fetch(`${baseURL}/agent/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, context }),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) {
        throw new Error(response.status === 404 ? '后端不支持流式对话，请重启本地服务' : `对话流连接失败（${response.status}）`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const frames = buffer.split('\n\n')
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const line = frame.split('\n').find(item => item.startsWith('data:'))
          if (!line) continue
          let event
          try {
            event = JSON.parse(line.slice(5).trim())
          } catch {
            continue
          }
          if (event.type === 'delta') handlers.onDelta?.(event.text)
          else if (event.type === 'phase') handlers.onPhase?.(event.text)
          else if (event.type === 'done') handlers.onDone?.(event.payload)
          else if (event.type === 'error') handlers.onError?.(event.message)
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError') handlers.onError?.(error.message)
    } finally {
      handlers.onClose?.()
    }
  }

  run()
  return () => controller.abort()
}

export const getRecipes = () => unwrap(api.get('/recipes'))
export const saveRecipe = payload => unwrap(api.post('/recipes', payload))
export const deleteRecipe = index => unwrap(api.delete(`/recipes/${index}`))

/* ---------------- Agent 配置 ---------------- */
export const getAgentSettings = () => unwrap(api.get('/settings/agent'))
export const saveAgentSettings = payload => unwrap(api.post('/settings/agent', payload))

/* ---------------- Agent 工具层 / 状态 / 偏好 ----------------
   工具清单与状态快照都是纯本地只读，不消耗 token。 */

/** 三组工具清单（查询 12 / 执行 9 / 辅助 6）+ 风险档位说明。 */
export const getAgentTools = () => unwrap(api.get('/agent/tools'))

/** 当前状态快照：任务、权限档、模型、磁盘、确认卡、可回滚、隔离区。 */
export const getAgentContext = () => unwrap(api.get('/agent/context'))

/** 偏好记忆（常用路径 / 排除标签 / 永久约束 / 常用策略）。 */
// 传 conversation_id 时额外返回「本会话最近用过的路径」（recent_paths）——
// 那是自动记的、跟着会话走；不传就只要全局长期偏好。
export const getAgentPrefs = conversationId =>
  unwrap(api.get('/agent/prefs', { params: conversationId ? { conversation_id: conversationId } : {} }))
export const saveAgentPrefs = prefs => unwrap(api.post('/agent/prefs', { prefs }))

/* ---------------- Agent 多步骤任务 ----------------
   预览只拆步骤、不创建任务；start 只跑只读分析段；
   approve 才在同一个 job 里继续执行段（写权限在 approve 处再拦一次）。 */

export const planAgentTask = (message, context = {}) =>
  unwrap(api.post('/agent/plan', { message, context }))

export const startAgentTask = payload => unwrap(api.post('/agent/task', payload))

export const approveAgentTask = jobId => unwrap(api.post(`/agent/task/${encodeURIComponent(jobId)}/approve`))

/** 修改方案：只换条件重算（零 token），仍停在等待确认，不会自动动文件。 */
export const reviseAgentTask = (jobId, constraints) =>
  unwrap(api.post(`/agent/task/${encodeURIComponent(jobId)}/revise`, { constraints }))

/* 草稿阶段的修改方案：方案还没建任务，所以同一个 /agent/plan 改走重算分支，
   意图由前端带回。与 reviseAgentTask 共用同一套条件通道，结果一致。
   message 必填但重算分支不读它——留着是为了两条路径请求形状一致。 */
export const reviseAgentPlan = (message, intent, constraints, context = {}) =>
  unwrap(api.post('/agent/plan', { message, context, revise: { intent, constraints } }))

/** 任务结果观察：读成「人话总结 + 失败归因 + 下一步」，零 token。 */
export const observeAgentJob = jobId => unwrap(api.get(`/agent/observe/${encodeURIComponent(jobId)}`))

/* ---------------- 权限档位 ----------------
   read / standard / full 三档；切到 full 必须带 confirm: true。 */
export const getSafetySettings = () => unwrap(api.get('/settings/safety'))
export const saveSafetySettings = payload => unwrap(api.post('/settings/safety', payload))

export default api

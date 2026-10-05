import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api',
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

/* ---------------- Agent 模型配置 ----------------
   接口只回掩码，不回传完整 key；api_key / model 传空串表示清除该字段。 */
export const getAgentSettings = () => unwrap(api.get('/settings/agent'))
export const saveAgentSettings = payload => unwrap(api.post('/settings/agent', payload))

/* ---------------- 权限档位 ----------------
   read / standard / full 三档；切到 full 必须带 confirm: true。 */
export const getSafetySettings = () => unwrap(api.get('/settings/safety'))
export const saveSafetySettings = payload => unwrap(api.post('/settings/safety', payload))

export default api

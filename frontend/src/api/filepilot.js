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

const SCAN_CACHE_KEY = 'filepilot:lastScan'
const BACKUP_CACHE_KEY = 'filepilot:lastBackup'
const DUPLICATE_CACHE_KEY = 'filepilot:lastDuplicates'
const ORGANIZER_PREVIEW_CACHE_KEY = 'filepilot:organizerPreview'
const ORGANIZER_RESULT_CACHE_KEY = 'filepilot:organizerResult'

function writeCache(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)) } catch { /* 浏览器禁用存储时仍可正常调用 API */ }
}

function clearCache(key) {
  try { sessionStorage.removeItem(key) } catch { /* 忽略浏览器存储异常 */ }
}

function readCache(key) {
  try {
    const value = sessionStorage.getItem(key)
    return value ? JSON.parse(value) : null
  } catch {
    return null
  }
}

export async function healthCheck() {
  return (await api.get('/health')).data
}

export async function getAiStatus() {
  return (await api.get('/ai/status')).data
}

export async function scanFolder(path) {
  const result = (await api.post('/scan', { path })).data
  writeCache(SCAN_CACHE_KEY, result)
  return result
}

export async function backupProject(sourceDir, destDir) {
  const result = (await api.post('/backup', { source_dir: sourceDir, dest_dir: destDir })).data
  writeCache(BACKUP_CACHE_KEY, result)
  return result
}

export async function getLogs() {
  return (await api.get('/logs')).data
}

export async function exportReport(path) {
  return (await api.post('/export-report', { path })).data
}

export async function scanDuplicates(path) {
  const result = (await api.post('/duplicates/scan', { path })).data
  writeCache(DUPLICATE_CACHE_KEY, result)
  return result
}

export async function moveDuplicateFiles(root, files) {
  return (await api.post('/duplicates/move', { root, files })).data
}

export async function previewOrganization(sourceDir, targetDir) {
  const result = (await api.post('/organize/preview', { source_dir: sourceDir, target_dir: targetDir })).data
  writeCache(ORGANIZER_PREVIEW_CACHE_KEY, result)
  return result
}

export async function executeOrganization(sourceDir, targetDir) {
  const result = (await api.post('/organize/execute', { source_dir: sourceDir, target_dir: targetDir })).data
  writeCache(ORGANIZER_RESULT_CACHE_KEY, result)
  return result
}

export async function undoOrganization() {
  const result = (await api.post('/organize/undo')).data
  clearCache(ORGANIZER_RESULT_CACHE_KEY)
  return result
}

export async function getScanAdvice(payload) {
  return (await api.post('/ai/scan-advice', payload)).data
}

export const getCachedScan = () => readCache(SCAN_CACHE_KEY)
export const getCachedBackup = () => readCache(BACKUP_CACHE_KEY)
export const getCachedDuplicates = () => readCache(DUPLICATE_CACHE_KEY)
export const getCachedOrganizerPreview = () => readCache(ORGANIZER_PREVIEW_CACHE_KEY)
export const getCachedOrganizerResult = () => readCache(ORGANIZER_RESULT_CACHE_KEY)

export default api

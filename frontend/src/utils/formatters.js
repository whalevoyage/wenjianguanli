export function formatCount(value = 0) {
  return Number(value || 0).toLocaleString('zh-CN')
}

export function formatBytes(bytes = 0) {
  const value = Number(bytes || 0)
  if (!value) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  const amount = value / 1024 ** index

  return `${amount.toFixed(index ? 1 : 0)} ${units[index]}`
}

export function formatDateTime(value, fallback = '—') {
  return value ? String(value).replace('T', ' ') : fallback
}

export function formatDuration(seconds, fallback = '—') {
  const value = Number(seconds)
  if (!Number.isFinite(value) || value < 0) return fallback
  if (value < 60) return `${value.toFixed(value < 10 ? 1 : 0)} 秒`

  const minutes = Math.floor(value / 60)
  const remainder = Math.round(value % 60)

  return remainder ? `${minutes} 分 ${remainder} 秒` : `${minutes} 分钟`
}

export function basename(path, fallback = '—') {
  return path?.split(/[\\/]/).filter(Boolean).pop() || fallback
}

export function parentPath(path = '') {
  const index = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'))
  return index >= 0 ? path.slice(0, index) : ''
}

/**
 * 极简 treemap 布局：按数值比例递归二分。
 * 比 squarified 简单，但在这个体量下视觉上已经很够用。
 */
export function layoutTreemap(items, x, y, width, height) {
  if (!items?.length || width <= 0 || height <= 0) return []
  if (items.length === 1) return [{ ...items[0], x, y, w: width, h: height }]

  const total = items.reduce((sum, item) => sum + (item.value || 0), 0) || 1

  let acc = 0
  let split = 0
  while (split < items.length - 1 && acc + (items[split].value || 0) < total / 2) {
    acc += items[split].value || 0
    split += 1
  }
  if (split === 0) split = 1

  const first = items.slice(0, split)
  const second = items.slice(split)
  const firstTotal = first.reduce((sum, item) => sum + (item.value || 0), 0) || 1
  const ratio = Math.min(Math.max(firstTotal / total, 0.05), 0.95)

  if (width >= height) {
    const firstWidth = width * ratio
    return [
      ...layoutTreemap(first, x, y, firstWidth, height),
      ...layoutTreemap(second, x + firstWidth, y, width - firstWidth, height),
    ]
  }

  const firstHeight = height * ratio
  return [
    ...layoutTreemap(first, x, y, width, firstHeight),
    ...layoutTreemap(second, x, y + firstHeight, width, height - firstHeight),
  ]
}

export const CATEGORY_COLORS = {
  图片: '#4f90c2',
  视频: '#8a72c9',
  音频: '#3fa08a',
  文档: '#c9a227',
  表格: '#2f7d6a',
  压缩包: '#c98a3f',
  代码: '#5b7fa8',
  程序: '#a85b5b',
  其他: '#9aa7b4',
}

export function colorFor(node) {
  if (node.is_dir) return '#8fa8c4'
  const category = node.category || '其他'
  return CATEGORY_COLORS[category] || CATEGORY_COLORS['其他']
}

/**
 * 空间地图的图形工具。
 *
 * 这里只放「算」的部分（取色阶、合小项、算立方体顶点），不碰 DOM；
 * 具体的布局与样式都在 `views/SpaceMap.vue` 里。
 *
 * 配色一律走 global.css 的 `--chart-l1..l6` / `--chart-on1..6`，
 * 组件里不出现任何字面量颜色。
 */

const RAMP_STEPS = 6

/** 按名次取色阶：第 1 名最实（l1），越往后越淡，第 6 名之后共用最淡那档。 */
export function rampFill(rank = 0) {
  const step = Math.min(RAMP_STEPS, Math.max(1, Math.floor(rank) + 1))
  return { fill: `var(--chart-l${step})`, on: `var(--chart-on${step})`, step }
}

export function formatBytes(value = 0) {
  const size = Number(value || 0)
  if (!size) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1)
  return `${(size / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

export function shareOf(value, total) {
  const t = Number(total) || 0
  if (!t) return 0
  return Number(value || 0) / t
}

/** 占比显示：<1% 不四舍五入成 0，而是写成 <1%，免得一排全是 0%。 */
export function shareText(value, total) {
  const share = shareOf(value, total)
  if (!share) return '0%'
  if (share < 0.005) return '<1%'
  return `${Math.round(share * 100)}%`
}

/**
 * 把过小的项合并成「其他 N 项」。
 *
 * 不合并的话，占比 0.2% 的项也会占一条色块 —— 一屏几十个碎块，
 * 谁都读不出来（这正是空间地图原来"看着不知道在干什么"的原因之一）。
 */
export function mergeTiny(items = [], { keep = 6, minShare = 0.01 } = {}) {
  const total = items.reduce((sum, item) => sum + (item.size || 0), 0) || 1
  const head = []
  const rest = []
  items.forEach((item, index) => {
    const share = (item.size || 0) / total
    if (index < keep && share >= minShare) head.push({ ...item, share })
    else rest.push(item)
  })
  if (!rest.length) return { head, merged: null }
  const size = rest.reduce((sum, item) => sum + (item.size || 0), 0)
  return {
    head,
    merged: {
      name: `其他 ${rest.length} 项`,
      path: '',
      is_dir: false,
      size,
      share: size / total,
      count: rest.length,
      merged: true,
    },
  }
}

/**
 * 等距立方体的三个可见面。
 *
 * 用**同一色相的三档透明度**做出顶亮 / 左中 / 右暗的体积感 ——
 * 一个渐变都不需要，所以深色主题下也自动成立（透明叠在卡片底色上）。
 * `cx, cy` 是底面菱形中心，`w` 是半宽，`height` 是柱高。
 */
export function isoCube(cx, cy, w, height) {
  const hh = w / 2
  const top = cy - height
  const topFace = `${cx},${top - hh} ${cx + w},${top} ${cx},${top + hh} ${cx - w},${top}`
  const leftFace = `${cx - w},${cy} ${cx},${cy + hh} ${cx},${top + hh} ${cx - w},${top}`
  const rightFace = `${cx},${cy + hh} ${cx + w},${cy} ${cx + w},${top} ${cx},${top + hh}`
  return { topFace, leftFace, rightFace }
}

/**
 * 头部「数据城」的排布：前排放小的、后排放大的。
 * 反过来的话高的会把矮的全挡住，什么都看不见。
 */
export function cityScene(items = [], { maxCubes = 5 } = {}) {
  const top = items.slice(0, maxCubes)
  if (!top.length) return []
  const biggest = top[0].size || 1
  return top.map((item, index) => {
    const back = index < 3
    const cx = back ? 58 + index * 64 : 96 + (index - 3) * 62
    const cy = back ? 104 : 160
    const w = back ? 28 : 24
    // 开 0.72 次方：真实占比差几十倍时，线性高度会让小的几乎看不见
    const height = 12 + ((item.size || 0) / biggest) ** 0.72 * 64
    return { key: item.path || item.name, item, ...rampFill(index), ...isoCube(cx, cy, w, height) }
  })
}

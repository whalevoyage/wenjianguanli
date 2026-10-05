<script setup>
/**
 * 自绘 SVG 柱状图（无图表库）——「高级柱」：
 *   · 方柱（直角）+ 垂直渐变（顶亮 → 底淡），柱子越高越亮；
 *   · 当天有失败时，柱顶叠一小段「红帽」（语义色只在表达状态时出现）；
 *   · 没有任务的日子给一个贴底小墩，坐标轴不空、稀疏数据也不难看；
 *   · 只有 3 条极淡网格线，没有轴线；
 *   · 柱体错峰向上生长；
 *   · 悬停用自绘的 `ChartTip`（不用 <title>，否则原生黑框会和它同时出现）。
 *
 * ⚠️ 渐变原本是「全项目特批、只给这张图」的例外。2026-10-05 用户要求首页图表
 *   「按任务趋势那种做」，该限制已解除 —— 首页图表统一走 `--chart-*` 令牌。
 *   但这仍是**首页专属**语言，别扩散到其它页面。
 */
import { computed, ref } from 'vue'
import ChartTip, { TIP_H, TIP_W } from './ChartTip.vue'

const props = defineProps({
  data: { type: Array, default: () => [] },
})

const W = 280
const H = 126
const PAD = 6
const TOP = 14
const BASE = 100
const PLOT = BASE - TOP

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

const max = computed(() => Math.max(1, ...props.data.map(item => (item.done || 0) + (item.failed || 0))))
const hasData = computed(() => props.data.some(item => (item.done || 0) + (item.failed || 0) > 0))
const pitch = computed(() => (W - PAD * 2) / (props.data.length || 1))

const bars = computed(() => {
  const list = props.data
  const n = list.length || 1
  const step = pitch.value
  const width = Math.min(step * 0.66, 20)

  // 刻度日期：等距取样 + 必含末日，太近就挤掉上一个，避免文字重叠
  const labelSet = new Set()
  const tick = Math.max(1, Math.ceil(n / 4))
  for (let i = 0; i < n; i += tick) labelSet.add(i)
  if (!labelSet.has(n - 1)) {
    const lastStep = Math.max(...labelSet)
    if (n - 1 - lastStep < tick) labelSet.delete(lastStep)
    labelSet.add(n - 1)
  }

  return list.map((item, index) => {
    const done = item.done || 0
    const failed = item.failed || 0
    const value = done + failed
    const total = value ? Math.max((value / max.value) * PLOT, 3) : 3
    // 失败做成柱顶「红帽」：失败很少时也给个可见的最小高度，不至于淹没
    const failedH = failed ? Math.max((failed / max.value) * PLOT, 2.5) : 0
    const doneH = Math.max(total - failedH, 0)
    const x = PAD + index * step + (step - width) / 2
    const top = BASE - total
    return {
      x,
      width,
      top,
      doneY: top + failedH,
      doneH,
      failedY: top,
      failedH,
      empty: value === 0,
      isMax: value > 0 && value === max.value,
      cx: x + width / 2,
      delay: `${index * 32}ms`,
      label: String(item.date || '').slice(5),
      showLabel: labelSet.has(index),
    }
  })
})

const labels = computed(() => bars.value.filter(bar => bar.showLabel))
const maxBar = computed(() => bars.value.find(bar => bar.isMax) || null)
const maxValue = computed(() =>
  props.data.reduce((acc, item) => Math.max(acc, (item.done || 0) + (item.failed || 0)), 0),
)

/* ------------------------------------------------------------ 悬停提示 */
const wrap = ref(null)
const activeIndex = ref(-1)
const tip = ref({ open: false, x: 0, y: 0, caretX: 0, title: '', rows: [] })

function dayLabel(value) {
  const text = String(value || '')
  const at = new Date(`${text}T00:00:00`)
  if (Number.isNaN(at.getTime())) return text.slice(5)
  return `${text.slice(5)} ${WEEK[at.getDay()]}`
}

function hideTip() {
  activeIndex.value = -1
  if (tip.value.open) tip.value = { ...tip.value, open: false }
}

function onMove(event) {
  const el = wrap.value
  if (!el || !props.data.length) return
  const rect = el.getBoundingClientRect()
  const scale = rect.width / W
  if (!scale) return

  const svgX = (event.clientX - rect.left) / scale
  const index = Math.min(
    props.data.length - 1,
    Math.max(0, Math.round((svgX - PAD) / pitch.value - 0.5)),
  )
  const bar = bars.value[index]
  if (!bar) return
  activeIndex.value = index

  const cx = bar.cx * scale
  const x = Math.min(Math.max(cx - TIP_W / 2, 0), Math.max(rect.width - TIP_W, 0))
  const y = Math.max(bar.top * scale - TIP_H - 12, 0)
  const item = props.data[index]

  tip.value = {
    open: true,
    x,
    y,
    caretX: Math.min(Math.max(cx - x, 14), TIP_W - 14),
    title: dayLabel(item.date),
    rows: [
      { label: '完成', value: item.done || 0, tone: 'done' },
      { label: '失败', value: item.failed || 0, tone: 'fail' },
    ],
  }
}
</script>

<template>
  <div
    ref="wrap"
    class="pbars-wrap"
    :class="{ 'is-hovering': activeIndex >= 0 }"
    @mousemove="onMove"
    @mouseleave="hideTip"
  >
    <svg class="pbars" :viewBox="`0 0 ${W} ${H}`" width="100%" role="img" aria-label="近 14 天任务量">
      <defs>
        <linearGradient id="pbarFill" gradientUnits="userSpaceOnUse" x1="0" x2="0" :y1="TOP" :y2="BASE">
          <stop class="pbars__stop-top" offset="0%"></stop>
          <stop class="pbars__stop-bottom" offset="100%"></stop>
        </linearGradient>
      </defs>

      <line class="pbars__grid" :x1="PAD" :y1="TOP" :x2="W - PAD" :y2="TOP"></line>
      <line class="pbars__grid" :x1="PAD" :y1="(TOP + BASE) / 2" :x2="W - PAD" :y2="(TOP + BASE) / 2"></line>
      <line class="pbars__grid" :x1="PAD" :y1="BASE" :x2="W - PAD" :y2="BASE"></line>

      <g
        v-for="(bar, index) in bars"
        :key="index"
        class="pbars__col"
        :class="{ 'is-active': index === activeIndex }"
        :style="{ animationDelay: bar.delay }"
      >
        <rect
          v-if="bar.empty"
          class="pbars__bar is-empty"
          :x="bar.x"
          :y="BASE - 3"
          :width="bar.width"
          height="3"
        ></rect>
        <template v-else>
          <rect
            class="pbars__bar pbars__bar--done"
            :x="bar.x"
            :y="bar.doneY"
            :width="bar.width"
            :height="bar.doneH"
            fill="url(#pbarFill)"
          ></rect>
          <rect
            v-if="bar.failedH > 0"
            class="pbars__bar pbars__fail"
            :x="bar.x"
            :y="bar.failedY"
            :width="bar.width"
            :height="bar.failedH"
          ></rect>
        </template>
      </g>

      <template v-if="maxBar">
        <text class="pbars__value" :x="maxBar.cx" :y="Math.max(maxBar.top - 9, 13)" text-anchor="middle">
          {{ maxValue }}
        </text>
      </template>

      <text
        v-for="(bar, index) in labels"
        :key="`label-${index}`"
        class="pbars__label"
        :x="bar.cx"
        y="118"
        text-anchor="middle"
      >
        {{ bar.label }}
      </text>

      <text v-if="!hasData" x="140" y="60" text-anchor="middle" class="pbars__empty">近 14 天暂无任务</text>
    </svg>

    <ChartTip v-bind="tip" />
  </div>
</template>

<style scoped>
.pbars-wrap {
  position: relative;
  width: 100%;
}

.pbars {
  display: block;
  width: 100%;
  height: auto;
}

.pbars__grid {
  stroke: var(--hairline);
  stroke-width: 1;
}

/* 图表渐变：柱体顶亮底淡，柱子越高越亮；顶端用更艳的天蓝（--chart-from） */
.pbars__stop-top {
  stop-color: var(--chart-from);
  stop-opacity: 0.95;
}

.pbars__stop-bottom {
  stop-color: var(--chart-to);
  stop-opacity: 0.16;
}

/* 只给「完成」柱加同色柔光：空墩子是灰的、失败帽是红的，都不该被蓝光罩住 */
.pbars__bar--done {
  filter: drop-shadow(0 0 5px var(--chart-glow));
}

.pbars__bar.is-empty {
  fill: var(--inset);
}

/* 失败：柱顶「红帽」，只在当天确有失败时出现 */
.pbars__fail {
  fill: var(--danger);
}

.pbars__value {
  fill: var(--ink);
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.pbars__label {
  fill: var(--ink-4);
  font-size: var(--fs-micro);
}

.pbars__empty {
  fill: var(--ink-4);
  font-size: var(--fs-note);
}

.pbars__col {
  transform-box: fill-box;
  transform-origin: bottom;
  animation: pbar-rise 620ms var(--ease) both;
  transition: opacity var(--duration-quick) var(--ease);
}

/* 悬停时其余柱子退后，被指的那根保持清晰（别太暗，稀疏图上会像坏了） */
.pbars-wrap.is-hovering .pbars__col {
  opacity: 0.6;
}

.pbars-wrap.is-hovering .pbars__col.is-active {
  opacity: 1;
}

@keyframes pbar-rise {
  from {
    transform: scaleY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .pbars__col {
    animation: none;
  }
}
</style>

<script setup>
/**
 * 自绘 SVG 环形图（无图表库）。
 * 用 pathLength="100" 让 stroke-dasharray 直接按 0~100 计，避免手算周长。
 * 挂载时圆环有一段扫入动画；悬停时在环的右侧弹出 `ChartTip`（传了 tipRows 才启用）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import ChartTip, { TIP_H } from './ChartTip.vue'

const props = defineProps({
  percent: { type: Number, default: 0 },
  size: { type: Number, default: 132 },
  stroke: { type: Number, default: 13 },
  caption: { type: String, default: '已用' },
  tipTitle: { type: String, default: '' },
  tipRows: { type: Array, default: () => [] },
})

const REDUCED =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

const clamped = computed(() => Math.min(100, Math.max(0, Number(props.percent) || 0)))
const shown = computed(() => Math.round(clamped.value))
const progress = ref(REDUCED ? clamped.value : 0)

onMounted(() => {
  if (REDUCED) return
  requestAnimationFrame(() => {
    progress.value = clamped.value
  })
})

watch(clamped, value => {
  progress.value = value
})

/* ------------------------------------------------------------ 悬停提示 */
const tip = ref({ open: false, x: 0, y: 0, caretX: 0, caret: 'left', title: '', rows: [] })
const hasTip = computed(() => props.tipRows.length > 0)

function showTip() {
  if (!hasTip.value) return
  tip.value = {
    open: true,
    // 环比较小，浮层放到环的右侧，箭头指回环
    x: props.size + 10,
    y: Math.min(Math.max(props.size / 2 - TIP_H / 2, 0), Math.max(props.size - TIP_H, 0)),
    caretX: 0,
    caret: 'left',
    title: props.tipTitle,
    rows: props.tipRows,
  }
}

function hideTip() {
  if (tip.value.open) tip.value = { ...tip.value, open: false }
}
</script>

<template>
  <div
    class="donut-wrap"
    :style="{ width: `${size}px`, height: `${size}px` }"
    @mouseenter="showTip"
    @mouseleave="hideTip"
  >
    <svg
      class="donut"
      :width="size"
      :height="size"
      viewBox="0 0 120 120"
      role="img"
      :aria-label="`${caption} ${shown}%`"
    >
      <defs>
        <!-- 沿 Y 轴「上亮下深」，和趋势柱（顶亮底淡）同一套语言 -->
        <linearGradient id="donutArc" gradientUnits="userSpaceOnUse" x1="60" y1="14" x2="60" y2="106">
          <stop class="donut__stop-from" offset="0%"></stop>
          <stop class="donut__stop-to" offset="100%"></stop>
        </linearGradient>
      </defs>
      <circle cx="60" cy="60" r="46" fill="none" class="donut__track" :stroke-width="stroke" />
      <circle
        cx="60"
        cy="60"
        r="46"
        fill="none"
        class="donut__arc"
        :stroke-width="stroke"
        pathLength="100"
        :stroke-dasharray="`${progress} 100`"
        stroke-linecap="round"
        transform="rotate(-90 60 60)"
      />
      <text x="60" y="59" text-anchor="middle" class="donut__value">{{ shown }}</text>
      <text x="60" y="76" text-anchor="middle" class="donut__caption">{{ caption }}</text>
    </svg>

    <ChartTip v-bind="tip" />
  </div>
</template>

<style scoped>
.donut-wrap {
  position: relative;
  flex: none;
}

.donut {
  display: block;
}

.donut__track {
  stroke: var(--inset);
}

/* 弧线用渐变描边（上亮下深）+ 同色柔光：环够粗（13px），光晕立得住 */
.donut__arc {
  stroke: url(#donutArc);
  filter: drop-shadow(0 0 6px var(--chart-glow));
  transition: stroke-dasharray 900ms var(--ease);
}

.donut__stop-from {
  stop-color: var(--chart-from);
}

.donut__stop-to {
  stop-color: var(--chart-to);
}

.donut__value {
  fill: var(--ink);
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.donut__caption {
  fill: var(--ink-3);
  font-size: 11px;
  letter-spacing: 0.02em;
}

@media (prefers-reduced-motion: reduce) {
  .donut__arc {
    transition: none;
  }
}
</style>

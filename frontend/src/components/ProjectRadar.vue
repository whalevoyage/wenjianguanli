<script setup>
import { computed, ref } from 'vue'
import { formatBytes, formatCount } from '../utils/formatters'

/**
 * 项目雷达：把项目画成气泡。
 *   横轴 = 最后活跃（越右越久没动）
 *   纵轴 = 体积（非线性压缩，否则小项目会挤成一条线）
 *   点大小 = 文件数
 *   右侧橙色区 = 超过僵尸线的项目
 * 纯自绘 SVG，不引图表库。颜色一律走主题变量，深浅色都成立。
 */
const props = defineProps({
  points: { type: Array, required: true },
  zombieDays: { type: Number, default: 180 },
  highlight: { type: String, default: '' },
})

const emit = defineEmits(['select'])

const W = 720
const H = 340
const PAD = { left: 66, right: 24, top: 28, bottom: 48 }

const hovered = ref('')

/** 没有活跃时间的项目横轴无从定位，只留在下面的表里 */
const usable = computed(() =>
  props.points.filter(item => Number.isFinite(item.inactiveDays) && item.inactiveDays >= 0),
)

const frame = {
  x0: PAD.left,
  x1: W - PAD.right,
  y0: PAD.top,
  y1: H - PAD.bottom,
}

const scales = computed(() => {
  const list = usable.value
  const maxDays = Math.max(props.zombieDays * 2, ...list.map(item => item.inactiveDays), 30)
  const maxSize = Math.max(1, ...list.map(item => item.size))
  const maxCount = Math.max(1, ...list.map(item => item.fileCount))
  // 坐标域围绕绘图区内缩一个最大气泡半径，否则 0 天的点会被边缘切掉一半
  const pad = 16
  const spanX = frame.x1 - frame.x0 - pad * 2
  const spanY = frame.y1 - frame.y0 - pad * 2
  return {
    maxDays,
    maxSize,
    // sqrt 压缩：体积与天数的跨度常差两三个数量级，线性会把所有点挤到角落
    x: days => frame.x0 + pad + Math.sqrt(Math.min(Math.max(days, 0), maxDays) / maxDays) * spanX,
    y: size => frame.y1 - pad - Math.sqrt(Math.max(size, 0) / maxSize) * spanY,
    r: count => 5 + Math.sqrt(Math.max(count, 0) / maxCount) * 12,
  }
})

const bubbles = computed(() =>
  usable.value.map(item => ({
    ...item,
    cx: scales.value.x(item.inactiveDays),
    cy: scales.value.y(item.size),
    r: scales.value.r(item.fileCount),
  })),
)

const yTicks = computed(() =>
  [1, 0.5, 0.1].map(fraction => ({
    label: formatBytes(scales.value.maxSize * fraction),
    y: scales.value.y(scales.value.maxSize * fraction),
  })),
)

const xTicks = computed(() => {
  const days = [...new Set([0, props.zombieDays, Math.round(scales.value.maxDays)])]
  return days.map(value => ({ label: `${formatCount(value)} 天`, x: scales.value.x(value) }))
})

const zombieX = computed(() => scales.value.x(props.zombieDays))

/** 标签靠右时往回收，别把字顶出画布 */
const zoneLabelX = computed(() => Math.min(zombieX.value + 8, frame.x1 - 150))

const tip = computed(() => {
  const point = bubbles.value.find(item => item.path === hovered.value)
  if (!point) return null
  const width = 172
  const left = Math.min(Math.max(point.cx + 14, frame.x0 + 2), frame.x1 - width)
  const top = Math.max(point.cy - 58, 4)
  return { point, left, top, width }
})

function stateText(point) {
  if (point.isZombie) return '僵尸项目'
  if (point.inactiveDays < 1) return '今天还在动'
  return `${formatCount(point.inactiveDays)} 天没动`
}
</script>

<template>
  <div class="radar">
    <svg :viewBox="`0 0 ${W} ${H}`" width="100%" role="img">
      <title>项目分布雷达图</title>
      <desc>横轴为最后活跃天数，纵轴为项目体积，点大小为文件数，右侧为僵尸区。</desc>

      <defs>
        <clipPath id="project-radar-plot">
          <rect :x="frame.x0" :y="frame.y0" :width="frame.x1 - frame.x0" :height="frame.y1 - frame.y0" rx="10" />
        </clipPath>
      </defs>

      <rect
        :x="frame.x0" :y="frame.y0" :width="frame.x1 - frame.x0" :height="frame.y1 - frame.y0"
        rx="10" fill="var(--surface-2)" stroke="var(--hairline)" stroke-width="1"
      />

      <g clip-path="url(#project-radar-plot)">
        <!-- 僵尸区底色 -->
        <rect
          :x="zombieX" :y="frame.y0"
          :width="Math.max(frame.x1 - zombieX, 0)" :height="frame.y1 - frame.y0"
          fill="var(--warning-soft)"
        />

        <!-- 横向网格 -->
        <line
          v-for="tick in yTicks" :key="`grid-${tick.label}`"
          :x1="frame.x0" :x2="frame.x1" :y1="tick.y" :y2="tick.y"
          stroke="var(--hairline)" stroke-width="1"
        />

        <!-- 气泡：键盘可达（tabindex + Enter/Space 触发同一 select），
             聚焦时同样浮出提示卡，焦点描边走全局 :focus-visible 的中性环 -->
        <circle
          v-for="point in bubbles" :key="point.path"
          :cx="point.cx" :cy="point.cy" :r="point.r"
          :fill="point.isZombie ? 'var(--warning)' : 'var(--primary)'"
          :fill-opacity="hovered === point.path || highlight === point.path ? 0.9 : 0.55"
          :stroke="highlight === point.path ? 'var(--ink)' : 'var(--surface)'"
          :stroke-width="highlight === point.path ? 2 : 1.5"
          class="bubble"
          role="button"
          tabindex="0"
          :aria-label="`${point.name}，${stateText(point)}`"
          @mouseenter="hovered = point.path"
          @mouseleave="hovered = ''"
          @focus="hovered = point.path"
          @blur="hovered = ''"
          @click="emit('select', point.path)"
          @keydown.enter.prevent="emit('select', point.path)"
          @keydown.space.prevent="emit('select', point.path)"
        />
      </g>

      <!-- 僵尸阈值线压在绘图区上，虚线不被裁切 -->
      <line
        :x1="zombieX" :x2="zombieX" :y1="frame.y0" :y2="frame.y1"
        stroke="var(--warning)" stroke-width="1" stroke-dasharray="4 4"
      />
      <!-- 标签放在绘图区上方：区域内任意位置都可能被气泡压住 -->
      <text :x="zoneLabelX" :y="frame.y0 - 9" class="zone">僵尸区 · 超过 {{ formatCount(zombieDays) }} 天</text>

      <!-- 轴刻度 -->
      <g>
        <text
          v-for="tick in yTicks" :key="`ty-${tick.label}`"
          :x="frame.x0 - 8" :y="tick.y + 4" text-anchor="end" class="tick"
        >{{ tick.label }}</text>
        <text
          v-for="tick in xTicks" :key="`tx-${tick.label}`"
          :x="tick.x" :y="frame.y1 + 20" text-anchor="middle" class="tick"
        >{{ tick.label }}</text>
      </g>

      <!-- 悬停提示 -->
      <g v-if="tip">
        <rect :x="tip.left" :y="tip.top" :width="tip.width" height="52" rx="8" fill="var(--ink)" fill-opacity="0.92" />
        <text :x="tip.left + 10" :y="tip.top + 19" class="tip-name">{{ tip.point.name }}</text>
        <text :x="tip.left + 10" :y="tip.top + 34" class="tip-meta">
          {{ tip.point.sizeText }} · {{ formatCount(tip.point.fileCount) }} 个文件
        </text>
        <text :x="tip.left + 10" :y="tip.top + 47" class="tip-meta">{{ stateText(tip.point) }}</text>
      </g>
    </svg>

    <div class="radar-legend">
      <span class="legend-item"><i class="dot dot--alive"></i>活跃项目</span>
      <span class="legend-item"><i class="dot dot--zombie"></i>僵尸项目</span>
      <span class="legend-item legend-item--muted">点大小 = 文件数</span>
      <span class="legend-item legend-item--muted">越靠右 = 越久没动</span>
    </div>
  </div>
</template>

<style scoped>
.radar {
  margin-top: 4px;
}

.zone {
  fill: var(--warning-ink);
  font-size: 11px;
  font-weight: 600;
}

.tick {
  fill: var(--ink-3);
  font-size: 11px;
}

/* 提示条底色取 --ink，文字取 --surface：深浅色下都自动是「反色」关系 */
.tip-name {
  fill: var(--surface);
  font-size: 12px;
  font-weight: 600;
}

.tip-meta {
  fill: var(--surface);
  fill-opacity: 0.72;
  font-size: 11px;
}

.bubble {
  cursor: pointer;
  outline: none;
  transition: fill-opacity var(--duration-quick) var(--ease);
}

/* SVG 元素不支持 outline 属性，焦点环用描边表达（与全局 :focus-visible 同为中性色） */
.bubble:focus-visible {
  stroke: var(--ink);
  stroke-width: 2.5;
}

.radar-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
  padding: 10px 4px 0;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ink-2);
  font-size: 12px;
}

.legend-item--muted {
  color: var(--ink-3);
}

.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.dot--alive {
  background: var(--primary);
}

.dot--zombie {
  background: var(--warning);
}
</style>

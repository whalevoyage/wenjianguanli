<script setup>
import { computed } from 'vue'
import { useAnimatedNumber } from '../composables/useAnimatedNumber'

const props = defineProps({
  title: { type: String, required: true },
  value: { type: String, required: true },
  note: { type: String, default: '' },
  icon: { type: [Object, Function], required: true },
  tone: { type: String, default: 'blue' },
  trend: { type: String, default: '' },
})

const { displayValue, bumpKey } = useAnimatedNumber(() => props.value)

// 「数字 +」标记：任何非负数值都加（含 0 / 0 B）；非数字原样不带。
const showPlus = computed(() => {
  const m = String(props.value ?? '').match(/^([\d.,]+)/)
  if (!m) return false
  const n = Number.parseFloat(m[1].replace(/,/g, ''))
  return Number.isFinite(n) && n >= 0
})
</script>

<template>
  <article class="stat-card">
    <div class="stat-card__head">
      <span class="stat-card__title">{{ title }}</span>
      <div class="stat-card__icon" :class="`is-${tone}`">
        <el-icon :size="17"><component :is="icon" /></el-icon>
      </div>
    </div>

    <div class="stat-card__value" :key="bumpKey" :class="{ 'is-bumping': bumpKey > 0 }">
      {{ displayValue }}<span v-if="showPlus" class="stat-card__plus">+</span>
    </div>

    <div class="stat-card__foot">
      <span class="stat-card__note">{{ note }}</span>
      <el-tag v-if="trend" size="small" effect="plain">{{ trend }}</el-tag>
    </div>
  </article>
</template>

<style scoped>
/* 实色卡片：不参与毛玻璃，层级由「发丝线 + 极轻投影」承担 */
.stat-card {
  position: relative;
  min-height: 146px;
  display: grid;
  gap: 14px;
  padding: 20px 22px 18px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
  box-shadow: var(--sh-1);
  transition: background-color var(--dur) var(--ease), border-color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease), transform var(--dur) var(--ease);
}

/* 悬停不靠「投影上浮」，而是「表面提亮 + 极轻位移」——深色下同样成立 */
.stat-card:hover {
  background: var(--surface-2);
  box-shadow: var(--sh-2);
  transform: translateY(-2px);
}

.stat-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.stat-card__title {
  color: var(--ink-2);
  font-size: var(--fs-note);
  font-weight: 500;
  letter-spacing: -0.005em;
}

.stat-card__icon {
  width: 32px;
  height: 32px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur) var(--ease), color var(--dur) var(--ease);
}

/* 蓝色图标：渐变实底 + 白图标（原来是淡蓝底 + 蓝图标，在卡片头上几乎看不出）。
   只有 blue 改了 —— 首页 4 张卡都是 blue，其它 tone 保持原样，避免波及别的页面。 */
.is-blue {
  color: var(--on-primary);
  background: var(--grad-brand);
  box-shadow: 0 2px 8px var(--chart-glow);
}

.is-green {
  color: var(--success-ink);
  background: var(--success-soft);
}

.is-orange {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.is-purple {
  color: var(--violet-ink);
  background: var(--violet-soft);
}

.is-cyan {
  color: var(--info-ink);
  background: var(--info-soft);
}

.is-red {
  color: var(--danger-ink);
  background: var(--danger-soft);
}

.is-slate {
  color: var(--ink-2);
  background: var(--surface-2);
}

.stat-card__value {
  color: var(--ink);
  font-size: clamp(30px, 2.7vw, 42px);
  line-height: 1.05;
  font-weight: 600;
  letter-spacing: -0.038em;
  font-variant-numeric: tabular-nums;
  word-break: break-word;
  transform-origin: left center;
}

/* 数字「由小变大并明显回弹」：挂载 / 数值变化时被 bumpKey 重新触发 */
.stat-card__value.is-bumping {
  animation: value-bump 480ms var(--ease);
}

@keyframes value-bump {
  0% {
    transform: scale(0.82);
    opacity: 0.4;
  }

  60% {
    transform: scale(1.08);
  }

  100% {
    transform: scale(1);
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .stat-card__value.is-bumping {
    animation: none;
  }
}

/* 数字后的「+」标记：与数字同字号、同行、同色（由模板 v-if 控制） */
.stat-card__plus {
  font-size: 1em;
  font-weight: 700;
  color: var(--ink);
  margin-left: 1px;
  letter-spacing: 0;
}

.stat-card__foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  margin-top: auto;
}

.stat-card__note {
  color: var(--ink-3);
  font-size: var(--fs-note);
  line-height: 1.6;
}
</style>

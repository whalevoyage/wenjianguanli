<script>
/* 浮层尺寸：调用方要用它算定位，从组件导出，避免各处各写一份对不上 */
export const TIP_W = 112
export const TIP_H = 70
</script>

<script setup>
/**
 * 图表悬停提示（紧凑浅色数据卡）——全站图表统一用它，替掉浏览器原生 <title> tooltip。
 *
 * 只负责「画」：位置由调用方算好（相对父容器），父容器必须 position: relative。
 *   x / y      —— 浮层左上角（px）
 *   caretX     —— 下箭头相对浮层左侧的偏移（由调用方 clamp 到 14 ~ TIP_W-14）
 *   caret      —— 'bottom'（默认）| 'left' | 'none'
 *   rows       —— [{ label, value, tone }]，tone: 'done' | 'fail' | 空
 */
defineProps({
  open: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
  caretX: { type: Number, default: 0 },
  caret: { type: String, default: 'bottom' },
  title: { type: String, default: '' },
  rows: { type: Array, default: () => [] },
})
</script>

<template>
  <div
    class="tip"
    :class="{ 'is-open': open }"
    :style="{ left: `${x}px`, top: `${y}px`, width: `${TIP_W}px` }"
    aria-hidden="true"
  >
    <div class="tip__title">{{ title }}</div>
    <div class="tip__rule"></div>
    <div class="tip__rows">
      <div v-for="row in rows" :key="row.label" class="tip__row">
        <i class="tip__dot" :class="row.tone ? `is-${row.tone}` : null"></i>
        <span class="tip__label">{{ row.label }}</span>
        <b class="tip__value" :class="row.tone ? `is-${row.tone}` : null">{{ row.value }}</b>
      </div>
    </div>
    <i
      v-if="caret !== 'none'"
      class="tip__caret"
      :class="`is-${caret}`"
      :style="caret === 'left' ? null : { left: `${caretX}px` }"
    ></i>
  </div>
</template>

<style scoped>
.tip {
  position: absolute;
  z-index: 6;
  box-sizing: border-box;
  padding: 5px 9px 6px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 9px;
  box-shadow: var(--sh-2);
  opacity: 0;
  transform: translateY(3px);
  pointer-events: none;
  transition: opacity var(--duration-quick) var(--ease), transform var(--duration-quick) var(--ease);
}

.tip.is-open {
  opacity: 1;
  transform: translateY(0);
}

.tip__title {
  color: var(--ink-3);
  font-size: var(--fs-micro);
  letter-spacing: 0.02em;
}

.tip__rule {
  height: 1px;
  margin: 4px 0 5px;
  background: var(--hairline);
}

.tip__rows {
  display: grid;
  gap: 3px;
}

.tip__row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tip__dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 2px;
  background: var(--ink-4);
}

.tip__dot.is-done {
  background: var(--primary);
}

.tip__dot.is-fail {
  background: var(--danger);
}

.tip__label {
  color: var(--ink-2);
  font-size: var(--fs-note);
}

.tip__value {
  margin-left: auto;
  color: var(--ink);
  font-size: var(--fs-note);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.tip__value.is-fail {
  color: var(--danger-ink);
}

/* 指向被悬停对象的小箭头 */
.tip__caret {
  position: absolute;
  width: 7px;
  height: 7px;
  background: var(--surface);
  transform: rotate(45deg);
}

.tip__caret.is-bottom {
  bottom: -4px;
  margin-left: -3.5px;
  border-right: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline);
}

.tip__caret.is-left {
  left: -4px;
  top: 50%;
  margin-top: -3.5px;
  border-left: 1px solid var(--hairline);
  border-bottom: 1px solid var(--hairline);
}
</style>

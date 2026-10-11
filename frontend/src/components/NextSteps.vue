<script setup>
/**
 * 「对这个目录：查重 · 清理 · 迁移 …」这一行。
 *
 * 抽成组件是为了**样式只有一处**：之前空间地图里内联写了一份，
 * 现在别的页面也要用，再各写一份就会慢慢跑偏。
 *
 * 行为由 `composables/useHandoff.js` 负责：把目录塞进路由 query，目标页自动填好。
 */
import { useHandoff } from '../composables/useHandoff'

const props = defineProps({
  /** 要带过去的目录。空字符串时整行不渲染 */
  path: { type: String, default: '' },
  /** 来源页面的英文名（写进 `?from=`，目标页会翻成中文显示） */
  origin: { type: String, default: '' },
  /** [{ name, label }]，name 是路由名 */
  steps: { type: Array, default: () => [] },
})

const { handoffTo } = useHandoff()
</script>

<template>
  <div v-if="path && steps.length" class="next-steps">
    <span class="next-steps__label">对这个目录：</span>
    <button
      v-for="step in steps"
      :key="step.name"
      type="button"
      class="next-steps__btn"
      @click="handoffTo(step.name, path, origin)"
    >
      {{ step.label }}
    </button>
  </div>
</template>

<style scoped>
.next-steps {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.next-steps__label {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.next-steps__btn {
  padding: 4px 11px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-2);
  font: inherit;
  font-size: var(--fs-note);
  cursor: pointer;
  transition: border-color var(--duration-quick) var(--ease), color var(--duration-quick) var(--ease);
}

.next-steps__btn:hover {
  border-color: var(--hairline-strong);
  color: var(--ink);
}
</style>

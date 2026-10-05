<script setup>
import { ArrowRight } from '@element-plus/icons-vue'

/**
 * 快捷入口卡。
 * 之前是一张卡里切 4 格、无图标无箭头，看起来和只读的指标卡一模一样，
 * 用户不知道能不能点。这里把它做成一个有明确 affordance 的控件：
 *   hover → 表面提亮 + 箭头右移张开（transitions.dev 的 Learn more hover）
 *   press → spring 回缩
 */
defineProps({
  title: { type: String, required: true },
  note: { type: String, default: '' },
  icon: { type: [Object, Function], default: null },
  tone: { type: String, default: 'blue' },
})

defineEmits(['select'])
</script>

<template>
  <button type="button" class="action-tile" @click="$emit('select')">
    <span v-if="icon" class="action-tile__icon" :class="`is-${tone}`">
      <el-icon :size="18"><component :is="icon" /></el-icon>
    </span>

    <span class="action-tile__copy">
      <strong>{{ title }}</strong>
      <small v-if="note">{{ note }}</small>
    </span>

    <span class="action-tile__go" aria-hidden="true">
      <el-icon :size="14"><ArrowRight /></el-icon>
    </span>
  </button>
</template>

<style scoped>
.action-tile {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 13px;
  width: 100%;
  padding: 16px 18px;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius);
  box-shadow: var(--sh-1);
  cursor: pointer;
  transition: background-color var(--duration-fast) var(--ease),
    border-color var(--duration-fast) var(--ease),
    box-shadow var(--duration-fast) var(--ease), transform var(--duration-fast) var(--ease-spring);
}

.action-tile:hover {
  background: var(--surface-2);
  border-color: var(--hairline-strong);
  box-shadow: var(--sh-2);
  transform: translateY(-2px);
}

.action-tile:active {
  transform: scale(0.98) translateY(0);
  box-shadow: var(--sh-1);
}

.action-tile__icon {
  width: 36px;
  height: 36px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  transition: background-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.is-blue {
  color: var(--primary);
  background: var(--primary-soft);
}

.is-green {
  color: var(--success-ink);
  background: var(--success-soft);
}

.is-orange {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.is-violet {
  color: var(--violet-ink);
  background: var(--violet-soft);
}

.action-tile__copy {
  min-width: 0;
}

.action-tile__copy strong {
  display: block;
  color: var(--ink);
  font-size: var(--fs-body);
  font-weight: 500;
  letter-spacing: -0.008em;
}

.action-tile__copy small {
  display: block;
  margin-top: 3px;
  color: var(--ink-3);
  font-size: var(--fs-note);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.action-tile__go {
  flex: none;
  display: grid;
  place-items: center;
  color: var(--ink-4);
  /* 箭头单独过渡：hover 时才右移 */
  transition: transform var(--duration-fast) var(--ease-slide), color var(--duration-fast) var(--ease),
    opacity var(--duration-fast) var(--ease);
}

.action-tile:hover .action-tile__go {
  color: var(--primary);
  transform: translateX(var(--dist-micro));
}

.action-tile:focus-visible {
  outline: 2px solid var(--hairline-strong);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .action-tile,
  .action-tile__go {
    transition: none;
  }

  .action-tile:hover {
    transform: none;
  }
}
</style>

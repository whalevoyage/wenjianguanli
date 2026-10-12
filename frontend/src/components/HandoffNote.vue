<script setup>
/**
 * 「已带入目录」提示条。
 *
 * 跨页接力（见 `composables/useHandoff.js`）把目录填进输入框之后，
 * 得让用户知道**这个框为什么已经满了** —— 否则会以为是自己手滑填的。
 */
defineProps({
  path: { type: String, default: '' },
  from: { type: String, default: '' },
})

defineEmits(['dismiss'])
</script>

<template>
  <div class="handoff" role="status">
    <span class="handoff__text">
      已带入目录 <strong>{{ path }}</strong><template v-if="from"> · 从 {{ from }} 带过来</template>
    </span>
    <button type="button" class="handoff__close" @click="$emit('dismiss')">知道了</button>
  </div>
</template>

<style scoped>
.handoff {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--inset);
}

.handoff__text {
  min-width: 0;
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--fs-note);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.handoff__text strong {
  color: var(--ink);
  font-weight: 600;
}

.handoff__close {
  flex: 0 0 auto;
  padding: 4px 10px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-2);
  font: inherit;
  font-size: var(--fs-note);
  cursor: pointer;
}

.handoff__close:hover {
  border-color: var(--hairline-strong);
  color: var(--ink);
}
</style>

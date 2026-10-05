<script setup>
/**
 * 空状态：替代 el-empty 的默认线稿插画。
 * shadcn 的 Empty 结构 = icon + title + description + action，四段都可选。
 */
defineProps({
  title: { type: String, default: '暂无数据' },
  description: { type: String, default: '' },
  icon: { type: [Object, Function], default: null },
})
</script>

<template>
  <div class="empty-state">
    <div v-if="icon" class="empty-state__icon">
      <el-icon><component :is="icon" /></el-icon>
    </div>

    <strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>

    <div v-if="$slots.default" class="empty-state__action">
      <slot />
    </div>
  </div>
</template>

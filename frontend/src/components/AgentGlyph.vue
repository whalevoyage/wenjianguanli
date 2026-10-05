<script setup>
/**
 * 「对话峰」标记：圆角对话气泡（左下小尾）+ 一条上行的折线。
 * 语义 = 「用对话把指令翻上山」（对话 × 山脊），延续 Atlas 的几何单色系。
 *
 * 用法：
 *   <el-icon :size="20"><AgentGlyph /></el-icon>   // 随 font-size（1em）缩放
 *   <AgentGlyph :size="64" />                      // 指定像素
 */
defineProps({
  size: { type: [Number, String], default: 0 },
})
</script>

<template>
  <svg
    class="agent-glyph"
    :width="size ? `${size}px` : '1em'"
    :height="size ? `${size}px` : '1em'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <rect x="3" y="4" width="18" height="13.5" rx="3.5" />
    <path d="M8.5 17.5 L8.5 20.7 L12.3 17.5" />
    <path d="M6.8 13.2 L9.8 8.6 L12.3 11 L16.6 6.8" />
  </svg>
</template>

<style scoped>
.agent-glyph {
  display: block;
}
</style>

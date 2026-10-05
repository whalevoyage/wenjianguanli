<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowUp, PieChart } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import { startTree } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import JobProgress from '../components/JobProgress.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { colorFor, layoutTreemap } from '../utils/treemap'

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel } = useJob('space')

const path = ref('')
const depth = ref(2)
const root = ref(null)
const stack = ref([])

const depthOptions = [
  { label: '2 层', value: 2 },
  { label: '3 层', value: 3 },
  { label: '4 层', value: 4 },
]

const current = computed(() => stack.value[stack.value.length - 1] || root.value)

const WIDTH = 1000
const HEIGHT = 460

const blocks = computed(() => {
  const node = current.value
  if (!node?.children?.length) return []

  const children = node.children
    .filter(item => (item.size || 0) > 0)
    .slice()
    .sort((a, b) => (b.size || 0) - (a.size || 0))
    .slice(0, 40)
    .map(item => ({
      ...item,
      value: item.size || 1,
      size_text: item.size_text || formatBytes(item.size),
    }))

  return layoutTreemap(children, 0, 0, WIDTH, HEIGHT)
})

function formatBytes(value = 0) {
  const size = Number(value || 0)
  if (!size) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1)
  return `${(size / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`
}

const breadcrumb = computed(() => {
  const chain = []
  if (root.value) chain.push({ name: root.value.name, index: -1 })
  stack.value.forEach((node, index) => chain.push({ name: node.name, index }))
  return chain
})

async function start() {
  if (!path.value.trim()) return
  root.value = null
  stack.value = []
  const started = await run(() => startTree({
    path: path.value.trim(),
    depth: depth.value,
    excludes: excludes.value,
  }))
  return started
}

/* 任务出结果就画树。用 watch 而不是 setInterval——
   原来那个 300ms 轮询在卸载时没清，会一直泄漏下去。 */
watch(
  () => job.value?.status,
  status => {
    const tree = job.value?.result?.tree
    if (status === 'done' && tree) root.value = tree
  },
  { immediate: true },
)

function drill(node) {
  if (!node.is_dir || !node.children?.length) return
  stack.value = [...stack.value, node]
}

function jumpTo(index) {
  stack.value = index < 0 ? [] : stack.value.slice(0, index + 1)
}

function goUp() {
  stack.value = stack.value.slice(0, -1)
}

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page space-map-page">

    <section class="app-card panel-card">
      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="统计路径"
        placeholder="例如：C:\\ 或 D:\\Media"
      />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">初始展开层数</span>
          <el-segmented v-model="depth" :options="depthOptions" />
        </div>
        <el-button type="primary" size="large" :loading="running" :icon="PieChart" @click="start">
          {{ running ? '正在统计' : '生成空间地图' }}
        </el-button>
      </div>

      <JobProgress v-if="job" :job="job" empty-title="先生成一次空间地图" :rollbackable="false" @cancel="cancel" />
    </section>

    <section v-if="root" class="app-card panel-card">
      <div class="panel-title-row">
        <div class="crumbs">
          <button
            v-for="crumb in breadcrumb"
            :key="`${crumb.index}-${crumb.name}`"
            type="button"
            class="crumb"
            @click="jumpTo(crumb.index)"
          >
            {{ crumb.name }}
          </button>
        </div>

        <div class="map-tools">
          <el-button v-if="stack.length" size="small" :icon="ArrowUp" @click="goUp">上一层</el-button>
          <span class="muted">{{ current?.children?.length || 0 }} 个子项 · {{ current?.size_text }}</span>
        </div>
      </div>

      <div v-if="blocks.length" class="treemap">
        <!-- 只有可下钻的目录块才是 button；文件/空目录渲染成 div，
             避免「可聚焦、有点击样式却点了没反应」的假控件（v22 审查 P0-4） -->
        <component
          :is="block.is_dir && block.children?.length ? 'button' : 'div'"
          v-for="block in blocks"
          :key="block.path"
          :type="block.is_dir && block.children?.length ? 'button' : null"
          class="treemap__block"
          :class="{ 'is-dir': block.is_dir && block.children?.length }"
          :style="{
            left: `${(block.x / WIDTH) * 100}%`,
            top: `${(block.y / HEIGHT) * 100}%`,
            width: `${(block.w / WIDTH) * 100}%`,
            height: `${(block.h / HEIGHT) * 100}%`,
            background: colorFor(block),
          }"
          :title="`${block.name} · ${block.size_text}`"
          @click="drill(block)"
        >
          <span v-if="block.w > 90 && block.h > 48" class="treemap__label">
            <strong>{{ block.name }}</strong>
            <small>{{ block.size_text }}</small>
          </span>
        </component>
      </div>

      <EmptyState
        v-else
        :icon="PieChart"
        title="这一层没有可显示的内容"
      />
    </section>
  </div>
</template>

<style scoped>
/* 宽屏下把操作区从“孤岛”拉回工作台尺度，减少左右留白；
   空态仍保持克制，不额外堆叠装饰内容。 */
.space-map-page {
  max-width: 1120px;
  padding-bottom: 12px;
}

.space-map-page > .app-card:first-child {
  min-height: 360px;
}

/* 穿透子组件必须 :deep()，否则 scoped 的 data-attribute 加不到
   JobProgress 内部元素，整条规则静默失效（v22 审查 P0-1） */
.space-map-page > .app-card:first-child :deep(.job-progress--empty) {
  min-height: 78px;
  display: grid;
  place-items: center;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.crumb {
  max-width: 200px;
  overflow: hidden;
  padding: 5px 11px;
  color: var(--ink-2);
  background: var(--inset);
  border: 1px solid var(--hairline);
  border-radius: 999px;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.crumb:hover {
  border-color: var(--hairline-strong);
}

.map-tools {
  display: flex;
  align-items: center;
  gap: 12px;
  white-space: nowrap;
}

.treemap {
  position: relative;
  height: 460px;
  background: var(--inset);
  border: 1px solid var(--hairline);
  border-radius: 14px;
  overflow: hidden;
}

.treemap__block {
  position: absolute;
  display: grid;
  place-items: center;
  padding: 6px;
  color: var(--surface);
  border: 1px solid var(--hairline-strong);
  border-radius: 4px;
  overflow: hidden;
  cursor: default;
  transition: filter var(--duration-quick) var(--ease);
}

.treemap__block.is-dir {
  cursor: pointer;
}

/* 只有可下钻的块给悬停反馈 */
.treemap__block.is-dir:hover {
  filter: brightness(1.08);
}

.treemap__label {
  display: grid;
  gap: 2px;
  text-align: center;
}

.treemap__label strong {
  font-size: 12px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.treemap__label small {
  font-size: 11px;
  opacity: 0.85;
}
</style>

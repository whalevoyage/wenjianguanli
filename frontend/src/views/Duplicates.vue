<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Coin, CopyDocument, Delete, DocumentCopy, Files } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import StatCard from '../components/StatCard.vue'
import { startClean, startDuplicateScan } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import JobProgress from '../components/JobProgress.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatBytes, formatCount } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const scan = useJob('duplicates.scan')
const clean = useJob('duplicates.clean')

const path = ref('')
const result = ref(null)
const activeGroups = ref([])
const selected = ref(new Set())

const metrics = computed(() => {
  if (!result.value) return []
  const data = result.value
  const avg = data.group_count ? Math.round(data.redundant_file_count / data.group_count) : 0
  return [
    {
      title: '已扫描文件',
      value: formatCount(data.scanned_files),
      note: `候选 ${formatCount(data.candidate_files)} · 按大小淘汰 ${formatCount(data.eliminated_by_size)}`,
      icon: Files,
      tone: 'blue',
    },
    {
      title: '重复组',
      value: formatCount(data.group_count),
      note: avg ? `平均每组 ${avg} 个副本` : '暂无重复',
      icon: CopyDocument,
      tone: 'blue',
    },
    {
      title: '重复副本',
      value: formatCount(data.redundant_file_count),
      note: '每组保留 1 个，其余可清理',
      icon: DocumentCopy,
      tone: 'blue',
    },
    {
      title: '可回收空间',
      value: data.reclaimable_size_text,
      note: '清理冗余副本后可腾出',
      icon: Coin,
      tone: 'blue',
    },
  ]
})

const selectionStats = computed(() => {
  let size = 0
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) {
      if (selected.value.has(file.path)) size += file.size
    }
  }
  return { count: selected.value.size, size, size_text: formatBytes(size) }
})

const groupSummary = computed(() => (result.value?.groups || []).map((group, index) => ({
  id: index + 1,
  count: group.count,
  size_text: group.size_text,
  redundant_text: formatBytes(group.size * Math.max(group.count - 1, 0)),
  hash: group.sha256_short,
  dirs: new Set((group.files || []).map(file => file.path?.replace(/[\\/][^\\/]+$/, ''))).size,
})))

function toggle(file) {
  const next = new Set(selected.value)
  next.has(file.path) ? next.delete(file.path) : next.add(file.path)
  selected.value = next
}

function selectAllSafe() {
  const next = new Set()
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) next.add(file.path)
  }
  selected.value = next
}

function clearSelection() {
  selected.value = new Set()
}

function selectGroup(group) {
  const next = new Set(selected.value)
  for (const file of group.redundant_files || []) next.add(file.path)
  selected.value = next
}

async function start() {
  if (!path.value.trim()) return
  result.value = null
  selected.value = new Set()
  await scan.run(() => startDuplicateScan({ path: path.value.trim(), excludes: excludes.value }))
}

async function cleanup() {
  if (!selected.value.size) return
  const items = []
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) {
      if (selected.value.has(file.path)) {
        items.push({ path: file.path, is_dir: false, rule: 'duplicate', size: file.size })
      }
    }
  }
  const job = await clean.run(() => startClean({ items, mode: 'isolate' }))
  if (job) {
    ElMessage.success(`已把 ${items.length} 个副本送进隔离区，可随时还原`)
    selected.value = new Set()
  }
}

watch(() => scan.job.value?.status, (status) => {
  if (status === 'done' && scan.job.value?.result) {
    result.value = scan.job.value.result
    activeGroups.value = result.value.groups?.length ? ['0'] : []
  }
})

watch(() => clean.job.value?.status, (status) => {
  if (status === 'done') start()
})

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="查找路径"
        placeholder="例如：D:\\ 或 D:\\Downloads"
      />

      <div class="row-actions">
        <el-button type="primary" size="large" :loading="scan.running.value" :icon="CopyDocument" @click="start">
          {{ scan.running.value ? '正在查重' : '开始查重' }}
        </el-button>
      </div>

      <JobProgress v-if="scan.job.value" :job="scan.job.value" empty-title="先跑一次查重" @cancel="scan.cancel" />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in metrics" :key="item.title" v-bind="item" />
      </section>

      <el-alert
        type="success"
        :closable="false"
        show-icon
        :title="`按大小直接淘汰了 ${formatCount(result.eliminated_by_size)} 个文件，只对 ${formatCount(result.candidate_files)} 个候选算了哈希`"
      />

      <section v-if="result.groups?.length" class="app-card panel-card">
        <div class="duplicate-summary">
          <div class="duplicate-summary__head"><span>组号</span><span>文件数</span><span>单文件大小</span><span>冗余占用</span><span>哈希前缀</span><span>目录数</span></div>
          <div v-for="group in groupSummary" :key="group.id" class="duplicate-summary__row"><strong>#{{ group.id }}</strong><span>{{ group.count }}</span><span>{{ group.size_text }}</span><span>{{ group.redundant_text }}</span><code>{{ group.hash }}</code><span>{{ group.dirs }}</span></div>
        </div>
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">重复组</h3>
          </div>
          <div class="group-tools">
            <el-button size="small" @click="selectAllSafe">全选副本</el-button>
            <el-button size="small" plain @click="clearSelection">清空</el-button>
          </div>
        </div>

        <el-collapse v-model="activeGroups">
          <el-collapse-item
            v-for="(group, index) in result.groups"
            :key="group.sha256"
            :name="String(index)"
          >
            <template #title>
              <div class="group-title">
                <strong>{{ group.count }} 个相同文件</strong>
                <span>{{ group.size_text }} / 个 · 可省 {{ formatBytes(group.size * (group.count - 1)) }}</span>
                <code>{{ group.sha256_short }}</code>
              </div>
            </template>

            <div class="file-list">
              <div
                v-for="(file, fileIndex) in group.files"
                :key="file.path"
                class="file-row"
                :class="{ 'is-kept': fileIndex === 0 }"
              >
                <el-checkbox
                  :model-value="fileIndex === 0 ? false : selected.has(file.path)"
                  :disabled="fileIndex === 0"
                  @update:model-value="fileIndex === 0 ? null : toggle(file)"
                />

                <div class="file-row__copy">
                  <strong>{{ file.name }}</strong>
                  <small class="path-text" :title="file.path">{{ file.path }}</small>
                </div>

                <span class="status-dot" :class="fileIndex === 0 ? 'status-dot--success' : 'status-dot--warning'">{{ fileIndex === 0 ? '保留' : '副本' }}</span>
              </div>

              <el-button size="small" plain @click="selectGroup(group)">勾选这一组的副本</el-button>
            </div>
          </el-collapse-item>
        </el-collapse>
      </section>

      <EmptyState
        v-else
        :icon="CopyDocument"
        title="没有发现重复文件"
      />

      <section v-if="selected.size" class="app-card panel-card sticky-bar">
        <div>
          <strong>已选 {{ selectionStats.count }} 个副本</strong>
          <span class="muted">预计释放 {{ selectionStats.size_text }}，移动后仍可从回收站还原</span>
        </div>
        <el-button type="primary" :icon="Delete" :loading="clean.running.value" @click="cleanup">
          送进隔离区
        </el-button>
      </section>

      <JobProgress v-if="clean.job.value" :job="clean.job.value" @cancel="clean.cancel" @rollback="clean.rollback" />
    </template>
  </div>
</template>

<style scoped>
.duplicate-summary { margin-bottom: 24px; overflow-x: auto; }
.duplicate-summary__head, .duplicate-summary__row { display: grid; grid-template-columns: .6fr .7fr 1fr 1fr 1fr .7fr; min-width: 0; gap: 14px; align-items: center; }
.duplicate-summary__head { padding: 0 12px 10px; color: var(--ink-2); font-size: var(--fs-micro); font-weight: 600; }
.duplicate-summary__row { min-height: 44px; padding: 8px 12px; border-top: 1px solid var(--hairline); font-size: var(--fs-note); }

@media (max-width: 640px) {
  .duplicate-summary__head,
  .duplicate-summary__row {
    grid-template-columns: .65fr 1fr .8fr;
    gap: 8px;
  }

  .duplicate-summary__head > :nth-child(2),
  .duplicate-summary__row > :nth-child(2),
  .duplicate-summary__head > :nth-child(4),
  .duplicate-summary__row > :nth-child(4),
  .duplicate-summary__head > :nth-child(5),
  .duplicate-summary__row > :nth-child(5) {
    display: none;
  }
}

/* 窄屏降级：隐去「哈希前缀 / 目录数」两个次要列，消灭卡片内横向滚动（v22 P0-3） */
@media (max-width: 640px) {
  .duplicate-summary__head,
  .duplicate-summary__row {
    grid-template-columns: 0.6fr 0.7fr 1fr 1fr;
    min-width: 0;
  }

  .duplicate-summary__head > :nth-child(5),
  .duplicate-summary__head > :nth-child(6),
  .duplicate-summary__row > :nth-child(5),
  .duplicate-summary__row > :nth-child(6) {
    display: none;
  }
}

.group-tools {
  display: flex;
  gap: 8px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.group-title strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.group-title span {
  color: var(--ink-3);
  font-size: 12px;
}

.group-title code {
  padding: 2px 8px;
  color: var(--ink-3);
  background: var(--inset);
  border-radius: 6px;
  font-size: 11px;
}

.file-list {
  display: grid;
  gap: 8px;
}

.file-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.file-row.is-kept {
  background: var(--success-soft);
  border-color: var(--success-soft);
}

.file-row__copy {
  min-width: 0;
}

.file-row__copy strong {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.file-row__copy small {
  display: block;
  margin-top: 4px;
}

.sticky-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  border-color: var(--primary);
}

.sticky-bar strong {
  display: block;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

.sticky-bar span {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}
</style>

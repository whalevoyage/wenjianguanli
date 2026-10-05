<script setup>
import { computed, onMounted, ref } from 'vue'
import { Clock, Files, FolderOpened, PieChart, Search } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import { startScan } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import DataTable from '../components/DataTable.vue'
import JobProgress from '../components/JobProgress.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatBytes, formatCount, formatDuration } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel, rollback } = useJob('scan')

const path = ref('')
const staleDays = ref(30)
const result = ref(null)

const staleOptions = [
  { label: '30 天未动', value: 30 },
  { label: '90 天未动', value: 90 },
  { label: '180 天未动', value: 180 },
]

const metrics = computed(() => {
  const data = result.value
  if (!data) return []
  const entries = (data.total_files || 0) + (data.total_dirs || 0)
  return [
    {
      title: '文件总数',
      value: formatCount(data.total_files),
      note: data.total_dirs
        ? `平均每目录 ${(data.total_files / data.total_dirs).toFixed(1)} 个`
        : '暂无目录',
      icon: Files,
      tone: 'blue',
    },
    {
      title: '文件夹数量',
      value: formatCount(data.total_dirs),
      note: entries ? `占全部条目的 ${((data.total_dirs / entries) * 100).toFixed(1)}%` : '暂无记录',
      icon: FolderOpened,
      tone: 'blue',
    },
    {
      title: '总占用空间',
      value: data.total_size_text,
      note: data.total_files ? `平均每文件 ${formatBytes(data.total_size / data.total_files)}` : '暂无文件',
      icon: PieChart,
      tone: 'blue',
    },
    {
      title: '扫描耗时',
      value: formatDuration(data.duration_seconds),
      note: data.duration_seconds
        ? `平均每秒 ${formatCount(Math.round(entries / data.duration_seconds))} 个条目`
        : '扫描 + 统计',
      icon: Clock,
      tone: 'blue',
    },
  ]
})

const staleStats = computed(() => {
  if (!result.value) return []
  return [
    { label: `${result.value.stale_days} 天以上未修改`, value: formatCount(result.value.stale_file_count) },
    { label: '这部分占用', value: result.value.reclaimable_size_text },
  ]
})

const categoryChart = computed(() => {
  const items = result.value?.category_stats || []
  const max = Math.max(...items.map(item => Number(item.size || 0)), 1)
  return items.map(item => ({
    ...item,
    width: `${Math.max((Number(item.size || 0) / max) * 100, 3)}%`,
  }))
})

const largeColumns = [
  { prop: 'rank', label: '排名', width: 70, align: 'right' },
  { prop: 'name', label: '文件名', minWidth: 220 },
  { prop: 'directory', label: '所在目录', minWidth: 300 },
  { prop: 'size_text', label: '大小', width: 110, align: 'right' },
  { prop: 'type', label: '类型', width: 100 },
  { prop: 'modified_time', label: '修改时间', width: 150 },
]

const largeRows = computed(() => (result.value?.large_files || []).map((file, index) => ({
  ...file,
  rank: index + 1,
  directory: file.path ? file.path.replace(/[\\/][^\\/]+$/, '') : '—',
})))

const categoryColumns = [
  { prop: 'label', label: '类型', minWidth: 130 },
  { prop: 'count', label: '文件数', width: 100, align: 'right' },
  { prop: 'size_text', label: '总大小', width: 120, align: 'right' },
  { prop: 'percentage', label: '占比', width: 110, align: 'right' },
]

async function start() {
  if (!path.value.trim()) return
  result.value = null
  await run(() => startScan({
    path: path.value.trim(),
    excludes: excludes.value,
    stale_days: staleDays.value,
  }))
}

async function refreshResult() {
  if (job.value?.status === 'done') result.value = job.value.result
}

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
        label="扫描路径"
        placeholder="例如：D:\\ 或 D:\\Workspace"
      />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">长期未动阈值</span>
          <el-segmented v-model="staleDays" :options="staleOptions" />
        </div>

        <el-button type="primary" size="large" :loading="running" :icon="Search" @click="start">
          {{ running ? '正在扫描' : '开始扫描' }}
        </el-button>
      </div>

      <el-alert
        class="panel-note"
        type="info"
        :closable="false"
        show-icon
        title="扫描只读取目录信息，不上传任何文件内容。碰到桌面和系统目录会自动跳过。"
      />
    </section>

    <section v-if="job" class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">任务进度</h3>
        </div>
        <el-button v-if="job?.status === 'done'" text type="primary" @click="refreshResult">载入结果</el-button>
      </div>

      <JobProgress :job="job" empty-title="先运行一次扫描" @cancel="cancel" @rollback="rollback" />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in metrics" :key="item.title" v-bind="item" />
      </section>

      <el-alert
        v-if="result.warnings?.length"
        type="warning"
        :closable="false"
        show-icon
        :title="`有 ${result.warnings.length} 个项目无法读取（权限不足、文件被占用或路径异常）`"
      />

      <section class="section-grid">
        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">类型分布</h3>
            </div>
          </div>

          <DataTable v-if="categoryChart.length" :columns="categoryColumns" :data="categoryChart.slice(0, 10)" empty-text="没有统计到文件">
            <template #count="{ value }"><span class="tnum">{{ formatCount(value) }}</span></template>
            <template #percentage="{ value }"><span class="tnum">{{ value }}%</span></template>
          </DataTable>

          <EmptyState
            v-else
            :icon="Search"
            title="没有统计到文件"
          />
        </article>

        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">长期未动</h3>
            </div>
          </div>

          <div class="brief-list">
            <div v-for="item in staleStats" :key="item.label" class="brief-item">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
          </div>

          <div v-if="result.stale_files?.length" class="stale-list">
            <div v-for="file in result.stale_files.slice(0, 8)" :key="file.path" class="stale-item">
              <strong>{{ file.name }}</strong>
              <span>{{ file.size_text }} · {{ file.modified_time }}</span>
              <small class="path-text" :title="file.path">{{ file.path }}</small>
            </div>
          </div>

          <EmptyState
            v-else
            :icon="Clock"
            title="没有长期未动的文件"
          />
        </article>
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">大文件</h3>
            <p class="panel-subtitle">TOP 10 · {{ formatCount(result.large_file_count) }} 个</p>
          </div>
        </div>

        <DataTable :columns="largeColumns" :data="largeRows.slice(0, 10)" empty-text="没有符合条件的大文件" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.brief-list,
.stale-list {
  display: grid;
  gap: 10px;
}

.brief-item,
.stale-item {
  padding: 14px 16px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 14px;
}

.brief-item span {
  color: var(--ink-3);
  font-size: 12px;
}

.brief-item span,
.brief-item strong,
.stale-item strong,
.stale-item span,
.stale-item small {
  display: block;
}

.brief-item strong {
  margin-top: 6px;
  color: var(--ink);
  font-size: 20px;
  font-weight: 600;
}

.stale-item strong {
  color: var(--ink);
  font-size: 13px;
}

.stale-item span {
  margin-top: 4px;
  color: var(--ink-3);
  font-size: 12px;
}

.stale-item small {
  margin-top: 5px;
}
</style>

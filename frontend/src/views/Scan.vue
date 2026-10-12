<script setup>
import { computed, onMounted, ref } from 'vue'
import { Clock, Delete, Files, FolderOpened, PieChart, Search } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import { startScan, saveScanSnapshot, listScanSnapshots, compareScanSnapshots } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import DataTable from '../components/DataTable.vue'
import JobProgress from '../components/JobProgress.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import HandoffNote from '../components/HandoffNote.vue'
import NextSteps from '../components/NextSteps.vue'
import { useHandoffTarget } from '../composables/useHandoff'
import { useSelectionClean } from '../composables/useSelectionClean'
import { formatBytes, formatCount, formatDuration } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel, rollback } = useJob('scan')

const paths = ref([])
/* 跨页接力：从别的页面带着目录过来时直接追加；也能从这里把目录带给别的页面 */
const { handed, from } = useHandoffTarget(paths, { multiple: true })

/** 反向接力：用同一个目录去别的能力，不用再选一次 */
const nextSteps = [
  { name: 'duplicates', label: '查重' },
  { name: 'junk', label: '清理' },
  { name: 'backup', label: '备份' },
  { name: 'space', label: '空间地图' },
]
const primaryPath = computed(() => paths.value[0] || '')

const staleDays = ref(30)
const result = ref(null)

/* 处理扫描结果：勾选 → 送进隔离区 / 送进系统回收站。
   复用全站清理链路，不新增"直接删文件"的接口。 */
const { selected, cleanMode, clean, modeOptions, toggle, setAll, clear, submit } = useSelectionClean('scan.clean')

/* 可勾选的项：大文件（TOP 10）+ 长期未动（前 8）—— 与界面上展示的一致 */
const pickableRows = computed(() => [
  ...(result.value?.large_files || []).slice(0, 10),
  ...(result.value?.stale_files || []).slice(0, 8),
])
const pickablePaths = computed(() => pickableRows.value.map(row => row.path))
const allPicked = computed(() =>
  pickablePaths.value.length > 0 && pickablePaths.value.every(path => selected.value.has(path)))
const selectionStats = computed(() => {
  const size = new Map(pickableRows.value.map(row => [row.path, row.size || 0]))
  let total = 0
  for (const path of selected.value) total += size.get(path) || 0
  return { count: selected.value.size, size_text: formatBytes(total) }
})

function togglePickAll() {
  if (allPicked.value) clear()
  else setAll(pickablePaths.value)
}

function runClean() {
  const byPath = new Map(pickableRows.value.map(row => [row.path, row]))
  const items = [...selected.value].map(path => ({
    path, is_dir: false, rule: 'scan', size: byPath.get(path)?.size || 0,
  }))
  submit(items)
}

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
  { prop: '_pick', label: '选', width: 52, align: 'center' },
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
  const targets = paths.value.map(p => p.trim()).filter(Boolean)
  if (!targets.length) return
  result.value = null
  await run(() => startScan({
    paths: targets,
    excludes: excludes.value,
    stale_days: staleDays.value,
  }))
}

async function refreshResult() {
  if (job.value?.status === 'done') {
    result.value = job.value.result
    diff.value = null
    await loadSnapshots()
  }
}

/* ---- 扫描快照（B-ScanDiff）：保存 / 列出 / 对比 ---- */
const snapshots = ref([])
const baseId = ref('')
const targetId = ref('')
const diff = ref(null)

async function loadSnapshots() {
  if (!result.value) return
  try {
    const data = await listScanSnapshots(result.value.root)
    snapshots.value = data.snapshots || []
    const ids = snapshots.value.map(s => s.id)
    if (ids.length >= 2) {
      targetId.value = ids[ids.length - 1]
      baseId.value = ids[ids.length - 2]
    } else if (ids.length === 1) {
      targetId.value = ids[ids.length - 1]
      baseId.value = ''
    } else {
      baseId.value = ''
      targetId.value = ''
    }
  } catch {
    snapshots.value = []
  }
}

async function saveCurrentSnapshot() {
  if (!result.value) return
  await saveScanSnapshot({ root: result.value.root, result: result.value })
  diff.value = null
  await loadSnapshots()
}

const canCompare = computed(
  () => baseId.value && targetId.value && baseId.value !== targetId.value,
)

async function runCompare() {
  if (!canCompare.value || !result.value) return
  const data = await compareScanSnapshots({
    root: result.value.root,
    base_id: baseId.value,
    target_id: targetId.value,
  })
  diff.value = data
}

function fmtDelta(n) {
  if (n > 0) return `+${n}`
  if (n < 0) return `${n}`
  return '0'
}

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <HandoffNote v-if="handed" :path="handed" :from="from" @dismiss="handed = ''" />


      <TargetPicker
        :multiple="true"
        v-model:paths="paths"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="扫描路径"
        placeholder="可多选：分别添加多个目录一起扫描"
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

      <NextSteps :path="primaryPath" origin="scan" :steps="nextSteps" />

      <el-alert
        v-if="result.warnings?.length"
        type="warning"
        :closable="false"
        show-icon
        :title="`有 ${result.warnings.length} 个项目无法读取（权限不足、文件被占用或路径异常）`"
      />

      <el-alert
        v-if="result.notes?.length"
        type="info"
        :closable="false"
        show-icon
        :title="`已自动处理来源：${result.notes.join('；')}`"
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
              <div class="stale-item__head">
                <el-checkbox
                  :model-value="selected.has(file.path)"
                  @update:model-value="toggle(file.path)"
                />
                <strong>{{ file.name }}</strong>
              </div>
              <span>{{ file.size_text }} · {{ file.modified_time }}</span>
              <small class="path-text" :title="file.path">{{ file.path }}</small>
              <small class="muted">{{ file.source }}</small>
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

        <DataTable :columns="largeColumns" :data="largeRows.slice(0, 10)" empty-text="没有符合条件的大文件">
          <template #_pick="{ row }">
            <el-checkbox
              :model-value="selected.has(row.path)"
              @update:model-value="toggle(row.path)"
            />
          </template>
        </DataTable>
      </section>

      <section v-if="selected.size" class="app-card panel-card sticky-bar">
        <div class="sticky-bar__info">
          <strong>已选 {{ selectionStats.count }} 项</strong>
          <span class="muted">预计释放 {{ selectionStats.size_text }}</span>
          <el-segmented v-model="cleanMode" :options="modeOptions" size="small" />
        </div>
        <div class="sticky-bar__actions">
          <el-button size="small" plain @click="togglePickAll">{{ allPicked ? '清空' : '全选' }}</el-button>
          <el-button type="primary" :icon="Delete" :loading="clean.running.value" @click="runClean">
            执行清理
          </el-button>
        </div>
      </section>

      <JobProgress v-if="clean.job.value" :job="clean.job.value" @cancel="clean.cancel" @rollback="clean.rollback" />

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">历史快照</h3>
            <p class="panel-subtitle">保存扫描结果，对比两次看看哪里变大了</p>
          </div>
          <el-button size="small" :disabled="!result" @click="saveCurrentSnapshot">保存当前为快照</el-button>
        </div>

        <div v-if="snapshots.length" class="snapshot-controls">
          <el-select v-model="baseId" placeholder="基准" size="small" class="snap-select">
            <el-option v-for="s in snapshots" :key="s.id" :label="s.time" :value="s.id" />
          </el-select>
          <span class="muted">→</span>
          <el-select v-model="targetId" placeholder="目标" size="small" class="snap-select">
            <el-option v-for="s in snapshots" :key="s.id" :label="s.time" :value="s.id" />
          </el-select>
          <el-button size="small" type="primary" :disabled="!canCompare" @click="runCompare">对比</el-button>
        </div>
        <p v-else class="muted small">还没有快照。先点「保存当前为快照」存一份，下次扫描后再存一份就能对比。</p>

        <template v-if="diff">
          <el-alert
            v-if="diff.scope_mismatch"
            type="warning"
            :closable="false"
            show-icon
            title="两次扫描的范围不一致（排除规则或深度不同），下面的增减仅供参考"
          />

          <div class="stats-grid diff-summary">
            <StatCard
              title="总量变化"
              :value="diff.total_size_delta_text"
              :note="`文件 ${fmtDelta(diff.total_files_delta)} · 目录 ${fmtDelta(diff.total_dirs_delta)}`"
              :icon="PieChart"
              tone="blue"
            />
            <StatCard title="基准" :value="diff.base.total_size_text" :note="diff.base.time" :icon="Clock" tone="blue" />
            <StatCard title="目标" :value="diff.target.total_size_text" :note="diff.target.time" :icon="Clock" tone="blue" />
          </div>

          <div class="section-grid">
            <article v-if="diff.added.length" class="app-card panel-card">
              <h4 class="diff-title">新增目录</h4>
              <div class="diff-list">
                <div v-for="item in diff.added" :key="item.path" class="diff-item">
                  <span class="diff-path" :title="item.path">{{ item.path }}</span>
                  <strong class="diff-size up">+{{ item.size_text }}</strong>
                </div>
              </div>
            </article>

            <article v-if="diff.removed.length" class="app-card panel-card">
              <h4 class="diff-title">消失目录</h4>
              <div class="diff-list">
                <div v-for="item in diff.removed" :key="item.path" class="diff-item">
                  <span class="diff-path" :title="item.path">{{ item.path }}</span>
                  <strong class="diff-size down">-{{ item.size_text }}</strong>
                </div>
              </div>
            </article>

            <article v-if="diff.grown.length" class="app-card panel-card">
              <h4 class="diff-title">变大目录</h4>
              <div class="diff-list">
                <div v-for="item in diff.grown" :key="item.path" class="diff-item">
                  <span class="diff-path" :title="item.path">{{ item.path }}</span>
                  <strong class="diff-size up">{{ item.delta_text }}</strong>
                </div>
              </div>
            </article>

            <article v-if="diff.shrunk.length" class="app-card panel-card">
              <h4 class="diff-title">变小目录</h4>
              <div class="diff-list">
                <div v-for="item in diff.shrunk" :key="item.path" class="diff-item">
                  <span class="diff-path" :title="item.path">{{ item.path }}</span>
                  <strong class="diff-size down">{{ item.delta_text }}</strong>
                </div>
              </div>
            </article>
          </div>
        </template>
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

.snapshot-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0 14px;
}

.snap-select {
  width: 200px;
}

.muted {
  color: var(--ink-3);
  font-size: 12px;
}

.small {
  font-size: 12px;
}

.diff-summary {
  margin: 14px 0;
}

.diff-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2);
}

.diff-list {
  display: grid;
  gap: 8px;
  max-height: 260px;
  overflow: auto;
}

.diff-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.diff-path {
  font-size: 12px;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diff-size {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.diff-size.up {
  color: var(--danger, #e5484d);
}

.diff-size.down {
  color: var(--success, #30a46c);
}

/* 长期未动项：勾选框与文件名同一行 */
.stale-item__head {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 结果处理条：与查重 / 垃圾页一致 */
.sticky-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  border-color: var(--primary);
}

.sticky-bar__info strong {
  display: block;
}

.sticky-bar__info .muted {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}

.sticky-bar__info :deep(.el-segmented) {
  margin-top: 8px;
}

.sticky-bar__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>

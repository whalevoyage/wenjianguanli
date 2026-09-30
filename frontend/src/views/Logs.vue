<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { getLogs } from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import PageHeader from '../components/PageHeader.vue'
import { formatBytes, formatCount, formatDateTime } from '../utils/formatters'

const typeFilter = ref('全部')
const resultFilter = ref('全部')
const keyword = ref('')
const loading = ref(false)
const rawLogs = ref([])

const actionLabels = {
  scan: '文件扫描',
  backup: '项目备份',
  duplicate_scan: '重复检测',
  clean: '安全清理',
  organize: '文件整理',
  undo: '撤销整理',
  export: '报告导出',
  ai_scan_advice: 'AI 分析',
}

const logRows = computed(() =>
  [...rawLogs.value].reverse().map((item, index) => ({
    id: `${item.time}-${index}`,
    time: formatDateTime(item.time),
    type: actionLabels[item.type] || item.type || '未知操作',
    path: item.root || item.source || item.path || item.zip_path || item.target || '—',
    result: item.success === false ? '失败' : item.errors?.length ? '部分完成' : '成功',
    note: buildNote(item),
  })),
)

const typeOptions = computed(() => ['全部', ...new Set(logRows.value.map(item => item.type))])

const filteredData = computed(() =>
  logRows.value.filter(item =>
    (typeFilter.value === '全部' || item.type === typeFilter.value) &&
    (resultFilter.value === '全部' || item.result === resultFilter.value) &&
    (!keyword.value || `${item.path}${item.note}`.toLowerCase().includes(keyword.value.toLowerCase())),
  ),
)

const successRate = computed(() => {
  if (!logRows.value.length) return '—'
  return `${((logRows.value.filter(item => item.result === '成功').length / logRows.value.length) * 100).toFixed(1)}%`
})

const actionChart = computed(() => {
  const counter = {}

  for (const item of logRows.value) {
    counter[item.type] = (counter[item.type] || 0) + 1
  }

  const items = Object.entries(counter)
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)

  const max = Math.max(...items.map(item => item.count), 1)

  return items.map(item => ({
    ...item,
    width: `${Math.max((item.count / max) * 100, 8)}%`,
  }))
})

const columns = [
  { prop: 'time', label: '操作时间', width: 190 },
  { prop: 'type', label: '操作类型', width: 110 },
  { prop: 'path', label: '路径', minWidth: 320 },
  { prop: 'result', label: '结果', width: 96 },
  { prop: 'note', label: '备注', minWidth: 240 },
]

function buildNote(item) {
  if (item.type === 'scan') return `${formatCount(item.total_files)} 个文件，${formatBytes(item.total_size)}`
  if (item.type === 'backup') return `${formatCount(item.file_count)} 个文件，${formatBytes(item.size)}`
  if (item.type === 'export') return `报告导出 · 基于 ${formatCount(item.total_files)} 个文件`
  if (item.type === 'organize') return `整理 ${formatCount(item.moved_count)} 个文件`
  if (item.type === 'undo') return `恢复 ${formatCount(item.restored_count)} 个文件`
  if (item.type === 'clean') return `移动 ${formatCount(item.moved_count)} 个项目到恢复区`
  if (item.type === 'duplicate_scan') return `发现 ${formatCount(item.groups)} 个重复组，可释放 ${formatBytes(item.reclaimable_size)}`
  if (item.type === 'ai_scan_advice') return `DeepSeek ${item.focus || '综合判断'} · ${formatCount(item.total_tokens)} tokens`
  return item.error_msg || '已记录'
}

async function loadLogs(showMessage = false) {
  loading.value = true

  try {
    rawLogs.value = await getLogs()
    if (showMessage) ElMessage.success('日志已刷新')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function typeTag(type) {
  return ({
    文件扫描: 'primary',
    项目备份: 'success',
    重复检测: 'warning',
    安全清理: 'danger',
    文件整理: 'info',
    撤销整理: '',
    报告导出: 'info',
    'AI 分析': 'success',
  })[type] || ''
}

onMounted(() => loadLogs())
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="操作日志"
      description="所有真实动作都在这里回看。先缩小范围，再看细节，能更快定位某次扫描、整理、备份或 AI 分析。"
      eyebrow="审计"
    >
      <template #meta>
        <div class="meta-pill">来源：activity.jsonl</div>
        <div class="meta-pill">最多显示最近 50 条</div>
      </template>
      <template #actions>
        <el-button :loading="loading" @click="loadLogs(true)">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <section class="metric-strip">
      <div class="metric-strip__item">
        <div class="metric-strip__label">记录数</div>
        <div class="metric-strip__value">{{ logRows.length }}</div>
      </div>
      <div class="metric-strip__item">
        <div class="metric-strip__label">成功率</div>
        <div class="metric-strip__value">{{ successRate }}</div>
      </div>
      <div class="metric-strip__item">
        <div class="metric-strip__label">扫描次数</div>
        <div class="metric-strip__value">{{ logRows.filter(item => item.type === '文件扫描').length }}</div>
      </div>
      <div class="metric-strip__item">
        <div class="metric-strip__label">AI 分析次数</div>
        <div class="metric-strip__value">{{ logRows.filter(item => item.type === 'AI 分析').length }}</div>
      </div>
    </section>

    <section class="section-grid">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">动作分布</h3>
            <p class="panel-subtitle">先看最近日志里哪几类动作最常出现。</p>
          </div>
        </div>

        <div v-if="actionChart.length" class="chart-list">
          <div v-for="item in actionChart" :key="item.label" class="chart-item">
            <div class="chart-item__head">
              <strong>{{ item.label }}</strong>
              <span>{{ item.count }} 次</span>
            </div>
            <div class="chart-bar">
              <div class="chart-bar__fill" :style="{ width: item.width }"></div>
            </div>
          </div>
        </div>

        <el-empty v-else description="暂无日志数据" :image-size="80" />
      </article>

      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">筛选</h3>
            <p class="panel-subtitle">先筛类型和结果，再搜路径或备注，定位会更快。</p>
          </div>
          <span class="filter-count">当前 {{ filteredData.length }} 条</span>
        </div>

        <div class="filters">
          <el-select v-model="typeFilter">
            <el-option
              v-for="item in typeOptions"
              :key="item"
              :label="item === '全部' ? '全部类型' : item"
              :value="item"
            />
          </el-select>

          <el-select v-model="resultFilter">
            <el-option v-for="item in ['全部', '成功', '部分完成', '失败']" :key="item" :label="item === '全部' ? '全部结果' : item" :value="item" />
          </el-select>

          <el-input v-model="keyword" clearable placeholder="搜索路径或备注" class="filters__search">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </article>
    </section>

    <section class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">日志明细</h3>
          <p class="panel-subtitle">保留每一次真实操作的时间、路径、结果和简要说明。</p>
        </div>
      </div>

      <DataTable v-loading="loading" :columns="columns" :data="filteredData" empty-text="暂无操作日志">
        <template #type="{ value }">
          <el-tag :type="typeTag(value)" size="small" effect="light">{{ value }}</el-tag>
        </template>
        <template #path="{ value }"><span class="path-text" :title="value">{{ value }}</span></template>
        <template #result="{ value }">
          <el-tag :type="value === '失败' ? 'danger' : value === '部分完成' ? 'warning' : 'success'" size="small" effect="plain">
            {{ value }}
          </el-tag>
        </template>
      </DataTable>
    </section>
  </div>
</template>

<style scoped>
.meta-pill {
  padding: 8px 12px;
  color: var(--text-secondary);
  background: #f7f9fb;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
}

.filter-count {
  color: var(--text-muted);
  font-size: 12px;
}

.filters {
  display: grid;
  grid-template-columns: 180px 150px minmax(0, 1fr);
  gap: 10px;
}

.filters__search {
  max-width: 360px;
}

.chart-list {
  display: grid;
  gap: 10px;
}

.chart-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.chart-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.chart-item__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.chart-item__head span {
  color: var(--text-muted);
  font-size: 12px;
}

.chart-bar {
  width: 100%;
  height: 8px;
  background: #edf2f6;
  border-radius: 999px;
  overflow: hidden;
}

.chart-bar__fill {
  height: 100%;
  background: var(--primary);
  border-radius: 999px;
}

@media (max-width: 900px) {
  .filters {
    grid-template-columns: 1fr;
  }

  .filters__search {
    max-width: none;
  }
}
</style>

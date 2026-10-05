<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CircleCheck,
  Collection,
  Download,
  Filter,
  Menu,
  Refresh,
  Search,
  Tickets,
} from '@element-plus/icons-vue'
import { exportLogs, getLogs } from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import StatCard from '../components/StatCard.vue'
import { formatCount, formatDateTime, formatShortTime } from '../utils/formatters'

const TYPE_LABELS = {
  scan: '扫描分析',
  tree: '空间地图',
  virtual: '整理预览',
  projects: '项目雷达',
  duplicate: '重复文件',
  junk: '垃圾文件',
  migrate: '跨盘迁移',
  clean: '清理',
  backup: '项目备份',
}

const raw = ref([])
const source = ref('logs.json')
const loading = ref(false)
const typeFilter = ref('全部')
const resultFilter = ref('全部')
const keyword = ref('')
const exporting = ref(false)

const rows = computed(() => [...raw.value].reverse().map((item, index) => ({
  id: `${item.time}-${index}`,
  time: formatShortTime(item.time),
  fullTime: formatDateTime(item.time),
  type: TYPE_LABELS[item.type] || item.type || '未知类型',
  path: item.path || '—',
  result: item.result || '成功',
  note: item.note && !/[\\/]/.test(item.note) ? item.note : '—',
})))

const typeOptions = computed(() => ['全部', ...new Set(rows.value.map(item => item.type))])

const filtered = computed(() => rows.value.filter(item => (
  (typeFilter.value === '全部' || item.type === typeFilter.value)
  && (resultFilter.value === '全部' || item.result === resultFilter.value)
  && (!keyword.value || `${item.path}${item.note}`.toLowerCase().includes(keyword.value.toLowerCase()))
)))

const successRate = computed(() => {
  if (!rows.value.length) return '—'
  const ok = rows.value.filter(item => item.result === '成功').length
  return `${((ok / rows.value.length) * 100).toFixed(1)}%`
})

const okCount = computed(() => rows.value.filter(item => item.result === '成功').length)

/* 指标卡：与首页共用 StatCard（渐变图标 + 大数字 + 底部注释）。
   注释只放「补充信息」，不复述上面的大数字。 */
const cards = computed(() => {
  const total = rows.value.length
  const shown = filtered.value.length
  const types = typeOptions.value.slice(1)
  return [
    {
      title: '记录数',
      value: formatCount(total),
      note: `来源 ${source.value}`,
      icon: Tickets,
      tone: 'blue',
    },
    {
      title: '成功率',
      value: successRate.value,
      note: total
        ? `成功 ${formatCount(okCount.value)} · 其它 ${formatCount(total - okCount.value)}`
        : '暂无记录',
      icon: CircleCheck,
      tone: 'blue',
    },
    {
      title: '当前筛选',
      value: formatCount(shown),
      note: shown === total ? '未启用筛选' : `已过滤掉 ${formatCount(total - shown)} 条`,
      icon: Filter,
      tone: 'blue',
    },
    {
      title: '操作类型',
      value: types.length,
      note: types.slice(0, 2).join(' · ') || '暂无记录',
      icon: Menu,
      tone: 'blue',
    },
  ]
})

const columns = [
  { prop: 'time', label: '操作时间', width: 130 },
  { prop: 'type', label: '操作类型', width: 120 },
  { prop: 'path', label: '路径', minWidth: 300 },
  { prop: 'result', label: '结果', width: 100 },
  { prop: 'note', label: '备注', minWidth: 220 },
]

function resultTag(result) {
  if (result === '成功') return 'success'
  if (result === '失败') return 'danger'
  if (result === '已取消') return 'info'
  return 'warning'
}

async function load(showMessage = false) {
  loading.value = true
  try {
    const data = await getLogs()
    raw.value = data.logs || []
    source.value = data.source || 'logs.json'
    if (showMessage) ElMessage.success('日志已刷新')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function exportFile() {
  exporting.value = true
  try {
    const result = await exportLogs()
    ElMessage.success(`已导出 ${result.count} 条到 ${result.path}`)
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    exporting.value = false
  }
}

onMounted(() => load())
</script>

<template>
  <div class="section-stack">

    <section class="stats-grid">
      <StatCard v-for="item in cards" :key="item.title" v-bind="item" />
    </section>

    <div class="filters-row">
      <div class="filters">
        <el-select v-model="typeFilter">
          <el-option v-for="item in typeOptions" :key="item" :label="item === '全部' ? '全部类型' : item" :value="item" />
        </el-select>

        <el-select v-model="resultFilter">
          <el-option v-for="item in ['全部', '成功', '失败', '已取消', '已回滚']" :key="item"
                     :label="item === '全部' ? '全部结果' : item" :value="item" />
        </el-select>

        <el-input v-model="keyword" clearable placeholder="搜索路径或备注">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
      </div>

      <!-- 页头去掉后，原本挂在页头上的操作落到筛选行右端 -->
      <div class="page-actions">
        <el-button :icon="Download" :loading="exporting" @click="exportFile">导出</el-button>
        <el-button :icon="Refresh" @click="load(true)">刷新</el-button>
      </div>
    </div>

    <section class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">日志明细</h3>
        </div>
        <span class="panel-count">来源 {{ source }}</span>
      </div>

      <DataTable
        v-loading="loading"
        :columns="columns"
        :data="filtered"
        empty-text="暂无操作日志"
        :empty-icon="Collection"
      >
        <template #time="{ row }"><span :title="row.fullTime">{{ row.time }}</span></template>
        <template #type="{ value }">
          <el-tag size="small" effect="light" type="info">{{ value }}</el-tag>
        </template>
        <template #path="{ value }"><span class="path-text" :title="value">{{ value }}</span></template>
        <template #result="{ value }">
          <span class="status-dot" :class="`status-dot--${resultTag(value) === 'danger' ? 'danger' : resultTag(value) === 'success' ? 'success' : 'warning'}`">{{ value }}</span>
        </template>
      </DataTable>
    </section>
  </div>
</template>

<style scoped>
.filters {
  display: grid;
  grid-template-columns: 180px 150px minmax(0, 1fr);
  gap: 10px;
}

.filters-row { display: flex; align-items: center; justify-content: space-between; min-height: 40px; }

@media (max-width: 900px) {
  .filters {
    grid-template-columns: 1fr;
  }
}
</style>

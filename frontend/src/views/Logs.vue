<script setup>
import { computed, onMounted, ref, watch } from 'vue'
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
import { exportLogs, getLogs, getReclaimedStats } from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import StatCard from '../components/StatCard.vue'
import { formatCount, formatDateTime, formatShortTime } from '../utils/formatters'

const TYPE_LABELS = {
  scan: '扫描分析',
  tree: '空间地图',
  index: '内容索引',
  virtual: '整理预览',
  projects: '项目雷达',
  duplicate: '重复文件',
  similar: '相似图片',
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

/* 分页。日志动辄几百条，一次性全塞给 el-table（464 行 × 5 列，其中 4 列还挂自定义 slot）
   会把主线程占住一秒多（实测 longtask 单次 646ms）—— 切进来时画面会白一下。
   分页后首屏只渲染一页，渲染开销降到几十毫秒。 */
const page = ref(1)
const pageSize = ref(50)

const paged = computed(() => {
  const list = filtered.value
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  const current = Math.min(page.value, maxPage)
  return list.slice((current - 1) * pageSize.value, current * pageSize.value)
})

/* 换筛选条件或搜索词后回到第一页，否则会停在一个已经越界的页码上 */
watch([typeFilter, resultFilter, keyword], () => {
  page.value = 1
})

const successRate = computed(() => {
  if (!rows.value.length) return '—'
  const ok = rows.value.filter(item => item.result === '成功').length
  return `${((ok / rows.value.length) * 100).toFixed(1)}%`
})

const okCount = computed(() => rows.value.filter(item => item.result === '成功').length)

/* 指标卡：与首页共用 StatCard（渐变图标 + 大数字 + 底部注释）。
   注释只放「补充信息」，不复述上面的大数字。 */
/* 累计腾出 —— 自用工具最需要的那个数字：不是"做了什么"，而是"省了多少"。
   口径见后端 reclaimed_stats()：文件先进隔离区，清空隔离区后才真正释放磁盘空间。 */
const reclaimed = ref(null)

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
      title: '累计腾出',
      value: reclaimed.value?.total_text || '0 B',
      note: reclaimed.value?.count
        ? `${reclaimed.value.count} 次清理/迁移 · 今天 ${reclaimed.value.today_text}`
        : '还没有清理过',
      icon: Collection,
      tone: 'green',
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

/* 让出两帧：先把「骨架 + 遮罩」交给浏览器画出来，再灌数据。
   离场页的 opacity 淡出跑在合成器线程上、不受主线程阻塞影响，会一路推进到全透明；
   而新页面的图层若还没提交绘制，屏幕上就只剩空白底色 —— 那正是「白屏一下」的来源。
   让出一帧之后遮罩已经画在屏幕上，长任务期间看到的是遮罩，而不是空白。 */
function nextPaint() {
  return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
}

async function loadReclaimed() {
  try {
    reclaimed.value = await getReclaimedStats()
  } catch {
    /* 拿不到就显示 0，不拦着日志列表 */
  }
}

async function load(showMessage = false) {
  loading.value = true
  try {
    const data = await getLogs()
    await nextPaint()
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

onMounted(() => {
  load()
  loadReclaimed()
})
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
        :data="paged"
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

      <!-- 分页条：左「共 N 条」、右翻页，上缘一条 hairline 与表格拉开层次。
           条数文案自己写，不用 pagination 的 total 版式 —— 全站没配 Element Plus 中文
           locale，那个版式会渲染成英文「Total 464」，jumper 也是「Go to」。 -->
      <div v-if="filtered.length" class="logs-pager">
        <span class="logs-pager__total">共 {{ formatCount(filtered.length) }} 条</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="filtered.length"
          layout="prev, pager, next"
          background
        />
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 分页条：上缘一条 hairline 与表格分开 */
.logs-pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--hairline);
}

.logs-pager__total {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

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

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Clock,
  Coin,
  DataAnalysis,
  Document,
  FolderOpened,
  Operation,
} from '@element-plus/icons-vue'
import { getCachedBackup, getCachedScan, getLogs } from '../api/filepilot'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import { basename, formatBytes, formatCount, formatDateTime } from '../utils/formatters'

const cachedScan = ref(getCachedScan())
const cachedBackup = ref(getCachedBackup())
const logs = ref([])
const loading = ref(false)
const loadError = ref('')

const quickActions = [
  { label: '开始扫描', note: '扫描目录与大文件', path: '/scan' },
  { label: '整理文件', note: '先预览再执行', path: '/organizer' },
  { label: '处理重复', note: '识别并移入恢复区', path: '/duplicates' },
  { label: '项目备份', note: '生成 ZIP 快照', path: '/backup' },
]

const actionLabels = {
  scan: '文件扫描',
  backup: '项目备份',
  export: '报告导出',
  duplicate_scan: '重复检测',
  clean: '安全清理',
  organize: '文件整理',
  undo: '撤销整理',
  ai_scan_advice: 'AI 分析',
}

const latestScanLog = computed(() => [...logs.value].reverse().find(item => item.type === 'scan') || null)
const latestBackupLog = computed(() => [...logs.value].reverse().find(item => item.type === 'backup') || null)
const scanInfo = computed(() => cachedScan.value || latestScanLog.value)
const backupInfo = computed(() => cachedBackup.value || latestBackupLog.value)

const overviewCards = computed(() => {
  const scan = scanInfo.value
  const backup = backupInfo.value

  return [
    {
      title: '最近扫描文件数',
      value: formatCount(scan?.total_files),
      note: scan?.root || '还没有扫描记录',
      trend: scan ? '真实目录' : '',
      tone: 'blue',
      icon: Document,
    },
    {
      title: '空间占用',
      value: scan?.total_size_text || formatBytes(scan?.total_size),
      note: `文件夹 ${formatCount(scan?.total_dirs)}`,
      tone: 'purple',
      icon: Coin,
    },
    {
      title: '大文件数量',
      value: formatCount(cachedScan.value?.large_files?.length),
      note: '来自最近一次扫描',
      tone: 'orange',
      icon: DataAnalysis,
    },
    {
      title: '最近扫描时间',
      value: formatDateTime(cachedScan.value?.scan_time || latestScanLog.value?.time, '暂无记录'),
      note: '扫描完成时间',
      tone: 'cyan',
      icon: Clock,
    },
    {
      title: '最近备份文件数',
      value: formatCount(backup?.file_count),
      note: basename(backup?.zip_path, '暂无备份记录'),
      tone: 'green',
      icon: FolderOpened,
    },
    {
      title: '最近备份时间',
      value: formatDateTime(cachedBackup.value?.backup_time || latestBackupLog.value?.time, '暂无记录'),
      note: backup?.success === false ? '上次备份失败' : '最近一次备份',
      tone: 'slate',
      icon: Operation,
    },
  ]
})

const categoryChart = computed(() =>
  (cachedScan.value?.category_stats || []).slice(0, 6).map(item => ({
    ...item,
    width: `${Math.max(Number(item.percentage || 0), 3)}%`,
  })),
)

const recentOperations = computed(() =>
  [...logs.value].reverse().slice(0, 8).map(item => ({
    time: formatDateTime(item.time),
    type: actionLabels[item.type] || item.type || '其他操作',
    target: item.root || item.source || item.path || item.zip_path || item.target || '—',
    result: item.success === false ? '失败' : item.errors?.length ? '部分完成' : '成功',
  })),
)

const actionChart = computed(() => {
  const counter = {}

  for (const item of logs.value) {
    const label = actionLabels[item.type] || item.type || '其他操作'
    counter[label] = (counter[label] || 0) + 1
  }

  const entries = Object.entries(counter)
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 6)

  const max = Math.max(...entries.map(item => item.count), 1)

  return entries.map(item => ({
    ...item,
    width: `${Math.max((item.count / max) * 100, 8)}%`,
  }))
})

const largeFilesChart = computed(() => {
  const files = (cachedScan.value?.large_files || []).slice(0, 6)
  const max = Math.max(...files.map(item => Number(item.size || 0)), 1)

  return files.map(item => ({
    ...item,
    width: `${Math.max((Number(item.size || 0) / max) * 100, 8)}%`,
  }))
})

const backupSnapshot = computed(() => ({
  path: backupInfo.value?.zip_path || '还没有备份输出',
  size: backupInfo.value?.size_text || formatBytes(backupInfo.value?.size),
  count: formatCount(backupInfo.value?.file_count),
  time: formatDateTime(backupInfo.value?.backup_time || latestBackupLog.value?.time, '暂无记录'),
}))

async function loadDashboard() {
  loading.value = true
  loadError.value = ''

  try {
    logs.value = await getLogs()
  } catch (error) {
    loadError.value = error.message
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

onMounted(loadDashboard)
</script>

<template>
  <div v-loading="loading" class="section-stack">
    <PageHeader
      title="工作台"
      description="把扫描、整理、重复文件、备份和报告放在一个更清晰的首页里，先看全局，再决定下一步。"
      eyebrow="总览"
    >
      <template #actions>
        <div class="header-actions">
          <el-button type="primary" @click="$router.push('/scan')">开始扫描</el-button>
          <el-button plain @click="$router.push('/backup')">项目备份</el-button>
        </div>
      </template>
    </PageHeader>

    <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />

    <section class="quick-strip app-card">
      <button
        v-for="item in quickActions"
        :key="item.path"
        type="button"
        class="quick-strip__item"
        @click="$router.push(item.path)"
      >
        <strong>{{ item.label }}</strong>
        <span>{{ item.note }}</span>
      </button>
    </section>

    <section class="stats-grid">
      <StatCard v-for="item in overviewCards" :key="item.title" v-bind="item" />
    </section>

    <section class="section-grid">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">空间结构</h3>
            <p class="panel-subtitle">按空间占比快速看最近一次扫描结果，先知道容量主要压在哪些类型上。</p>
          </div>
          <el-button text type="primary" @click="$router.push('/scan')">查看扫描页</el-button>
        </div>

        <div v-if="categoryChart.length" class="chart-list">
          <div v-for="item in categoryChart" :key="item.label" class="chart-item">
            <div class="chart-item__head">
              <strong>{{ item.label }}</strong>
              <span>{{ item.size_text }} · {{ item.percentage }}%</span>
            </div>
            <div class="chart-bar">
              <div class="chart-bar__fill" :style="{ width: item.width }"></div>
            </div>
            <small>{{ formatCount(item.count) }} 个文件</small>
          </div>
        </div>

        <el-empty v-else description="还没有扫描结果" :image-size="80" />
      </article>

      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">备份概况</h3>
            <p class="panel-subtitle">先确认最近有没有可以回退的版本，再决定是否动目录。</p>
          </div>
          <el-button text type="primary" @click="$router.push('/backup')">进入备份</el-button>
        </div>

        <div class="brief-list">
          <div class="brief-item">
            <span>备份文件</span>
            <strong :title="backupSnapshot.path">{{ backupSnapshot.path }}</strong>
          </div>
          <div class="brief-item">
            <span>压缩包大小</span>
            <strong>{{ backupSnapshot.size }}</strong>
          </div>
          <div class="brief-item">
            <span>文件数量</span>
            <strong>{{ backupSnapshot.count }}</strong>
          </div>
          <div class="brief-item">
            <span>备份时间</span>
            <strong>{{ backupSnapshot.time }}</strong>
          </div>
        </div>
      </article>
    </section>

    <section class="duo-grid">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">最近动作</h3>
            <p class="panel-subtitle">看最近都做了什么，以及各类操作的使用频率。</p>
          </div>
          <el-button text type="primary" @click="$router.push('/logs')">全部日志</el-button>
        </div>

        <div v-if="actionChart.length" class="mini-chart">
          <div v-for="item in actionChart" :key="item.label" class="mini-chart__row">
            <span>{{ item.label }}</span>
            <div class="mini-chart__bar">
              <div class="mini-chart__fill" :style="{ width: item.width }"></div>
            </div>
            <strong>{{ item.count }}</strong>
          </div>
        </div>

        <div v-if="recentOperations.length" class="activity-list">
          <div v-for="item in recentOperations" :key="`${item.time}-${item.type}`" class="activity-item">
            <span>{{ item.time }}</span>
            <div>
              <strong>{{ item.type }}</strong>
              <p :title="item.target">{{ item.target }}</p>
            </div>
            <el-tag
              :type="item.result === '成功' ? 'success' : item.result === '失败' ? 'danger' : 'warning'"
              effect="light"
            >
              {{ item.result }}
            </el-tag>
          </div>
        </div>

        <el-empty v-else description="暂无操作记录" :image-size="80" />
      </article>

      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">大文件重点</h3>
            <p class="panel-subtitle">按大小快速看最值得先处理的对象。</p>
          </div>
          <el-button text type="primary" @click="$router.push({ path: '/scan', query: { focus: 'large-files' } })">
            查看全部
          </el-button>
        </div>

        <div v-if="largeFilesChart.length" class="chart-list">
          <div v-for="item in largeFilesChart" :key="item.path" class="chart-item">
            <div class="chart-item__head">
              <strong>{{ item.name }}</strong>
              <span>{{ item.size_text || formatBytes(item.size) }}</span>
            </div>
            <div class="chart-bar">
              <div class="chart-bar__fill is-accent" :style="{ width: item.width }"></div>
            </div>
            <small class="path-text" :title="item.path">{{ item.path }}</small>
          </div>
        </div>

        <el-empty v-else description="暂无大文件结果" :image-size="80" />
      </article>
    </section>
  </div>
</template>

<style scoped>
.header-actions {
  display: flex;
  gap: 10px;
}

.quick-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  overflow: hidden;
}

.quick-strip__item {
  padding: 18px 20px;
  text-align: left;
  background: transparent;
  border: 0;
  border-right: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.18s ease;
}

.quick-strip__item:last-child {
  border-right: 0;
}

.quick-strip__item:hover {
  background: #f8fafc;
}

.quick-strip__item strong,
.quick-strip__item span,
.brief-item span,
.brief-item strong,
.chart-item__head strong,
.chart-item__head span,
.chart-item small {
  display: block;
}

.quick-strip__item strong {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 700;
}

.quick-strip__item span,
.brief-item span,
.chart-item__head span,
.chart-item small {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.brief-list,
.activity-list,
.chart-list {
  display: grid;
  gap: 10px;
}

.brief-item,
.activity-item,
.chart-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.brief-item strong,
.chart-item__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.brief-item strong {
  margin-top: 6px;
}

.chart-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.chart-bar,
.mini-chart__bar {
  width: 100%;
  height: 8px;
  background: #edf2f6;
  border-radius: 999px;
  overflow: hidden;
}

.chart-bar__fill,
.mini-chart__fill {
  height: 100%;
  background: var(--primary);
  border-radius: 999px;
}

.chart-bar__fill.is-accent {
  background: var(--accent);
}

.mini-chart {
  display: grid;
  gap: 10px;
  margin-bottom: 16px;
}

.mini-chart__row {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) 28px;
  align-items: center;
  gap: 10px;
}

.mini-chart__row span {
  color: var(--text-secondary);
  font-size: 12px;
}

.mini-chart__row strong {
  color: var(--text-primary);
  font-size: 12px;
  text-align: right;
}

.activity-item {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}

.activity-item span,
.activity-item p {
  color: var(--text-muted);
  font-size: 12px;
}

.activity-item strong {
  color: var(--text-primary);
  font-size: 14px;
}

.activity-item p {
  overflow: hidden;
  margin: 5px 0 0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1080px) {
  .quick-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .quick-strip__item:nth-child(2n) {
    border-right: 0;
  }

  .quick-strip__item:nth-child(-n + 2) {
    border-bottom: 1px solid var(--border);
  }
}

@media (max-width: 900px) {
  .header-actions,
  .activity-item,
  .mini-chart__row,
  .chart-item__head {
    grid-template-columns: 1fr;
  }

  .header-actions {
    display: grid;
  }
}

@media (max-width: 640px) {
  .quick-strip {
    grid-template-columns: 1fr;
  }

  .quick-strip__item {
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .quick-strip__item:last-child {
    border-bottom: 0;
  }
}
</style>

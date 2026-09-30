<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Files, FolderOpened } from '@element-plus/icons-vue'
import { exportReport, getCachedScan } from '../api/filepilot'
import PageHeader from '../components/PageHeader.vue'
import { formatCount, formatDateTime } from '../utils/formatters'

const cachedScan = getCachedScan()

const directory = ref(cachedScan?.root || '')
const exporting = ref(false)
const exportResult = ref(null)
const statusText = ref(
  cachedScan
    ? `最近扫描目录：${cachedScan.root}`
    : '输入目录后即可重新扫描并导出本地文本报告。',
)

const summaryMetrics = computed(() => {
  if (!cachedScan) return []

  return [
    { label: '最近扫描文件', value: formatCount(cachedScan.total_files) },
    { label: '文件夹数量', value: formatCount(cachedScan.total_dirs) },
    { label: '占用空间', value: cachedScan.total_size_text },
    { label: '文件类型', value: formatCount(cachedScan.category_stats?.length) },
  ]
})

const categoryChart = computed(() => {
  const items = (cachedScan?.category_stats || []).slice(0, 6)
  const max = Math.max(...items.map(item => Number(item.size || 0)), 1)

  return items.map(item => ({
    ...item,
    width: `${Math.max((Number(item.size || 0) / max) * 100, 8)}%`,
  }))
})

const reportBlocks = computed(() => [
  {
    title: '目录概览',
    value: cachedScan?.root || directory.value || '等待输入目录',
    note: '记录扫描目录、文件总数和总体空间占用。',
  },
  {
    title: '类型统计',
    value: cachedScan ? `${formatCount(cachedScan.category_stats?.length)} 类` : '导出时生成',
    note: '按类型整理空间分布，方便后续清理或汇报。',
  },
  {
    title: '大文件列表',
    value: cachedScan?.largest_file?.name || '导出时生成',
    note: '会列出占空间最多的文件，便于后续定位。',
  },
])

async function startExport() {
  const path = directory.value.trim()
  if (!path) {
    ElMessage.warning('请输入要导出报告的完整目录路径')
    return
  }

  exporting.value = true
  statusText.value = '正在重新扫描目录并生成文本报告，请稍候...'

  try {
    exportResult.value = await exportReport(path)
    statusText.value = `报告已生成于 ${formatDateTime(exportResult.value.created_at)}`
    ElMessage.success('本地分析报告导出成功')
  } catch (error) {
    statusText.value = '导出失败，请检查目录路径或确认后端服务已启动'
    ElMessage.error(error.message)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="报告导出"
      description="把本地扫描结果沉淀成一份文本报告，适合留档、回看，或者给自己做阶段记录。"
      eyebrow="本地报告"
    >
      <template #meta>
        <div class="meta-pill">UTF-8 文本</div>
        <div class="meta-pill">输出到 reports 目录</div>
      </template>
      <template #actions>
        <el-tag type="success" effect="light">
          {{ cachedScan?.root ? '可复用最近扫描结果' : '导出时重新扫描目录' }}
        </el-tag>
      </template>
    </PageHeader>

    <section class="command-board app-card">
      <div class="command-board__copy">
        <span class="surface-caption">导出目录</span>
        <h3>把扫描结果留成一份文字版本。</h3>
        <p>报告会重新扫描你指定的目录，并写入本地 `reports` 目录。这样即使之后关掉页面，也能回看这次结果。</p>
      </div>

      <div class="command-board__form">
        <div>
          <span class="field-label">报告目录</span>
          <el-input
            v-model="directory"
            size="large"
            clearable
            placeholder="例如：D:\\Workspace"
            @keyup.enter="startExport"
          >
            <template #prefix><el-icon><FolderOpened /></el-icon></template>
          </el-input>
        </div>

        <div class="command-board__actions">
          <el-button type="primary" size="large" :loading="exporting" @click="startExport">
            <el-icon><Download /></el-icon>{{ exporting ? '正在导出' : '导出报告' }}
          </el-button>
        </div>

        <div class="status-card">
          <strong>{{ statusText }}</strong>
          <span>报告只写入本地 `reports` 目录，不上传文件内容。</span>
        </div>
      </div>
    </section>

    <section v-if="summaryMetrics.length" class="metric-strip">
      <div v-for="item in summaryMetrics" :key="item.label" class="metric-strip__item">
        <div class="metric-strip__label">{{ item.label }}</div>
        <div class="metric-strip__value">{{ item.value }}</div>
      </div>
    </section>

    <el-alert v-else type="info" :closable="false" show-icon>
      当前没有最近扫描缓存。直接导出也可以，后端会重新扫描你输入的目录。
    </el-alert>

    <section class="section-grid">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">报告重点预览</h3>
            <p class="panel-subtitle">如果最近有扫描结果，这里会先用统计图把空间分布展示出来。</p>
          </div>
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

        <el-empty v-else description="暂无可预览的扫描统计" :image-size="80" />
      </article>

      <div class="content-stack">
        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">导出内容</h3>
              <p class="panel-subtitle">报告不会很花，重点就是把后续最常回看的信息留住。</p>
            </div>
          </div>

          <div class="brief-list">
            <div v-for="item in reportBlocks" :key="item.title" class="brief-item">
              <span>{{ item.title }}</span>
              <strong :title="item.value">{{ item.value }}</strong>
              <p>{{ item.note }}</p>
            </div>
          </div>
        </article>

        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">最近导出</h3>
              <p class="panel-subtitle">导出完成后，这里会保留路径、大小和时间。</p>
            </div>
          </div>

          <template v-if="exportResult">
            <div class="result-card">
              <div class="result-icon">
                <el-icon :size="20"><Files /></el-icon>
              </div>
              <div class="result-body">
                <span>报告路径</span>
                <strong class="path-text" :title="exportResult.report_path">{{ exportResult.report_path }}</strong>
                <p>{{ exportResult.size_text }} · {{ formatDateTime(exportResult.created_at) }}</p>
              </div>
            </div>
          </template>

          <el-empty v-else description="还没有导出记录" :image-size="72" />
        </article>
      </div>
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

.command-board {
  display: grid;
  grid-template-columns: minmax(280px, 0.9fr) minmax(0, 1.1fr);
  gap: 18px;
  padding: 22px;
}

.command-board__copy h3 {
  margin: 14px 0 10px;
  color: var(--text-primary);
  font-size: clamp(24px, 2.8vw, 32px);
  line-height: 1.2;
  letter-spacing: -0.05em;
}

.command-board__copy p {
  margin: 0;
  color: var(--text-secondary);
  line-height: 1.8;
}

.command-board__form {
  display: grid;
  gap: 14px;
}

.command-board__actions {
  display: flex;
  gap: 10px;
}

.field-label {
  display: block;
  margin-bottom: 8px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.status-card {
  padding: 16px 18px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.status-card strong,
.status-card span,
.brief-item span,
.brief-item strong,
.chart-item__head strong,
.chart-item__head span,
.chart-item small {
  display: block;
}

.status-card strong {
  color: var(--text-primary);
  font-size: 14px;
}

.status-card span,
.brief-item span,
.chart-item__head span,
.chart-item small,
.brief-item p,
.result-body p {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.chart-list,
.brief-list {
  display: grid;
  gap: 10px;
}

.chart-item,
.brief-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.brief-item strong,
.chart-item__head strong,
.result-body strong {
  color: var(--text-primary);
  font-size: 14px;
}

.brief-item strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.brief-item p,
.result-body p {
  margin-bottom: 0;
}

.chart-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
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

.result-card {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.result-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  color: var(--primary);
  background: rgba(23, 104, 172, 0.1);
  border-radius: 12px;
}

.result-body span {
  display: block;
  color: var(--text-muted);
  font-size: 12px;
}

@media (max-width: 1080px) {
  .command-board {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .command-board__actions,
  .chart-item__head,
  .result-card {
    grid-template-columns: 1fr;
  }

  .command-board__actions {
    display: grid;
  }
}
</style>

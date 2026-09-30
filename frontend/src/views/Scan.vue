<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CircleCheck, FolderOpened, Operation, Search } from '@element-plus/icons-vue'
import { getAiStatus, getCachedScan, getScanAdvice, scanFolder } from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import PageHeader from '../components/PageHeader.vue'
import {
  basename,
  formatBytes,
  formatCount,
  formatDateTime,
  formatDuration,
  parentPath,
} from '../utils/formatters'

const route = useRoute()
const cached = getCachedScan()

const directory = ref(cached?.root || '')
const result = ref(cached)
const loading = ref(false)
const largeFileSection = ref(null)
const threshold = ref(100 * 1024 ** 2)
const statusText = ref(
  cached
    ? `最近扫描完成于 ${formatDateTime(cached.scan_time)}`
    : '输入一个目录后开始真实扫描，系统会读取目录结构、类型占用和大文件信息。',
)

const aiStatus = ref(null)
const aiLoading = ref(false)
const aiFocus = ref('释放空间')
const aiQuestion = ref('')
const aiAdvice = ref(null)

const thresholdOptions = [
  { label: '100 MB', value: 100 * 1024 ** 2 },
  { label: '500 MB', value: 500 * 1024 ** 2 },
  { label: '1 GB', value: 1024 ** 3 },
]

const aiFocusOptions = [
  { label: '释放空间', value: '释放空间' },
  { label: '整理归档', value: '整理归档' },
  { label: '风险检查', value: '风险检查' },
]

const summaryMetrics = computed(() => (result.value
  ? [
      { label: '文件总数', value: formatCount(result.value.total_files) },
      { label: '文件夹数量', value: formatCount(result.value.total_dirs) },
      { label: '总占用空间', value: result.value.total_size_text },
      { label: '扫描耗时', value: formatDuration(result.value.duration_seconds) },
    ]
  : []))

const scanBrief = computed(() => {
  if (!result.value) return []

  const largestFile = result.value.largest_file || result.value.large_files?.[0]

  return [
    { label: '扫描目录', value: result.value.root || directory.value || '—' },
    { label: '完成时间', value: formatDateTime(result.value.scan_time) },
    { label: '最大文件', value: largestFile ? `${largestFile.name} · ${largestFile.size_text}` : '—' },
    { label: '读取异常', value: `${result.value.warnings?.length || 0} 项` },
  ]
})

const categoryChart = computed(() =>
  (result.value?.category_stats || []).slice(0, 8).map(item => ({
    ...item,
    width: `${Math.max(Number(item.percentage || 0), 3)}%`,
  })),
)

const filteredLargeFiles = computed(() =>
  (result.value?.large_files || []).filter(item => item.size >= threshold.value),
)

const largeFilesChart = computed(() => {
  const files = filteredLargeFiles.value.slice(0, 6)
  const max = Math.max(...files.map(item => Number(item.size || 0)), 1)

  return files.map(item => ({
    ...item,
    width: `${Math.max((Number(item.size || 0) / max) * 100, 8)}%`,
  }))
})

const aiReady = computed(() => Boolean(aiStatus.value?.configured))

const aiSummary = computed(() => {
  if (!result.value) return null

  return {
    root_name: basename(result.value.root, '当前目录'),
    total_files: result.value.total_files,
    total_dirs: result.value.total_dirs,
    total_size_text: result.value.total_size_text,
    warnings_count: result.value.warnings?.length || 0,
    scan_time: result.value.scan_time,
    duration_seconds: result.value.duration_seconds,
    category_stats: (result.value.category_stats || []).slice(0, 8).map(item => ({
      label: item.label,
      count: item.count,
      size_text: item.size_text,
      percentage: item.percentage,
    })),
    large_files: (result.value.large_files || []).slice(0, 8).map(item => ({
      name: item.name,
      size_text: item.size_text,
      type: item.type,
      modified_time: item.modified_time,
      parent_name: basename(parentPath(item.path), '当前目录'),
    })),
  }
})

const aiUsageText = computed(() => {
  if (!aiAdvice.value?.usage) return ''
  return `约 ${formatCount(aiAdvice.value.usage.total_tokens || 0)} tokens`
})

const typeColumns = [
  { prop: 'label', label: '文件类型', minWidth: 180 },
  { prop: 'count', label: '文件数量', width: 120, align: 'right' },
  { prop: 'size_text', label: '占用空间', width: 120, align: 'right' },
  { prop: 'percentage', label: '空间占比', width: 120, align: 'right' },
]

const fileColumns = [
  { prop: 'name', label: '文件名', minWidth: 210 },
  { prop: 'path', label: '完整路径', minWidth: 360 },
  { prop: 'size_text', label: '大小', width: 105, align: 'right' },
  { prop: 'type', label: '类型', width: 110 },
  { prop: 'modified_time', label: '修改时间', width: 170 },
]

function scrollToLargeFileSection(behavior = 'smooth') {
  largeFileSection.value?.scrollIntoView({ behavior, block: 'start' })
}

async function maybeFocusLargeFiles(behavior = 'smooth') {
  if (route.query.focus !== 'large-files') return
  await nextTick()
  scrollToLargeFileSection(behavior)
}

async function fetchAiStatus() {
  try {
    aiStatus.value = await getAiStatus()
  } catch {
    aiStatus.value = {
      provider: 'DeepSeek',
      configured: false,
      model: 'deepseek-v4-flash',
      data_scope: 'summary_only',
    }
  }
}

async function startScan() {
  const path = directory.value.trim()
  if (!path) {
    ElMessage.warning('请输入要扫描的完整目录路径')
    return
  }

  loading.value = true
  statusText.value = '正在扫描目录，请稍候...'
  aiAdvice.value = null

  try {
    result.value = await scanFolder(path)
    statusText.value = `扫描完成：共发现 ${formatCount(result.value.total_files)} 个文件`
    ElMessage.success('目录扫描完成')

    if (result.value.warnings?.length) {
      ElMessage.warning(`有 ${result.value.warnings.length} 个项目因权限或占用问题被跳过`)
    }

    await maybeFocusLargeFiles()
  } catch (error) {
    statusText.value = '扫描失败，请检查目录路径或确认后端服务已启动'
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function analyzeWithDeepSeek() {
  if (!result.value || !aiSummary.value) {
    ElMessage.warning('请先完成一次真实扫描')
    return
  }

  if (!aiReady.value) {
    ElMessage.warning('后端尚未检测到 DeepSeek 配置')
    return
  }

  aiLoading.value = true

  try {
    aiAdvice.value = await getScanAdvice({
      ...aiSummary.value,
      focus: aiFocus.value,
      question: aiQuestion.value.trim(),
    })
    ElMessage.success('DeepSeek 分析完成')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    aiLoading.value = false
  }
}

watch(() => route.query.focus, () => {
  maybeFocusLargeFiles()
}, { immediate: true })

watch(result, () => {
  maybeFocusLargeFiles('auto')
})

onMounted(() => {
  fetchAiStatus()
  maybeFocusLargeFiles('auto')
})
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="扫描分析"
      description="真实扫描、本地统计、大文件筛查和 DeepSeek 辅助判断都放在这一页。先看结构，再决定清理还是整理。"
      eyebrow="本地数据"
    >
      <template #meta>
        <div class="meta-pill">真实目录扫描</div>
        <div class="meta-pill">结果缓存到当前会话</div>
        <div class="meta-pill">仅发送摘要给 DeepSeek</div>
      </template>
      <template #actions>
        <el-tag type="success" effect="light">
          <el-icon><CircleCheck /></el-icon>&nbsp;本地后端已接入
        </el-tag>
      </template>
    </PageHeader>

    <section class="command-board app-card">
      <div class="command-board__copy">
        <span class="surface-caption">扫描目录</span>
        <h3>先把真实目录扫一遍，再决定下一步。</h3>
        <p>系统只读取本机目录信息，不上传文件正文。扫描结果会直接用于重复文件、整理、报告和 AI 分析。</p>
      </div>

      <div class="command-board__form">
        <div>
          <span class="field-label">目录路径</span>
          <el-input
            v-model="directory"
            size="large"
            placeholder="例如：D:\\Workspace"
            clearable
            @keyup.enter="startScan"
          >
            <template #prefix><el-icon><FolderOpened /></el-icon></template>
          </el-input>
        </div>

        <div class="command-board__actions">
          <el-button type="primary" size="large" :loading="loading" @click="startScan">
            <el-icon><Search /></el-icon>{{ loading ? '正在扫描' : '开始扫描' }}
          </el-button>
          <el-button plain size="large" @click="$router.push('/reports')">导出报告</el-button>
        </div>

        <div class="status-card">
          <strong>{{ statusText }}</strong>
          <span>扫描只面向你电脑上的真实目录数据，适合先排查空间占用和大文件。</span>
        </div>
      </div>
    </section>

    <template v-if="result">
      <section class="metric-strip">
        <div v-for="item in summaryMetrics" :key="item.label" class="metric-strip__item">
          <div class="metric-strip__label">{{ item.label }}</div>
          <div class="metric-strip__value">{{ item.value }}</div>
        </div>
      </section>

      <el-alert v-if="result.warnings?.length" type="warning" :closable="false" show-icon>
        本次扫描有 {{ result.warnings.length }} 个项目无法读取，通常是权限不足、文件占用或路径异常导致。
      </el-alert>

      <section class="section-grid">
        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">类型分布</h3>
              <p class="panel-subtitle">先看空间主要压在哪些类型上，再决定该清理哪里。</p>
            </div>
          </div>

          <div v-if="categoryChart.length" class="chart-list chart-list--space">
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

          <DataTable :columns="typeColumns" :data="result.category_stats" show-index>
            <template #count="{ value }">{{ formatCount(value) }}</template>
            <template #percentage="{ value }">{{ value }}%</template>
          </DataTable>
        </article>

        <div class="content-stack">
          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">扫描摘要</h3>
                <p class="panel-subtitle">把本次关键上下文收在一侧，方便快速判断。</p>
              </div>
            </div>

            <div class="brief-list">
              <div v-for="item in scanBrief" :key="item.label" class="brief-item">
                <span>{{ item.label }}</span>
                <strong :title="item.value">{{ item.value }}</strong>
              </div>
            </div>
          </article>

          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">下一步</h3>
                <p class="panel-subtitle">扫完以后，最常见的三个动作通常是这几个。</p>
              </div>
            </div>

            <div class="jump-list">
              <button type="button" class="jump-item" @click="$router.push({ path: '/scan', query: { focus: 'large-files' } })">
                <strong>查看大文件</strong>
                <span>先看最占空间的文件</span>
              </button>
              <button type="button" class="jump-item" @click="$router.push('/duplicates')">
                <strong>检查重复文件</strong>
                <span>找出重复副本并处理</span>
              </button>
              <button type="button" class="jump-item" @click="$router.push('/organizer')">
                <strong>开始整理归档</strong>
                <span>按类别重排真实目录</span>
              </button>
            </div>
          </article>
        </div>
      </section>

      <section ref="largeFileSection" class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">大文件</h3>
            <p class="panel-subtitle">按阈值快速筛一遍，优先处理最占空间的对象。</p>
          </div>

          <div class="toolbar-inline">
            <el-segmented v-model="threshold" :options="thresholdOptions" />
            <el-button plain @click="$router.push('/reports')">导出报告</el-button>
          </div>
        </div>

        <div class="toolbar-meta">
          <span>当前阈值：{{ thresholdOptions.find(item => item.value === threshold)?.label || '100 MB' }}</span>
          <span>命中 {{ filteredLargeFiles.length }} 个文件</span>
          <span>合计 {{ formatBytes(filteredLargeFiles.reduce((sum, item) => sum + item.size, 0)) }}</span>
        </div>

        <div v-if="largeFilesChart.length" class="chart-list chart-list--space">
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

        <DataTable :columns="fileColumns" :data="filteredLargeFiles" show-index empty-text="当前阈值下没有匹配的大文件">
          <template #path="{ value }"><span class="path-text" :title="value">{{ value }}</span></template>
          <template #modified_time="{ value }">{{ formatDateTime(value) }}</template>
        </DataTable>
      </section>

      <section class="app-card panel-card ai-panel">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">DeepSeek 分析</h3>
            <p class="panel-subtitle">默认只发送扫描摘要、类型统计和前几项大文件信息，不发送文件正文。</p>
          </div>
          <el-tag :type="aiReady ? 'success' : 'warning'" effect="light">
            {{ aiReady ? (aiStatus?.model || 'DeepSeek 已就绪') : '等待 DeepSeek 配置' }}
          </el-tag>
        </div>

        <div class="ai-controls">
          <div class="ai-controls__row">
            <div>
              <span class="field-label">分析方向</span>
              <el-select v-model="aiFocus">
                <el-option
                  v-for="item in aiFocusOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </div>

            <el-button type="primary" size="large" :loading="aiLoading" :disabled="!result || !aiReady" @click="analyzeWithDeepSeek">
              <el-icon><Operation /></el-icon>{{ aiLoading ? '分析中' : '开始分析' }}
            </el-button>
          </div>

          <div>
            <span class="field-label">补充说明</span>
            <el-input
              v-model="aiQuestion"
              type="textarea"
              :rows="3"
              placeholder="例如：优先释放空间，但不要动源码、依赖和工作项目。"
            />
          </div>
        </div>

        <div v-if="aiAdvice" class="ai-result">
          <div class="ai-result__head">
            <strong>{{ aiAdvice.overview }}</strong>
            <span>{{ formatDateTime(aiAdvice.generated_at) }} · {{ aiUsageText }}</span>
          </div>

          <div v-if="aiAdvice.priority_actions?.length" class="ai-block">
            <h4>优先动作</h4>
            <div v-for="(item, index) in aiAdvice.priority_actions" :key="`${index}-${item.title}`" class="ai-line">
              <strong>{{ String(index + 1).padStart(2, '0') }} · {{ item.title }}</strong>
              <p>{{ item.reason }}</p>
              <span>{{ item.impact }}</span>
            </div>
          </div>

          <div v-if="aiAdvice.risks?.length" class="ai-block">
            <h4>风险提醒</h4>
            <div v-for="item in aiAdvice.risks" :key="item" class="ai-line">
              <p>{{ item }}</p>
            </div>
          </div>

          <div v-if="aiAdvice.next_steps?.length" class="ai-block">
            <h4>下一步</h4>
            <div v-for="item in aiAdvice.next_steps" :key="item" class="ai-line">
              <p>{{ item }}</p>
            </div>
          </div>
        </div>

        <el-empty v-else description="还没有 AI 分析结果" :image-size="88" />

        <div class="ai-footer">Wxy</div>
      </section>
    </template>

    <section v-else class="app-card empty-card">
      <el-empty description="还没有扫描结果，先输入一个目录路径再开始。" />
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

.command-board__actions,
.toolbar-inline {
  display: flex;
  gap: 10px;
  align-items: center;
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
.chart-item small {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.brief-list,
.chart-list,
.ai-result,
.jump-list {
  display: grid;
  gap: 10px;
}

.brief-item,
.chart-item,
.ai-line,
.jump-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.brief-item strong,
.chart-item__head strong,
.jump-item strong {
  color: var(--text-primary);
  font-size: 14px;
}

.brief-item strong {
  overflow: hidden;
  margin-top: 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chart-list--space {
  margin-bottom: 16px;
}

.chart-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
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

.chart-bar__fill.is-accent {
  background: var(--accent);
}

.jump-item {
  text-align: left;
  cursor: pointer;
}

.jump-item span {
  display: block;
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.toolbar-meta {
  display: flex;
  gap: 18px;
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 12px;
  flex-wrap: wrap;
}

.ai-panel {
  display: grid;
  gap: 16px;
}

.ai-controls {
  display: grid;
  gap: 14px;
}

.ai-controls__row {
  display: grid;
  grid-template-columns: minmax(220px, 280px) auto;
  gap: 14px;
  align-items: end;
}

.ai-result__head {
  padding: 16px 18px;
  background: #f6f9fb;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.ai-result__head strong,
.ai-result__head span {
  display: block;
}

.ai-result__head strong {
  color: var(--text-primary);
  font-size: 15px;
  line-height: 1.7;
}

.ai-result__head span {
  margin-top: 8px;
  color: var(--text-muted);
  font-size: 12px;
}

.ai-block {
  display: grid;
  gap: 10px;
}

.ai-block h4 {
  margin: 0;
  color: var(--text-primary);
  font-size: 14px;
}

.ai-line strong {
  display: block;
  color: var(--text-primary);
  font-size: 14px;
}

.ai-line p {
  margin: 8px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.8;
}

.ai-line span {
  display: block;
  margin-top: 8px;
  color: var(--warning);
  font-size: 12px;
}

.ai-footer {
  padding-top: 4px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: right;
  letter-spacing: 0.08em;
}

@media (max-width: 1080px) {
  .command-board {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .command-board__actions,
  .ai-controls__row,
  .chart-item__head {
    grid-template-columns: 1fr;
  }

  .command-board__actions,
  .toolbar-inline {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

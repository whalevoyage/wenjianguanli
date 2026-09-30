<script setup>
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, RefreshRight, Sort } from '@element-plus/icons-vue'
import {
  executeOrganization,
  getCachedOrganizerPreview,
  getCachedOrganizerResult,
  previewOrganization,
  undoOrganization,
} from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import PageHeader from '../components/PageHeader.vue'
import { formatCount, formatDateTime } from '../utils/formatters'

const cachedPreview = getCachedOrganizerPreview()
const cachedResult = getCachedOrganizerResult()

const source = ref(cachedPreview?.source_dir || cachedResult?.source_dir || '')
const target = ref(cachedPreview?.target_dir || cachedResult?.target_dir || '')
const preview = ref(cachedPreview)
const organizeResult = ref(cachedResult)
const previewing = ref(false)
const executing = ref(false)
const undoing = ref(false)
const statusText = ref(
  cachedPreview
    ? `最近预览生成于 ${formatDateTime(cachedPreview.created_at)}`
    : '先输入源目录和目标目录，系统会基于本机真实文件生成整理预览。',
)

const categoryColumns = [
  { prop: 'label', label: '目标分类', minWidth: 160 },
  { prop: 'count', label: '文件数', width: 110, align: 'right' },
  { prop: 'size_text', label: '移动空间', width: 120, align: 'right' },
]

const previewColumns = [
  { prop: 'source', label: '原路径', minWidth: 280 },
  { prop: 'label', label: '分类', width: 110 },
  { prop: 'target', label: '目标路径', minWidth: 300 },
  { prop: 'size_text', label: '大小', width: 100, align: 'right' },
]

const rules = [
  '先预览，再执行，避免把批量整理变成不可控操作。',
  '目标目录中如果出现同名文件，系统会自动追加编号，不覆盖原文件。',
  '撤销只针对当前后端会话中最近一次成功执行的整理任务。',
]

const summaryMetrics = computed(() => {
  if (!preview.value) return []

  return [
    { label: '待整理文件', value: formatCount(preview.value.item_count) },
    { label: '预计移动空间', value: preview.value.total_size_text },
    { label: '目标分类', value: formatCount(preview.value.category_stats.length) },
    { label: '预览异常', value: `${preview.value.errors?.length || 0} 项` },
  ]
})

const previewBrief = computed(() => {
  if (!preview.value) return []

  return [
    { label: '源目录', value: preview.value.source_dir },
    { label: '目标目录', value: preview.value.target_dir },
    { label: '预览时间', value: formatDateTime(preview.value.created_at) },
    { label: '最近执行', value: organizeResult.value ? formatDateTime(organizeResult.value.finished_at) : '暂无' },
  ]
})

const categoryChart = computed(() => {
  const items = (preview.value?.category_stats || []).slice(0, 6)
  const max = Math.max(...items.map(item => Number(item.size || 0)), 1)

  return items.map(item => ({
    ...item,
    width: `${Math.max((Number(item.size || 0) / max) * 100, 8)}%`,
  }))
})

async function confirmAction(message, title) {
  try {
    await ElMessageBox.confirm(message, title, {
      type: 'warning',
      confirmButtonText: '继续',
      cancelButtonText: '取消',
    })
    return true
  } catch {
    return false
  }
}

async function generatePreview() {
  const sourceDir = source.value.trim()
  const targetDir = target.value.trim()

  if (!sourceDir || !targetDir) {
    ElMessage.warning('请输入源目录和目标目录')
    return
  }

  previewing.value = true
  statusText.value = '正在生成真实整理预览，请稍候...'

  try {
    preview.value = await previewOrganization(sourceDir, targetDir)
    statusText.value = `预览完成：共生成 ${formatCount(preview.value.item_count)} 条整理计划`
    ElMessage.success('整理预览生成完成')

    if (preview.value.errors?.length) {
      ElMessage.warning(`有 ${preview.value.errors.length} 个项目未能加入整理计划`)
    }
  } catch (error) {
    statusText.value = '生成预览失败，请检查目录路径或确认后端服务已启动'
    ElMessage.error(error.message)
  } finally {
    previewing.value = false
  }
}

async function runOrganization() {
  const sourceDir = source.value.trim()
  const targetDir = target.value.trim()

  if (!sourceDir || !targetDir) {
    ElMessage.warning('请输入源目录和目标目录')
    return
  }

  const confirmed = await confirmAction(
    '整理会真实移动文件到目标目录，但支持撤销最近一次操作。是否继续？',
    '确认执行整理',
  )
  if (!confirmed) return

  executing.value = true

  try {
    organizeResult.value = await executeOrganization(sourceDir, targetDir)
    ElMessage.success(`整理完成，共移动 ${organizeResult.value.moved_count} 个文件`)

    try {
      preview.value = await previewOrganization(sourceDir, targetDir)
      statusText.value = '整理已执行完成，当前预览已刷新'
    } catch {
      statusText.value = '整理已执行完成，但刷新预览失败，可手动再生成一次'
    }
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    executing.value = false
  }
}

async function undoLastOrganization() {
  const confirmed = await confirmAction(
    '这会撤销当前后端会话中最近一次成功的整理任务。是否继续？',
    '确认撤销整理',
  )
  if (!confirmed) return

  undoing.value = true

  try {
    const payload = await undoOrganization()
    ElMessage.success(`撤销完成，共恢复 ${payload.restored_count} 个文件`)
    organizeResult.value = null

    if (source.value.trim() && target.value.trim()) {
      try {
        preview.value = await previewOrganization(source.value.trim(), target.value.trim())
        statusText.value = '最近一次整理已撤销，预览已同步刷新'
      } catch {
        statusText.value = '撤销已完成，但刷新预览失败，可手动再生成一次'
      }
    } else {
      statusText.value = '最近一次整理已撤销'
    }
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    undoing.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="文件整理"
      description="真实目录先做预览，再决定是否执行。整理完成后还能撤销最近一次操作，适合做批量归档。"
      eyebrow="本地整理"
    >
      <template #meta>
        <div class="meta-pill">真实目录预览</div>
        <div class="meta-pill">支持撤销</div>
      </template>
      <template #actions>
        <el-button plain :loading="undoing" :disabled="!organizeResult" @click="undoLastOrganization">
          <el-icon><RefreshRight /></el-icon>撤销最近一次整理
        </el-button>
      </template>
    </PageHeader>

    <section class="command-board app-card">
      <div class="command-board__copy">
        <span class="surface-caption">整理路径</span>
        <h3>先看预览，再决定要不要动文件。</h3>
        <p>整理功能会读取你电脑上的真实目录结构，按文件类型生成目标路径。只有在你确认执行后，系统才会真正移动文件。</p>
      </div>

      <div class="command-board__form">
        <div class="command-board__grid">
          <div>
            <span class="field-label">源目录</span>
            <el-input v-model="source" size="large" placeholder="例如：D:\\Inbox" clearable>
              <template #prefix><el-icon><FolderOpened /></el-icon></template>
            </el-input>
          </div>

          <div>
            <span class="field-label">目标目录</span>
            <el-input v-model="target" size="large" placeholder="例如：D:\\Organized" clearable>
              <template #prefix><el-icon><FolderOpened /></el-icon></template>
            </el-input>
          </div>
        </div>

        <div class="command-board__actions">
          <el-button type="primary" size="large" :loading="previewing" @click="generatePreview">
            <el-icon><Sort /></el-icon>{{ previewing ? '生成中' : '生成预览' }}
          </el-button>
          <el-button type="primary" plain size="large" :loading="executing" :disabled="!preview" @click="runOrganization">
            执行整理
          </el-button>
        </div>

        <div class="status-card">
          <strong>{{ statusText }}</strong>
          <span>只有你确认执行后，系统才会真正移动文件。</span>
        </div>
      </div>
    </section>

    <template v-if="preview">
      <section class="metric-strip">
        <div v-for="item in summaryMetrics" :key="item.label" class="metric-strip__item">
          <div class="metric-strip__label">{{ item.label }}</div>
          <div class="metric-strip__value">{{ item.value }}</div>
        </div>
      </section>

      <el-alert v-if="preview.errors?.length" type="warning" :closable="false" show-icon>
        本次预览有 {{ preview.errors.length }} 个项目无法纳入整理计划，通常是路径异常、权限不足或文件被占用。
      </el-alert>

      <el-alert
        v-if="organizeResult"
        :type="organizeResult.success ? 'success' : 'warning'"
        :closable="false"
        show-icon
      >
        最近一次整理共移动 {{ organizeResult.moved_count }} 个文件，累计 {{ organizeResult.moved_size_text }}，
        完成于 {{ formatDateTime(organizeResult.finished_at) }}。
      </el-alert>

      <section class="section-grid">
        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">分类统计图</h3>
              <p class="panel-subtitle">先看文件会被整理到哪些分类，再决定是否执行。</p>
            </div>
            <el-tag effect="plain">{{ preview.category_stats.length }} 类</el-tag>
          </div>

          <div v-if="categoryChart.length" class="chart-list">
            <div v-for="item in categoryChart" :key="item.label" class="chart-item">
              <div class="chart-item__head">
                <strong>{{ item.label }}</strong>
                <span>{{ item.size_text }}</span>
              </div>
              <div class="chart-bar">
                <div class="chart-bar__fill" :style="{ width: item.width }"></div>
              </div>
              <small>{{ formatCount(item.count) }} 个文件</small>
            </div>
          </div>

          <DataTable :columns="categoryColumns" :data="preview.category_stats" empty-text="当前预览没有需要整理的文件" />
        </article>

        <div class="content-stack">
          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">本次摘要</h3>
                <p class="panel-subtitle">把源目录、目标目录和最近执行信息收在一侧。</p>
              </div>
            </div>

            <div class="brief-list">
              <div v-for="item in previewBrief" :key="item.label" class="brief-item">
                <span>{{ item.label }}</span>
                <strong :title="item.value">{{ item.value }}</strong>
              </div>
            </div>
          </article>

          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">整理规则</h3>
                <p class="panel-subtitle">边界比说明文案更重要，整理能力要可控。</p>
              </div>
            </div>

            <div class="rule-list">
              <div v-for="(item, index) in rules" :key="item" class="rule-item">
                <span>{{ String(index + 1).padStart(2, '0') }}</span>
                <p>{{ item }}</p>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">整理预览</h3>
            <p class="panel-subtitle">以下全部来自你电脑上的真实目录结构，执行后会按这份规则移动文件。</p>
          </div>
          <el-tag effect="plain">共 {{ formatCount(preview.item_count) }} 条计划</el-tag>
        </div>

        <DataTable :columns="previewColumns" :data="preview.items" empty-text="当前没有需要整理的文件">
          <template #source="{ value }"><span class="path-text" :title="value">{{ value }}</span></template>
          <template #target="{ value }"><span class="path-text" :title="value">{{ value }}</span></template>
          <template #label="{ value }"><el-tag size="small" effect="plain">{{ value }}</el-tag></template>
        </DataTable>
      </section>
    </template>
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

.command-board__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
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
.chart-item small {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.chart-list,
.brief-list,
.rule-list {
  display: grid;
  gap: 10px;
}

.chart-item,
.brief-item,
.rule-item {
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.rule-item {
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: 12px;
  align-items: start;
}

.rule-item span {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  color: var(--primary-strong);
  background: rgba(23, 104, 172, 0.1);
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
}

.rule-item p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.75;
}

@media (max-width: 1080px) {
  .command-board {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .command-board__grid,
  .command-board__actions,
  .chart-item__head {
    grid-template-columns: 1fr;
  }

  .command-board__actions {
    display: grid;
  }
}
</style>

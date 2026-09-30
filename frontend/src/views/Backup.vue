<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheck, FolderOpened, RefreshRight } from '@element-plus/icons-vue'
import { backupProject, getCachedBackup, getLogs } from '../api/filepilot'
import DataTable from '../components/DataTable.vue'
import PageHeader from '../components/PageHeader.vue'
import { basename, formatBytes, formatCount, formatDateTime, parentPath } from '../utils/formatters'

const cached = getCachedBackup()

const projectPath = ref(cached?.source || '')
const backupPath = ref(cached?.zip_path ? parentPath(cached.zip_path) : '')
const backupResult = ref(cached)
const backingUp = ref(false)
const recordsLoading = ref(false)
const recentRecords = ref([])
const statusText = ref(
  cached
    ? `最近备份完成于 ${formatDateTime(cached.backup_time)}`
    : '输入项目目录和保存目录后即可生成本地 ZIP 快照。',
)

const exclusions = ['node_modules', 'dist', 'build', '__pycache__', '.venv', 'venv', '.git', 'target', '.idea', '.vscode']

const columns = [
  { prop: 'time', label: '备份时间', width: 170 },
  { prop: 'project', label: '项目', width: 140 },
  { prop: 'file', label: '压缩包文件', minWidth: 250 },
  { prop: 'count', label: '文件数', width: 90, align: 'right' },
  { prop: 'size', label: '大小', width: 95, align: 'right' },
  { prop: 'result', label: '结果', width: 82 },
]

const backupSummary = computed(() => backupResult.value || cached || null)

const summaryMetrics = computed(() => [
  { label: '最近压缩包', value: backupSummary.value?.size_text || formatBytes(backupSummary.value?.size) },
  { label: '最近备份文件数', value: formatCount(backupSummary.value?.file_count) },
  { label: '备份记录数', value: formatCount(recentRecords.value.length) },
  { label: '默认排除目录', value: `${exclusions.length} 项` },
])

const latestBrief = computed(() => {
  if (!backupResult.value) return []

  return [
    { label: '项目目录', value: backupResult.value.source },
    { label: '输出路径', value: backupResult.value.zip_path },
    { label: '压缩包大小', value: backupResult.value.size_text },
    { label: '备份时间', value: formatDateTime(backupResult.value.backup_time) },
  ]
})

async function loadBackupLogs(showMessage = false) {
  recordsLoading.value = true

  try {
    const logs = await getLogs()
    recentRecords.value = logs
      .filter(item => item.type === 'backup')
      .reverse()
      .slice(0, 10)
      .map(item => ({
        time: formatDateTime(item.time),
        project: basename(item.source),
        file: basename(item.zip_path),
        count: formatCount(item.file_count),
        size: formatBytes(item.size),
        result: item.success ? '成功' : '失败',
      }))

    if (showMessage) ElMessage.success('备份记录已刷新')
  } catch (error) {
    recentRecords.value = []
    ElMessage.error(error.message)
  } finally {
    recordsLoading.value = false
  }
}

async function startBackup() {
  const source = projectPath.value.trim()
  const destination = backupPath.value.trim()

  if (!source || !destination) {
    ElMessage.warning('请输入项目目录和备份保存目录')
    return
  }

  backingUp.value = true
  statusText.value = '正在打包项目，请稍候...'

  try {
    backupResult.value = await backupProject(source, destination)
    statusText.value = `备份完成：已生成 ${backupResult.value.size_text} 的 ZIP 快照`
    ElMessage.success('项目 ZIP 备份完成')
    await loadBackupLogs()
  } catch (error) {
    statusText.value = '备份失败，请检查目录路径或确认后端服务已启动'
    ElMessage.error(error.message)
  } finally {
    backingUp.value = false
  }
}

onMounted(loadBackupLogs)
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="项目备份"
      description="给本地项目先留一个 ZIP 快照，再去做整理、清理或批量移动。备份这一页只做一件事：快速、可靠地留底。"
      eyebrow="备份"
    >
      <template #meta>
        <div class="meta-pill">本机生成 ZIP</div>
        <div class="meta-pill">结果写入日志</div>
      </template>
      <template #actions>
        <el-button type="primary" :loading="backingUp" @click="startBackup">
          <el-icon><RefreshRight /></el-icon>{{ backingUp ? '正在备份' : '开始备份' }}
        </el-button>
      </template>
    </PageHeader>

    <section class="command-board app-card">
      <div class="command-board__copy">
        <span class="surface-caption">备份目录</span>
        <h3>先留一份快照，再动项目。</h3>
        <p>系统会在本机直接生成 ZIP 文件，并自动跳过常见依赖目录和构建产物，减少体积和干扰。</p>
      </div>

      <div class="command-board__form">
        <div class="command-board__grid">
          <div>
            <span class="field-label">项目目录</span>
            <el-input
              v-model="projectPath"
              size="large"
              placeholder="例如：D:\\Workspace\\ProjectNova"
              clearable
            >
              <template #prefix><el-icon><FolderOpened /></el-icon></template>
            </el-input>
          </div>

          <div>
            <span class="field-label">备份保存目录</span>
            <el-input
              v-model="backupPath"
              size="large"
              placeholder="例如：D:\\Backups"
              clearable
              @keyup.enter="startBackup"
            >
              <template #prefix><el-icon><FolderOpened /></el-icon></template>
            </el-input>
          </div>
        </div>

        <div class="status-card">
          <strong>{{ statusText }}</strong>
          <span>默认会跳过依赖、构建产物、虚拟环境和 Git 目录。</span>
        </div>
      </div>
    </section>

    <section class="metric-strip">
      <div v-for="item in summaryMetrics" :key="item.label" class="metric-strip__item">
        <div class="metric-strip__label">{{ item.label }}</div>
        <div class="metric-strip__value">{{ item.value }}</div>
      </div>
    </section>

    <section class="section-grid">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">默认排除项</h3>
            <p class="panel-subtitle">为了让快照更干净，系统默认会忽略这些常见目录。</p>
          </div>
        </div>

        <div class="tag-panel">
          <el-tag v-for="item in exclusions" :key="item" effect="plain" type="info">{{ item }}</el-tag>
        </div>
      </article>

      <article class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">最近一次备份</h3>
            <p class="panel-subtitle">确认输出路径、大小和时间，确保这次快照真的落盘了。</p>
          </div>
        </div>

        <template v-if="backupResult">
          <div class="result-head">
            <div class="result-icon">
              <el-icon :size="22"><CircleCheck /></el-icon>
            </div>
            <div>
              <span>状态</span>
              <strong>{{ backupResult.success ? '备份成功' : '备份失败' }}</strong>
            </div>
            <el-tag :type="backupResult.success ? 'success' : 'danger'" effect="light">
              {{ backupResult.success ? '成功' : '失败' }}
            </el-tag>
          </div>

          <div class="brief-list">
            <div v-for="item in latestBrief" :key="item.label" class="brief-item">
              <span>{{ item.label }}</span>
              <strong :title="item.value">{{ item.value }}</strong>
            </div>
          </div>
        </template>

        <el-empty v-else description="还没有备份记录" :image-size="80" />
      </article>
    </section>

    <section class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">最近备份记录</h3>
          <p class="panel-subtitle">来自本地日志，可直接回看最近几次成功或失败的备份。</p>
        </div>
        <el-button plain :loading="recordsLoading" @click="loadBackupLogs(true)">刷新记录</el-button>
      </div>

      <DataTable v-loading="recordsLoading" :columns="columns" :data="recentRecords" empty-text="暂无真实备份记录">
        <template #result="{ value }">
          <el-tag :type="value === '成功' ? 'success' : 'danger'" size="small" effect="light">{{ value }}</el-tag>
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
.brief-item strong {
  display: block;
}

.status-card strong {
  color: var(--text-primary);
  font-size: 14px;
}

.status-card span,
.brief-item span {
  margin-top: 6px;
  color: var(--text-muted);
  font-size: 12px;
}

.tag-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.result-head {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.result-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  color: var(--success);
  background: rgba(23, 122, 95, 0.1);
  border-radius: 12px;
}

.result-head span,
.result-head strong {
  display: block;
}

.result-head span {
  color: var(--text-muted);
  font-size: 12px;
}

.result-head strong,
.brief-item strong {
  color: var(--text-primary);
  font-size: 14px;
}

.result-head strong {
  margin-top: 4px;
  font-size: 16px;
}

.brief-list {
  display: grid;
  gap: 10px;
}

.brief-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.brief-item strong {
  overflow: hidden;
  margin-top: 6px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1080px) {
  .command-board {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .command-board__grid,
  .result-head {
    grid-template-columns: 1fr;
  }
}
</style>

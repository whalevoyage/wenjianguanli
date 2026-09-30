<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, FolderOpened, Lock, Search } from '@element-plus/icons-vue'
import { getCachedDuplicates, moveDuplicateFiles, scanDuplicates } from '../api/filepilot'
import PageHeader from '../components/PageHeader.vue'
import { formatCount, formatDateTime, formatDuration } from '../utils/formatters'

const cached = getCachedDuplicates()

const directory = ref(cached?.root || '')
const result = ref(cached)
const lastMove = ref(null)
const loading = ref(false)
const moving = ref(false)
const activeGroups = ref((cached?.groups || []).slice(0, 2).map((_, index) => String(index)))
const statusText = ref(
  cached
    ? `最近查重完成于 ${formatDateTime(cached.scan_time)}`
    : '输入一个目录后开始真实查重，系统会先按文件大小筛选，再用 SHA256 精确识别重复副本。',
)

const rules = [
  '每组默认保留第一份文件，其余副本只会移动到恢复区，不会直接删除。',
  '查重只会对大小相同且非空的候选文件计算哈希，速度和准确率会更平衡。',
  '如果目录里存在权限不足或被占用的文件，系统会跳过并在结果里提示。',
]

const allRedundantFiles = computed(() =>
  (result.value?.groups || []).flatMap(group => group.redundant_files),
)

const summaryMetrics = computed(() => {
  if (!result.value) return []

  return [
    { label: '扫描文件', value: formatCount(result.value.scanned_files) },
    { label: '重复组数', value: formatCount(result.value.group_count) },
    { label: '重复副本', value: formatCount(result.value.redundant_file_count) },
    { label: '可释放空间', value: result.value.reclaimable_size_text },
  ]
})

const scanBrief = computed(() => {
  if (!result.value) return []

  return [
    { label: '查重目录', value: result.value.root },
    { label: '完成时间', value: formatDateTime(result.value.scan_time) },
    { label: '扫描耗时', value: formatDuration(result.value.duration_seconds) },
    { label: '读取异常', value: `${result.value.errors?.length || 0} 项` },
  ]
})

const groupChart = computed(() => {
  const groups = (result.value?.groups || []).slice(0, 6)
  const max = Math.max(...groups.map(item => Number(item.reclaimable_size || 0)), 1)

  return groups.map((item, index) => ({
    ...item,
    label: `重复组 ${String(index + 1).padStart(2, '0')}`,
    width: `${Math.max((Number(item.reclaimable_size || 0) / max) * 100, 8)}%`,
  }))
})

async function startDuplicateScan() {
  const path = directory.value.trim()
  if (!path) {
    ElMessage.warning('请输入要查重的完整目录路径')
    return
  }

  loading.value = true
  statusText.value = '正在扫描并识别重复文件，请稍候...'

  try {
    result.value = await scanDuplicates(path)
    activeGroups.value = result.value.groups.slice(0, 2).map((_, index) => String(index))
    statusText.value = `查重完成：发现 ${formatCount(result.value.group_count)} 组重复文件`
    ElMessage.success('重复文件检测完成')

    if (result.value.errors?.length) {
      ElMessage.warning(`有 ${result.value.errors.length} 个项目因权限或读取问题被跳过`)
    }
  } catch (error) {
    statusText.value = '查重失败，请检查目录路径或确认后端服务已启动'
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function moveDuplicates(files, label) {
  if (!result.value?.root) {
    ElMessage.warning('请先完成一次重复文件检测')
    return
  }

  if (!files.length) {
    ElMessage.info('当前没有可移动的重复副本')
    return
  }

  moving.value = true

  try {
    const payload = await moveDuplicateFiles(result.value.root, files)
    lastMove.value = payload

    if (payload.success) {
      ElMessage.success(`${label}已移动到恢复区`)
    } else {
      ElMessage.warning('部分文件未能移动，已保留本次处理详情')
    }

    result.value = await scanDuplicates(result.value.root)
    activeGroups.value = result.value.groups.slice(0, 2).map((_, index) => String(index))
    statusText.value = '重复副本处理完成，查重结果已刷新'
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    moving.value = false
  }
}
</script>

<template>
  <div class="section-stack">
    <PageHeader
      title="重复文件"
      description="真实目录先查重，再按组处理。系统默认只移动副本到恢复区，不会直接删除原文件。"
      eyebrow="本地查重"
    >
      <template #meta>
        <div class="meta-pill">真实目录扫描</div>
        <div class="meta-pill">SHA256 校验</div>
      </template>
      <template #actions>
        <el-tag type="success" effect="light">
          <el-icon><Lock /></el-icon>&nbsp;安全移动模式
        </el-tag>
      </template>
    </PageHeader>

    <section class="command-board app-card">
      <div class="command-board__copy">
        <span class="surface-caption">查重目录</span>
        <h3>先找出副本，再决定处理哪些。</h3>
        <p>最适合先查素材库、下载目录、备份目录这类容易重复堆积的位置。系统只会把副本移到恢复区，方便回看。</p>
      </div>

      <div class="command-board__form">
        <div>
          <span class="field-label">目录路径</span>
          <el-input
            v-model="directory"
            size="large"
            placeholder="例如：D:\\Workspace"
            clearable
            @keyup.enter="startDuplicateScan"
          >
            <template #prefix><el-icon><FolderOpened /></el-icon></template>
          </el-input>
        </div>

        <div class="command-board__actions">
          <el-button type="primary" size="large" :loading="loading" @click="startDuplicateScan">
            <el-icon><Search /></el-icon>{{ loading ? '正在查重' : '开始查重' }}
          </el-button>
          <el-button
            plain
            size="large"
            :loading="moving"
            :disabled="!allRedundantFiles.length"
            @click="moveDuplicates(allRedundantFiles, '全部重复副本')"
          >
            移动全部副本
          </el-button>
        </div>

        <div class="status-card">
          <strong>{{ statusText }}</strong>
          <span>结果会缓存到当前会话，方便你反复对比和处理。</span>
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

      <el-alert v-if="result.errors?.length" type="warning" :closable="false" show-icon>
        本次查重有 {{ result.errors.length }} 个项目无法读取，通常是权限不足、文件占用或路径异常导致。
      </el-alert>

      <el-alert v-if="lastMove" :type="lastMove.success ? 'success' : 'warning'" :closable="false" show-icon>
        最近一次移动已写入恢复区：{{ lastMove.recovery_dir }}，共处理 {{ lastMove.moved_count }} 个副本。
      </el-alert>

      <section class="section-grid">
        <article class="app-card panel-card">
          <div class="panel-title-row">
            <div>
              <h3 class="panel-title">重复组明细</h3>
              <p class="panel-subtitle">每组默认保留第一份，其余副本可以按组处理，也可以一键移动全部副本。</p>
            </div>
            <el-tag effect="plain">{{ formatCount(result.group_count) }} 组</el-tag>
          </div>

          <el-empty v-if="!result.groups.length" description="当前目录没有检测到重复文件" :image-size="88" />

          <el-collapse v-else v-model="activeGroups">
            <el-collapse-item v-for="(group, index) in result.groups" :key="group.sha256" :name="String(index)">
              <template #title>
                <div class="group-title">
                  <span class="group-index">{{ index + 1 }}</span>
                  <div class="group-main">
                    <strong>重复组 {{ String(index + 1).padStart(2, '0') }}</strong>
                    <small>{{ group.file_count }} 个文件 · 单文件 {{ group.size_text }}</small>
                  </div>
                  <div class="group-side">
                    <strong>{{ group.reclaimable_size_text }}</strong>
                    <small>可释放</small>
                  </div>
                </div>
              </template>

              <div class="path-list">
                <div v-for="(path, fileIndex) in group.files" :key="path" class="path-row">
                  <el-icon><CopyDocument /></el-icon>
                  <span class="path-text" :title="path">{{ path }}</span>
                  <el-tag v-if="fileIndex === 0" type="success" size="small" effect="light">保留</el-tag>
                  <el-tag v-else type="warning" size="small" effect="plain">副本</el-tag>
                </div>
              </div>

              <div class="group-actions">
                <el-button
                  type="warning"
                  plain
                  :loading="moving"
                  :disabled="!group.redundant_files.length"
                  @click="moveDuplicates(group.redundant_files, `重复组 ${String(index + 1).padStart(2, '0')}`)"
                >
                  仅处理本组副本
                </el-button>
                <span>只移动到恢复区，便于后续回看。</span>
              </div>
            </el-collapse-item>
          </el-collapse>
        </article>

        <div class="content-stack">
          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">可释放空间图</h3>
                <p class="panel-subtitle">按组看一眼，优先处理最占空间的那几组。</p>
              </div>
            </div>

            <div v-if="groupChart.length" class="chart-list">
              <div v-for="item in groupChart" :key="item.sha256" class="chart-item">
                <div class="chart-item__head">
                  <strong>{{ item.label }}</strong>
                  <span>{{ item.reclaimable_size_text }}</span>
                </div>
                <div class="chart-bar">
                  <div class="chart-bar__fill" :style="{ width: item.width }"></div>
                </div>
                <small>{{ item.file_count }} 个文件 · SHA256 {{ item.sha256_short }}</small>
              </div>
            </div>

            <el-empty v-else description="暂无重复组数据" :image-size="72" />
          </article>

          <article class="app-card panel-card">
            <div class="panel-title-row">
              <div>
                <h3 class="panel-title">本次摘要</h3>
                <p class="panel-subtitle">把关键上下文收在一侧，方便快速判断。</p>
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
                <h3 class="panel-title">处理规则</h3>
                <p class="panel-subtitle">真实数据场景下，把边界说清楚比花哨更重要。</p>
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

.group-title {
  width: 100%;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding-right: 12px;
}

.group-index {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  color: var(--primary-strong);
  background: rgba(23, 104, 172, 0.1);
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
}

.group-main strong,
.group-main small,
.group-side strong,
.group-side small {
  display: block;
}

.group-main strong,
.group-side strong,
.brief-item strong,
.chart-item__head strong {
  color: var(--text-primary);
  font-size: 14px;
}

.group-main small,
.group-side small {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 12px;
}

.group-side {
  text-align: right;
}

.path-list,
.chart-list,
.brief-list,
.rule-list {
  display: grid;
  gap: 10px;
}

.path-list {
  padding: 8px 0 12px 44px;
}

.path-row,
.brief-item,
.chart-item,
.rule-item {
  padding: 14px 16px;
  background: #fafbfd;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.path-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.path-row .el-icon {
  color: var(--text-muted);
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

.group-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 0 8px 44px;
}

.group-actions span {
  color: var(--text-muted);
  font-size: 12px;
}

:deep(.el-collapse) {
  border: 0;
}

:deep(.el-collapse-item__header) {
  height: 72px;
  border-top: 1px solid var(--border);
  border-bottom: 0;
}

:deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}

@media (max-width: 1080px) {
  .command-board {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .command-board__actions,
  .chart-item__head,
  .group-actions {
    grid-template-columns: 1fr;
  }

  .command-board__actions,
  .group-actions {
    display: grid;
  }

  .group-title {
    grid-template-columns: 1fr;
  }

  .group-side {
    text-align: left;
  }

  .path-list,
  .group-actions {
    padding-left: 0;
  }

  .path-row {
    grid-template-columns: 1fr;
  }
}
</style>

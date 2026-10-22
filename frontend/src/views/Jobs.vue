<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Operation, Promotion, Refresh } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import {
  approveAgentTask,
  cancelJob,
  getJournal,
  listJobs,
  rollbackJob,
  undoJournalEntry,
} from '../api/filepilot'
import JobProgress from '../components/JobProgress.vue'
import { formatDateTime, formatShortTime } from '../utils/formatters'

const jobs = ref([])
const loading = ref(false)
const selectedId = ref('')
const typeFilter = ref('全部')

const typeOptions = computed(() => ['全部', ...new Set(jobs.value.map(job => job.type_label).filter(Boolean))])

const filtered = computed(() => (
  typeFilter.value === '全部'
    ? jobs.value
    : jobs.value.filter(job => job.type_label === typeFilter.value)
))

const selected = computed(() => jobs.value.find(job => job.id === selectedId.value) || null)

/**
 * 「进行中」只数真的在跑的；「待确认」单独数 —— 它是**等用户动手**的状态。
 * 以前只数 running/pending，于是一个停在「等待确认」的任务会让这里写「进行中 0 个」，
 * 而右边详情还写着「等待确认」：用户会以为任务丢了（2026-10-11 用户报的就是这个）。
 */
const activeCount = computed(() => jobs.value.filter(job => ['running', 'pending'].includes(job.status)).length)
const waitingCount = computed(() => jobs.value.filter(job => job.status === 'waiting').length)

/** 停在「等待你确认」的 Agent 任务 —— 它需要一个「继续执行」的入口。 */
const canResume = job => Boolean(job) && job.type === 'agent_task' && job.status === 'waiting'

async function resumeAgentTask(job) {
  if (!job) return
  try {
    await approveAgentTask(job.id)
    await load()
    selectedId.value = job.id
    ElMessage.success('已继续执行')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

/* ---- 最近操作（Phase 8 的后端能力，Phase 13 接到界面）----
   这里回答的是「我刚才那一下还能不能撤回」，跟下面的任务列表不一样：
   任务是「跑过什么」，这里是「能撤销什么」。 */
const journal = ref([])
const undoing = ref('')

const undoableCount = computed(() => journal.value.filter(item => item.undoable && !item.undone).length)

async function loadJournal() {
  try {
    const data = await getJournal({ limit: 8 })
    journal.value = data.entries || []
  } catch {
    // 流水读不到不该影响任务列表 —— 它只是附加信息
    journal.value = []
  }
}

function sourceLabel(source) {
  return { user: '你', agent: 'Agent', scheduler: '定时' }[source] || source
}

/** 为什么这条不能撤 —— 必须说清原因，否则"不可撤销"看起来像 bug。 */
function whyLocked(entry) {
  if (entry.type === 'backup') return '备份只产出一份压缩包，没有改动原文件'
  if (entry.type === 'scan' || entry.type === 'index') return '只读操作，没有改动文件'
  if (entry.source === 'agent') return 'Agent 的清理确认后已彻底删除，无法恢复'
  return '这次操作没有可回滚的文件动作'
}

async function undoEntry(entry) {
  const count = entry.action_count || 0
  try {
    await ElMessageBox.confirm(
      `将撤销这次「${entry.title || entry.type}」：把 ${count} 个文件动作反向执行`
      + '（删除的搬回、移动的搬回原位）。已彻底删除的文件不在此列。继续？',
      '撤销这次操作',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  undoing.value = entry.id
  try {
    const result = await undoJournalEntry(entry.id)
    const restored = result.restored_count ?? 0
    const errors = result.errors || []
    if (errors.length) {
      ElMessage.warning(`已撤销 ${restored} 项，${errors.length} 项失败：${errors[0]}`)
    } else {
      ElMessage.success(`已撤销，恢复 ${restored} 项`)
    }
    await Promise.all([loadJournal(), load()])
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    undoing.value = ''
  }
}

async function load(showMessage = false) {
  loading.value = true
  try {
    const data = await listJobs(100)
    jobs.value = data.jobs || []
    if (!selectedId.value && jobs.value.length) selectedId.value = jobs.value[0].id
    await loadJournal()
    if (showMessage) ElMessage.success('任务列表已刷新')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function cancelJobById(job) {
  try {
    await cancelJob(job.id)
    ElMessage.success('已请求中断')
    load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function rollbackJobById(job) {
  try {
    const result = await rollbackJob(job.id)
    result.errors?.length
      ? ElMessage.warning(`回滚完成，${result.errors.length} 项失败`)
      : ElMessage.success(`已回滚 ${result.restored_count} 项`)
    load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

function resultSummary(job) {
  const result = job?.result
  if (!result) return []
  const pairs = [
    ['文件总数', result.total_files],
    ['总体积', result.total_size_text],
    ['重复组', result.group_count],
    ['可回收', result.reclaimable_size_text],
    ['问题数', result.total_count],
    ['可释放', result.total_size_text],
    ['迁移项', result.item_count],
    ['已校验', result.verified_count],
    ['已删除源', result.deleted_count],
    ['清理项', result.cleaned_count],
    ['释放空间', result.freed_size_text],
    ['备份文件数', result.file_count],
    ['压缩包', result.size_text],
  ]
  return pairs
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 6)
    .map(([label, value]) => ({ label, value: String(value) }))
}

/** 参数值展示：数组拆成小 tag，空值显示 —，其余对象转紧凑 JSON */
function displayParam(value) {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

onMounted(() => load())
</script>

<template>
  <div v-loading="loading" class="section-stack">
    <!-- 最近操作：Phase 8 在后端做了撤销能力，这里是它的入口。
         跟下面的任务列表的区别：任务是「跑过什么」，这里是「能撤销什么」。 -->
    <section class="app-card panel-card">
      <div class="panel-title-row">
        <h3 class="panel-title">最近操作</h3>
        <div class="panel-title-actions">
          <span class="panel-count">
            <template v-if="undoableCount">可撤销 {{ undoableCount }} 条</template>
            <template v-else>暂无可撤销的操作</template>
          </span>
        </div>
      </div>

      <ul v-if="journal.length" class="journal-list">
        <li v-for="entry in journal" :key="entry.id" class="journal-item">
          <div class="journal-item__body">
            <span class="journal-item__title">{{ entry.title || entry.type }}</span>
            <span class="journal-item__meta">
              {{ formatShortTime(entry.at) }} · {{ sourceLabel(entry.source) }}
              · 成功 {{ entry.done }}<template v-if="entry.failed"> · 失败 {{ entry.failed }}</template>
            </span>
          </div>

          <span v-if="entry.undone" class="journal-item__state">已撤销</span>
          <el-button
            v-else-if="entry.undoable"
            size="small"
            :loading="undoing === entry.id"
            @click="undoEntry(entry)"
          >
            撤销
          </el-button>
          <!-- 不可撤销的必须给出原因，否则看起来像按钮坏了 -->
          <span v-else class="journal-item__locked" :title="whyLocked(entry)">不可撤销</span>
        </li>
      </ul>
      <p v-else class="journal-empty">
        还没有操作记录 —— 清理、迁移、整理跑完会出现在这里。
      </p>
    </section>
    <section class="jobs-layout">
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <h3 class="panel-title">任务列表</h3>
          <div class="panel-title-actions">
            <span class="panel-count">
              进行中 {{ activeCount }} 个<template v-if="waitingCount"> · 待确认 {{ waitingCount }} 个</template> · 共 {{ jobs.length }} 条
            </span>
            <el-button :icon="Refresh" text circle title="刷新" @click="load(true)" />
          </div>
        </div>

        <div v-if="jobs.length" class="type-chips">
          <button
            v-for="item in typeOptions"
            :key="item"
            type="button"
            class="type-chip"
            :class="{ 'is-active': typeFilter === item }"
            @click="typeFilter = item"
          >
            {{ item === '全部' ? '全部' : item }}
          </button>
        </div>

        <div v-if="filtered.length" class="job-list">
          <button
            v-for="job in filtered"
            :key="job.id"
            type="button"
            class="job-item"
            :class="{ 'is-active': job.id === selectedId }"
            @click="selectedId = job.id"
          >
            <div class="job-item__head">
              <span class="job-item__type" :class="`is-${job.status}`">{{ job.type_label }}</span>
              <span class="job-item__time" :title="formatDateTime(job.created_at)">{{ formatShortTime(job.created_at) }}</span>
            </div>
            <p class="job-item__title" :title="job.title">{{ job.title }}</p>
          </button>
        </div>

        <EmptyState
          v-else
          :icon="Operation"
          title="还没有任务"
        />
      </article>

      <article class="app-card panel-card">
        <template v-if="selected">
          <JobProgress
            :job="selected"
            @cancel="cancelJobById(selected)"
            @rollback="rollbackJobById(selected)"
          />

          <!-- 停在「等确认」的 Agent 任务：这里是它唯一的通用续跑入口。
               以前只有 Agent 页的计划卡能继续，换个页面就再也推不动了。 -->
          <div v-if="canResume(selected)" class="job-resume">
            <el-button type="primary" :icon="Promotion" @click="resumeAgentTask(selected)">
              继续执行
            </el-button>
            <span class="job-resume__hint">
              这个任务停在等你确认，点「继续执行」进入执行段；永久清理会在方案中标明不可恢复。
            </span>
          </div>

          <div v-if="selected.params && Object.keys(selected.params).length" class="job-detail-section">
            <div class="job-detail-section__title">参数</div>
            <div class="job-params">
              <template v-for="(value, key) in selected.params" :key="key">
                <span class="job-params__key">{{ key }}</span>
                <div class="job-params__value">
                  <template v-if="Array.isArray(value)">
                    <span v-for="(tag, index) in value.slice(0, 5)" :key="index" class="param-tag">{{ tag }}</span>
                    <span v-if="value.length > 5" class="param-tag param-tag--more">+{{ value.length - 5 }}</span>
                  </template>
                  <template v-else>{{ displayParam(value) }}</template>
                </div>
              </template>
            </div>
          </div>

          <div v-if="resultSummary(selected).length" class="job-detail-section">
            <div class="job-detail-section__title">结果</div>
            <div class="result-grid">
              <div v-for="item in resultSummary(selected)" :key="item.label" class="result-item">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
          </div>
        </template>

        <EmptyState
          v-else
          :icon="Operation"
          title="从左侧选一条任务"
        />
      </article>
    </section>
  </div>
</template>

<style scoped>
.jobs-layout {
  display: grid;
  grid-template-columns: minmax(280px, 4fr) minmax(0, 8fr);
  gap: 18px;
  align-items: start;
}

/* 类型筛选 chips：取代原来孤零零占一行的 select + 刷新按钮 */
.type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.type-chip {
  padding: 3px 11px;
  border: 0;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-2);
  font-size: var(--fs-note);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}

.type-chip:hover {
  background: var(--inset);
}

.type-chip.is-active {
  background: var(--primary-soft);
  color: var(--primary-ink);
  font-weight: 600;
}

/* 列表：对齐 Agent 任务面板语言——静止透明 / hover --inset / 当前项 --primary-soft */
.job-list {
  display: grid;
  gap: 2px;
  max-height: calc(100vh - 300px);
  min-height: 200px;
  overflow-y: auto;
}

.job-item {
  padding: 9px 12px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: 10px;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease);
}

.job-item:hover {
  background: var(--inset);
}

.job-item.is-active {
  background: var(--primary-soft);
}

.job-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

/* 状态点前置在类型名前：蓝=进行/排队，橙=等待确认，绿=完成，红=失败，灰=取消 */
.job-item__type {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ink);
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}

/* 待确认的任务：给它一行提示 + 一个主按钮，别让它埋在列表里没人管 */
.job-resume {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--inset);
}

.job-resume__hint {
  color: var(--ink-3);
  font-size: var(--fs-note);
  line-height: 1.5;
}

.job-item__type::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-4);
  flex: none;
}

.job-item__type.is-running::before,
.job-item__type.is-pending::before {
  background: var(--primary);
}

.job-item__type.is-waiting::before {
  background: var(--warning);
}

.job-item__type.is-done::before {
  background: var(--success);
}

.job-item__type.is-failed::before {
  background: var(--danger);
}

.job-item__time {
  flex: none;
  color: var(--ink-3);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.job-item__title {
  overflow: hidden;
  margin: 3px 0 0 12px;
  color: var(--ink-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 参数区：两列 key/value 网格，数组值拆小 tag（原 global.css 里重复两份的样式收归此处） */
.job-detail-section + .job-detail-section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--hairline);
}

.job-detail-section__title {
  margin-bottom: 10px;
  color: var(--ink-3);
  font-size: var(--fs-micro);
  font-weight: 600;
  letter-spacing: 0.04em;
}

.job-params {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 8px 14px;
  align-items: baseline;
}

.job-params__key {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.job-params__value {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  color: var(--ink);
  font-size: var(--fs-note);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.param-tag {
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  background: var(--inset);
  color: var(--ink-2);
  font-size: var(--fs-note);
  font-weight: 400;
}

.param-tag--more {
  color: var(--ink-3);
}

/* 结果摘要：去边框，纯 --inset 底 */
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 8px;
}

.result-item {
  padding: 10px 12px;
  background: var(--inset);
  border-radius: 10px;
}

.result-item span {
  display: block;
  color: var(--ink-3);
  font-size: 12px;
}

.result-item strong {
  display: block;
  margin-top: 2px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

@media (max-width: 1100px) {
  .jobs-layout {
    grid-template-columns: 1fr;
  }
}

/* ---- 最近操作 ---- */
.journal-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.journal-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 12px;
  background: var(--surface-2);
  border-radius: 10px;
}

.journal-item__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.journal-item__title {
  font-size: var(--fs-body);
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.journal-item__meta {
  font-size: var(--fs-meta);
  color: var(--ink-3);
}

.journal-item__state {
  font-size: var(--fs-meta);
  color: var(--ink-3);
  flex: none;
}

.journal-item__locked {
  flex: none;
  font-size: var(--fs-meta);
  color: var(--ink-4);
  cursor: help;
  border-bottom: 1px dashed var(--ink-4);
}

.journal-empty {
  margin: 0;
  padding: 10px 2px;
  font-size: var(--fs-meta);
  color: var(--ink-3);
}
</style>

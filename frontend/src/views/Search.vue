<script setup>
/**
 * 全文检索（B1）。
 *
 * 分两块，刻意不对称：
 *   · **建索引**是 Job —— 耗时长、要进度、要能中断；
 *   · **检索**是即时 GET —— 敲完回车就要出结果，套一层 Job + SSE 只会平白加延迟。
 *
 * 中文是靠「逐字预切 + 相邻短语」搜到的：FTS5 默认分词器会把一整段中文当成一个 token，
 * 2 字词根本命中不了（细节见 `docs/SEARCH_PLAN.md`）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Files, FolderOpened, Search, Timer } from '@element-plus/icons-vue'
import { getSearchStatus, removeSearchRoot, revealFile, searchFiles, startSearchIndex } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import { useHandoffTarget } from '../composables/useHandoff'
import HandoffNote from '../components/HandoffNote.vue'
import NextSteps from '../components/NextSteps.vue'
import TargetPicker from '../components/TargetPicker.vue'
import JobProgress from '../components/JobProgress.vue'
import EmptyState from '../components/EmptyState.vue'

/* 高亮标记：后端插在命中词两侧的两颗控制字符。
   前端按它切段渲染 —— **不走 v-html**：文件名与正文内容不可信，不能直接当 HTML 注入。 */
const MARK_OPEN = '\u0001'
const MARK_CLOSE = '\u0002'

/** 把带标记的文本切成 [{text, hit}] 片段 */
function segments(text) {
  if (!text) return []
  const parts = []
  let rest = text
  while (rest) {
    const start = rest.indexOf(MARK_OPEN)
    if (start < 0) {
      parts.push({ text: rest, hit: false })
      break
    }
    if (start > 0) parts.push({ text: rest.slice(0, start), hit: false })
    let tail = rest.slice(start + 1)
    const end = tail.indexOf(MARK_CLOSE)
    if (end < 0) {
      parts.push({ text: tail, hit: true })
      break
    }
    parts.push({ text: tail.slice(0, end), hit: true })
    rest = tail.slice(end + 1)
  }
  return parts.filter(part => part.text)
}

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel } = useJob('search.index')

const paths = ref([])
/* 跨页接力：从别的页面带着目录过来时，追加进列表（不自动开跑） */
const { handed, from } = useHandoffTarget(paths, { multiple: true })
/* 反向接力只用一个主目录即可 */
const primaryPath = computed(() => paths.value[0] || '')

const keyword = ref('')
const status = ref({ enabled: true, roots: [], total_files: 0 })
const result = ref(null)
const lastIndexed = ref(null)
const searching = ref(false)
/* 进页面时自动更新过期索引（走的是增量：只重读变化的文件，没变的一律跳过）。
   默认关 —— 原设计是「只提示、不自动跑」，这个开关把选择权交给用户。 */
const AUTO_REFRESH_KEY = 'atlas.search.autoRefresh'
const autoRefresh = ref(false)
try {
  autoRefresh.value = localStorage.getItem(AUTO_REFRESH_KEY) === '1'
} catch {
  autoRefresh.value = false   /* 隐私模式等拿不到 localStorage，当作关闭 */
}

/** 结果之后能干什么 —— 这一页也要遵守「看 → 挑 → 执行」契约，不能是路的尽头 */
const nextSteps = [
  { name: 'duplicates', label: '查重' },
  { name: 'junk', label: '清理' },
  { name: 'space', label: '空间地图' },
]

/* 按钮灰着必须说明原因（契约：不许干灰着） */
const indexHint = computed(() => {
  if (!status.value.enabled) return '当前构建不支持全文索引（SQLite 缺少 FTS5）。'
  if (!paths.value.some(p => p.trim())) return '先选一个要建立索引的目录。'
  return '把文件名与正文建进本地索引 —— 只读取文件，不移动、不删除任何东西。'
})

const searchHint = computed(() => {
  if (!status.value.enabled) return '当前构建不支持全文索引。'
  if (!status.value.total_files) return '还没有任何索引 —— 先在上面建一次。'
  if (!result.value) return '输入关键词后按回车查找。'
  if (!result.value.total) return `没有匹配「${result.value.query}」的文件。`
  return `命中 ${result.value.total} 个 · 耗时 ${result.value.took_ms} ms`
})

/* 索引过期：**只提示，不自动重建**（方案拍板点⑥）。
   重建是个可能跑很久的任务 —— 自用工具不该在用户没点过的情况下自己发起。 */
const staleNote = computed(() => {
  const stale = (status.value.roots || []).filter(root => root.stale)
  if (!stale.length) return ''
  const oldest = Math.max(...stale.map(root => root.age_days || 0))
  const days = status.value.stale_days ?? 7
  return `索引是 ${oldest} 天前建的，可能已经过期（超过 ${days} 天）。需要的话点上面的「建立 / 更新索引」—— 不会自动重建。`
})

const rows = computed(() => (result.value?.results || []).map(item => ({
  ...item,
  nameParts: segments(item.name),
  snippetParts: segments(item.snippet),
})))

const canIndex = computed(() => paths.value.some(p => p.trim()) && status.value.enabled && !running.value)
const canSearch = computed(() => Boolean(keyword.value.trim()) && Boolean(status.value.total_files) && !searching.value)

async function loadStatus() {
  try {
    status.value = await getSearchStatus()
  } catch {
    /* 状态拿不到不影响建索引与检索，静默即可 */
  }
}

async function buildIndex() {
  if (!canIndex.value) return
  const targets = paths.value.map(p => p.trim()).filter(Boolean)
  await run(() => startSearchIndex({
    paths: targets,
    excludes: excludes.value,
  }))
}

/** 开着开关、且有过期索引且当前没在跑 —— 就自动跑一次增量更新。 */
async function autoRefreshStale() {
  if (!autoRefresh.value || running.value || !status.value.enabled) return
  const stale = (status.value.roots || []).filter(root => root.stale).map(root => root.path)
  if (!stale.length) return
  await run(() => startSearchIndex({ paths: stale, excludes: excludes.value }))
}

function onAutoRefreshChange(value) {
  try {
    localStorage.setItem(AUTO_REFRESH_KEY, value ? '1' : '0')
  } catch {
    /* 隐私模式等拿不到，忽略即可 */
  }
  if (value) autoRefreshStale()
}

async function search() {
  if (!canSearch.value) return
  searching.value = true
  try {
    result.value = await searchFiles(keyword.value.trim())
  } finally {
    searching.value = false
  }
}

async function forgetRoot(rootPath) {
  await removeSearchRoot(rootPath)
  result.value = null
  await loadStatus()
}

/** 在资源管理器里选中这个文件。后端只放行**已索引过**的路径，失败会给原因。 */
async function reveal(itemPath) {
  try {
    await revealFile(itemPath)
  } catch (error) {
    ElMessage.error(error.message || '打不开这个位置')
  }
}

/* 索引任务跑完就刷新：根目录数与文件数变了，搜索按钮的可用状态也跟着变 */
watch(() => job.value?.status, value => {
  if (value === 'done') {
    lastIndexed.value = job.value?.result || null
    loadStatus()
  }
})

onMounted(async () => {
  await loadConfig().catch(() => {})
  await loadStatus()
  await autoRefreshStale()
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <HandoffNote v-if="handed" :path="handed" :from="from" @dismiss="handed = ''" />

      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">建立索引</h3>
          <p class="panel-subtitle">只在本地 SQLite 里建一份索引，不上传任何内容</p>
        </div>
      </div>

      <TargetPicker
        :multiple="true"
        v-model:paths="paths"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="要索引的目录"
        placeholder="可多选：一次给多个目录建索引"
      />

      <div class="row-actions">
        <span class="tree-hint">{{ indexHint }}</span>
        <el-button type="primary" size="large" :icon="Search" :loading="running" :disabled="!canIndex" @click="buildIndex">
          {{ running ? '正在建索引' : '建立 / 更新索引' }}
        </el-button>
      </div>

      <el-alert
        v-if="!status.enabled"
        class="panel-note"
        type="warning"
        :closable="false"
        show-icon
        title="当前构建的 SQLite 没有 FTS5，全文检索不可用（其余功能不受影响）。"
      />

      <JobProgress v-if="job" :job="job" :rollbackable="false" empty-title="先建一次索引" @cancel="cancel" />

      <div v-if="lastIndexed" class="index-summary">
        <span>新增 {{ lastIndexed.added }}</span>
        <span>更新 {{ lastIndexed.updated }}</span>
        <span>跳过 {{ lastIndexed.skipped }}</span>
        <span>移除 {{ lastIndexed.removed }}</span>
        <span v-if="lastIndexed.text_files">含正文 {{ lastIndexed.text_files }}</span>
        <span v-if="lastIndexed.truncated" class="index-summary__warn">已达单次上限，结果已截断</span>
      </div>
    </section>

    <section v-if="status.roots.length" class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">已索引目录</h3>
          <p class="panel-subtitle">共 {{ status.total_files }} 个文件</p>
        </div>
      </div>

      <el-alert
        v-if="staleNote"
        class="panel-note"
        type="warning"
        :closable="false"
        show-icon
        :title="staleNote"
      />

      <div class="auto-refresh-row">
        <el-checkbox v-model="autoRefresh" @change="onAutoRefreshChange">
          进页面时自动更新过期索引
        </el-checkbox>
        <span class="auto-refresh-row__note">增量更新，只重读变化的文件；关闭时只提示、不自动跑</span>
      </div>

      <div class="root-list">
        <div v-for="root in status.roots" :key="root.id" class="root-item">
          <div class="root-item__main">
            <strong class="root-item__path" :title="root.path">{{ root.path }}</strong>
            <span class="root-item__meta">
              {{ root.file_count }} 个文件 · {{ root.indexed_at || '尚未索引' }}
            </span>
          </div>
          <el-button text type="primary" @click="forgetRoot(root.path)">移除索引</el-button>
        </div>
      </div>
    </section>

    <section class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">查找</h3>
          <p class="panel-subtitle">按文件名与正文内容查找，命中的词会高亮</p>
        </div>
      </div>

      <div class="row-actions">
        <el-input
          v-model="keyword"
          class="search-input"
          :prefix-icon="Search"
          placeholder="例如：表型、budget、report notes"
          clearable
          @keyup.enter="search"
        />
        <span class="tree-hint">{{ searchHint }}</span>
        <el-button type="primary" size="large" :icon="Search" :loading="searching" :disabled="!canSearch" @click="search">
          查找
        </el-button>
      </div>
    </section>

    <template v-if="result">
      <NextSteps v-if="primaryPath" :path="primaryPath" origin="search" :steps="nextSteps" />

      <section v-if="rows.length" class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">命中结果</h3>
            <p class="panel-subtitle">{{ result.total }} 个 · {{ result.took_ms }} ms</p>
          </div>
        </div>

        <div class="hit-list">
          <article v-for="item in rows" :key="item.path" class="hit">
            <div class="hit__head">
              <span class="hit__icon"><el-icon><Files /></el-icon></span>
              <span class="hit__name">
                <span v-for="(part, index) in item.nameParts" :key="index" :class="{ 'is-hit': part.hit }">{{ part.text }}</span>
              </span>
              <span class="hit__size">{{ item.size_text }}</span>
            </div>

            <p v-if="item.snippetParts.length" class="hit__snippet">
              <span v-for="(part, index) in item.snippetParts" :key="index" :class="{ 'is-hit': part.hit }">{{ part.text }}</span>
            </p>

            <div class="hit__meta">
              <span class="hit__dir" :title="item.path"><el-icon><FolderOpened /></el-icon>{{ item.directory }}</span>
              <span>{{ item.modified_time }}</span>
              <span v-if="!item.is_text" class="hit__tag">只索引了文件名</span>
              <el-button class="hit__reveal" text type="primary" @click="reveal(item.path)">
                在资源管理器打开
              </el-button>
            </div>
          </article>
        </div>
      </section>

      <EmptyState
        v-else
        :icon="Search"
        title="没有匹配的文件"
      />

      <el-alert
        v-if="result.truncated"
        type="info"
        :closable="false"
        show-icon
        title="命中较多，只显示前面一部分 —— 把关键词写得更具体能收窄结果。"
      />
    </template>

    <section v-else class="app-card panel-card">
      <EmptyState
        :icon="Timer"
        :title="status.total_files ? '输入关键词开始查找' : '先建一次索引'"
      />
    </section>
  </div>
</template>

<style scoped>
.search-input {
  flex: 1 1 320px;
  min-width: 240px;
}

.index-summary {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-3);
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.index-summary__warn {
  color: var(--warning);
}

.root-list,
.hit-list {
  display: grid;
  gap: var(--space-2);
}

.root-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
}

.root-item__main {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.root-item__path {
  overflow: hidden;
  color: var(--ink);
  font-size: var(--fs-body);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.root-item__meta {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.hit {
  display: grid;
  gap: 6px;
  padding: var(--space-3) var(--space-4);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
}

.hit__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.hit__icon {
  display: inline-flex;
  color: var(--ink-3);
}

.hit__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink);
  font-size: var(--fs-body);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hit__size {
  color: var(--ink-3);
  font-size: var(--fs-note);
  font-variant-numeric: tabular-nums;
}

.hit__snippet {
  margin: 0;
  color: var(--ink-2);
  font-size: var(--fs-note);
  line-height: 1.6;
  word-break: break-all;
}

.hit__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.hit__dir {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hit__tag {
  color: var(--ink-3);
}

/* 命中高亮：只给背景与字重，不动颜色（色值一律走 token） */
.is-hit {
  background: var(--primary-soft);
  color: var(--ink);
  font-weight: 600;
  border-radius: 3px;
}

/* 自动增量更新开关：与陈旧告警同一块，说明放同一行 */
.auto-refresh-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 8px 0 4px;
}

.auto-refresh-row__note {
  color: var(--ink-3);
  font-size: var(--fs-note);
}
</style>

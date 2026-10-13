<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Coin, Files, FolderOpened, Grid, Lock, PieChart, Sort, Timer, Warning } from '@element-plus/icons-vue'
import { applyVirtual, exportVirtual, startMigrate, startVirtual } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import NextSteps from '../components/NextSteps.vue'
import HandoffNote from '../components/HandoffNote.vue'
import { useHandoffTarget } from '../composables/useHandoff'
import DataTable from '../components/DataTable.vue'
import EmptyState from '../components/EmptyState.vue'
import JobProgress from '../components/JobProgress.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatBytes, formatCount, formatDuration } from '../utils/formatters'

/**
 * 整理预览：只生成「整理后会是什么样」，一个字节都不动。
 * 真要动手时，这份预览会翻译成 migrate 的清单，走同一套
 * 「复制 → 逐文件校验 → 校验通过才删源」的执行与回滚链路。
 */
const { config, excludes, load: loadConfig } = useConfig()

const preview = useJob('virtual.preview')
const planJob = useJob('virtual.plan')
const execJob = useJob('virtual.exec')
const inPlaceJob = useJob('virtual.inplace')

const path = ref('')
const groupBy = ref('type')
const minSize = ref(0)
const result = ref(null)
const openKeys = ref([])
const target = ref('')
const planResult = ref(null)
const exporting = ref(false)

/* 整理完/整理前都能从这一页去别处看看。
   ★ 必须放在 `const path` **之后** —— 这里要把 path 这个 ref 传进去，
     写在前面会撞上暂时性死区（`Cannot access 'path' before initialization`）。 */
const { handed, from } = useHandoffTarget(path)
const nextSteps = [
  { name: 'space', label: '空间地图' },
  { name: 'duplicates', label: '查重' },
  { name: 'junk', label: '清理' },
]

const modeOptions = computed(() =>
  (config.value?.virtual_modes || []).map(mode => ({ label: mode.label, value: mode.id })),
)

const minSizeOptions = [
  { label: '不限体积', value: 0 },
  { label: '≥ 1 MB', value: 1024 * 1024 },
  { label: '≥ 10 MB', value: 10 * 1024 * 1024 },
  { label: '≥ 100 MB', value: 100 * 1024 * 1024 },
]

const itemColumns = [
  { prop: 'name', label: '文件名', minWidth: 220 },
  { prop: 'path', label: '当前位置', minWidth: 320 },
  { prop: 'modified_time', label: '修改时间', width: 150 },
  { prop: 'size_text', label: '大小', width: 110, align: 'right' },
]

const summary = computed(() => {
  const data = result.value
  if (!data) return []
  return [
    {
      title: '纳入文件',
      value: formatCount(data.kept_files),
      note: '按体积阈值过滤后',
      icon: Files,
      tone: 'blue',
    },
    {
      title: '总体积',
      value: data.scanned_size_text,
      note: '纳入文件的体积合计',
      icon: PieChart,
      tone: 'blue',
    },
    {
      title: '分组数',
      value: formatCount(data.bucket_count),
      note: '按当前分组方式切分',
      icon: Grid,
      tone: 'blue',
    },
    {
      title: '统计耗时',
      value: formatDuration(data.duration_seconds),
      note: '扫描 + 分组',
      icon: Timer,
      tone: 'blue',
    },
  ]
})

const planSummary = computed(() => {
  const data = planResult.value
  if (!data) return []
  const items = [
    {
      title: '待整理文件',
      value: formatCount(data.item_count),
      note: '将按分组移动到目标目录',
      icon: Files,
      tone: 'blue',
    },
    {
      title: '总体积',
      value: data.total_size_text,
      note: '移动前请确认目标盘放得下',
      icon: PieChart,
      tone: 'blue',
    },
    {
      title: '目标盘可用',
      value: data.target_free_text,
      note: '需不小于总体积',
      icon: Coin,
      tone: 'blue',
    },
    {
      title: '同名冲突',
      value: formatCount(data.conflicts),
      note: data.conflicts ? '执行时需逐个确认' : '无同名冲突',
      icon: Warning,
      tone: 'blue',
    },
  ]
  if (data.skipped_protected) {
    items.push({
      title: '保护目录跳过',
      value: formatCount(data.skipped_protected),
      note: '受写保护，强制排除',
      icon: Lock,
      tone: 'blue',
    })
  }
  return items
})

/** 预览清单 → migrate 的显式清单（源 → 目标根/分组路径/原名） */
/**
 * 这个页面有四步（预览 → 选目标 → 出计划 → 执行），后面几步都在页面下方。
 * 只看到预览就以为"到头了"是这个页面最真实的误解 —— 所以顶部要有一条步骤指示。
 */
const stepLabels = ['预览', '出计划', '确认执行']
const stepIndex = computed(() => {
  if (execJob.job.value) return 3
  if (planResult.value) return 2
  return 1
})

/** 计划按钮为什么是灰的 —— 干灰着不说明原因，用户只会以为功能坏了。 */
const planHint = computed(() => {
  if (!result.value) return '先在上面生成一次预览。'
  if (!target.value.trim()) return '先选一个「整理到」的目标根目录 —— 选好后这里会列出要移动的文件数。'
  return `清单里有 ${planItems.value.length} 个文件，点右边按钮生成可执行计划。`
})

const planItems = computed(() => {
  const data = result.value
  const base = target.value.trim().replace(/[\\/]+$/, '')
  if (!data || !base) return []
  const items = []
  for (const bucket of data.buckets) {
    const groupDir = bucket.group_path.replace(/\//g, '\\')
    for (const item of bucket.items) {
      items.push({ source: item.path, target: `${base}\\${groupDir}\\${item.name}`, size: item.size })
    }
  }
  return items
})

/** 就地整理的清单：目标位置直接用预览给出的 preview_target —— 就在原目录下的分组子目录里，不换盘 */
const inPlaceItems = computed(() => {
  const data = result.value
  if (!data) return []
  const items = []
  for (const bucket of data.buckets) {
    for (const item of bucket.items) {
      items.push({ source: item.path, target: item.preview_target, size: item.size })
    }
  }
  return items
})

const inPlaceSizeText = computed(() =>
  formatBytes(inPlaceItems.value.reduce((sum, item) => sum + (item.size || 0), 0)))

function toggle(key) {
  openKeys.value = openKeys.value.includes(key)
    ? openKeys.value.filter(item => item !== key)
    : [...openKeys.value, key]
}

/** 导出用的清单：选了目标根目录就写真实位置，否则写预览位置 */
const exportItems = computed(() => {
  const data = result.value
  if (!data) return []
  const base = target.value.trim().replace(/[\\/]+$/, '')
  const items = []
  for (const bucket of data.buckets) {
    const groupDir = bucket.group_path.replace(/\//g, '\\')
    for (const item of bucket.items) {
      items.push({
        group_label: bucket.label,
        name: item.name,
        source: item.path,
        target: base ? `${base}\\${groupDir}\\${item.name}` : item.preview_target,
        size: item.size,
      })
    }
  }
  return items
})

async function exportCsv() {
  if (!exportItems.value.length) return
  exporting.value = true
  try {
    const data = await exportVirtual({
      root: result.value.root,
      group_by: result.value.group_by,
      items: exportItems.value,
    })
    ElMessage.success(`已导出 ${formatCount(data.count)} 条清单 → ${data.path}`)
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    exporting.value = false
  }
}

function groupDirText(bucket) {
  return `${result.value.root}\\${bucket.group_path.replace(/\//g, '\\')}`
}

async function makePreview() {
  if (!path.value.trim()) {
    ElMessage.warning('请先选择要预览的目录')
    return
  }
  result.value = null
  planResult.value = null
  openKeys.value = []
  const job = await preview.run(() => startVirtual({
    path: path.value.trim(),
    group_by: groupBy.value,
    excludes: excludes.value,
    min_size: Number(minSize.value) || 0,
  }))
  if (job) ElMessage.success('预览已生成，没有移动任何文件')
}

async function makePlan() {
  if (!result.value || !planItems.value.length) return
  if (!target.value.trim()) {
    ElMessage.warning('请选择整理到的目标根目录')
    return
  }
  planResult.value = null
  await planJob.run(() => startMigrate({
    source_dir: result.value.root,
    target_dir: target.value.trim(),
    dry_run: true,
    mode: 'move',
    items: planItems.value,
  }))
}

async function runOrganize() {
  const data = planResult.value
  if (!data?.plan?.length) return
  try {
    await ElMessageBox.confirm(
      `将移动 ${formatCount(data.item_count)} 个文件（${data.total_size_text}）到 ${data.target_dir}。`
      + '每个文件都会先复制并逐字节校验，校验通过后才删除源文件；执行中可中断，完成后可一键回滚。',
      '确认真实整理',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  const job = await execJob.run(() => startMigrate({
    source_dir: result.value.root,
    target_dir: target.value.trim(),
    dry_run: false,
    mode: 'move',
    items: planItems.value,
  }))
  if (job) ElMessage.success('整理任务已启动，复制与校验会同步显示进度')
}

/** 就地整理：不另挑目标盘，直接把文件移进原目录下的分组子目录（只移动、不删除、可回滚） */
async function runInPlace() {
  const items = inPlaceItems.value
  if (!items.length) return
  try {
    await ElMessageBox.confirm(
      `将 ${formatCount(items.length)} 个文件（${inPlaceSizeText.value}）移动到本目录下的分组子目录。`
      + '只移动、不删除任何文件；执行后可在任务页整体回滚。确认继续？',
      '确认就地整理',
      { type: 'warning', confirmButtonText: '确认整理', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }
  const job = await inPlaceJob.run(() => applyVirtual({
    path: result.value.root,
    group_by: result.value.group_by,
    items,
  }))
  if (job) ElMessage.success('就地整理已启动，文件会按分组归位')
}

watch(() => preview.job.value?.status, (status) => {
  if (status === 'done' && preview.job.value?.result) {
    result.value = preview.job.value.result
    const first = result.value.buckets?.[0]
    openKeys.value = first ? [first.key] : []
  }
})

watch(() => planJob.job.value?.status, (status) => {
  if (status === 'done' && planJob.job.value?.result) {
    planResult.value = planJob.job.value.result
  }
})

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <!-- 步骤指示：四步里有三步在下方，不先说明白就会被当成"预览完就结束了" -->
      <ol class="steps">
        <li
          v-for="(label, index) in stepLabels"
          :key="label"
          class="steps__item"
          :class="{ 'is-current': stepIndex === index + 1, 'is-done': stepIndex > index + 1 }"
        >
          <span class="steps__dot">{{ stepIndex > index + 1 ? '✓' : index + 1 }}</span>
          <span class="steps__label">{{ label }}</span>
        </li>
      </ol>

      <HandoffNote v-if="handed" :path="handed" :from="from" @dismiss="handed = ''" />

      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="要预览的目录"
        placeholder="例如：D:\\"
      />

      <el-divider />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">分组方式</span>
          <el-segmented v-model="groupBy" :options="modeOptions" />
        </div>

        <div class="option-block">
          <span class="field-label">只统计大于</span>
          <el-segmented v-model="minSize" :options="minSizeOptions" />
        </div>

        <el-button
          type="primary"
          size="large"
          :icon="Files"
          :loading="preview.running.value"
          @click="makePreview"
        >
          {{ preview.running.value ? '正在预览' : '生成整理预览' }}
        </el-button>
      </div>

      <el-alert
        class="panel-note"
        type="info"
        :closable="false"
        show-icon
        title="这一页只做预览：不创建目录，也不移动、删除任何文件。"
      />

      <JobProgress :job="preview.job.value" :rollbackable="false" @cancel="preview.cancel" />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in summary" :key="item.title" v-bind="item" />
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">如果这样整理</h3>
          </div>
          <div class="title-actions">
            <div class="meta-pill">预览 · 未移动任何文件</div>
            <el-button
              size="small"
              :loading="exporting"
              :disabled="!exportItems.length"
              @click="exportCsv"
            >
              导出清单 CSV
            </el-button>
          </div>
        </div>

        <div class="tree">
          <div v-for="bucket in result.buckets" :key="bucket.key" class="tree-node">
            <button
              type="button"
              class="tree-head"
              :class="{ 'is-open': openKeys.includes(bucket.key) }"
              @click="toggle(bucket.key)"
            >
              <span class="tree-caret" aria-hidden="true"></span>
              <span class="tree-icon"><el-icon :size="18"><FolderOpened /></el-icon></span>
              <span class="tree-name">{{ bucket.label }}</span>
              <span class="tree-meta">{{ formatCount(bucket.count) }} 个 · {{ bucket.size_text }}</span>
              <span class="tree-bar"><i :style="{ width: `${Math.max(bucket.percentage, 1)}%` }"></i></span>
              <span class="tree-pct">{{ bucket.percentage }}%</span>
            </button>

            <div v-if="openKeys.includes(bucket.key)" class="tree-body">
              <div class="tree-target">整理后位置：{{ groupDirText(bucket) }}\</div>

              <div v-if="bucket.extensions?.length" class="tree-facets">
                <span v-for="facet in bucket.extensions" :key="facet.key" class="facet-pill">
                  {{ facet.key }} · {{ formatCount(facet.count) }}
                </span>
              </div>

              <DataTable
                :columns="itemColumns"
                :data="bucket.items"
                :empty-icon="Files"
                empty-text="这一组没有列出文件"
              />

              <p v-if="bucket.truncated" class="tree-hint">
                该组文件较多，清单只列出前 {{ formatCount(result.item_limit) }} 个（上方统计仍是全量）。
              </p>
            </div>
          </div>
        </div>

        <p v-if="result.truncated" class="tree-hint">
          扫描达到单次上限（已处理 {{ formatCount(result.scanned_files) }} 个文件），结果已截断。
        </p>
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">真要这么整理</h3>
          </div>
          <div class="meta-pill">会真实移动文件</div>
        </div>

        <!-- 和迁移页同一句话：先说清"这一步不动文件"，用户才敢往下点 -->
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="这一步只生成计划，不会动任何文件。确认清单之后再执行。"
        />

        <TargetPicker
          v-model:path="target"
          :drives="config?.drives || []"
          :show-excludes="false"
          label="整理到（目标根目录）"
          placeholder="例如：D:\\_整理"
        />

        <div class="row-actions">
          <span class="tree-hint">{{ planHint }}</span>
          <el-button
            size="large"
            :icon="Sort"
            :disabled="!planItems.length"
            :loading="planJob.running.value"
            @click="makePlan"
          >
            生成真实整理计划
          </el-button>
        </div>

        <JobProgress :job="planJob.job.value" :rollbackable="false" @cancel="planJob.cancel" />
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">或者：就地整理（不换盘）</h3>
          </div>
          <div class="meta-pill">确认后即移动</div>
        </div>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="不用另挑目标盘：直接在这个目录里分组建子目录、把文件归位。只移动、不删除，执行后可在任务页整体回滚。"
        />

        <div class="row-actions">
          <span class="tree-hint">
            将把 {{ formatCount(inPlaceItems.length) }} 个文件（{{ inPlaceSizeText }}）移进 {{ path }} 下的分组子目录。
          </span>
          <el-button
            size="large"
            :icon="Sort"
            :disabled="!inPlaceItems.length"
            :loading="inPlaceJob.running.value"
            @click="runInPlace"
          >
            就地整理
          </el-button>
        </div>

        <JobProgress :job="inPlaceJob.job.value" @cancel="inPlaceJob.cancel" @rollback="inPlaceJob.rollback" />
      </section>

      <template v-if="planResult">
        <section class="stats-grid">
          <StatCard v-for="item in planSummary" :key="item.title" v-bind="item" />
        </section>

        <section class="app-card panel-card">
          <el-alert
            type="warning"
            :closable="false"
            show-icon
            title="确认后才会真正移动文件：复制 → 逐文件校验 → 校验通过才删源。"
          />

          <div class="row-actions">
            <span class="tree-hint">目标：{{ planResult.target_dir }}</span>
            <el-button
              type="primary"
              size="large"
              :loading="execJob.running.value"
              :disabled="!planResult.plan?.length"
              @click="runOrganize"
            >
              确认执行真实整理
            </el-button>
          </div>

          <JobProgress :job="execJob.job.value" @cancel="execJob.cancel" @rollback="execJob.rollback" />
        </section>
      </template>
    </template>

    <section v-else class="app-card panel-card">
      <EmptyState
        :icon="Files"
        title="先生成一次预览"

      />
    </section>

    <!-- 走完整条整理链之后，给个"接下来去哪" —— 不然这一页就是路的尽头 -->
    <NextSteps v-if="result" :path="path" origin="virtual" :steps="nextSteps" />
  </div>
</template>

<style scoped>

/* 步骤指示：细、不抢主色，只在"当前步"上着色。
   这个页面有四步而且后三步都在下方，没有这条指示就很容易以为"预览完就结束了"。 */
.steps {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 16px;
  padding: 0;
  list-style: none;
}

.steps__item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.steps__item + .steps__item::before {
  content: '';
  width: 22px;
  height: 1px;
  margin-right: 6px;
  background: var(--hairline);
}

.steps__dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 0.5px solid var(--hairline);
  border-radius: 50%;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.steps__item.is-current {
  color: var(--ink);
  font-weight: 600;
}

.steps__item.is-current .steps__dot {
  border-color: var(--primary);
  background: var(--primary);
  /* 主色底上的前景色只能用 token，不准写字面量 #fff */
  color: var(--on-primary);
}

.steps__item.is-done {
  color: var(--ink-2);
}
.title-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* ------------------------------------------------------------ 虚拟结构树 */
.tree {
  margin-top: 14px;
  border: 1px solid var(--hairline);
  border-radius: 12px;
  overflow: hidden;
}

.tree-node + .tree-node {
  border-top: 1px solid var(--hairline);
}

.tree-head {
  display: grid;
  grid-template-columns: 12px 20px minmax(0, 1fr) auto 148px 54px;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 14px 16px;
  background: transparent;
  border: none;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease);
}

.tree-head:hover {
  background: var(--surface-2);
}

.tree-caret {
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 6px solid var(--ink-3);
  transition: transform var(--duration-quick) var(--ease);
}

.tree-head.is-open .tree-caret {
  transform: rotate(90deg);
}

.tree-icon {
  display: grid;
  place-items: center;
  color: var(--primary);
}

.tree-name {
  min-width: 0;
  color: var(--ink);
  font-size: 15px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tree-meta {
  color: var(--ink-3);
  font-size: 13px;
  white-space: nowrap;
}

.tree-bar {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}

.tree-bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--primary);
}

.tree-pct {
  color: var(--ink-2);
  font-size: 13px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.tree-body {
  padding: 0 16px 16px 60px;
  background: var(--surface-2);
}

.tree-target {
  padding: 12px 0 10px;
  color: var(--ink-3);
  font-size: 12px;
  word-break: break-all;
}

.tree-facets {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.facet-pill {
  padding: 2px 10px;
  color: var(--ink-2);
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 999px;
  font-size: 12px;
}

.tree-hint {
  margin: 12px 0 0;
  color: var(--ink-3);
  font-size: 12px;
}
</style>

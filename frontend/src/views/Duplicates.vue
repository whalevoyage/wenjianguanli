<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Coin, CopyDocument, Delete, DocumentCopy, Files, Picture } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import StatCard from '../components/StatCard.vue'
import { startClean, startDuplicateScan, startSimilarScan } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import { useHandoffTarget } from '../composables/useHandoff'
import { useSelectionClean } from '../composables/useSelectionClean'
import HandoffNote from '../components/HandoffNote.vue'
import NextSteps from '../components/NextSteps.vue'
import JobProgress from '../components/JobProgress.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatBytes, formatCount } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const scan = useJob('duplicates.scan')
const clean = useJob('duplicates.clean')
const simRun = useJob('duplicates.similar')

const paths = ref([])
/* 跨页接力：从别的页面带着目录过来时，直接追加进这个列表（不自动开跑） */
const { handed, from } = useHandoffTarget(paths, { multiple: true })

/** 反向接力：用同一个目录去别的能力，不用再选一次 */
const nextSteps = [{ name: 'space', label: '空间地图' }, { name: 'migrate', label: '迁移到别处' }]
const primaryPath = computed(() => paths.value[0] || '')
const result = ref(null)
/* 相似图片的结果单独放 —— 相似 ≠ 相同，界面上必须分开呈现，绝不混进「完全重复」 */
const similar = ref(null)
const activeGroups = ref([])
const selected = ref(new Set())
/* 相似阈值：越小越严格。6 是默认，调到 10 会宽松很多（误报也变多） */
const similarThreshold = ref(6)
/* 旋转容忍：同一张图转 90/180/270° 也算相似。默认关（哈希算约 3 倍） */
const similarRotations = ref(false)

/* 相似图块的"处理"：勾选 → 送隔离区 / 送回收站。
   与"完全重复"分开管（两块面板本来就分开）；同样复用 /api/clean，不新增删除接口。 */
const {
  selected: simSelected,
  cleanMode: simCleanMode,
  clean: simClean,
  modeOptions: simModeOptions,
  toggle: toggleSim,
  setAll: setAllSim,
  clear: clearSim,
  submit: submitSim,
} = useSelectionClean('duplicates.similar.clean')

const simFiles = computed(() => (similar.value?.groups || []).flatMap(group => group.files || []))
const simPaths = computed(() => simFiles.value.map(file => file.path))
const simAllPicked = computed(() =>
  simPaths.value.length > 0 && simPaths.value.every(path => simSelected.value.has(path)))
const simStats = computed(() => {
  const size = new Map(simFiles.value.map(file => [file.path, file.size || 0]))
  let total = 0
  for (const path of simSelected.value) total += size.get(path) || 0
  return { count: simSelected.value.size, size_text: formatBytes(total) }
})

function toggleSimAll() {
  if (simAllPicked.value) clearSim()
  else setAllSim(simPaths.value)
}

function runSimClean() {
  const byPath = new Map(simFiles.value.map(file => [file.path, file]))
  const items = [...simSelected.value].map(path => ({
    path, is_dir: false, rule: 'similar', size: byPath.get(path)?.size || 0,
  }))
  submitSim(items)
}

const metrics = computed(() => {
  if (!result.value) return []
  const data = result.value
  const avg = data.group_count ? Math.round(data.redundant_file_count / data.group_count) : 0
  return [
    {
      title: '已扫描文件',
      value: formatCount(data.scanned_files),
      note: `候选 ${formatCount(data.candidate_files)} · 按大小淘汰 ${formatCount(data.eliminated_by_size)}`,
      icon: Files,
      tone: 'blue',
    },
    {
      title: '重复组',
      value: formatCount(data.group_count),
      note: avg ? `平均每组 ${avg} 个副本` : '暂无重复',
      icon: CopyDocument,
      tone: 'blue',
    },
    {
      title: '重复副本',
      value: formatCount(data.redundant_file_count),
      note: '每组保留 1 个，其余可清理',
      icon: DocumentCopy,
      tone: 'blue',
    },
    {
      title: '可回收空间',
      value: data.reclaimable_size_text,
      note: '清理冗余副本后可腾出',
      icon: Coin,
      tone: 'blue',
    },
  ]
})

const selectionStats = computed(() => {
  let size = 0
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) {
      if (selected.value.has(file.path)) size += file.size
    }
  }
  return { count: selected.value.size, size, size_text: formatBytes(size) }
})

const groupSummary = computed(() => (result.value?.groups || []).map((group, index) => ({
  id: index + 1,
  count: group.count,
  size_text: group.size_text,
  redundant_text: formatBytes(group.size * Math.max(group.count - 1, 0)),
  hash: group.sha256_short,
  dirs: new Set((group.files || []).map(file => file.path?.replace(/[\\/][^\\/]+$/, ''))).size,
})))

function toggle(file) {
  const next = new Set(selected.value)
  next.has(file.path) ? next.delete(file.path) : next.add(file.path)
  selected.value = next
}

function selectAllSafe() {
  const next = new Set()
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) next.add(file.path)
  }
  selected.value = next
}

function clearSelection() {
  selected.value = new Set()
}

function selectGroup(group) {
  const next = new Set(selected.value)
  for (const file of group.redundant_files || []) next.add(file.path)
  selected.value = next
}

async function start() {
  const targets = paths.value.map(p => p.trim()).filter(Boolean)
  if (!targets.length) return
  result.value = null
  selected.value = new Set()
  await scan.run(() => startDuplicateScan({ paths: targets, excludes: excludes.value }))
}

/**
 * 找「画面相似」的图片（缩过、压过的那些）。
 *
 * 和上面的「开始查重」是两条独立的流程：那个比的是字节完全一样，这个比的是画面像不像。
 * 结果**分开放**，因为相似不等于相同 —— 删哪张得自己看图。
 */
async function startSimilar() {
  const targets = paths.value.map(p => p.trim()).filter(Boolean)
  if (!targets.length) return
  similar.value = null
  await simRun.run(() => startSimilarScan({
    paths: targets,
    excludes: excludes.value,
    threshold: similarThreshold.value,
    rotations: similarRotations.value,
  }))
}

async function cleanup() {
  if (!selected.value.size) return
  const items = []
  for (const group of result.value?.groups || []) {
    for (const file of group.redundant_files || []) {
      if (selected.value.has(file.path)) {
        items.push({ path: file.path, is_dir: false, rule: 'duplicate', size: file.size })
      }
    }
  }
  const job = await clean.run(() => startClean({ items, mode: 'isolate' }))
  if (job) {
    ElMessage.success(`已把 ${items.length} 个副本送进隔离区，可随时还原`)
    selected.value = new Set()
  }
}

watch(() => scan.job.value?.status, (status) => {
  if (status === 'done' && scan.job.value?.result) {
    result.value = scan.job.value.result
    activeGroups.value = result.value.groups?.length ? ['0'] : []
  }
})

watch(() => simRun.job.value?.status, (status) => {
  if (status === 'done' && simRun.job.value?.result) similar.value = simRun.job.value.result
})

watch(() => clean.job.value?.status, (status) => {
  if (status === 'done') start()
})

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <HandoffNote v-if="handed" :path="handed" :from="from" @dismiss="handed = ''" />

      <TargetPicker
        :multiple="true"
        v-model:paths="paths"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="查找路径"
        placeholder="可多选：分别添加多个目录一起查重"
      />

      <div class="row-actions">
        <el-button type="primary" size="large" :loading="scan.running.value" :icon="CopyDocument" @click="start">
          {{ scan.running.value ? '正在查重' : '开始查重' }}
        </el-button>
        <!-- 第二条流水线：字节完全一样 vs 画面像不像，两件事 -->
        <el-button size="large" :loading="simRun.running.value" :icon="Picture" @click="startSimilar">
          {{ simRun.running.value ? '正在比对画面' : '找出相似图片' }}
        </el-button>
        <el-select v-model="similarThreshold" size="large" class="similar-threshold" title="相似阈值：越小越严格，默认 6">
          <el-option v-for="n in [4, 6, 8, 10, 12]" :key="n" :label="`相似阈值 ${n}`" :value="n" />
        </el-select>
        <el-checkbox
          v-model="similarRotations"
          size="large"
          class="similar-rotations"
          title="开启后：同一张图旋转 90°/180°/270° 也能匹配（哈希计算约 3 倍）"
        >
          旋转容忍
        </el-checkbox>
      </div>

      <JobProgress v-if="scan.job.value" :job="scan.job.value" empty-title="先跑一次查重" @cancel="scan.cancel" />
      <JobProgress
        v-if="simRun.job.value"
        :job="simRun.job.value"
        empty-title="点「找出相似图片」开始比对"
        :rollbackable="false"
        @cancel="simRun.cancel"
      />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in metrics" :key="item.title" v-bind="item" />
      </section>

      <NextSteps :path="primaryPath" origin="duplicates" :steps="nextSteps" />

      <el-alert
        type="success"
        :closable="false"
        show-icon
        :title="`按大小直接淘汰了 ${formatCount(result.eliminated_by_size)} 个文件，只对 ${formatCount(result.candidate_files)} 个候选算了哈希`"
      />

      <el-alert
        v-if="result.notes?.length"
        type="info"
        :closable="false"
        show-icon
        :title="`已自动处理来源：${result.notes.join('；')}`"
      />

      <section v-if="result.groups?.length" class="app-card panel-card">
        <div class="duplicate-summary">
          <div class="duplicate-summary__head"><span>组号</span><span>文件数</span><span>单文件大小</span><span>冗余占用</span><span>哈希前缀</span><span>目录数</span></div>
          <div v-for="group in groupSummary" :key="group.id" class="duplicate-summary__row"><strong>#{{ group.id }}</strong><span>{{ group.count }}</span><span>{{ group.size_text }}</span><span>{{ group.redundant_text }}</span><code>{{ group.hash }}</code><span>{{ group.dirs }}</span></div>
        </div>
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">重复组</h3>
          </div>
          <div class="group-tools">
            <el-button size="small" @click="selectAllSafe">全选副本</el-button>
            <el-button size="small" plain @click="clearSelection">清空</el-button>
          </div>
        </div>

        <el-collapse v-model="activeGroups">
          <el-collapse-item
            v-for="(group, index) in result.groups"
            :key="group.sha256"
            :name="String(index)"
          >
            <template #title>
              <div class="group-title">
                <strong>{{ group.count }} 个相同文件</strong>
                <span>{{ group.size_text }} / 个 · 可省 {{ formatBytes(group.size * (group.count - 1)) }}</span>
                <code>{{ group.sha256_short }}</code>
                <el-tag v-if="group.cross_source" size="small" type="warning" effect="plain">跨目录重复</el-tag>
              </div>
            </template>

            <div class="file-list">
              <div
                v-for="(file, fileIndex) in group.files"
                :key="file.path"
                class="file-row"
                :class="{ 'is-kept': fileIndex === 0 }"
              >
                <el-checkbox
                  :model-value="fileIndex === 0 ? false : selected.has(file.path)"
                  :disabled="fileIndex === 0"
                  @update:model-value="fileIndex === 0 ? null : toggle(file)"
                />

                <div class="file-row__copy">
                  <strong>{{ file.name }}</strong>
                  <small class="path-text" :title="file.path">{{ file.path }}</small>
                  <small class="muted">{{ file.source }}</small>
                </div>

                <span class="status-dot" :class="fileIndex === 0 ? 'status-dot--success' : 'status-dot--warning'">{{ fileIndex === 0 ? '保留' : '副本' }}</span>
              </div>

              <el-button size="small" plain @click="selectGroup(group)">勾选这一组的副本</el-button>
            </div>
          </el-collapse-item>
        </el-collapse>
      </section>

      <EmptyState
        v-else
        :icon="CopyDocument"
        title="没有发现重复文件"
      />

      <section v-if="selected.size" class="app-card panel-card sticky-bar">
        <div>
          <strong>已选 {{ selectionStats.count }} 个副本</strong>
          <span class="muted">预计释放 {{ selectionStats.size_text }}，移动后仍可从回收站还原</span>
        </div>
        <el-button type="primary" :icon="Delete" :loading="clean.running.value" @click="cleanup">
          送进隔离区
        </el-button>
      </section>

      <JobProgress v-if="clean.job.value" :job="clean.job.value" @cancel="clean.cancel" @rollback="clean.rollback" />
    </template>

    <!-- 相似图片：**独立成块**，不并进上面的「完全重复」。
         相似 ≠ 相同，删哪张得自己看图确认 —— 给的是"逐张勾选"，不做一键全选。 -->
    <section v-if="similar" class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">相似图片</h3>
          <p class="panel-subtitle">
            按画面相似度分组（缩过、压过、另存过的那些）；相似不等于相同，删哪张请自己看图确认
          </p>
        </div>
        <div class="similar-head-actions">
          <span class="muted">
            {{ formatCount(similar.scanned) }} 张参与比对<template v-if="similar.group_count"> · {{ similar.group_count }} 组 · 可省 {{ similar.redundant_text }}</template>
          </span>
          <el-button v-if="simFiles.length" size="small" plain @click="toggleSimAll">
            {{ simAllPicked ? '清空选择' : '全选' }}
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="similar.available === false"
        type="warning"
        :closable="false"
        show-icon
        :title="similar.reason"
      />

      <el-alert
        v-else-if="!similar.groups?.length"
        type="info"
        :closable="false"
        show-icon
        :title="similar.note || '没有找到相似的图片'"
      />

      <div v-else class="similar-list">
        <div v-for="(group, index) in similar.groups" :key="index" class="similar-group">
          <div class="similar-group__head">
            <strong>第 {{ index + 1 }} 组</strong>
            <span class="muted">{{ group.count }} 张 · 可省 {{ group.redundant_text }}</span>
          </div>
          <div
            v-for="file in group.files"
            :key="file.path"
            class="similar-file"
            :class="{ 'is-keep': group.keep === file.path }"
          >
            <el-checkbox
              :model-value="simSelected.has(file.path)"
              @update:model-value="toggleSim(file.path)"
            />
            <span class="similar-file__name">{{ file.name }}</span>
            <small class="path-text" :title="file.path">{{ file.path }}</small>
            <span class="similar-file__meta">{{ file.size_text }} · {{ file.mtime }}</span>
            <el-tag v-if="group.keep === file.path" size="small" type="success" effect="plain">建议保留</el-tag>
            <span v-else class="similar-file__mark">候选可删</span>
          </div>
        </div>
      </div>

      <section v-if="simSelected.size" class="app-card panel-card sticky-bar">
        <div class="sticky-bar__info">
          <strong>已选 {{ simStats.count }} 张</strong>
          <span class="muted">预计释放 {{ simStats.size_text }}，可随时还原</span>
          <el-segmented v-model="simCleanMode" :options="simModeOptions" size="small" />
        </div>
        <el-button type="primary" :icon="Delete" :loading="simClean.running.value" @click="runSimClean">
          执行清理
        </el-button>
      </section>

      <JobProgress v-if="simClean.job.value" :job="simClean.job.value" @cancel="simClean.cancel" @rollback="simClean.rollback" />
    </section>

  </div>
</template>

<style scoped>
.duplicate-summary { margin-bottom: 24px; overflow-x: auto; }
.duplicate-summary__head, .duplicate-summary__row { display: grid; grid-template-columns: .6fr .7fr 1fr 1fr 1fr .7fr; min-width: 0; gap: 14px; align-items: center; }
.duplicate-summary__head { padding: 0 12px 10px; color: var(--ink-2); font-size: var(--fs-micro); font-weight: 600; }
.duplicate-summary__row { min-height: 44px; padding: 8px 12px; border-top: 1px solid var(--hairline); font-size: var(--fs-note); }

@media (max-width: 640px) {
  .duplicate-summary__head,
  .duplicate-summary__row {
    grid-template-columns: .65fr 1fr .8fr;
    gap: 8px;
  }

  .duplicate-summary__head > :nth-child(2),
  .duplicate-summary__row > :nth-child(2),
  .duplicate-summary__head > :nth-child(4),
  .duplicate-summary__row > :nth-child(4),
  .duplicate-summary__head > :nth-child(5),
  .duplicate-summary__row > :nth-child(5) {
    display: none;
  }
}

/* 窄屏降级：隐去「哈希前缀 / 目录数」两个次要列，消灭卡片内横向滚动（v22 P0-3） */
@media (max-width: 640px) {
  .duplicate-summary__head,
  .duplicate-summary__row {
    grid-template-columns: 0.6fr 0.7fr 1fr 1fr;
    min-width: 0;
  }

  .duplicate-summary__head > :nth-child(5),
  .duplicate-summary__head > :nth-child(6),
  .duplicate-summary__row > :nth-child(5),
  .duplicate-summary__row > :nth-child(6) {
    display: none;
  }
}

.group-tools {
  display: flex;
  gap: 8px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.group-title strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
}

.group-title span {
  color: var(--ink-3);
  font-size: 12px;
}

.group-title code {
  padding: 2px 8px;
  color: var(--ink-3);
  background: var(--inset);
  border-radius: 6px;
  font-size: 11px;
}

.file-list {
  display: grid;
  gap: 8px;
}

.file-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.file-row.is-kept {
  background: var(--success-soft);
  border-color: var(--success-soft);
}

.file-row__copy {
  min-width: 0;
}

.file-row__copy strong {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.file-row__copy small {
  display: block;
  margin-top: 4px;
}

/* 相似图片列表：整体比「完全重复」那块更克制 —— 它只是候选，不是结论 */
.similar-threshold {
  width: 132px;
}

.similar-list {
  display: grid;
  gap: 16px;
}

.similar-group {
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.similar-group__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--fs-body);
}

.similar-file {
  display: grid;
  grid-template-columns: auto minmax(120px, 220px) minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 5px 8px;
  border-radius: 8px;
  font-size: var(--fs-note);
}

.similar-file.is-keep {
  background: var(--inset);
}

.similar-file__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.similar-file__meta,
.similar-file__mark {
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 相似图片列表：整体比「完全重复」那块更克制 —— 它只是候选，不是结论 */
.similar-threshold {
  width: 132px;
}

.similar-list {
  display: grid;
  gap: 16px;
}

.similar-group {
  display: grid;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.similar-group__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: var(--fs-body);
}

.similar-file {
  display: grid;
  grid-template-columns: auto minmax(120px, 220px) minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 5px 8px;
  border-radius: 8px;
  font-size: var(--fs-note);
}

.similar-file.is-keep {
  background: var(--inset);
}

.similar-file__name {
  overflow: hidden;
  color: var(--ink);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.similar-file__meta,
.similar-file__mark {
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.sticky-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  border-color: var(--primary);
}

.sticky-bar strong {
  display: block;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

.sticky-bar span {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}

.similar-rotations {
  margin-left: 2px;
}

/* 相似图片标题行右侧：统计 + 全选按钮 */
.similar-head-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sticky-bar__info :deep(.el-segmented) {
  margin-top: 8px;
}
</style>

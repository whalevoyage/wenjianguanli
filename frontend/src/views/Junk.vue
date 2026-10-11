<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CircleCheck, Coin, Delete, Files, QuestionFilled, Refresh, Warning } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import StatCard from '../components/StatCard.vue'
import { explainItem, startClean, startJunkScan } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import { useHandoffTarget } from '../composables/useHandoff'
import HandoffNote from '../components/HandoffNote.vue'
import NextSteps from '../components/NextSteps.vue'
import JobProgress from '../components/JobProgress.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatBytes, formatCount } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const scan = useJob('junk.scan')
const clean = useJob('junk.clean')

const path = ref('')
/* 跨页接力：从别的页面带着目录过来时，直接填好这个框（不自动开跑） */
const { handed, from } = useHandoffTarget(path)

/** 反向接力：用同一个目录去别的能力，不用再选一次 */
const nextSteps = [{ name: 'space', label: '空间地图' }, { name: 'migrate', label: '迁移到别处' }]
const includeCaution = ref(false)
const cleanMode = ref('isolate')
const selected = ref(new Set())

/* 每项的「这是什么」解释，按路径存；没问过就没有这个 key */
const explanations = ref({})

/**
 * 问模型「这是什么、删了会怎样」。
 *
 * ⚠️ 这是全项目**第二处会联网、会花 token 的地方**（第一处是 Agent 助手页）。
 *    所以只在这个问号被**主动点**时才调 —— 没有自动调用、没有「批量解释全部」。
 *    没配 Key / 断网都会如实说出原因，绝不编内容。
 *    再点一次收起，免得解释把列表撑长。
 */
async function askExplain(item) {
  const key = item.path
  const current = explanations.value[key]
  if (current?.loading) return
  if (current?.text) {
    const next = { ...explanations.value }
    delete next[key]
    explanations.value = next
    return
  }
  explanations.value = { ...explanations.value, [key]: { loading: true } }
  try {
    const data = await explainItem({
      kind: 'junk',
      item: { name: item.name, path: item.path, size_text: item.size_text, reason: item.reason },
    })
    let entry
    if (data.available === false) entry = { reason: data.reason }
    else if (data.error) entry = { reason: data.error }
    else entry = { text: data.text, tokens: data.tokens }
    explanations.value = { ...explanations.value, [key]: entry }
  } catch (error) {
    explanations.value = { ...explanations.value, [key]: { reason: error.message } }
  }
}

const result = ref(null)

const modeOptions = [
  { label: '送进隔离区（可还原）', value: 'isolate' },
  { label: '送进系统回收站', value: 'recycle' },
]

const levels = computed(() => result.value?.levels || [])

/* 指标卡：与首页共用 StatCard。注释只放补充信息，不复述大数字。 */
const cards = computed(() => {
  const data = result.value
  if (!data) return []
  const list = data.levels || []
  const total = data.total_count || 0
  const safeCount = list.find(item => item.level === 'safe')?.items?.length || 0
  const safeRatio = data.total_size ? Math.round((data.safe_size / data.total_size) * 100) : 0
  const avg = total ? data.total_size / total : 0
  return [
    {
      title: '发现问题',
      value: formatCount(total),
      note: `安全级 ${formatCount(safeCount)} · 谨慎级 ${formatCount(Math.max(total - safeCount, 0))}`,
      icon: Warning,
      tone: 'blue',
    },
    {
      title: '预计可释放',
      value: data.total_size_text,
      note: total ? `平均每项 ${formatBytes(avg)}` : '暂无命中',
      icon: Coin,
      tone: 'blue',
    },
    {
      title: '安全级可释放',
      value: data.safe_size_text,
      note: `占可释放总量的 ${safeRatio}%`,
      icon: CircleCheck,
      tone: 'blue',
    },
    {
      title: '已检查文件',
      value: formatCount(data.scanned_files),
      note: data.scanned_files
        ? `命中率 ${((total / data.scanned_files) * 100).toFixed(1)}%`
        : '暂无记录',
      icon: Files,
      tone: 'blue',
    },
  ]
})

const selectionStats = computed(() => {
  let size = 0
  for (const level of levels.value) {
    for (const item of level.items || []) {
      if (selected.value.has(item.path)) size += item.size
    }
  }
  return { count: selected.value.size, size_text: formatBytes(size) }
})

const LEVEL_TONE = { safe: 'success', confirm: 'warning', caution: 'danger' }

function toggle(item) {
  const next = new Set(selected.value)
  next.has(item.path) ? next.delete(item.path) : next.add(item.path)
  selected.value = next
}

function toggleLevel(level) {
  const next = new Set(selected.value)
  const items = (level.items || []).map(item => item.path)
  const allSelected = items.every(p => next.has(p))
  items.forEach(p => (allSelected ? next.delete(p) : next.add(p)))
  selected.value = next
}

async function start() {
  if (!path.value.trim()) return
  result.value = null
  selected.value = new Set()
  await scan.run(() => startJunkScan({
    path: path.value.trim(),
    excludes: excludes.value,
    include_caution: includeCaution.value,
  }))
}

async function runClean() {
  if (!selected.value.size) return
  const items = []
  for (const level of levels.value) {
    for (const item of level.items || []) {
      if (selected.value.has(item.path)) {
        items.push({ path: item.path, is_dir: item.is_dir, rule: item.rule, size: item.size })
      }
    }
  }
  const job = await clean.run(() => startClean({ items, mode: cleanMode.value }))
  if (job) {
    ElMessage.success(`已处理 ${items.length} 个项目`)
    selected.value = new Set()
  }
}

watch(() => scan.job.value?.status, (status) => {
  if (status === 'done' && scan.job.value?.result) {
    result.value = scan.job.value.result
    const next = new Set()
    for (const level of result.value.levels || []) {
      if (level.level === 'safe') {
        for (const item of level.items || []) next.add(item.path)
      }
    }
    selected.value = next
  }
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

      <el-alert
        v-if="config?.junk_rule_issues?.length"
        type="warning"
        :closable="false"
        show-icon
        :title="`你放的自定义规则里有 ${config.junk_rule_issues.length} 条没读进来（其余规则照常生效）`"
      >
        <ul class="rule-issues">
          <li v-for="issue in config.junk_rule_issues" :key="issue">{{ issue }}</li>
        </ul>
        <p class="rule-issues__path">文件：{{ config.junk_rules_file }}</p>
      </el-alert>

      <el-alert
        v-if="config?.junk_rule_issues?.length"
        type="warning"
        :closable="false"
        show-icon
        :title="`你放的自定义规则里有 ${config.junk_rule_issues.length} 条没读进来（其余规则照常生效）`"
      >
        <ul class="rule-issues">
          <li v-for="issue in config.junk_rule_issues" :key="issue">{{ issue }}</li>
        </ul>
        <p class="rule-issues__path">文件：{{ config.junk_rules_file }}</p>
      </el-alert>

      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="扫描路径"
        placeholder="例如：C:\\ 或 D:\\Downloads"
      />

      <div class="row-actions">
        <div class="switch-field">
          <el-switch v-model="includeCaution" />
          <div>
            <strong>包含「谨慎」级项目</strong>
            <small>大于 1GB 的文件、AppData 数据目录，很可能是个人的重要文件</small>
          </div>
        </div>

        <el-button type="primary" size="large" :loading="scan.running.value" :icon="Refresh" @click="start">
          {{ scan.running.value ? '正在检查' : '开始检查' }}
        </el-button>
      </div>

      <JobProgress v-if="scan.job.value" :job="scan.job.value" empty-title="先做一次垃圾检查" @cancel="scan.cancel" />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in cards" :key="item.title" v-bind="item" />
      </section>

      <NextSteps :path="path" origin="junk" :steps="nextSteps" />

      <section
        v-for="level in levels"
        :key="level.level"
        class="app-card panel-card"
      >
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">
              <el-tag :type="LEVEL_TONE[level.level]" size="small" effect="light">{{ level.label }}</el-tag>
              &nbsp;{{ level.count }} 项 · {{ level.size_text }}
            </h3>
            <p class="panel-subtitle">{{ level.note }}</p>
          </div>
          <el-button size="small" plain @click="toggleLevel(level)">整组勾选 / 取消</el-button>
        </div>

        <div v-if="level.items?.length" class="junk-list">
          <div v-for="item in level.items.slice(0, 200)" :key="item.path" class="junk-row">
            <el-checkbox :model-value="selected.has(item.path)" @update:model-value="toggle(item)" />
            <div class="junk-row__copy">
              <strong>{{ item.name }}</strong>
              <small class="path-text" :title="item.path">{{ item.path }}</small>
              <!-- 解释就地显示：有内容才占位，没问过就什么都不显示 -->
              <small v-if="explanations[item.path]?.text" class="junk-row__explain">
                {{ explanations[item.path].text }}
              </small>
              <small v-else-if="explanations[item.path]?.reason" class="junk-row__explain is-quiet">
                {{ explanations[item.path].reason }}
              </small>
            </div>
            <el-tag size="small" effect="plain" type="info">{{ item.is_dir ? '空目录' : item.size_text }}</el-tag>
            <el-button
              class="junk-row__ask"
              size="small"
              text
              :icon="QuestionFilled"
              :loading="explanations[item.path]?.loading"
              title="这是什么？删了会怎样（需要联网，会消耗 token）"
              @click="askExplain(item)"
            />
          </div>

          <p v-if="level.items.length > 200" class="muted">
            仅显示前 200 项，共 {{ formatCount(level.items.length) }} 项；清理时会按勾选的实际项执行。
          </p>
        </div>

        <EmptyState
          v-else
          :icon="Delete"
          title="这一级没有发现问题"
        />
      </section>

      <section v-if="selected.size" class="app-card panel-card sticky-bar">
        <div>
          <strong>已选 {{ selectionStats.count }} 项</strong>
          <span class="muted">预计释放 {{ selectionStats.size_text }}</span>
          <el-segmented v-model="cleanMode" :options="modeOptions" size="small" />
        </div>
        <el-button type="primary" :icon="Delete" :loading="clean.running.value" @click="runClean">
          执行清理
        </el-button>
      </section>

      <JobProgress v-if="clean.job.value" :job="clean.job.value" @cancel="clean.cancel" @rollback="clean.rollback" />
    </template>
  </div>
</template>

<style scoped>
/* 全局 .row-actions 是 align-items: flex-end；本页左侧是开关，垂直居中才对齐（局部覆写） */
.row-actions {
  align-items: center;
}

.switch-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.switch-field strong {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.switch-field small {
  display: block;
  margin-top: 3px;
  color: var(--ink-3);
  font-size: 12px;
}

.junk-list {
  display: grid;
  gap: 8px;
}

.junk-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px;
}

.rule-issues {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: var(--fs-note);
  line-height: 1.7;
}

.rule-issues__path {
  margin: 6px 0 0;
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.rule-issues {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: var(--fs-note);
  line-height: 1.7;
}

.rule-issues__path {
  margin: 6px 0 0;
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.junk-row__explain {
  margin-top: 3px;
  color: var(--ink-2);
  font-size: var(--fs-note);
  line-height: 1.55;
}

/* 失败/未配置时用更淡的色，别和解释正文抢注意力 */
.junk-row__explain.is-quiet {
  color: var(--ink-3);
}

.junk-row__ask {
  flex: 0 0 auto;
  color: var(--ink-3);
}

.junk-row__ask:hover {
  color: var(--primary);
}

.junk-row__explain {
  margin-top: 3px;
  color: var(--ink-2);
  font-size: var(--fs-note);
  line-height: 1.55;
}

/* 失败/未配置时用更淡的色，别和解释正文抢注意力 */
.junk-row__explain.is-quiet {
  color: var(--ink-3);
}

.junk-row__ask {
  flex: 0 0 auto;
  color: var(--ink-3);
}

.junk-row__ask:hover {
  color: var(--primary);
}

.junk-row__copy {
  min-width: 0;
}

.junk-row__copy strong {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.junk-row__copy small {
  display: block;
  margin-top: 4px;
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
  margin: 4px 0 10px;
  font-size: 12px;
}
</style>

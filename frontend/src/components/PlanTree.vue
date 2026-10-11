<script setup>
/**
 * 计划树（第七阶段）。
 *
 * 一个组件同时承担两种密度：
 *   - 单步计划：只有一步，渲染出来就是原来那张计划卡的样子
 *   - 多步方案：完整 timeline + 每步产出 + 预计数字 + 决策按钮
 *
 * 只负责展示与派发意图，任何动作都由 Agent.vue 决定并调用既有接口——
 * 这里不发请求，也不判断权限档够不够。
 *
 * `editable` 打开时就地编辑条件（体积阈值 / 排除项 / 迁移方式），
 * 重算由父组件调用 /revise 完成。**只放后端真的会消费的参数**：
 * 覆盖同名、保留目录结构、完成后删源这三个键后端迁移并不读，
 * 放上来就是假开关，不如不放。
 */
import { computed, ref, watch } from 'vue'
import { ElIcon } from 'element-plus'
import { ArrowRight, Check, Close, Loading, WarningFilled } from '@element-plus/icons-vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  intent: { type: Object, default: () => ({}) },
  estimate: { type: Object, default: () => ({}) },
  proposal: { type: Object, default: () => ({}) },
  constraints: { type: Object, default: () => ({}) },
  tagOptions: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  /** 任务是否停在「等待确认」。只有这一刻才该出现执行类按钮。 */
  waiting: { type: Boolean, default: false },
  /** 是否允许就地改条件（任务停在等待确认时为 true） */
  editable: { type: Boolean, default: false },
  reviseBusy: { type: Boolean, default: false },
  permissionMode: { type: String, default: 'standard' },
  /** 折叠态。父组件可通过 :open 强制，组件自身也能折叠并向上同步。 */
  open: { type: Boolean, default: true },
})

const emit = defineEmits(['toggle', 'execute', 'safe-only', 'modify', 'discard', 'details', 'revise'])

/* 组件自己管折叠态：父组件换一份数据（比如换成任务里的计划树）时再跟随 prop */
const openState = ref(props.open)
watch(() => props.open, value => { openState.value = value })

function toggle() {
  openState.value = !openState.value
  emit('toggle', openState.value)
}

const RISK_LABEL = { low: '低风险', medium: '中风险', high: '高风险' }

const goal = computed(() => props.intent?.goal || props.proposal?.label || '执行方案')
const riskLevel = computed(() => props.intent?.risk_level || props.proposal?.risk_level || 'low')
const riskText = computed(() => RISK_LABEL[riskLevel.value] || '低风险')
const riskReason = computed(() => props.intent?.risk_reason || props.proposal?.risk_reason || '')

const hasPlan = computed(() => props.steps.length > 0)
const multi = computed(() => props.steps.length > 1)
const write = computed(() => Boolean(props.proposal?.executable) || props.intent?.needs_confirmation)

/** 推进到哪一步：用于给「运行中」的那一行加动效 */
const activeIndex = computed(() => {
  const running = props.steps.findIndex(step => step.status === 'running')
  if (running >= 0) return running
  const waiting = props.steps.findIndex(step => step.status === 'waiting')
  if (waiting >= 0) return waiting
  return props.steps.length - 1
})

const estimate = computed(() => props.proposal?.executable ? props.proposal : props.estimate || {})
const estimateKnown = computed(() => Boolean(estimate.value?.freed_text || estimate.value?.item_count))
const canApprove = computed(() => Boolean(props.proposal?.executable) && props.waiting)

/** 按钮行右端的状态说明。只说此刻一定成立的事，不替调用方下结论。 */
const stateNote = computed(() => {
  if (props.waiting) return '停在这里等你决定'
  if (props.busy) return '分析中，全程只读'
  return '只读预览，确认前不会动文件'
})

const constraintBits = computed(() => {
  const source = props.constraints || props.intent?.constraints || {}
  const bits = []
  if (source.exclude_tags?.length) bits.push(`不动 ${source.exclude_tags.join('、')}`)
  if (source.exclude_paths?.length) bits.push(`排除 ${source.exclude_paths.length} 个目录`)
  if (source.min_size_mb) bits.push(`仅 ≥ ${source.min_size_mb} MB`)
  if (source.extensions?.length) bits.push(`仅 ${source.extensions.slice(0, 5).join('/')}`)
  if (source.safe_only !== false && source.safe_only !== undefined) bits.push('只取安全级')
  if (source.blocked_paths?.length) bits.push(`${source.blocked_paths.length} 个目录是永久约束，改不掉`)
  return bits
})

function iconOf(status) {
  if (status === 'done') return Check
  if (status === 'failed') return Close
  if (status === 'running') return Loading
  if (status === 'waiting') return WarningFilled
  return ArrowRight
}

/* ---------------------------------------------------------- 就地改条件 */
const editing = ref(false)
const newPath = ref('')

const sc = computed(() => props.constraints || props.intent?.constraints || {})
const isMigrate = computed(() => ['migrate_plan', 'migrate_execute'].includes(props.intent?.action))
const isDuplicate = computed(() => ['duplicate_clean', 'duplicate_scan'].includes(props.intent?.action))
const isJunk = computed(() => ['junk_clean', 'junk_scan'].includes(props.intent?.action))

/** 永久约束：界面上要显示但删不掉，否则用户以为是 bug */
const locked = computed(() => sc.value.blocked_paths || [])

const form = ref({
  minSize: '',
  extensions: '',
  safeOnly: true,
  tags: [],
  paths: [],
  keep: '',
  migrateMode: 'move',
})

function resetForm() {
  const source = sc.value
  form.value = {
    minSize: source.min_size_mb ? String(source.min_size_mb) : '',
    extensions: (source.extensions || []).join(', '),
    safeOnly: source.safe_only !== false,
    tags: [...(source.exclude_tags || [])],
    paths: [...(source.exclude_paths || [])],
    keep: source.keep || '',
    migrateMode: source.migrate_mode || 'move',
  }
  newPath.value = ''
}

watch(() => props.constraints, resetForm, { deep: true, immediate: true })

function openEditor() {
  resetForm()
  editing.value = true
}

function toggleTag(name) {
  const list = form.value.tags
  const index = list.indexOf(name)
  if (index >= 0) list.splice(index, 1)
  else list.push(name)
}

function addPath() {
  const value = newPath.value.trim()
  if (!value || form.value.paths.includes(value)) return
  form.value.paths.push(value)
  newPath.value = ''
}

function removePath(value) {
  form.value.paths = form.value.paths.filter(item => item !== value)
}

function submitRevise() {
  // 永久约束不用在这里补回去——后端 apply_hard_rules 会强制加回并记进 blocked_paths。
  // 前端把它当成「删不掉」展示就够了，别自己造一套并行逻辑。
  const patch = {
    min_size_mb: form.value.minSize === '' ? null : Number(form.value.minSize),
    extensions: form.value.extensions,
    safe_only: form.value.safeOnly,
    exclude_tags: [...form.value.tags],
    exclude_paths: [...form.value.paths],
  }
  if (isDuplicate.value) patch.keep = form.value.keep || null
  if (isMigrate.value) patch.migrate_mode = form.value.migrateMode
  editing.value = false
  emit('revise', patch)
}
</script>

<template>
  <section class="plan" :class="{ 'is-write': write, 'is-open': openState }">
    <button type="button" class="plan__head" @click="toggle">
      <el-icon :size="13" class="plan__caret"><ArrowRight /></el-icon>
      <strong class="plan__goal">{{ goal }}</strong>
      <span class="plan__risk" :class="`is-${riskLevel}`">{{ riskText }}</span>
      <span class="plan__spacer"></span>
      <span v-if="multi" class="plan__count">{{ steps.length }} 步</span>
    </button>

    <div v-show="openState" class="plan__body">
      <p v-if="riskReason" class="plan__risk-note">{{ riskReason }}</p>

      <ol v-if="hasPlan" class="plan__steps">
        <li
          v-for="(step, index) in steps"
          :key="step.index ?? index"
          class="plan__step"
          :class="[`is-${step.status || 'pending'}`, { 'is-active': index === activeIndex && busy }]"
        >
          <span class="plan__marker">
            <el-icon :size="11"><component :is="iconOf(step.status)" /></el-icon>
          </span>
          <div class="plan__step-body">
            <div class="plan__step-title">{{ step.title }}</div>
            <div class="plan__step-digest">{{ step.digest || step.note || step.output || step.input }}</div>
            <div v-if="step.risk === 'high' || step.risk === 'medium'" class="plan__step-risk">
              {{ step.risk === 'high' ? '高风险步骤' : '会改动文件' }}
            </div>
          </div>
        </li>
      </ol>

      <div v-if="estimateKnown" class="plan__numbers">
        <span v-if="estimate.freed_text" class="plan__number">
          {{ estimate.freed_label || '预计释放' }} <strong>{{ estimate.freed_text }}</strong>
        </span>
        <span v-if="estimate.item_count" class="plan__number">
          文件数 <strong>{{ estimate.item_count }}</strong>
        </span>
        <span v-if="estimate.duration_text" class="plan__number">
          预计耗时 <strong>{{ estimate.duration_text }}</strong>
        </span>
        <span v-if="estimate.batches" class="plan__number">
          分 <strong>{{ estimate.batches }}</strong> 批执行
        </span>
      </div>

      <div v-if="constraintBits.length" class="plan__constraints">
        <span v-for="bit in constraintBits" :key="bit">{{ bit }}</span>
      </div>

      <p v-if="proposal?.note" class="plan__note">{{ proposal.note }}</p>
      <p v-if="proposal?.blocked?.length" class="plan__blocked">
        {{ proposal.blocked.join('；') }}
      </p>

      <!-- 就地改条件：只放后端真的会消费的参数，重算零 token -->
      <div v-if="editing" class="revise">
        <div class="revise__row">
          <span class="revise__label">体积阈值</span>
          <el-input v-model="form.minSize" size="small" class="revise__num" placeholder="不限" />
          <span class="revise__unit">MB 以上</span>
          <label v-if="isJunk" class="revise__check">
            <el-checkbox v-model="form.safeOnly" size="small" />只取安全级
          </label>
        </div>

        <div class="revise__row">
          <span class="revise__label">不动这些</span>
          <div class="revise__inline">
            <button
              v-for="tag in tagOptions"
              :key="tag.name"
              type="button"
              class="revise__tag"
              :class="{ 'is-on': form.tags.includes(tag.name) }"
              :title="tag.note"
              @click="toggleTag(tag.name)"
            >{{ tag.name }}</button>
            <span v-if="!tagOptions.length" class="revise__empty">规则库还没配置标签</span>
          </div>
        </div>

        <div class="revise__row">
          <span class="revise__label">排除路径</span>
          <div class="revise__paths">
            <span
              v-for="item in form.paths"
              :key="item"
              class="revise__path"
              :class="{ 'is-locked': locked.includes(item) }"
              :title="locked.includes(item) ? '永久约束，要改去偏好记忆里删' : item"
            >
              {{ item }}
              <button v-if="!locked.includes(item)" type="button" @click="removePath(item)">×</button>
              <span v-else class="revise__lock">锁</span>
            </span>
            <span v-if="!form.paths.length" class="revise__empty">还没有</span>
          </div>
          <div class="revise__add">
            <el-input
              v-model="newPath"
              size="small"
              placeholder="例如 D:\素材"
              @keyup.enter="addPath"
            />
            <el-button size="small" plain @click="addPath">加</el-button>
          </div>
        </div>

        <div v-if="isMigrate" class="revise__row">
          <span class="revise__label">只处理</span>
          <el-input
            v-model="form.extensions"
            size="small"
            placeholder="留空表示不限，如 pdf, mp4"
          />
        </div>

        <div v-if="isDuplicate" class="revise__row">
          <span class="revise__label">保留</span>
          <label class="revise__check">
            <el-checkbox v-model="form.keep" true-label="newest" false-label="" size="small" />
            最新修改的那一份
          </label>
          <span class="revise__unit">不勾则保留最早的一份</span>
        </div>

        <div v-if="isMigrate" class="revise__row">
          <span class="revise__label">迁移方式</span>
          <label class="revise__check">
            <input v-model="form.migrateMode" type="radio" value="move" />移动到目标（删源）
          </label>
          <label class="revise__check">
            <input v-model="form.migrateMode" type="radio" value="copy" />只复制（源文件保留）
          </label>
        </div>

        <p v-if="locked.length" class="revise__note">
          {{ locked.join('、') }} 是永久约束，这里删不掉——要改请去右上角偏好记忆里删。
        </p>

        <div class="revise__actions">
          <el-button size="small" type="primary" :loading="reviseBusy" @click="submitRevise">
            重算方案
          </el-button>
          <el-button size="small" plain @click="editing = false">取消</el-button>
          <span class="revise__hint">只换条件重算，不花 token；重算后仍然要你确认才会动文件</span>
        </div>
      </div>

      <div class="plan__actions">
        <el-button
          v-if="canApprove"
          size="small"
          type="warning"
          :disabled="permissionMode === 'readonly'"
          @click="emit('execute')"
        >继续执行</el-button>
        <el-button
          v-if="canApprove"
          size="small"
          plain
          :disabled="permissionMode === 'readonly'"
          @click="emit('safe-only')"
        >只执行安全项</el-button>
        <el-button
          v-if="editable"
          size="small"
          plain
          :disabled="editing"
          @click="openEditor"
        >修改方案</el-button>
        <el-button v-else-if="hasPlan" size="small" plain @click="emit('modify')">
          改条件重说
        </el-button>
        <el-button v-if="hasPlan" size="small" plain @click="emit('details')">查看详细清单</el-button>
        <el-button v-if="busy" size="small" plain @click="emit('discard')">撤销本次计划</el-button>
        <span v-if="hasPlan" class="plan__actions-spacer"></span>
        <span v-if="permissionMode === 'readonly' && canApprove" class="plan__actions-note">
          只读档不可执行写操作
        </span>
        <span v-else-if="hasPlan" class="plan__actions-note">{{ stateNote }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.plan {
  border: 1px solid var(--hairline);
  border-radius: var(--radius-ctl);
  background: var(--surface);
  overflow: hidden;
}

.plan.is-write {
  border-color: var(--hairline-strong);
}

/* 卡头：浅灰底 + 下缘一条 hairline，把它从卡身里分出来。
   整块一色只会看起来像一坨；有「表头」才有秩序感，也才像一张正经的卡片。
   收起时不画那条线 —— 否则会和卡片自身的下边框挤成一条双线。 */
.plan__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 0;
  border-bottom: 1px solid transparent;
  background: var(--surface-2);
  cursor: pointer;
  text-align: left;
}

.plan.is-open .plan__head {
  border-bottom-color: var(--hairline);
}

.plan__caret {
  color: var(--ink-3);
  transition: transform 0.16s ease;
}

.plan.is-open .plan__caret {
  transform: rotate(90deg);
}

.plan__goal {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}

.plan__risk {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
}

.plan__risk.is-low {
  color: var(--success-ink);
  background: var(--success-soft);
}

.plan__risk.is-medium {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.plan__risk.is-high {
  color: var(--danger-ink);
  background: var(--danger-soft);
}

.plan__spacer {
  flex: 1;
}

.plan__count {
  font-size: 12px;
  color: var(--ink-3);
}

.plan__body {
  padding: var(--space-3);
}

.plan__risk-note {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--ink-3);
}

.plan__steps {
  list-style: none;
  margin: 0;
  padding: 0;
}

.plan__step {
  display: flex;
  gap: 10px;
  padding: 7px 0;
}

.plan__marker {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  flex: none;
  border-radius: var(--radius-pill);
  border: 1px solid var(--hairline-strong);
  color: var(--ink-4);
  background: var(--surface);
}

.plan__step.is-done .plan__marker {
  border-color: var(--success);
  color: var(--success-ink);
  background: var(--success-soft);
}

.plan__step.is-running .plan__marker {
  border-color: var(--primary);
  color: var(--primary);
  background: var(--primary-soft);
}

.plan__step.is-waiting .plan__marker {
  border-color: var(--warning);
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.plan__step.is-failed .plan__marker {
  border-color: var(--danger);
  color: var(--danger-ink);
  background: var(--danger-soft);
}

.plan__step-body {
  min-width: 0;
}

.plan__step-title {
  font-size: 13px;
  color: var(--ink);
}

.plan__step.is-pending .plan__step-title {
  color: var(--ink-3);
}

.plan__step-digest {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.5;
}

.plan__step-risk {
  font-size: 11px;
  color: var(--warning-ink);
}

.plan__numbers {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline);
  font-size: 12px;
  color: var(--ink-2);
}

.plan__number strong {
  font-weight: 500;
  color: var(--ink);
}

.plan__constraints {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-3);
}

.plan__note,
.plan__blocked {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.5;
}

.plan__blocked {
  color: var(--warning-ink);
}

.plan__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.plan__actions-spacer {
  flex: 1;
}

/* 状态说明挂在按钮行右端。刻意不写「0 token」：
   这份计划可能是本地规则免费出的，也可能是花过 token 换来的，
   组件本身拿不到来源，替它下结论就是在编。 */
.plan__actions-note {
  font-size: var(--fs-note);
  color: var(--ink-3);
}

/* ------------------------------------------------ 就地改条件（修改方案） */
.revise {
  display: grid;
  gap: 10px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

.revise__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
}

.revise__label {
  flex: none;
  width: 62px;
  font-size: 12px;
  color: var(--ink-2);
}

.revise__num {
  width: 92px;
}

.revise__unit {
  font-size: 12px;
  color: var(--ink-3);
}

.revise__check {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
}

.revise__inline {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.revise__tag {
  padding: 3px 10px;
  color: var(--ink-2);
  font-size: 12px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  cursor: pointer;
}

.revise__tag.is-on {
  color: var(--primary-ink);
  background: var(--primary-soft);
  border-color: var(--primary-soft);
}

.revise__paths {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.revise__path {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  padding: 3px 8px;
  font-size: 12px;
  color: var(--ink-2);
  background: var(--inset);
  border-radius: var(--radius-pill);
}

.revise__path.is-locked {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.revise__path button {
  padding: 0;
  color: var(--ink-3);
  font-size: 13px;
  line-height: 1;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.revise__path button:hover {
  color: var(--danger-ink);
}

.revise__lock {
  font-size: 11px;
}

.revise__empty {
  font-size: 12px;
  color: var(--ink-4);
}

.revise__add {
  display: flex;
  flex: none;
  gap: 6px;
  width: 240px;
}

.revise__note {
  margin: 0;
  font-size: 12px;
  color: var(--warning-ink);
  line-height: 1.5;
}

.revise__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.revise__hint {
  font-size: 12px;
  color: var(--ink-3);
}

@media (max-width: 720px) {
  .revise__label {
    width: 100%;
  }

  .revise__add {
    width: 100%;
  }
}
</style>

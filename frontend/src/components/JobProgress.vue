<script setup>
import { computed } from 'vue'
import { ElMessageBox } from 'element-plus'
import { CircleCheck, CircleClose, RefreshLeft, VideoPause } from '@element-plus/icons-vue'
import { formatDateTime } from '../utils/formatters'

const props = defineProps({
  job: { type: Object, default: null },
  cancellable: { type: Boolean, default: true },
  rollbackable: { type: Boolean, default: true },
  emptyTitle: { type: String, default: '暂无任务' },
})

const emit = defineEmits(['cancel', 'rollback'])

/** 中断任务是一次「危险动作」：先确认，避免误点把跑着的任务杀掉。
  按钮主次：安全动作「继续运行」做成主色蓝（推荐路径），
  危险动作「中断」做成红色（danger），避免蓝色主按钮诱导误点。 */
async function requestCancel() {
  try {
    await ElMessageBox.confirm(
      '任务会停在半途。已处理的内容按各自策略保留；删除项仍在隔离区，可回滚。',
      '中断这个任务？',
      {
        type: 'warning',
        confirmButtonText: '中断',
        cancelButtonText: '继续运行',
        confirmButtonClass: 'el-button--danger',
        cancelButtonClass: 'el-button--primary',
      },
    )
  } catch {
    return
  }
  emit('cancel')
}

const PHASES = ['扫描', '分析', '生成清单', '等待确认', '执行', '校验', '完成']

const STATUS_META = {
  pending: { label: '排队中', type: 'info' },
  running: { label: '进行中', type: 'primary' },
  waiting: { label: '等待确认', type: 'warning' },
  done: { label: '已完成', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
}

const status = computed(() => STATUS_META[props.job?.status] || STATUS_META.pending)
const phaseIndex = computed(() => props.job?.phase_index ?? 0)
const isRunning = computed(() => ['pending', 'running', 'waiting'].includes(props.job?.status))

/** 步骤条 + 进度条只在任务还没跑完时渲染；
    done / failed / cancelled 直接看头部状态和结果摘要，不再摆一条走到一半的流程。 */
const showSteps = computed(() => Boolean(props.job) && isRunning.value)
const showLive = computed(() => Boolean(props.job) && isRunning.value && props.job.status !== 'pending')

const steps = computed(() => PHASES.map((label, index) => ({
  label,
  index,
  state: index < phaseIndex.value ? 'done'
    : index === phaseIndex.value && isRunning.value ? 'active'
      : index === phaseIndex.value ? 'done' : 'pending',
})))

const progressPercent = computed(() => {
  if (props.job?.status === 'done') return 100
  return Math.max(0, Math.min(99, props.job?.percent ?? 0))
})

const elapsed = computed(() => {
  const started = props.job?.started_at
  const finished = props.job?.finished_at
  if (!started) return ''
  const end = finished ? new Date(finished) : new Date()
  const seconds = Math.max(0, Math.round((end - new Date(started)) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
})

const DOT_CLASS = {
  pending: 'status-dot--primary',
  running: 'status-dot--primary',
  waiting: 'status-dot--warning',
  done: 'status-dot--success',
  failed: 'status-dot--danger',
  cancelled: '',
}
const dotClass = computed(() => DOT_CLASS[props.job?.status] ?? '')

const startTimeText = computed(() => {
  const started = props.job?.started_at || props.job?.created_at
  return started ? formatDateTime(started) : ''
})

const elapsedText = computed(() => {
  if (!elapsed.value) return ''
  return isRunning.value ? `已运行 ${elapsed.value}` : `耗时 ${elapsed.value}`
})

const metaText = computed(() => [startTimeText.value, elapsedText.value].filter(Boolean).join(' · '))
</script>

<template>
  <div v-if="!job" class="job-progress job-progress--empty">
    <span class="muted">{{ emptyTitle }}</span>
  </div>

  <div v-else class="job-progress">
    <div class="job-progress__head">
      <div class="job-progress__title">
        <span class="job-progress__type">{{ job.type_label || job.type }}</span>
        <strong>{{ job.title }}</strong>
        <div class="job-progress__meta">
          <span v-if="metaText">{{ metaText }} ·</span>
          <span class="status-dot" :class="dotClass">{{ status.label }}</span>
        </div>
      </div>

      <div class="job-progress__actions">
        <el-button
          v-if="cancellable && isRunning"
          :icon="VideoPause"
          plain
          size="small"
          @click="requestCancel"
        >
          中断任务
        </el-button>

        <el-button
          v-if="rollbackable && job.rollbackable && !isRunning"
          :icon="RefreshLeft"
          plain
          size="small"
          @click="$emit('rollback')"
        >
          回滚这次改动
        </el-button>

        <el-tag v-if="job.status === 'failed'" type="danger" effect="plain" size="small">
          <el-icon><CircleClose /></el-icon>&nbsp;未能完成
        </el-tag>
      </div>
    </div>

    <div v-if="showSteps" class="flow">
      <div
        v-for="step in steps"
        :key="step.label"
        class="flow__step"
        :class="`is-${step.state}`"
      >
        <div class="flow__dot">
          <el-icon v-if="step.state === 'done'" :size="11"><CircleCheck /></el-icon>
        </div>
        <span class="flow__label">{{ step.label }}</span>
        <div v-if="step.index < steps.length - 1" class="flow__line"></div>
      </div>
    </div>

    <template v-if="showLive">
      <div class="bar">
        <div class="bar__fill" :style="{ width: `${progressPercent}%` }"></div>
      </div>

      <div class="job-progress__live">
        <span class="job-progress__message">{{ job.message }}</span>
        <span class="job-progress__stats">
          <template v-if="job.total"> {{ job.current }} / {{ job.total }} · </template>
          {{ progressPercent }}%<template v-if="elapsed"> · {{ elapsed }}</template>
        </span>
      </div>

      <div v-if="job.current_path" class="path-text job-progress__path" :title="job.current_path">
        {{ job.current_path }}
      </div>
    </template>

    <el-alert
      v-if="job.status === 'failed' && job.error"
      type="error"
      :title="job.error"
      :closable="false"
      show-icon
    />

    <div v-if="job.actions?.length" class="job-progress__notice">
      <el-icon><CircleCheck /></el-icon>
      本次任务已记录 {{ job.actions.length }} 条可回滚操作
    </div>
  </div>
</template>

<style scoped>
.job-progress {
  /* 局部尺寸档位：全局 token 没有这些步进，值与原字面量完全一致（v22 视觉零变化）。
     能映射全局 token 的一律用 token（--fs-micro/note/title、--radius-pill）。 */
  --jp-radius: 14px;
  --jp-radius-sm: 11px;
  --jp-dot: 18px;
  --jp-step-min: 74px;
  --jp-bar-h: 8px;
  --jp-line-h: 2px;
  --jp-glow: 0 0 0 4px var(--primary-soft);
  --jp-fs-msg: 13px;
  display: grid;
  gap: 14px;
}

.job-progress--empty {
  padding: 22px;
  /* 白底 + 虚线框：与卡片同色，不再是一块灰。
     （原来是 --surface-2 灰底，在纯白卡上会显出一条整宽灰带，
     和首页「卡片内部全白」的观感不一致。） */
  background: var(--surface);
  border: 1px dashed var(--hairline-strong);
  border-radius: var(--jp-radius);
  text-align: center;
}

.job-progress__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.job-progress__title {
  min-width: 0;
}

.job-progress__type {
  display: block;
  margin-bottom: 4px;
  color: var(--ink-3);
  font-size: var(--fs-micro);
  font-weight: 600;
  letter-spacing: 0.06em;
}

.job-progress__title strong {
  display: block;
  color: var(--ink);
  font-size: var(--fs-title);
  font-weight: 600;
  word-break: break-all;
}

/* 元信息放标题下方（对齐 Agent 页的语言）：时间 · 耗时 · 状态 */
.job-progress__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 5px;
  color: var(--ink-3);
  font-size: var(--fs-micro);
}

.job-progress__actions {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.flow {
  display: flex;
  align-items: flex-start;
  overflow-x: auto;
  padding: 4px 0 2px;
}

.flow__step {
  position: relative;
  flex: 1;
  min-width: var(--jp-step-min);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.flow__dot {
  width: var(--jp-dot);
  height: var(--jp-dot);
  display: grid;
  place-items: center;
  color: var(--ink-2);
  background: var(--inset);
  border-radius: var(--radius-pill);
  z-index: 1;
  transition: background-color var(--dur) var(--ease), color var(--dur) var(--ease),
    box-shadow var(--dur) var(--ease);
}

.flow__label {
  color: var(--ink-3);
  font-size: var(--fs-micro);
  white-space: nowrap;
}

.flow__line {
  position: absolute;
  top: calc(var(--jp-dot) / 2);
  left: 50%;
  width: 100%;
  height: var(--jp-line-h);
  background: var(--hairline-strong);
}

.flow__step.is-done .flow__dot {
  background: var(--success);
  color: var(--surface);
}

.flow__step.is-done .flow__label {
  color: var(--ink-2);
}

.flow__step.is-active .flow__dot {
  background: var(--primary);
  color: var(--surface);
  box-shadow: var(--jp-glow);
}

.flow__step.is-active .flow__label {
  color: var(--primary-ink);
  font-weight: 600;
}

.flow__step.is-done .flow__line {
  background: var(--success);
}

.bar {
  height: var(--jp-bar-h);
  background: var(--inset);
  border-radius: var(--radius-pill);
  overflow: hidden;
}

.bar__fill {
  height: 100%;
  background: var(--primary);
  border-radius: var(--radius-pill);
  transition: width var(--duration-slow) var(--ease);
}

.job-progress__live {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.job-progress__message {
  color: var(--ink-2);
  font-size: var(--jp-fs-msg);
}

.job-progress__stats {
  color: var(--ink-3);
  font-size: var(--fs-note);
  white-space: nowrap;
}

.job-progress__path {
  max-width: 100%;
}

.job-progress__notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  color: var(--success-ink);
  background: var(--success-soft);
  border-radius: var(--jp-radius-sm);
  font-size: var(--fs-note);
}
</style>

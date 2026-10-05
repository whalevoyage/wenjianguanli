<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Coin,
  CopyDocument,
  Delete,
  DeleteFilled,
  FolderOpened,
  Operation,
  PieChart,
  Refresh,
  Search,
  Sort,
} from '@element-plus/icons-vue'
import { getOverview } from '../api/filepilot'
import StatCard from '../components/StatCard.vue'
import ActionTile from '../components/ActionTile.vue'
import EmptyState from '../components/EmptyState.vue'
import ChartTip, { TIP_H, TIP_W } from '../components/ChartTip.vue'
import DonutChart from '../components/DonutChart.vue'
import TrendBars from '../components/TrendBars.vue'
import { formatCount, formatShortTime } from '../utils/formatters'
import { readOverviewCache, writeOverviewCache } from '../utils/overviewCache'

const router = useRouter()

/* 再进首页时先渲染上一次的 overview（不再从空白等接口），同时在后台静默刷新。
   缓存放在模块作用域（utils/overviewCache），否则会随组件实例重建。 */
const data = ref(readOverviewCache())
const loading = ref(false)
const error = ref('')

/* 快捷入口统一用一个主色：四个入口是同一类动作，不需要靠颜色区分。 */
const quickActions = [
  { label: '扫描目录', path: '/scan', icon: Search, tone: 'blue' },
  { label: '垃圾扫描', path: '/junk', icon: Delete, tone: 'blue' },
  { label: '重复文件查重', path: '/duplicates', icon: CopyDocument, tone: 'blue' },
  { label: '跨盘迁移', path: '/migrate', icon: Sort, tone: 'blue' },
]

const cards = computed(() => {
  if (!data.value) return []
  const breakdown = data.value.reclaimable_breakdown || {}
  const total = data.value.disk_total || {}
  return [
    {
      title: '可释放空间',
      value: data.value.reclaimable_size_text,
      note: `垃圾 ${breakdown.junk_text || '0 B'} · 重复 ${breakdown.duplicate_text || '0 B'}`,
      tone: 'blue',
      icon: Coin,
    },
    {
      title: '进行中任务',
      value: formatCount(data.value.jobs?.running),
      note: `等待确认 ${formatCount(data.value.jobs?.waiting)} 个`,
      tone: 'blue',
      icon: Operation,
    },
    {
      title: '回收站占用',
      value: data.value.trash?.size_text,
      note: `${formatCount(data.value.trash?.count)} 个项目待处理`,
      tone: 'blue',
      icon: DeleteFilled,
    },
    {
      title: '磁盘占用率',
      value: `${total.percent ?? 0}%`,
      note: `已用 ${total.used_text || '—'} / 共 ${total.total_text || '—'}`,
      tone: 'blue',
      icon: PieChart,
    },
  ]
})

/* 任务统计：后端 job_stats 缺失时给一份安全空值，模板不必到处判空。 */
const stats = computed(
  () =>
    data.value?.job_stats || {
      by_status: {},
      by_type: [],
      daily: [],
      done: 0,
      failed: 0,
      success_rate: null,
    },
)

/* 趋势卡的数字必须和曲线同口径（都取 14 天窗口内），否则角标写"近 14 天"就名不副实 */
const windowStats = computed(() => {
  const base = stats.value
  return (
    base.window || { done: base.done, failed: base.failed, success_rate: base.success_rate }
  )
})

/* 类型分布：取前 5，条形宽度按本组最大值归一 */
const typeBars = computed(() => {
  const list = (stats.value.by_type || []).slice(0, 5)
  const max = Math.max(1, ...list.map(item => item.count))
  return list.map(item => ({ ...item, percent: Math.round((item.count / max) * 100) }))
})

/* ---------------------------------------------------------- 悬停提示 */
/* 环形图：全盘已用 / 可用 */
const diskTipRows = computed(() => {
  const total = data.value?.disk_total || {}
  return [
    { label: '已用', value: total.used_text || '—', tone: 'done' },
    { label: '可用', value: total.free_text || '—' },
  ]
})

/* 类型分布：本行在全部任务里的占比（分母取全部类型，不只是前 5） */
const typeTotal = computed(
  () => (stats.value.by_type || []).reduce((acc, item) => acc + item.count, 0) || 1,
)

const typeWrap = ref(null)
const typeTip = ref({ open: false, x: 0, y: 0, caretX: 0, caret: 'bottom', title: '', rows: [] })

function showTypeTip(item, event) {
  const row = event.currentTarget
  const wrapEl = typeWrap.value
  if (!row || !wrapEl) return
  const r = row.getBoundingClientRect()
  const w = wrapEl.getBoundingClientRect()
  const center = r.left - w.left + r.width / 2
  const x = Math.min(Math.max(center - TIP_W / 2, 0), Math.max(w.width - TIP_W, 0))
  // 优先浮在本行上方；上面没位置就放到下方
  const above = r.top - w.top - TIP_H - 8
  const y = above >= 0 ? above : r.bottom - w.top + 8
  typeTip.value = {
    open: true,
    x,
    y,
    caretX: Math.min(Math.max(center - x, 14), TIP_W - 14),
    caret: 'bottom',
    title: item.label,
    rows: [
      // 沿用磁盘图的「主 / 次」模式：主指标给主色点，派生指标留灰。
      // 两个都给灰会让提示卡看起来「没上色」（v21 修）。
      { label: '任务数', value: item.count, tone: 'done' },
      { label: '占比', value: `${Math.round((item.count / typeTotal.value) * 100)}%` },
    ],
  }
}

function hideTypeTip() {
  if (typeTip.value.open) typeTip.value = { ...typeTip.value, open: false }
}

/* 语义色分离：金色是 Agent/AI 的品牌标识，不再兼任 warning */
const LEVEL_COLOR = {
  danger: 'var(--danger)',
  warning: 'var(--warning)',
  ok: 'var(--primary)',
}

const DISK_STATUS = { danger: '告急', warning: '偏高', ok: '正常' }

function levelColor(level) {
  return LEVEL_COLOR[level] || LEVEL_COLOR.ok
}

const recentJobs = computed(() => (data.value?.jobs?.recent || []).slice(0, 6))

const JOB_TAG_TEXT = {
  done: '完成',
  failed: '失败',
  running: '进行中',
  cancelled: '已取消',
}

async function load() {
  loading.value = !data.value      // 有缓存时静默刷新，不再铺整页 loading
  error.value = ''
  try {
    const fresh = await getOverview()
    data.value = fresh
    writeOverviewCache(fresh)
  } catch (err) {
    error.value = err.message
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="section-stack">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <section class="stats-grid">
      <StatCard v-for="item in cards" :key="item.title" v-bind="item" />
    </section>

    <section class="dash-grid">
      <!-- 磁盘容量：环形图（全盘占比）+ 每盘明细 -->
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <h3 class="panel-title">磁盘容量</h3>
          <div class="panel-title-actions">
            <span v-if="data" class="panel-count">
              合计 {{ data.disk_total?.used_text }} / {{ data.disk_total?.total_text }}
            </span>
            <el-button circle text :icon="Refresh" :loading="loading" aria-label="刷新" @click="load" />
          </div>
        </div>

        <div v-if="data?.disks?.length" class="disk-flex">
          <DonutChart
            :percent="data.disk_total?.percent || 0"
            :size="148"
            caption="全盘已用"
            tip-title="全盘容量"
            :tip-rows="diskTipRows"
          />

          <div class="disk-list">
            <div v-for="disk in data.disks" :key="disk.letter" class="disk-row">
              <div class="disk-row__top">
                <span class="disk-row__name">
                  <el-icon :size="15"><FolderOpened /></el-icon>
                  <strong>{{ disk.letter }}:</strong>
                </span>
                <span class="disk-row__pct tnum" :style="{ color: levelColor(disk.level) }">
                  {{ disk.percent }}%
                </span>
              </div>
              <div class="disk-row__bar">
                <i :style="{ width: `${Math.min(Math.max(disk.percent, 1.5), 100)}%`, backgroundColor: levelColor(disk.level) }"></i>
                <b class="disk-row__mark"></b>
              </div>
              <div class="disk-row__foot">
                <span class="status-chip" :class="`is-${disk.level}`">{{ DISK_STATUS[disk.level] || '正常' }}</span>
                <span class="tnum">已用 {{ disk.used_text }}</span>
                <span class="tnum">可用 {{ disk.free_text }}</span>
                <span class="tnum disk-row__total">共 {{ disk.total_text }}</span>
              </div>
            </div>
          </div>
        </div>

        <EmptyState v-else :icon="FolderOpened" title="没有读到磁盘信息" />
      </article>

      <!-- 任务趋势：大数字 + 平滑曲线（曲线下 8%→0 渐变托底） -->
      <article class="app-card panel-card trend-card">
        <div class="panel-title-row">
          <h3 class="panel-title">任务趋势</h3>
          <div class="panel-title-actions">
            <span class="panel-count">近 14 天</span>
          </div>
        </div>

        <div class="trend-summary">
          <div class="trend-summary__main">
            <b class="trend-summary__value tnum">{{ windowStats.done }}</b>
            <span class="trend-summary__label">完成</span>
          </div>
          <div class="trend-summary__rest">
            <span class="trend-summary__item">
              <i class="trend-summary__swatch"></i>失败 <b class="tnum is-fail">{{ windowStats.failed }}</b>
            </span>
            <span v-if="windowStats.success_rate !== null">
              成功率 <b class="tnum">{{ windowStats.success_rate }}%</b>
            </span>
          </div>
        </div>

        <TrendBars :data="stats.daily" />
      </article>
    </section>

    <section class="duo-grid">
      <!-- 任务类型分布 -->
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <h3 class="panel-title">任务类型分布</h3>
        </div>
        <div v-if="typeBars.length" ref="typeWrap" class="type-list" @mouseleave="hideTypeTip">
          <div
            v-for="item in typeBars"
            :key="item.type"
            class="type-row"
            @mouseenter="showTypeTip(item, $event)"
          >
            <span class="type-row__label">{{ item.label }}</span>
            <span class="type-row__bar"><i :style="{ width: `${item.percent}%` }"></i></span>
            <span class="type-row__count tnum">{{ item.count }}</span>
          </div>
          <ChartTip v-bind="typeTip" />
        </div>
        <EmptyState v-else :icon="Operation" title="暂无任务记录" />
      </article>

      <!-- 最近任务 -->
      <article class="app-card panel-card">
        <div class="panel-title-row">
          <h3 class="panel-title">最近任务</h3>
          <el-button text type="primary" @click="router.push('/jobs')">全部任务</el-button>
        </div>

        <div v-if="recentJobs.length" class="recent-table">
          <div class="recent-table__head">
            <span>任务类型</span><span>目标路径</span><span>完成时间</span><span>状态</span>
          </div>
          <div
            v-for="job in recentJobs"
            :key="job.id"
            class="recent-table__row"
            @click="router.push({ path: '/jobs', query: { id: job.id } })"
          >
            <strong>{{ job.type_label || job.title }}</strong>
            <span class="path-text" :title="job.target">{{ job.target || '—' }}</span>
            <span class="tnum" :title="job.finished_at || job.created_at">
              {{ formatShortTime(job.finished_at || job.created_at) }}
            </span>
            <span
              class="status-dot"
              :class="`status-dot--${job.status === 'failed' ? 'danger' : job.status === 'done' ? 'success' : 'warning'}`"
            >{{ JOB_TAG_TEXT[job.status] || '排队' }}</span>
          </div>
        </div>

        <EmptyState v-else :icon="Operation" title="还没有任务记录" />
      </article>
    </section>

    <section class="action-grid">
      <ActionTile
        v-for="item in quickActions"
        :key="item.path"
        :title="item.label"
        :icon="item.icon"
        :tone="item.tone"
        @select="router.push(item.path)"
      />
    </section>
  </div>
</template>

<style scoped>
/* 首页两栏：磁盘容量略宽，趋势图稍窄，各自按内容收高 */
.dash-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 24px;
}

.action-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

/* --------------------------------------------------------------- 磁盘区块 */
.disk-flex {
  display: flex;
  align-items: center;
  gap: 28px;
}

.disk-list {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 18px;
}

.disk-row {
  display: grid;
  gap: 8px;
}

.disk-row__top {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.disk-row__name {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--ink-2);
}

.disk-row__name strong {
  color: var(--ink);
  font-size: var(--fs-title);
  font-weight: 600;
  letter-spacing: -0.012em;
}

.disk-row__pct {
  margin-left: auto;
  font-size: var(--fs-h2);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1;
}

.disk-row__bar {
  position: relative;
  height: 8px;
  overflow: hidden;
  background: var(--inset);
  border-radius: var(--radius-pill);
}

.disk-row__bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  /* 语义色由内联的 background-color 给（蓝/橙/红 = 占用等级，不许改色相）；
     这里只叠一层横向高光，让色条「有光」而不是死板一块纯色。 */
  background-image: linear-gradient(90deg, rgba(255, 255, 255, 0.32), rgba(255, 255, 255, 0) 64%);
  transition: width var(--duration-slow) var(--ease-out);
}

/* 90% 危险阈值刻度：让「标红」这件事在视觉上可预期 */
.disk-row__mark {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 90%;
  width: 1.5px;
  background: var(--ink-4);
  opacity: 0.55;
}

.disk-row__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.disk-row__total {
  margin-left: auto;
}

.status-chip {
  padding: 1px 8px;
  border-radius: var(--radius-pill);
  font-size: var(--fs-micro);
  font-weight: 500;
  line-height: 1.6;
}

.status-chip.is-danger {
  color: var(--danger-ink);
  background: var(--danger-soft);
}

.status-chip.is-warning {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.status-chip.is-ok {
  color: var(--success-ink);
  background: var(--success-soft);
}

/* ------------------------------------------------------------- 趋势区块 */
/* 卡片撑满同排高度：数字在上、曲线沉底，中间的空档由 flex 吸收，避免卡片底部留大片空白 */
.trend-card {
  display: flex;
  flex-direction: column;
}

.trend-card > svg {
  margin-top: auto;
}

.trend-summary {
  display: flex;
  align-items: baseline;
  gap: 18px;
  margin: 4px 0 14px;
}

.trend-summary__main {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.trend-summary__value {
  color: var(--ink);
  font-size: var(--fs-h1);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1;
}

.trend-summary__label {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.trend-summary__rest {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-left: auto;
  color: var(--ink-3);
  font-size: var(--fs-note);
  white-space: nowrap;
}

.trend-summary__item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

/* 图例小方块：对应柱顶的失败「红帽」 */
.trend-summary__swatch {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--danger);
}

.trend-summary__rest b {
  color: var(--ink);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.trend-summary__rest b.is-fail {
  color: var(--danger-ink);
}

/* --------------------------------------------------------- 类型分布区块 */
.type-list {
  position: relative;
  display: grid;
  gap: 14px;
}

.type-row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) 36px;
  align-items: center;
  gap: 12px;
}

.type-row__label {
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--fs-note);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.type-row__bar {
  display: block;
  height: 8px;
  overflow: hidden;
  background: var(--inset);
  border-radius: var(--radius-pill);
}

.type-row__bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  /* 与趋势柱 / 环形弧同一套渐变语言：左亮 → 右深 */
  background: linear-gradient(90deg, var(--chart-from), var(--chart-to));
  transition: width var(--duration-slow) var(--ease-out);
}

.type-row__count {
  color: var(--ink-2);
  font-size: var(--fs-note);
  text-align: right;
}

/* ----------------------------------------------------------- 最近任务表 */
.recent-table {
  overflow-x: auto;
}

.recent-table__head,
.recent-table__row {
  display: grid;
  grid-template-columns: 1fr 1.5fr 1fr 0.7fr;
  gap: 14px;
  min-width: 460px;
  align-items: center;
}

.recent-table__head {
  padding: 0 4px 10px;
  color: var(--ink-2);
  font-size: var(--fs-micro);
  font-weight: 600;
  letter-spacing: 0.02em;
}

.recent-table__row {
  min-height: 46px;
  padding: 8px 4px;
  border-top: 1px solid var(--hairline);
  font-size: var(--fs-note);
  cursor: pointer;
}

.recent-table__row:hover {
  background: var(--surface-2);
}

.recent-table__row strong {
  font-weight: 600;
}

/* 状态圆点已提为全局类（global.css .status-dot），此处不再私有一份 */

@media (max-width: 1080px) {
  .dash-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .action-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .disk-flex {
    flex-direction: column;
    align-items: stretch;
    gap: 20px;
  }

  .duo-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .action-grid {
    grid-template-columns: 1fr;
  }

  /* 窄屏降级：隐去「完成时间」次要列，消灭卡片内横向滚动（v22 P0-3） */
  .recent-table__head,
  .recent-table__row {
    grid-template-columns: 1fr 1.5fr 0.7fr;
    min-width: 0;
  }

  .recent-table__head > :nth-child(3),
  .recent-table__row > :nth-child(3) {
    display: none;
  }
}
</style>

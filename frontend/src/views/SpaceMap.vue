<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowUp, PieChart, Right } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import { startTree } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import JobProgress from '../components/JobProgress.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { cityScene, formatBytes, rampFill, shareOf, shareText } from '../utils/spaceViz'
import { useHandoff } from '../composables/useHandoff'

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel } = useJob('space')
const { handoffTo } = useHandoff()

const path = ref('')
/* 默认 3 层：后端默认也是 3。写成 2 的话，**只包含子目录的目录会被算成 0 字节**
   （深度之外的子孙不参与统计），于是整层里有一半条目直接从图上消失。 */
const depth = ref(3)
const root = ref(null)
const stack = ref([])

const depthOptions = [
  { label: '2 层', value: 2 },
  { label: '3 层', value: 3 },
  { label: '4 层', value: 4 },
]

const current = computed(() => stack.value[stack.value.length - 1] || root.value)

/**
 * 视图切换：卡片（默认）/ 环形。
 *
 * 刻意**没有**「层级条」这一项 —— 横向条形那一族被明确否过两次，
 * 不做成选项也就不会再翻烙饼。环形负责"一眼看构成"，卡片负责"读数值、往下钻"。
 */
const viewMode = ref('cards')
const viewOptions = [
  { label: '卡片', value: 'cards' },
  { label: '环形', value: 'ring' },
]

/** 环形的每一段：把份额换算成弧长（半径固定，用 stroke-dasharray 画）。
   数据源是 `tiles` —— 它已经做过「合并小项」，所以环上最多 8 段 + 一段「其他」。 */
const RING_RADIUS = 60
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS
const ringSegments = computed(() => {
  let offset = 0
  return tiles.value.map(tile => {
    const size = tile.item ? (tile.item.size || 0) : (tile.size || 0)
    const length = shareOf(size, total.value) * RING_CIRCUMFERENCE
    const segment = {
      key: tile.key,
      name: tile.name,
      fill: tile.fill,
      size,
      merged: Boolean(tile.merged),
      is_dir: Boolean(tile.item?.is_dir),
      item: tile.item,
      dash: `${length} ${Math.max(RING_CIRCUMFERENCE - length, 0)}`,
      offset: -offset,
      share_text: shareText(size, total.value),
    }
    offset += length
    return segment
  })
})

/** 当前层的子项，按大小降序 —— 构成条和排行都用它。
    「折叠」节点（统计深度到顶了、大小未知）也留着：标成「未展开」，点它进去看，
    而不是静默丢掉 —— 丢掉就等于「这一层没东西」，是骗人的。 */
const items = computed(() => (current.value?.children || [])
  .filter(item => (item.size || 0) > 0 || item.collapsed)
  .slice()
  .sort((a, b) => (b.size || 0) - (a.size || 0)))

const total = computed(() => items.value.reduce((sum, item) => sum + (item.size || 0), 0))

/**
 * 磁贴卡片：前 8 个（占比 ≥1%，前 3 名保底）+ 一张「其他 N 项」。
 *
 * **刻意淘汰了「横向条形 + 排行列表」那一族**（用户连否两轮）：
 * 卡片本身就是下钻入口，大号占比也比一根细线更能抓住重点。
 * 「整体构成」不再另画一条横条 —— 头部的 3D 数据城已经在干这件事了。
 */
const tiles = computed(() => {
  const all = items.value
  if (!all.length) return []
  const keep = all
    .filter((item, index) => index < 3 || shareOf(item.size, total.value) >= 0.01)
    .slice(0, 8)
  const rest = all.slice(keep.length)
  const cards = keep.map((item, index) => ({
    key: item.path || item.name,
    item,
    name: item.name,
    fill: rampFill(index).fill,
    share: item.size ? shareText(item.size, total.value) : '未展开',
    sizeText: item.size ? formatBytes(item.size) : '',
    expandable: Boolean(item.is_dir),
  }))
  if (rest.length) {
    const size = rest.reduce((sum, item) => sum + (item.size || 0), 0)
    cards.push({
      key: '__merged__',
      merged: true,
      item: null,
      name: `其他 ${rest.length} 项`,
      fill: 'var(--chart-track)',
      share: shareText(size, total.value),
      sizeText: formatBytes(size),
      expandable: false,
    })
  }
  return cards
})

const cubes = computed(() => cityScene(items.value))
const dirCount = computed(() => items.value.filter(item => item.is_dir).length)
const skipped = computed(() => root.value?.skipped || [])
const skippedTotal = computed(() => root.value?.skipped_total || 0)
const depthUsed = computed(() => root.value?.depth ?? depth.value)
const sizeText = computed(() => current.value?.size_text || formatBytes(current.value?.size || 0))
const canGoUp = computed(() => stack.value.length > 0
  || String(root.value?.path || '').replace(/[\\/]+$/, '').lastIndexOf('\\') > 1)

/** 当前正在看的那一层目录 —— 「下一步」作用在它身上，而不是某一张卡片。 */
const currentPath = computed(() => current.value?.path || root.value?.path || '')

/**
 * 下一步能去哪。这里**不新增功能**，只是把已经做好的页面串起来：
 * 以前在空间地图看到一个大目录，要自己去查重/清理页再选一次目录，
 * 同一件事做两遍。现在把目录接力过去，那边会自动填好。
 */
const nextSteps = [
  { name: 'duplicates', label: '查重' },
  { name: 'junk', label: '清理' },
  { name: 'migrate', label: '迁移' },
  { name: 'backup', label: '备份' },
  { name: 'projects', label: '项目雷达' },
]

/**
 * 面包屑：根路径的每一段 + 树内下钻的每一层。
 *
 * 两段来源不同、点法也不同：路径段是「跳到那个目录重新统计」，
 * 树内那段是「退回第 N 层」。合并成一条线，用户只需要认路径。
 */
const crumbs = computed(() => {
  const raw = String(root.value?.path || '').replace(/[\\/]+$/, '')
  let acc = ''
  const pathPart = raw.split(/[\\/]+/).filter(Boolean).map((name, index) => {
    acc = index === 0 ? `${name}\\` : `${acc}${name}\\`
    return { name: index === 0 ? `${name}\\` : name, path: acc, stackIndex: -1 }
  })
  const stackPart = stack.value.map((node, index) => ({
    name: node.name, path: node.path, stackIndex: index,
  }))
  return [...pathPart, ...stackPart]
})

function jumpTo(crumb) {
  if (crumb.stackIndex >= 0) {
    stack.value = stack.value.slice(0, crumb.stackIndex + 1)
    return
  }
  path.value = crumb.path
  stack.value = []
  start()
}

/**
 * 下钻。
 *
 * 有子节点就地进栈（瞬间切换）；**折叠节点（统计深度到顶了）改为以它为根重新统计** ——
 * 以前这里什么都不做，点进去是一片空白，是条死胡同。
 */
function drillInto(node) {
  if (!node || node.merged || !node.is_dir) return
  if (node.children?.length) {
    stack.value = [...stack.value, node]
    return
  }
  path.value = node.path
  stack.value = []
  start()
}

function goUp() {
  if (stack.value.length) {
    stack.value = stack.value.slice(0, -1)
    return
  }
  const raw = String(root.value?.path || '').replace(/[\\/]+$/, '')
  const cut = raw.lastIndexOf('\\')
  if (cut <= 1) return // 「C:\」就到顶了
  path.value = raw.slice(0, cut + 1)
  start()
}

async function start() {
  if (!path.value.trim()) return
  root.value = null
  stack.value = []
  const started = await run(() => startTree({
    path: path.value.trim(),
    depth: depth.value,
    excludes: excludes.value,
  }))
  return started
}

/* 任务出结果就画图。用 watch 而不是 setInterval——
   原来那个 300ms 轮询在卸载时没清，会一直泄漏下去。 */
watch(
  () => job.value?.status,
  status => {
    const tree = job.value?.result?.tree
    if (status === 'done' && tree) {
      root.value = tree
      stack.value = []
    }
  },
  { immediate: true },
)

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page space-map-page">

    <section class="app-card panel-card">
      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="统计路径"
        placeholder="例如：C:\\ 或 D:\\Media"
      />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">初始展开层数</span>
          <el-segmented v-model="depth" :options="depthOptions" />
        </div>
        <el-button type="primary" size="large" :loading="running" :icon="PieChart" @click="start">
          {{ running ? '正在统计' : '生成空间地图' }}
        </el-button>
      </div>

      <JobProgress v-if="job" :job="job" empty-title="先生成一次空间地图" :rollbackable="false" @cancel="cancel" />
    </section>

    <section v-if="root" class="app-card panel-card viz">
      <div class="viz-head">
        <div class="viz-head__text">
          <div class="viz-crumbs">
            <button
              v-for="(crumb, index) in crumbs"
              :key="`${crumb.path}-${index}`"
              type="button"
              class="viz-crumb"
              @click="jumpTo(crumb)"
            >
              {{ crumb.name }}
            </button>
          </div>

          <p class="viz-note">
            只统计可写目录 · 每层算到第 {{ depthUsed }} 层<template v-if="skipped.length"> · 已跳过 {{ skipped.map(item => item.name).slice(0, 5).join('、') }}<template v-if="skipped.length > 5"> 等 {{ skipped.length }} 类</template></template>
          </p>
        </div>

        <div v-if="cubes.length" class="viz-art" aria-hidden="true">
          <svg viewBox="0 0 244 186">
            <g v-for="cube in cubes" :key="cube.key">
              <polygon :points="cube.topFace" :fill="cube.fill" />
              <polygon :points="cube.leftFace" :fill="cube.fill" opacity="0.72" />
              <polygon :points="cube.rightFace" :fill="cube.fill" opacity="0.5" />
            </g>
          </svg>
        </div>
      </div>

      <div class="viz-stats">
        <div class="viz-stat">
          <span class="viz-stat__label">可清理范围</span>
          <span class="viz-stat__value">{{ sizeText }}</span>
        </div>
        <div class="viz-stat">
          <span class="viz-stat__label">计入目录</span>
          <span class="viz-stat__value">{{ dirCount }} 个</span>
        </div>
        <div class="viz-stat">
          <span class="viz-stat__label">未统计目录</span>
          <span class="viz-stat__value">{{ skippedTotal }} 个</span>
        </div>
      </div>

      <div class="viz-viewbar">
        <span class="viz-viewbar__label">显示</span>
        <el-segmented v-model="viewMode" :options="viewOptions" size="small" />
      </div>

      <div v-if="viewMode === 'ring' && ringSegments.length" class="viz-ring">
        <svg viewBox="0 0 160 160" width="176" height="176" role="img">
          <title>当前层的构成</title>
          <g transform="rotate(-90 80 80)" fill="none">
            <circle cx="80" cy="80" :r="RING_RADIUS" stroke="var(--chart-track)" :stroke-width="26" />
            <circle
              v-for="segment in ringSegments"
              :key="segment.key"
              cx="80"
              cy="80"
              :r="RING_RADIUS"
              :stroke="segment.fill"
              stroke-width="26"
              :stroke-dasharray="segment.dash"
              :stroke-dashoffset="segment.offset"
            />
          </g>
          <text x="80" y="78" text-anchor="middle" font-size="14" font-weight="600" fill="var(--ink)">
            {{ sizeText }}
          </text>
          <text x="80" y="94" text-anchor="middle" font-size="11" fill="var(--ink-3)">
            {{ items.length }} 项
          </text>
        </svg>
        <div class="viz-ring__legend">
          <button
            v-for="segment in ringSegments"
            :key="segment.key"
            type="button"
            class="viz-ring__row"
            :disabled="segment.merged || !segment.is_dir"
            @click="drillInto(segment.item)"
          >
            <span class="viz-ring__dot" :style="{ backgroundColor: segment.fill }"></span>
            <span class="viz-ring__name" :title="segment.name">{{ segment.name }}</span>
            <span class="viz-ring__size">{{ formatBytes(segment.size) }}</span>
            <span class="viz-ring__pct">{{ segment.share_text }}</span>
          </button>
        </div>
      </div>

      <div v-if="viewMode === 'cards' && tiles.length" class="viz-tiles">
        <button
          v-for="tile in tiles"
          :key="tile.key"
          type="button"
          class="viz-tile"
          :class="{ 'is-merged': tile.merged }"
          @click="drillInto(tile.item)"
        >
          <span class="viz-tile__head">
            <span class="viz-tile__dot" :style="{ backgroundColor: tile.fill }"></span>
            <span class="viz-tile__name" :title="tile.name">{{ tile.name }}</span>
            <el-icon v-if="tile.expandable" class="viz-tile__go" :size="12"><Right /></el-icon>
          </span>
          <span class="viz-tile__foot">
            <span class="viz-tile__pct">{{ tile.share }}</span>
            <span class="viz-tile__size">{{ tile.sizeText }}</span>
          </span>
        </button>
      </div>

      <!-- 空态只在这种情况出现：**这一层真的什么都没有**。
           写成 `v-else` 的话，切到环形视图时（卡片那块 v-if 为假）它会跟着冒出来 —— 明明有内容却报空。 -->
      <EmptyState v-if="!items.length" :icon="PieChart" title="这一层没有可显示的内容" />

      <div class="viz-foot">
        <p class="viz-foot__note">卡片按占比从大到小排列；点任意一张继续往里看</p>
        <div class="viz-next">
          <span class="viz-next__label">对这个目录：</span>
          <button
            v-for="step in nextSteps"
            :key="step.name"
            type="button"
            class="viz-next__btn"
            :disabled="!currentPath"
            @click="handoffTo(step.name, currentPath, 'space')"
          >
            {{ step.label }}
          </button>
        </div>
        <el-button v-if="canGoUp" size="small" :icon="ArrowUp" @click="goUp">上一层</el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 与其它表单页一致：不再另设宽度上限，统一由 --content-width 收口。 */
.space-map-page {
  padding-bottom: 12px;
}

.space-map-page > .app-card:first-child {
  min-height: 360px;
}

/* 穿透子组件必须 :deep()，否则 scoped 的 data-attribute 加不到
   JobProgress 内部元素，整条规则静默失效（v22 审查 P0-1） */
.space-map-page > .app-card:first-child :deep(.job-progress--empty) {
  min-height: 78px;
  display: grid;
  place-items: center;
}

/* ---------------------------------------------------------------
   结果区。这里是**唯一允许用渐变 / 阴影 / 发光**的地方
   （用户 2026-10-10 明确授权：数据可视化例外），
   但颜色一律走 global.css 的 --chart-* token，组件里不写字面量。
   --------------------------------------------------------------- */
.viz {
  display: grid;
  gap: 16px;
}

/* 头部只放「面包屑 + 口径 + 3D 数据城」。
   指标块**必须放在头部之外**：放在里面的话它的三列宽度会被 3D 城挤窄，
   和下面卡片的三列对不齐 —— 竖线错开看着就是「不协和」。 */
.viz-head {
  display: flex;
  gap: 24px;
  align-items: center;
}

.viz-head__text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 8px;
}

.viz-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
  font-size: var(--fs-card-title);
}

.viz-crumb {
  max-width: 200px;
  overflow: hidden;
  padding: 4px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-2);
  font: inherit;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.viz-crumb:last-child {
  color: var(--ink);
  font-weight: 600;
}

.viz-crumb:hover {
  background: var(--inset);
}

/* 不要卡 max-width：一卡住，右边就空出一大片，
   和右上角的 3D 数据城之间形成断层。让它铺满左栏。 */
.viz-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.viz-stat {
  display: grid;
  gap: 1px;
  padding: 9px 12px;
  border-radius: 10px;
  background: var(--inset);
}

.viz-stat__label {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.viz-stat__value {
  color: var(--ink);
  font-size: 17px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.viz-note {
  margin: 0;
  color: var(--ink-3);
  font-size: var(--fs-note);
  line-height: 1.6;
}

/* 头部「数据城」：三个面用同一色相的三档透明度做体积感，再给一层同色柔光 */
.viz-art {
  flex: 0 0 168px;
  filter: drop-shadow(0 4px 10px var(--chart-glow));
}

.viz-art svg {
  display: block;
  overflow: visible;
}

/* 磁贴卡片网格。
   卡片**本身就是下钻入口** —— 这是选它而不是环形/方块矩阵的原因：
   那两种都得再配一份可点的图例。 */
.viz-viewbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.viz-viewbar__label {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

/* 环形：只负责「一眼看构成」，读数值和往下钻交给右边那列 */
.viz-ring {
  display: flex;
  gap: 22px;
  align-items: center;
  padding: 4px 0;
}

.viz-ring__legend {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

.viz-ring__row {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto 46px;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--fs-note);
  text-align: left;
  cursor: pointer;
}

.viz-ring__row:disabled {
  cursor: default;
  color: var(--ink-2);
}

.viz-ring__row:not(:disabled):hover {
  background: var(--inset);
}

.viz-ring__dot {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}

.viz-ring__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.viz-ring__size,
.viz-ring__pct {
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}

.viz-tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 1080px) {
  .viz-tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.viz-tile {
  display: grid;
  gap: 5px;
  padding: 12px 14px;
  border: 0.5px solid var(--hairline);
  border-radius: 12px;
  background: transparent;
  color: var(--ink);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--duration-quick) var(--ease),
    background var(--duration-quick) var(--ease),
    box-shadow var(--duration-quick) var(--ease),
    transform var(--duration-quick) var(--ease);
}

/* 阴影 / 位移只有图表区允许（数据可视化例外） */
.viz-tile:hover {
  border-color: var(--hairline-strong);
  background: var(--inset);
  box-shadow: var(--chart-shadow);
  transform: translateY(-1px);
}

.viz-tile.is-merged {
  border-style: dashed;
  cursor: default;
}

.viz-tile.is-merged:hover {
  box-shadow: none;
  transform: none;
}

.viz-tile__head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

/* 占比和大小同一行：占比靠左当主角、大小靠右收尾 ——
   上下堆三行的话，卡片右半边会整块空着。 */
.viz-tile__foot {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.viz-tile__dot {
  flex: 0 0 9px;
  width: 9px;
  height: 9px;
  border-radius: 3px;
}

.viz-tile__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--fs-note);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.viz-tile__go {
  flex: 0 0 auto;
  color: var(--ink-3);
  opacity: 0;
  transition: opacity var(--duration-quick) var(--ease);
}

.viz-tile:hover .viz-tile__go {
  opacity: 1;
}

.viz-tile__pct {
  color: var(--ink);
  font-size: 26px;
  font-weight: 600;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

.viz-tile__size {
  color: var(--ink-3);
  font-size: var(--fs-note);
  font-variant-numeric: tabular-nums;
}

.viz-tile.is-merged .viz-tile__pct,
.viz-tile.is-merged .viz-tile__name {
  color: var(--ink-2);
  font-weight: 400;
}

.viz-foot {
  display: flex;
  align-items: center;
  gap: 10px 12px;
  flex-wrap: wrap;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
  color: var(--ink-3);
  font-size: var(--fs-note);
}

/* 说明独占一行，下面一行留给「下一步」和「上一层」 */
.viz-foot__note {
  flex: 1 1 100%;
  margin: 0;
}

/* 「下一步」：把当前目录接力到已经做好的那些页面去 */
.viz-next {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
}

.viz-next__btn {
  padding: 4px 11px;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--ink-2);
  font: inherit;
  font-size: var(--fs-note);
  cursor: pointer;
  transition: border-color var(--duration-quick) var(--ease), color var(--duration-quick) var(--ease);
}

.viz-next__btn:hover:not(:disabled) {
  border-color: var(--hairline-strong);
  color: var(--ink);
}

.viz-next__btn:disabled {
  opacity: 0.45;
  cursor: default;
}

@media (prefers-reduced-motion: reduce) {
  .viz-tile {
    transition: none;
  }
  .viz-tile:hover {
    transform: none;
  }
}
</style>

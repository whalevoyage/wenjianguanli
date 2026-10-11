<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Aim,
  Clock,
  Collection,
  CopyDocument,
  Delete,
  DeleteFilled,
  Files,
  FolderOpened,
  HomeFilled,
  Monitor,
  Moon,
  Operation,
  PieChart,
  Search,
  Sort,
  Sunny,
} from '@element-plus/icons-vue'
import { healthCheck } from './api/filepilot'
import AppSidebar from './components/AppSidebar.vue'
import AgentGlyph from './components/AgentGlyph.vue'
import { useTheme } from './composables/useTheme'

// 与 global.css 的 --duration-slow / --ease-slide 对齐；View Transition 参数只能在 JS 中定义。
const THEME_TRANSITION_DURATION = 480
const THEME_TRANSITION_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)'

const route = useRoute()
/* Agent 页是整块工作台（任务栏 + 对话区），要铺满内容区，不能套外层的内边距与宽度上限 */
const isFlushPage = computed(() => route.name === 'agent')
const { mode: themeMode, label: themeLabel, toggleMode } = useTheme()

const isMobile = ref(window.innerWidth < 960)
const navOpen = ref(false)
const sidebarCollapsed = ref(window.innerWidth >= 960 && window.innerWidth < 1280)
const backendOnline = ref(false)
const backendCheckedAt = ref('')

const themeIcon = computed(
  () => ({ auto: Monitor, light: Sunny, dark: Moon })[themeMode.value] || Monitor,
)

/**
 * 主题切换：优先用 View Transition 做一次从按钮位置扩散的圆形揭示（beUI 的手法）。
 * 不支持 / 用户偏好减少动效时，静默退化为直接切换。
 */
function toggleTheme(event) {
  const prefersReduced =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  if (typeof document.startViewTransition !== 'function' || prefersReduced) {
    toggleMode()
    return
  }

  const x = event?.clientX ?? window.innerWidth - 80
  const y = event?.clientY ?? 48
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y))

  try {
    const transition = document.startViewTransition(() => toggleMode())
    transition.ready
      .then(() => {
        document.documentElement.animate(
          {
            clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`],
          },
          {
            duration: THEME_TRANSITION_DURATION,
            easing: THEME_TRANSITION_EASING,
            pseudoElement: '::view-transition-new(root)',
          },
        )
      })
      .catch(() => {})
  } catch {
    toggleMode()
  }
}

const navGroups = [
  {
    label: '总览',
    items: [
      { path: '/', label: '首页', hint: '磁盘 · 任务 · 隔离区', icon: HomeFilled },
      { path: '/agent', label: 'Atlas 智能助手', hint: '用大白话下命令', icon: AgentGlyph, badge: 'AI' },
    ],
  },
  {
    label: '分析',
    items: [
      { path: '/space', label: '空间地图', hint: '看清谁在占空间', icon: PieChart },
      { path: '/virtual', label: '整理预览', hint: '只读 · 不动文件', icon: Files },
      { path: '/projects', label: '项目雷达', hint: '体积 · 活跃 · 僵尸', icon: Aim },
      { path: '/scan', label: '扫描分析', hint: '目录 · 大文件', icon: Search },
      { path: '/duplicates', label: '重复文件', hint: 'SHA-256 分桶', icon: CopyDocument },
      { path: '/junk', label: '垃圾文件', hint: '三级安全分级', icon: Delete },
    ],
  },
  {
    label: '执行',
    items: [
      { path: '/migrate', label: '跨盘迁移', hint: '复制 · 校验 · 删源', icon: Sort },
      { path: '/backup', label: '项目备份', hint: 'ZIP 快照', icon: FolderOpened },
      { path: '/jobs', label: '任务中心', hint: 'SSE 进度 · 回滚', icon: Operation },
    ],
  },
  {
    label: '记录',
    items: [
      { path: '/trash', label: '回收站', hint: '隔离区 · 还原', icon: DeleteFilled },
      { path: '/logs', label: '操作日志', hint: '审计流水', icon: Collection },
    ],
  },
]

function syncLayout() {
  const width = window.innerWidth
  isMobile.value = width < 960

  if (!isMobile.value) {
    navOpen.value = false
    sidebarCollapsed.value = width < 1280
  }
}

function toggleSidebar() {
  if (isMobile.value) {
    navOpen.value = !navOpen.value
    return
  }
  sidebarCollapsed.value = !sidebarCollapsed.value
}

async function checkBackend() {
  try {
    const result = await healthCheck()
    backendOnline.value = result.status === 'ok'
  } catch {
    backendOnline.value = false
  } finally {
    backendCheckedAt.value = new Intl.DateTimeFormat('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date())
  }
}

let timer = null

onMounted(() => {
  syncLayout()
  window.addEventListener('resize', syncLayout)
  checkBackend()
  timer = setInterval(checkBackend, 30000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncLayout)
  if (timer) clearInterval(timer)
})

watch(() => route.path, () => {
  navOpen.value = false
})

const title = computed(() => route.meta.title || '本地文件工作台')
const today = computed(() =>
  new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date()),
)
</script>

<template>
  <!-- 光晕层：全页的视觉主体。放在 #app 内、shell 之下，
       所以不经过 body 上那层纱罩，可以用满强度。 -->
  <div class="aurora" aria-hidden="true"></div>

  <div class="workspace-shell" :class="{ 'is-collapsed': sidebarCollapsed && !isMobile }">
    <aside v-if="!isMobile" class="workspace-sidebar">
      <AppSidebar
        :groups="navGroups"
        :active-path="route.path"
        :collapsed="sidebarCollapsed"
        :allow-collapse="true"
        :theme-icon="themeIcon"
        :theme-label="themeLabel"
        @toggle="toggleSidebar"
        @cycle-theme="toggleTheme"
      />
    </aside>

    <el-drawer
      v-model="navOpen"
      direction="ltr"
      size="292px"
      :with-header="false"
      class="mobile-drawer"
      append-to-body
    >
      <AppSidebar
        :groups="navGroups"
        :active-path="route.path"
        :collapsed="false"
        :theme-icon="themeIcon"
        :theme-label="themeLabel"
        @cycle-theme="toggleTheme"
        @navigate="navOpen = false"
      />
    </el-drawer>

    <section class="workspace-main">
      <!-- 桌面端没有顶栏：品牌名在侧栏、页面名在 H1、连接状态在侧栏底部，
           三样都在别处有了，留着只是多一层套壳。
           移动端没有常驻侧栏，这条 bar 仍然需要承载汉堡按钮与外观切换。 -->
      <header v-if="isMobile" class="workspace-topbar">
        <div class="workspace-topbar__left">
          <button type="button" class="nav-toggle" aria-label="打开导航" @click="toggleSidebar">
            <el-icon :size="16"><Operation /></el-icon>
          </button>

          <nav class="crumbs" aria-label="当前位置">
            <span class="crumbs__root">Atlas</span>
            <span class="crumbs__sep" aria-hidden="true">/</span>
            <strong class="crumbs__current">{{ title }}</strong>
          </nav>
        </div>

        <div class="workspace-topbar__right">
          <span class="chrome-chip chrome-chip--quiet">
            <el-icon :size="13"><Clock /></el-icon>
            <span>{{ today }}</span>
          </span>

          <span class="chrome-chip" :class="{ 'is-online': backendOnline }">
            <span class="chrome-chip__dot" aria-hidden="true"></span>
            <span>{{ backendOnline ? '后端已连接' : '等待后端' }}</span>
            <span v-if="backendCheckedAt" class="chrome-chip__muted">{{ backendCheckedAt }}</span>
          </span>

          <el-tooltip :content="`主题：${themeLabel} · 点击切换`" placement="bottom" :show-after="260">
            <button type="button" class="theme-toggle" aria-label="切换主题" @click="toggleTheme">
              <Transition name="icon-swap" mode="out-in">
                <el-icon :key="themeMode" :size="15">
                  <component :is="themeIcon" />
                </el-icon>
              </Transition>
              <span class="theme-toggle__label">{{ themeLabel }}</span>
            </button>
          </el-tooltip>
        </div>
      </header>

      <main class="workspace-content" :class="{ 'is-flush': isFlushPage }">
        <router-view v-slot="{ Component }">
          <transition name="page">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </section>
  </div>
</template>

<style scoped>
.aurora {
  position: fixed;
  inset: -30%;
  z-index: 0;
  pointer-events: none;
  /* 两团同色大光斑：一大一小、一明一暗，形成斜向的光的走向。
     刻意不做点阵、不做彩色、不做发光描边——那些都是「极客语汇」。 */
  background-image:
    radial-gradient(62% 48% at 20% 16%, var(--aur-a), transparent 70%),
    radial-gradient(56% 46% at 84% 80%, var(--aur-b), transparent 72%);
  animation: aurora-drift 72s var(--ease-linear) infinite alternate;
  will-change: transform;
}

html:not([data-theme='dark']) .aurora {
  animation: none;
}

.workspace-shell {
  position: relative;
  z-index: 1;
  min-height: 100vh;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
}

@media (prefers-reduced-motion: reduce) {
  .aurora {
    animation: none;
  }
}

/* 贴边通高：没有外边距、没有圆角、没有投影 */
.workspace-sidebar {
  position: sticky;
  top: 0;
  width: var(--sidebar-width);
  height: 100vh;
  transition: width var(--dur) var(--ease);
}

.is-collapsed .workspace-sidebar {
  width: var(--sidebar-collapsed);
}

.workspace-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

/* ---------- 顶栏：通栏粘性条，靠一条发丝线分界（不再是一枚悬浮药丸） ---------- */
.workspace-topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 0 24px;
  background: var(--glass);
  border-bottom: 1px solid var(--hairline);
  backdrop-filter: var(--blur);
  -webkit-backdrop-filter: var(--blur);
  transition: background-color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.workspace-topbar__left {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.nav-toggle {
  width: 34px;
  height: 34px;
  flex: none;
  display: none;
  place-items: center;
  color: var(--ink);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease);
}

.nav-toggle:active {
  transform: scale(0.95);
}

.crumbs {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--fs-body);
}

.crumbs__root {
  color: var(--ink-3);
  white-space: nowrap;
}

.crumbs__sep {
  color: var(--ink-4);
}

.crumbs__current {
  color: var(--ink);
  font-weight: 500;
  letter-spacing: -0.01em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-topbar__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.chrome-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 11px;
  color: var(--ink-2);
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  font-size: var(--fs-note);
  white-space: nowrap;
  transition: background-color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.chrome-chip--quiet {
  background: transparent;
  border-color: transparent;
  color: var(--ink-3);
}

.chrome-chip__dot {
  width: 7px;
  height: 7px;
  flex: none;
  border-radius: 50%;
  background: var(--warning);
  transition: background-color var(--dur) var(--ease);
}

.chrome-chip.is-online .chrome-chip__dot {
  background: var(--success);
  box-shadow: 0 0 0 3px var(--success-soft);
}

.chrome-chip__muted {
  color: var(--ink-4);
  font-variant-numeric: tabular-nums;
}

.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 12px;
  color: var(--ink);
  background: var(--surface-2);
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: var(--fs-note);
  font-weight: 500;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease), transform var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}

.theme-toggle:hover {
  background: var(--inset);
}

.theme-toggle:active {
  transform: scale(0.96);
}

.workspace-content {
  position: relative;              /* 页面过渡时，离场页以它为绝对定位基准 */
  width: min(100%, var(--content-width));
  margin: 0 auto;
  /* 桌面端没有顶栏，直接以页头开场 */
  padding: 28px 32px 48px;
}

/* Agent 页：铺满内容区（否则白底主区会被这层内边距框成一张「白卡」，四周露灰底） */
.workspace-content.is-flush {
  width: 100%;
  padding: 0;
}

/* ---------- 移动端抽屉：浮层，允许模糊 ---------- */
:deep(.mobile-drawer .el-drawer) {
  padding: 0;
  background: transparent;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}

:deep(.mobile-drawer .el-drawer__body) {
  padding: 0;
}

/* ---------- 页面过渡：旧页在上层淡出，新页首帧即不透明 ----------
   两版都试过，把结论写死在这里，别再回头改：
   ① mode="out-in" —— 旧页先彻底消失、新页再从 opacity:0 淡入，中间有空白帧，白屏。
   ② 两页同时渐变的交叉淡入 —— 旧页 150ms 淡出、新页 250ms 淡入，新页在头 250ms 里
      始终不是 100% 不透明；而内容区自己没有底色，透出来的是 body 的 --bg（浅色 #f4f5f7 /
      深色 #1c1c1e），于是就是「白一下 / 黑一下」。
   正解：进场不做任何动画（下面的 enter 类刻意不写规则），新页第一帧就 100% 不透明；
   离场页脱离文档流压在上层（z-index:2）淡出。任何一帧都有不透明的页面垫底，
   结构上不可能再露出应用底色，而视觉上「旧页淡去、新页显形」的过渡感仍在。 */
.page-leave-active {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 2;
  transition: opacity var(--duration-quick) var(--ease);
}

.page-leave-to {
  opacity: 0;
}

/* ---------- 主题图标：同槽位图标切换（旋转 + 模糊交叉） ---------- */
.icon-swap-enter-active,
.icon-swap-leave-active {
  transition: opacity var(--duration-quick) var(--ease),
    transform var(--duration-quick) var(--ease-spring), filter var(--duration-quick) var(--ease);
}

.icon-swap-enter-from {
  opacity: 0;
  transform: rotate(-40deg) scale(0.7);
  filter: blur(var(--blur-fx-micro));
}

.icon-swap-leave-to {
  opacity: 0;
  transform: rotate(40deg) scale(0.7);
  filter: blur(var(--blur-fx-micro));
}

@media (max-width: 959px) {
  .workspace-shell {
    display: block;
  }

  .workspace-topbar {
    height: auto;
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    padding: 10px 16px;
  }

  .nav-toggle {
    display: grid;
  }

  .workspace-topbar__right {
    justify-content: flex-start;
  }

  .workspace-content {
    padding: 8px 16px 40px;
  }
}

@media (max-width: 560px) {
  .chrome-chip--quiet,
  .theme-toggle__label {
    display: none;
  }
}
</style>

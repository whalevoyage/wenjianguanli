<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  Clock,
  Collection,
  CopyDocument,
  Document,
  FolderOpened,
  HomeFilled,
  Operation,
  Search,
  Sort,
} from '@element-plus/icons-vue'
import { healthCheck } from './api/filepilot'
import AppSidebar from './components/AppSidebar.vue'

const route = useRoute()

const isMobile = ref(window.innerWidth < 960)
const navOpen = ref(false)
const sidebarCollapsed = ref(window.innerWidth >= 960 && window.innerWidth < 1280)
const backendOnline = ref(false)
const backendCheckedAt = ref('')

const navItems = [
  { path: '/', label: '工作台', hint: '总览与最近动作', icon: HomeFilled },
  { path: '/scan', label: '扫描分析', hint: '扫描与大文件', icon: Search },
  { path: '/duplicates', label: '重复文件', hint: '查重与处理', icon: CopyDocument },
  { path: '/organizer', label: '文件整理', hint: '预览后执行', icon: Sort },
  { path: '/backup', label: '项目备份', hint: '生成 ZIP 快照', icon: FolderOpened },
  { path: '/reports', label: '报告导出', hint: '输出文本结果', icon: Document },
  { path: '/logs', label: '操作日志', hint: '回看历史记录', icon: Collection },
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

onMounted(() => {
  syncLayout()
  window.addEventListener('resize', syncLayout)
  checkBackend()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncLayout)
})

watch(() => route.path, () => {
  navOpen.value = false
})

const title = computed(() => route.meta.title || '本地文件工作台')
const description = computed(() => route.meta.description || '扫描、整理、备份、导出和日志都收在一个浏览器工作台里。')
const today = computed(() => new Intl.DateTimeFormat('zh-CN', {
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date()))
</script>

<template>
  <div class="workspace-shell" :class="{ 'is-collapsed': sidebarCollapsed && !isMobile }">
    <aside v-if="!isMobile" class="workspace-sidebar">
      <AppSidebar
        :items="navItems"
        :active-path="route.path"
        :collapsed="sidebarCollapsed"
        :backend-online="backendOnline"
        :allow-collapse="true"
        @toggle="toggleSidebar"
      />
    </aside>

    <el-drawer
      v-model="navOpen"
      direction="ltr"
      size="300px"
      :with-header="false"
      class="mobile-drawer"
      append-to-body
    >
      <AppSidebar
        :items="navItems"
        :active-path="route.path"
        :collapsed="false"
        :backend-online="backendOnline"
        @navigate="navOpen = false"
      />
    </el-drawer>

    <section class="workspace-main">
      <header class="workspace-topbar">
        <div class="workspace-topbar__left">
          <button type="button" class="nav-toggle" aria-label="打开导航" @click="toggleSidebar">
            <el-icon><Operation /></el-icon>
          </button>

          <div class="workspace-copy">
            <span class="workspace-copy__label">FilePilot Lite</span>
            <strong>{{ title }}</strong>
            <p>{{ description }}</p>
          </div>
        </div>

        <div class="workspace-topbar__right">
          <div class="workspace-badge">
            <el-icon><Clock /></el-icon>
            <span>{{ today }}</span>
          </div>
          <div class="workspace-badge">
            <span class="badge-dot" :class="{ 'is-online': backendOnline }"></span>
            <span>{{ backendOnline ? '后端已连接' : '等待后端' }}{{ backendCheckedAt ? ` · ${backendCheckedAt}` : '' }}</span>
          </div>
        </div>
      </header>

      <main class="workspace-content">
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </section>
  </div>
</template>

<style scoped>
.workspace-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 18px;
  padding: 18px;
}

.workspace-sidebar {
  position: sticky;
  top: 18px;
  width: var(--sidebar-width);
  height: calc(100vh - 36px);
  transition: width 0.2s ease;
}

.workspace-main {
  min-width: 0;
}

.is-collapsed .workspace-sidebar {
  width: var(--sidebar-collapsed);
}

.workspace-topbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 2px 18px;
  border-bottom: 1px solid var(--border);
}

.workspace-topbar__left {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.nav-toggle {
  width: 40px;
  height: 40px;
  display: none;
  place-items: center;
  color: var(--text-primary);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  cursor: pointer;
}

.workspace-copy {
  min-width: 0;
}

.workspace-copy__label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.workspace-copy strong {
  display: block;
  color: var(--text-primary);
  font-size: 26px;
  line-height: 1.15;
  font-weight: 700;
  letter-spacing: -0.04em;
}

.workspace-copy p {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
}

.workspace-topbar__right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.workspace-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 14px;
  color: var(--text-secondary);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}

.badge-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #d79a46;
}

.badge-dot.is-online {
  background: #22a06b;
}

.workspace-content {
  width: min(100%, var(--content-width));
  margin: 0 auto;
  padding: 24px 0 32px;
}

:deep(.mobile-drawer) {
  background: transparent;
}

:deep(.mobile-drawer .el-drawer) {
  padding: 16px;
  background: transparent;
  box-shadow: none;
}

:deep(.mobile-drawer .el-drawer__body) {
  padding: 0;
}

.page-enter-active,
.page-leave-active {
  transition: opacity 0.16s ease;
}

.page-enter-from,
.page-leave-to {
  opacity: 0;
}

@media (max-width: 959px) {
  .workspace-shell {
    display: block;
    padding: 14px;
  }

  .workspace-topbar {
    flex-direction: column;
    align-items: stretch;
    padding-top: 0;
  }

  .nav-toggle {
    display: grid;
  }

  .workspace-topbar__left,
  .workspace-topbar__right {
    justify-content: space-between;
  }

  .workspace-content {
    padding-top: 18px;
  }
}

@media (max-width: 640px) {
  .workspace-copy strong {
    font-size: 22px;
  }

  .workspace-topbar__right {
    flex-direction: column;
    align-items: stretch;
  }

  .workspace-badge {
    justify-content: center;
  }
}
</style>

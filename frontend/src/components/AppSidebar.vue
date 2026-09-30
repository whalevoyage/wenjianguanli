<script setup>
import { Files, Operation } from '@element-plus/icons-vue'

defineProps({
  activePath: { type: String, required: true },
  backendOnline: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  items: { type: Array, required: true },
  allowCollapse: { type: Boolean, default: false },
})

defineEmits(['navigate', 'toggle'])
</script>

<template>
  <div class="sidebar-shell" :class="{ 'is-collapsed': collapsed }">
    <div class="sidebar-brand">
      <div class="sidebar-brand__badge">
        <el-icon :size="18"><Files /></el-icon>
      </div>

      <div v-if="!collapsed" class="sidebar-brand__copy">
        <strong>FilePilot Lite</strong>
        <span>本地文件工作台</span>
      </div>

      <button
        v-if="allowCollapse"
        type="button"
        class="sidebar-brand__toggle"
        aria-label="切换导航栏宽度"
        @click="$emit('toggle')"
      >
        <el-icon><Operation /></el-icon>
      </button>
    </div>

    <div class="sidebar-section">
      <div v-if="!collapsed" class="sidebar-section__title">功能导航</div>

      <RouterLink
        v-for="item in items"
        :key="item.path"
        :to="item.path"
        class="nav-link"
        :class="{ 'is-active': activePath === item.path }"
        @click="$emit('navigate')"
      >
        <span class="nav-link__icon">
          <el-icon :size="18"><component :is="item.icon" /></el-icon>
        </span>

        <span v-if="!collapsed" class="nav-link__copy">
          <strong>{{ item.label }}</strong>
          <small>{{ item.hint }}</small>
        </span>
      </RouterLink>
    </div>

    <div class="sidebar-status" :class="{ 'is-online': backendOnline }">
      <span class="sidebar-status__dot"></span>

      <div v-if="!collapsed">
        <strong>{{ backendOnline ? '本地服务正常' : '等待本地服务' }}</strong>
        <small>{{ backendOnline ? '扫描、整理、备份与 AI 分析均可使用' : '请先启动后端服务' }}</small>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sidebar-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 20px;
  box-shadow: var(--shadow-sm);
}

.sidebar-shell.is-collapsed {
  padding-inline: 10px;
}

.sidebar-brand {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  margin-bottom: 18px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.sidebar-brand__badge {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--primary);
  border-radius: 12px;
}

.sidebar-brand__copy strong,
.sidebar-brand__copy span {
  display: block;
}

.sidebar-brand__copy strong {
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 700;
}

.sidebar-brand__copy span {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 11px;
}

.sidebar-brand__toggle {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  color: var(--text-secondary);
  background: #f7f9fb;
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
}

.sidebar-section {
  display: grid;
  gap: 6px;
}

.sidebar-section__title {
  margin: 2px 0 6px;
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.nav-link {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  min-height: 52px;
  padding: 6px;
  border-radius: 14px;
  transition: background 0.18s ease, border-color 0.18s ease;
}

.is-collapsed .nav-link {
  grid-template-columns: 1fr;
  justify-items: center;
}

.nav-link:hover {
  background: #f7f9fb;
}

.nav-link.is-active {
  background: #eef5ff;
}

.nav-link__icon {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  color: var(--text-secondary);
  background: #f7f9fb;
  border: 1px solid var(--border);
  border-radius: 12px;
}

.nav-link.is-active .nav-link__icon {
  color: var(--primary);
  background: #fff;
}

.nav-link__copy strong,
.nav-link__copy small {
  display: block;
}

.nav-link__copy strong {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.nav-link__copy small {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 11px;
}

.sidebar-status {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: auto;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 14px;
}

.sidebar-status__dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #d79a46;
}

.is-online .sidebar-status__dot {
  background: #22a06b;
}

.sidebar-status strong,
.sidebar-status small {
  display: block;
}

.sidebar-status strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 700;
}

.sidebar-status small {
  margin-top: 4px;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.55;
}
</style>

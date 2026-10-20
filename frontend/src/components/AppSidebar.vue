<script setup>
import { DArrowLeft, DArrowRight } from '@element-plus/icons-vue'

/**
 * 尺寸基线对齐参考图：
 *   导航项单行、行高 52px、字号 15.5px、图标 20px、左内边距 16px
 *   —— 之前做成两行 13.5px 小字是误判：单行并不小气，
 *      小气的是「小字号」。参考图就是单行，但字大、行高宽。
 * 副标题不再占视觉空间，改为原生 tooltip（悬停可见，信息不丢）。
 */
defineProps({
  activePath: { type: String, required: true },
  collapsed: { type: Boolean, default: false },
  groups: { type: Array, required: true },
  allowCollapse: { type: Boolean, default: false },
  themeIcon: { type: [Object, Function], default: null },
  themeLabel: { type: String, default: '跟随系统' },
  // 版本号：后端 /api/health 给，收起时与未拿到时不显示
  version: { type: String, default: '' },
})

defineEmits(['navigate', 'toggle', 'cycle-theme'])
</script>

<template>
  <div class="sidebar-shell" :class="{ 'is-collapsed': collapsed }">
    <div class="sidebar-brand">
      <div class="sidebar-brand__badge">
        <!-- Atlas 山脊：两道叠峰（前峰纯白 / 后峰 55% 白），
             单色相、无星芒。Atlas = 统揽全盘，山脊 = 登高望远。 -->
        <svg viewBox="0 0 40 40" width="22" height="22" aria-hidden="true">
          <path d="M 17.5 29 L 26 16.5 L 34.5 29 Z" fill="currentColor" opacity="0.55" />
          <path d="M 5.5 29 L 16 12 L 26.5 29 Z" fill="currentColor" />
        </svg>
      </div>

      <div v-if="!collapsed" class="sidebar-brand__copy">
        <strong>Atlas</strong>
      </div>
    </div>

    <nav class="sidebar-nav">
      <div v-for="group in groups" :key="group.label" class="sidebar-group">
        <div v-if="collapsed" class="sidebar-group__divider"></div>

        <RouterLink
          v-for="item in group.items"
          :key="item.path"
          :to="item.path"
          class="nav-link"
          :class="{ 'is-active': activePath === item.path }"
          :title="collapsed ? item.label : item.hint"
          @click="$emit('navigate')"
        >
          <span class="nav-link__icon">
            <el-icon :size="20"><component :is="item.icon" /></el-icon>
          </span>

          <span v-if="!collapsed" class="nav-link__label">{{ item.label }}</span>

          <span v-if="!collapsed && item.badge" class="nav-link__badge">{{ item.badge }}</span>
        </RouterLink>
      </div>
    </nav>

    <div class="sidebar-foot">
      <!-- 版本号：平时不打扰，报问题时要能一眼看到并复制 -->
      <div v-if="!collapsed && version" class="foot-version">v{{ version }}</div>
      <button
        type="button"
        class="foot-row"
        :title="`外观：${themeLabel} · 点击切换明暗`"
        @click="$emit('cycle-theme', $event)"
      >
        <span class="foot-row__icon">
          <el-icon :size="20"><component :is="themeIcon" /></el-icon>
        </span>
        <span v-if="!collapsed" class="foot-row__label">外观</span>
      </button>

      <button v-if="allowCollapse" type="button" class="foot-row" title="收起导航栏" @click="$emit('toggle')">
        <span class="foot-row__icon">
          <el-icon :size="20"><component :is="collapsed ? DArrowRight : DArrowLeft" /></el-icon>
        </span>
        <span v-if="!collapsed" class="foot-row__label">收起</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
/* 侧栏是贴边通高的 chrome 面板：没有外边距、圆角、投影，
   只靠一条发丝线与内容区分开。面板不透明（对齐参考图的纯白侧栏）。 */
.sidebar-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 18px 12px 12px;
  background: var(--surface);
  border-right: 1px solid var(--hairline);
  transition: background-color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

/* ------------------------------------------------------------------ 品牌区 */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 6px 18px;
  border-bottom: 1px solid var(--hairline);
}

.sidebar-brand__badge {
  width: 40px;
  height: 40px;
  flex: none;
  display: grid;
  place-items: center;
  color: var(--surface);
  background: var(--grad-brand);
  border-radius: 12px;
  /* 上沿一道内高光 + 中性投影：让方章有一点体积感，不像一块贴纸 */
  box-shadow: var(--sh-1);
}

.sidebar-brand__copy {
  min-width: 0;
  flex: 1;
}

/* 品牌名是这一栏唯一的「大字」，按参考图放到 19px */
.sidebar-brand__copy strong {
  display: block;
  color: var(--ink);
  font-size: 19px;
  font-weight: 600;
  letter-spacing: -0.024em;
}

/* -------------------------------------------------------------------- 导航 */
.sidebar-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* 底部留出与渐隐等高的内边距：滚到底时最后一项能完整露出来，不会被渐隐吃掉 */
  padding: 6px 0 20px;
  /* 底部渐隐改用 mask（渐到完全透明）。原来的覆盖式渐变只有 0.95 不透明，
     被裁掉的「半行」导航项会露出一道短横；mask 才是真的看不见。
     black 在这里只是遮罩的 alpha 通道，不是颜色。 */
  mask-image: linear-gradient(to bottom, black calc(100% - 28px), transparent);
  -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 28px), transparent);
  /* 彻底隐藏滚动条：导航底部那条 20px 渐隐已经承担了「下面还有内容」的提示，
     再常驻一条灰条只会破坏面板的整洁。滚轮 / 触控板 / 键盘照常可用。 */
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.sidebar-nav::-webkit-scrollbar {
  display: none;
  width: 0;
  height: 0;
}

/* 分组标题已按用户要求去掉。
   现在靠「组与组之间 10px 的额外间距」保留一点节奏，
   信息架构不靠标签，靠间距。 */
.sidebar-group + .sidebar-group {
  margin-top: 10px;
}

.sidebar-group__divider {
  height: 1px;
  margin: 8px 6px;
  background: var(--hairline);
}

/* 单行导航项：行高 52px、字号 15.5px、图标 20px。
   尺寸撑起来之后，单行反而比两行小字更大气。 */
.nav-link {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  height: 52px;
  padding: 0 14px;
  border-radius: 12px;
  transition: background-color var(--duration-quick) var(--ease),
    color var(--duration-quick) var(--ease);
}

.nav-link + .nav-link {
  margin-top: 2px;
}

.nav-link:hover {
  background: var(--surface-2);
}

/* 选中态：整行浅 tint + 主色图标与文字 */
.nav-link.is-active {
  background: var(--primary-soft);
}

.nav-link__icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  color: var(--ink-2);
  transition: color var(--duration-quick) var(--ease);
}

.nav-link:hover .nav-link__icon {
  color: var(--ink);
}

.nav-link.is-active .nav-link__icon {
  color: var(--primary);
}

.nav-link__label {
  min-width: 0;
  color: var(--ink);
  font-size: 15.5px;
  font-weight: 400;
  letter-spacing: -0.006em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color var(--duration-quick) var(--ease);
}

.nav-link.is-active .nav-link__label {
  color: var(--primary-ink);
  font-weight: 500;
}

/* AI 标识：金色描边胶囊（对齐参考图的徽章处理），只属于 Agent */
.nav-link__badge {
  flex: none;
  padding: 1px 8px;
  color: var(--gold);
  background: var(--gold-soft);
  border: 1px solid currentColor;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  line-height: 1.5;
  transition: background-color var(--duration-quick) var(--ease);
}

/* -------------------------------------------------------------- 底部功能区 */
.sidebar-foot {
  position: relative;
  flex: none;
  display: grid;
  gap: 2px;
  margin-top: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline-strong);
}

/* 导航底部的渐隐已改为 .sidebar-nav 上的 mask，这里不再叠一层覆盖式渐变 */

.foot-row {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 52px;
  padding: 0 14px;
  color: var(--ink);
  background: transparent;
  border: none;
  border-radius: 12px;
  font-family: inherit;
  font-size: 15.5px;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease),
    color var(--duration-quick) var(--ease);
}

.foot-row:hover {
  background: var(--surface-2);
}

.foot-row__icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  color: var(--ink-2);
}

.foot-row__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 当前状态只由左侧图标承担（显示器 / 太阳 / 月亮），
   右侧不再放文字值 —— 一行字飘在最右只会显得空。
   完整模式名放在 title 里，悬停可见。 */

/* --------------------------------------------------------- 折叠成图标栏 */
.is-collapsed .sidebar-brand {
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}

.is-collapsed .nav-link,
.is-collapsed .foot-row {
  grid-template-columns: 1fr;
  justify-items: center;
  padding: 0;
}
.foot-version {
  padding: 0 12px 8px;
  font-size: var(--fs-micro);
  color: var(--ink-4);
  user-select: text;
}
</style>

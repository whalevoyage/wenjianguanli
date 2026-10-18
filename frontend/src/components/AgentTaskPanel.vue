<template>
  <aside v-show="visible" class="task-panel">
    <div class="task-panel__head">
      <span class="task-panel__title">任务</span>
      <span v-if="totalCount" class="task-panel__count">{{ totalCount }}</span>
      <span class="task-panel__spacer"></span>
      <button type="button" class="task-panel__collapse" title="收起列表" @click="emit('toggle')">
        <el-icon :size="15"><DArrowLeft /></el-icon>
      </button>
    </div>

    <button type="button" class="task-new" @click="emit('create')">
      <el-icon :size="14"><Plus /></el-icon>新建任务
    </button>

    <div class="task-list">
      <template v-for="group in groups" :key="group.label">
        <div class="task-group__label">{{ group.label }}</div>
        <div
          v-for="item in group.items"
          :key="item.id"
          class="task-item"
          :class="{ 'is-active': item.active }"
          @click="emit('select', item.id)"
        >
          <div class="task-item__body">
            <span class="task-item__title">{{ item.title }}</span>
            <span class="task-item__meta">{{ item.meta }}</span>
          </div>
          <el-dropdown
            trigger="click"
            placement="bottom-end"
            @command="command => emit('command', command, item.id)"
          >
            <button type="button" class="task-item__more" title="更多" @click.stop>
              <el-icon :size="14"><MoreFilled /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="rename">重命名</el-dropdown-item>
                <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </template>
      <div v-if="!groups.length" class="task-list__empty">
        <el-icon :size="22"><ChatLineSquare /></el-icon>
        <span>还没有任务</span>
      </div>
    </div>
  </aside>
</template>

<script setup>
/**
 * 任务列表侧栏（Phase 7 从 Agent.vue 拆出）。
 *
 * 刻意做成**零逻辑组件**：分组、排序、副标题文案全部由父组件算好后传入，
 * 这里只负责渲染与把点击交回去。好处是拆分不会把任何业务规则搬进子组件 ——
 * 一旦哪天要改「任务按什么分组」，只需要改 Agent.vue 一处。
 *
 * item 形状：{ id, title, meta, active }
 */
import { computed } from 'vue'
import { ChatLineSquare, DArrowLeft, MoreFilled, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  visible: { type: Boolean, default: true },
  /** [{ label, items: [{ id, title, meta, active }] }] */
  groups: { type: Array, default: () => [] },
})

const emit = defineEmits(['toggle', 'create', 'select', 'command'])

const totalCount = computed(() => props.groups.reduce((sum, group) => sum + group.items.length, 0))
</script>

<style scoped>
/* ------------------------------------------------------- 任务列表（左） */
/* 面板与主区同为白底（用户要求「和界面一样的白色底」），靠右侧一根细线分栏；
   列表项再用 1px 描边框成「卡」，当前项描边转主色。
   竖向栅格统一：标题 / 新建按钮 / 列表项文字 / 项内「…」右边缘，都落在同两条线上。 */
.task-panel {
  flex: none;
  width: 248px;
  display: flex;
  flex-direction: column;
  padding: 12px;
  background: var(--surface);
  border-right: 1px solid var(--hairline);
}

/* 左缩进 = 列表项内边距（10px），让「任务」和列表项文字对齐；缩小后左右都不留大空白 */
.task-panel__head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 0 10px 10px;
  border-bottom: 1px solid var(--hairline);   /* 面板头与列表分节 */
}

.task-panel__title {
  color: var(--ink);
  font-size: var(--fs-title);
  font-weight: 600;
}

.task-panel__count {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.task-panel__spacer {
  flex: 1;
}

/* 24px 方钮：不描边；右边缘与「新建任务」按钮齐平 */
.task-panel__collapse {
  flex: none;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-md);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.task-panel__collapse:hover {
  color: var(--ink);
  background: var(--inset);
}

/* 无底、不描边；左对齐到列表项的同一左栅格（此前居中，是全面板唯一脱栅格的一行） */
/* 「新建任务」用主色文字（不加边框、不加底色）——
   面板顶部需要一个彩色锚点，否则整栏从标题到列表清一色灰。
   加框会把它抬成按钮，与「面板里全是记录」的调子不搭。 */
.task-new {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  width: 100%;
  height: 34px;
  margin: 8px 0 4px;
  padding: 0 10px;                 /* 与列表项文字同一左边缘 */
  color: var(--primary);
  background: transparent;         /* 白底（用户要求灰底换白底） */
  border: 0;
  border-radius: var(--agent-radius-card);
  box-shadow: none;
  font-family: inherit;
  font-size: var(--fs-note);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease),
    box-shadow var(--duration-quick) var(--ease);
}

.task-new:hover {
  color: var(--primary-ink);
  background: var(--primary-soft);   /* 悬停给一层主色淡底，与它的颜色呼应 */
}

.task-list {
  flex: 1;
  min-height: 0;
  display: grid;
  gap: 6px;
  align-content: start;
  overflow-y: auto;
  margin: 0 -2px;
  padding: 0 2px 8px;
}

.task-group__label {
  padding: 8px 0 2px 10px;
  color: var(--ink-3);
  font-size: var(--fs-micro);
  letter-spacing: 0.02em;
}

/* 第二组起：一条极淡的分节线，把「今天 / 昨天 / 更早」分成明显的段 */
.task-group__label:not(:first-child) {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

/* 列表项：不描边、不加灰底（用户要求「这个灰底都换成白底」），当前项只用标题加深 + 加粗区分。
   左内边距保持 10px —— 面板 padding 12px + 10px = 22px，这是全站统一的文字左边缘，
   改动它会让「新建任务」和列表项标题错开一条线。
   v25 曾给当前项加过左侧 2px 主色条，**用户当场否掉**，已移除 —— 别再翻烙饼。 */
.task-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 7px 10px;
  border-radius: var(--agent-radius-card);
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease);
}

.task-item:hover {
  background: var(--inset);        /* 悬停才出底，静止时是纯白 */
}

.task-item.is-active {
  background: var(--primary-soft);
}

.task-item__body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

/* 标题用真正的深色（`--ink`），不用 `--ink-2` 的中灰 ——
   五条中灰标题堆在一起，整栏就是一片灰，这就是「太灰」的主因。
   当前项因此不能再靠「比别人深」来区分（大家一样深），改靠字重 600 + 淡主色底。 */
.task-item__title {
  overflow: hidden;
  color: var(--ink);
  font-size: var(--agent-fs-body-tight);
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-item.is-active .task-item__title {
  font-weight: 600;
}

.task-item__meta {
  color: var(--ink-3);
  font-size: var(--fs-micro);
  font-variant-numeric: tabular-nums;
}

.task-item__more {
  flex: none;
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-sm);
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--duration-quick) var(--ease), color var(--duration-quick) var(--ease),
    background-color var(--duration-quick) var(--ease);
}

/* 「…」一律 hover 才出（当前项也不再常显），减少右侧常驻噪音 */
.task-item:hover .task-item__more {
  opacity: 1;
}

.task-item__more:hover {
  color: var(--ink);
  background: var(--inset);
}

.task-list__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 8px;
  color: var(--ink-4);
  font-size: var(--fs-note);
  text-align: center;
}
</style>

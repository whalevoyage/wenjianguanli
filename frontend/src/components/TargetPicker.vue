<script setup>
import { computed, ref } from 'vue'
import { Close, FolderOpened, Plus } from '@element-plus/icons-vue'

const props = defineProps({
  path: { type: String, default: '' },
  excludes: { type: Array, default: () => [] },
  drives: { type: Array, default: () => [] },
  label: { type: String, default: '目标路径' },
  placeholder: { type: String, default: '例如：D:\\Workspace' },
  showExcludes: { type: Boolean, default: true },
})

const emit = defineEmits(['update:path', 'update:excludes'])

const expanded = ref(false)
const draft = ref('')

const driveOptions = computed(() => (props.drives || []).map(letter => ({
  letter,
  root: `${letter}:\\`,
  active: props.path?.toUpperCase().startsWith(`${letter}:`),
})))

function pickDrive(root) {
  emit('update:path', root)
}

function removeExclude(name) {
  emit('update:excludes', props.excludes.filter(item => item !== name))
}

function addExclude() {
  const value = draft.value.trim()
  if (!value || props.excludes.includes(value)) {
    draft.value = ''
    return
  }
  emit('update:excludes', [...props.excludes, value])
  draft.value = ''
}
</script>

<template>
  <div class="target-picker">
    <div class="target-picker__row">
      <span class="target-picker__label">盘符</span>
      <div class="drive-chips">
        <button
          v-for="drive in driveOptions"
          :key="drive.letter"
          type="button"
          class="drive-chip"
          :class="{ 'is-active': drive.active }"
          @click="pickDrive(drive.root)"
        >
          {{ drive.letter }}:
        </button>
        <span v-if="!driveOptions.length" class="muted">未检测到磁盘</span>
      </div>
    </div>

    <div class="target-picker__row">
      <span class="target-picker__label">{{ label }}</span>
      <el-input
        :model-value="path"
        :placeholder="placeholder"
        clearable
        @update:model-value="value => emit('update:path', value)"
      >
        <template #prefix><el-icon><FolderOpened /></el-icon></template>
      </el-input>
    </div>

    <template v-if="showExcludes">
      <div class="target-picker__row target-picker__row--tight">
        <span class="target-picker__label">排除</span>
        <div class="exclude-summary">
          <button type="button" class="exclude-toggle" @click="expanded = !expanded">
            已排除 {{ excludes.length }} 项 {{ expanded ? '收起' : '展开' }}
          </button>
          <span class="exclude-note">桌面、系统目录等保护路径已强制排除，无法关闭</span>
        </div>
      </div>

      <div v-if="expanded" class="exclude-panel">
        <el-tag
          v-for="item in excludes"
          :key="item"
          closable
          effect="plain"
          type="info"
          @close="removeExclude(item)"
        >
          {{ item }}
        </el-tag>

        <div class="exclude-add">
          <el-input
            v-model="draft"
            size="small"
            placeholder="补充要排除的目录名"
            @keyup.enter="addExclude"
          />
          <el-button size="small" :icon="Plus" @click="addExclude">添加</el-button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.target-picker {
  display: grid;
  gap: 12px;
}

.target-picker__row {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.target-picker__row--tight {
  align-items: start;
}

.target-picker__label {
  color: var(--ink-3);
  font-size: 12px;
  font-weight: 600;
}

.drive-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.drive-chip {
  min-width: 52px;
  padding: 7px 14px;
  color: var(--ink-2);
  background: var(--inset);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  /* 逐属性枚举，不用通配简写 —— 否则会带动无关样式 */
  transition: background-color var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease),
    transform var(--duration-fast) var(--ease-spring);
}

.drive-chip:hover {
  border-color: var(--hairline-strong);
}

.drive-chip:active {
  transform: scale(0.97);
}

.drive-chip.is-active {
  color: var(--surface);
  background: var(--primary);
  border-color: var(--primary);
}

.exclude-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.exclude-toggle {
  padding: 5px 12px;
  color: var(--ink-2);
  background: var(--inset);
  border: 1px solid var(--hairline);
  border-radius: 999px;
  font-size: 12px;
  cursor: pointer;
}

.exclude-note {
  color: var(--ink-3);
  font-size: 12px;
}

.exclude-panel {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 12px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 13px;
}

.exclude-add {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 280px;
}

@media (max-width: 640px) {
  .target-picker__row {
    grid-template-columns: 1fr;
  }

  .exclude-add {
    width: 100%;
  }
}
</style>

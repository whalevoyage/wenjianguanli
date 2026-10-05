<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, RefreshLeft, Refresh } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'
import { getTrash, purgeTrash, restoreTrash } from '../api/filepilot'
import { formatBytes, formatCount, formatDateTime } from '../utils/formatters'

const data = ref(null)
const loading = ref(false)
const selected = ref(new Set())

const entries = computed(() => data.value?.entries || [])
const sourceGroups = computed(() => {
  const groups = new Map()
  for (const entry of entries.value) {
    const source = entry.rule === 'duplicate' ? '重复文件查重' : entry.rule === 'junk' ? '垃圾清理' : entry.rule || '手动'
    const item = groups.get(source) || { source, count: 0, size: 0, earliest: entry.time }
    item.count += 1; item.size += Number(entry.size || 0); item.earliest = item.earliest < entry.time ? item.earliest : entry.time
    groups.set(source, item)
  }
  return [...groups.values()]
})

const selectionSize = computed(() => {
  let size = 0
  for (const entry of entries.value) {
    if (selected.value.has(entry.id)) size += Number(entry.size || 0)
  }
  return size
})

const selectionText = computed(() => {
  const size = selectionSize.value
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = size
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(index ? 1 : 0)} ${units[index]}`
})

async function load(showMessage = false) {
  loading.value = true
  try {
    data.value = await getTrash()
    selected.value = new Set()
    if (showMessage) ElMessage.success('回收站已刷新')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function toggle(entry) {
  const next = new Set(selected.value)
  next.has(entry.id) ? next.delete(entry.id) : next.add(entry.id)
  selected.value = next
}

function toggleAll() {
  selected.value = selected.value.size === entries.value.length
    ? new Set()
    : new Set(entries.value.map(entry => entry.id))
}

async function restore() {
  if (!selected.value.size) return
  try {
    await restoreTrash([...selected.value])
    ElMessage.success('已还原到原位')
    load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function purge() {
  if (!selected.value.size) return
  try {
    await ElMessageBox.confirm(
      `这会把选中的 ${selected.value.size} 个项目从磁盘上彻底删除，无法用本工具还原。是否继续？`,
      '确认彻底删除',
      { type: 'warning', confirmButtonText: '彻底删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    const result = await purgeTrash([...selected.value])
    ElMessage.success(`已彻底删除 ${result.purged_count} 项，释放 ${result.freed_size_text}`)
    load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

onMounted(() => load())
</script>

<template>
  <div v-loading="loading" class="section-stack">
    <section class="app-card panel-card">
      <div v-if="sourceGroups.length" class="trash-summary">
        <div class="trash-summary__head"><span>来源操作</span><span>项目数</span><span>占用空间</span><span>最早进入</span></div>
        <div v-for="group in sourceGroups" :key="group.source" class="trash-summary__row"><strong>{{ group.source }}</strong><span>{{ group.count }}</span><span>{{ formatBytes(group.size) }}</span><span>{{ formatDateTime(group.earliest) }}</span></div>
      </div>
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">隔离区内容</h3>
          <p class="panel-subtitle path-text" :title="data?.trash_dir">{{ data?.trash_dir || '—' }}</p>
        </div>
        <div class="panel-title-actions">
          <span class="panel-count">{{ formatCount(data?.count) }} 个项目 · 占用 {{ data?.total_size_text || '0 B' }}</span>
          <el-button circle text :icon="Refresh" aria-label="刷新" @click="load(true)" />
          <el-button v-if="entries.length" size="small" plain @click="toggleAll">
            {{ selected.size === entries.length ? '取消全选' : '全选' }}
          </el-button>
        </div>
      </div>

      <div v-if="entries.length" class="trash-list">
        <div v-for="entry in entries" :key="entry.id" class="trash-row">
          <el-checkbox :model-value="selected.has(entry.id)" @update:model-value="toggle(entry)" />

          <div class="trash-row__copy">
            <strong>
              {{ entry.name }}
              <el-tag v-if="entry.is_dir" size="small" effect="plain" type="info">目录</el-tag>
            </strong>
            <small class="path-text" :title="entry.original">原位置：{{ entry.original }}</small>
          </div>

          <div class="trash-row__meta">
            <span>{{ entry.size_text }}</span>
            <small>{{ formatDateTime(entry.time) }}</small>
          </div>
        </div>
      </div>

      <EmptyState
        v-else
        :icon="Delete"
        title="隔离区是空的"
      />
    </section>

    <section v-if="selected.size" class="app-card panel-card sticky-bar">
      <div>
        <strong>已选 {{ selected.size }} 项</strong>
        <span class="muted">合计 {{ selectionText }}</span>
      </div>
      <div class="sticky-bar__actions">
        <el-button :icon="RefreshLeft" @click="restore">还原到原位</el-button>
        <el-button type="danger" plain :icon="Delete" @click="purge">彻底删除</el-button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.trash-list {
  display: grid;
  gap: 8px;
}

.trash-summary { margin-bottom: 24px; overflow-x: auto; }
.trash-summary__head, .trash-summary__row { display: grid; grid-template-columns: 1.4fr .6fr 1fr 1fr; min-width: 0; gap: 16px; align-items: center; }
.trash-summary__head { padding: 0 12px 10px; color: var(--ink-2); font-size: var(--fs-micro); font-weight: 600; }
.trash-summary__row { min-height: 44px; padding: 8px 12px; border-top: 1px solid var(--hairline); font-size: var(--fs-note); }

@media (max-width: 640px) {
  .trash-summary__head,
  .trash-summary__row {
    grid-template-columns: 1.4fr .8fr;
    gap: 8px;
  }

  .trash-summary__head > :nth-child(3),
  .trash-summary__row > :nth-child(3),
  .trash-summary__head > :nth-child(4),
  .trash-summary__row > :nth-child(4) {
    display: none;
  }
}

.trash-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 13px;
}

.trash-row__copy {
  min-width: 0;
}

.trash-row__copy strong {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.trash-row__copy small {
  display: block;
  margin-top: 5px;
}

.trash-row__meta {
  text-align: right;
  white-space: nowrap;
}

.trash-row__meta span {
  display: block;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.trash-row__meta small {
  display: block;
  margin-top: 4px;
  color: var(--ink-3);
  font-size: 11px;
}

.sticky-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  border-color: var(--primary);
}

.sticky-bar strong {
  display: block;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
}

.sticky-bar span {
  display: block;
  margin-top: 4px;
  font-size: 12px;
}

.sticky-bar__actions {
  display: flex;
  gap: 10px;
}
</style>

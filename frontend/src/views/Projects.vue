<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Aim, FolderOpened, PieChart, Timer, Warning } from '@element-plus/icons-vue'
import { startProjectScan } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import DataTable from '../components/DataTable.vue'
import EmptyState from '../components/EmptyState.vue'
import JobProgress from '../components/JobProgress.vue'
import ProjectRadar from '../components/ProjectRadar.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatCount, formatDuration } from '../utils/formatters'

/**
 * 项目雷达：把全盘项目画成气泡图，一眼扫出「又大又久没动」的那几个。
 * 只读：不移动、不删除任何文件。
 */
const CHART_LIMIT = 80

const { config, excludes, load: loadConfig } = useConfig()
const scan = useJob('projects')

const path = ref('')
const zombieDays = ref(180)
const maxDepth = ref(0)
const onlyZombie = ref(false)
const picked = ref('')
const result = ref(null)

const zombieOptions = [
  { label: '90 天没动', value: 90 },
  { label: '180 天没动', value: 180 },
  { label: '365 天没动', value: 365 },
]

const depthOptions = [
  { label: '不限深度', value: 0 },
  { label: '只看 2 层', value: 2 },
  { label: '只看 3 层', value: 3 },
]

const columns = [
  { prop: 'name', label: '项目', minWidth: 200 },
  { prop: 'shell', label: '类型', width: 110 },
  { prop: 'size_text', label: '体积', width: 100, align: 'right' },
  { prop: 'file_count', label: '文件数', width: 92, align: 'right' },
  { prop: 'last_active_text', label: '最后活跃', width: 130 },
  { prop: 'is_zombie', label: '状态', width: 100 },
  { prop: 'path', label: '路径', minWidth: 260 },
]

/** 图里画全部僵尸 + 体积最大的若干个，避免几百个气泡糊成一片 */
const chartPoints = computed(() => {
  const list = (result.value?.projects || []).filter(item => Number.isFinite(item.inactive_days))
  const zombies = list.filter(item => item.is_zombie)
  const alive = list.filter(item => !item.is_zombie).sort((a, b) => b.size - a.size)
  const budget = Math.max(CHART_LIMIT - zombies.length, 1)
  return [...zombies, ...alive.slice(0, budget)].map(item => ({
    path: item.path,
    name: item.name,
    size: item.size,
    sizeText: item.size_text,
    fileCount: item.file_count,
    inactiveDays: item.inactive_days,
    isZombie: item.is_zombie,
  }))
})

const rows = computed(() => {
  const list = result.value?.projects || []
  return onlyZombie.value ? list.filter(item => item.is_zombie) : list
})

const summary = computed(() => {
  const data = result.value
  if (!data) return []
  const zombiePct = data.project_count ? Math.round((data.zombie_count / data.project_count) * 100) : 0
  const sizePct = data.total_size ? Math.round((data.zombie_size / data.total_size) * 100) : 0
  return [
    {
      title: '项目数',
      value: formatCount(data.project_count),
      note: '扫描范围内识别出的项目',
      icon: FolderOpened,
      tone: 'blue',
    },
    {
      title: '项目总体积',
      value: data.total_size_text,
      note: '各项目体积合计',
      icon: PieChart,
      tone: 'blue',
    },
    {
      title: '僵尸项目',
      value: formatCount(data.zombie_count),
      note: `占全部项目的 ${zombiePct}%`,
      icon: Warning,
      tone: 'blue',
    },
    {
      title: '僵尸占用',
      value: data.zombie_size_text,
      note: `占项目总体积的 ${sizePct}%`,
      icon: Timer,
      tone: 'blue',
    },
  ]
})

const pickedProject = computed(() =>
  (result.value?.projects || []).find(item => item.path === picked.value) || null,
)

function inactiveText(row) {
  if (row.inactive_days === null || row.inactive_days === undefined) return '—'
  if (row.inactive_days < 1) return '今天还在动'
  return `${formatCount(row.inactive_days)} 天没动`
}

function rowClassName({ row }) {
  return row.path === picked.value ? 'is-picked' : ''
}

function pick(pathValue) {
  picked.value = picked.value === pathValue ? '' : pathValue
}

async function runScan() {
  if (!path.value.trim()) {
    ElMessage.warning('请先选择要扫描的目录')
    return
  }
  result.value = null
  onlyZombie.value = false
  picked.value = ''
  const job = await scan.run(() => startProjectScan({
    path: path.value.trim(),
    excludes: excludes.value,
    zombie_days: Number(zombieDays.value) || 180,
    max_depth: Number(maxDepth.value) || 0,
  }))
  if (job) ElMessage.success('项目雷达扫描完成')
}

watch(() => scan.job.value?.status, (status) => {
  if (status === 'done' && scan.job.value?.result) {
    result.value = scan.job.value.result
  }
})

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <TargetPicker
        v-model:path="path"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="要扫描的目录"
        placeholder="例如：D:\\"
      />

      <el-divider />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">僵尸线</span>
          <el-segmented v-model="zombieDays" :options="zombieOptions" />
        </div>

        <div class="option-block">
          <span class="field-label">扫描深度</span>
          <el-segmented v-model="maxDepth" :options="depthOptions" />
        </div>

        <el-button
          type="primary"
          size="large"
          :icon="Aim"
          :loading="scan.running.value"
          @click="runScan"
        >
          {{ scan.running.value ? '正在扫描' : '扫描项目' }}
        </el-button>
      </div>

      <el-alert
        class="panel-note"
        type="info"
        :closable="false"
        show-icon
        title="识别依据是项目标记文件（.git / package.json / pyproject.toml / *.sln 等），只统计不移动。"
      />

      <JobProgress v-if="scan.job.value" :job="scan.job.value" empty-title="先扫描一次项目" :rollbackable="false" @cancel="scan.cancel" />
    </section>

    <template v-if="result">
      <section class="stats-grid">
        <StatCard v-for="item in summary" :key="item.title" v-bind="item" />
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">项目分布</h3>
          </div>
          <div class="meta-pill">右上角的橙点最该处理</div>
        </div>

        <ProjectRadar
          :points="chartPoints"
          :zombie-days="result.zombie_days"
          :highlight="picked"
          @select="pick"
        />

        <p class="panel-hint">
          横轴是「多久没动」，纵轴是项目体积，点越大文件越多；虚线右侧的橙点就是僵尸项目。
          <template v-if="(result.projects || []).length > chartPoints.length">
            图里画了全部僵尸 + 体积最大的若干个（共 {{ formatCount(chartPoints.length) }} 个），完整清单见下表。
          </template>
        </p>
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">项目清单</h3>
          </div>
          <div class="title-actions">
            <span v-if="pickedProject" class="picked-pill">
              已选中 {{ pickedProject.name }}
              <button type="button" class="picked-pill__clear" @click="picked = ''">×</button>
            </span>
            <el-switch v-model="onlyZombie" active-text="只看僵尸项目" />
          </div>
        </div>

        <div v-if="result.shells?.length" class="shell-row">
          <span v-for="shell in result.shells" :key="shell.label" class="shell-pill">
            {{ shell.label }} · {{ formatCount(shell.count) }} 个 · {{ shell.size_text }}
          </span>
        </div>

        <DataTable
          :columns="columns"
          :data="rows"
          :empty-icon="FolderOpened"
          :row-class-name="rowClassName"
          empty-text="没有发现项目"
          empty-description="换个目录，或放宽扫描深度再看一次。"
        >
          <template #name="{ row }">
            <div class="cell-stack">
              <span class="cell-strong">{{ row.name }}</span>
              <span v-if="row.rel && row.rel !== row.name" class="cell-sub">{{ row.rel }}</span>
            </div>
          </template>

          <template #is_zombie="{ row }">
            <span v-if="row.is_zombie" class="state-pill state-pill--zombie">
              <el-icon :size="12"><Warning /></el-icon>
              僵尸
            </span>
            <span v-else class="state-pill">
              <el-icon :size="12"><Timer /></el-icon>
              {{ inactiveText(row) }}
            </span>
          </template>
        </DataTable>

        <p class="panel-hint">
          共 {{ formatCount(result.project_count) }} 个项目 / {{ result.total_size_text }}，扫描耗时
          {{ formatDuration(result.duration_seconds) }}。僵尸项目按「最后活跃早于 {{ result.zombie_days }} 天前」判定。
        </p>
      </section>
    </template>

    <section v-else class="app-card panel-card">
      <EmptyState
        :icon="Aim"
        title="先扫描一次项目"
      />
    </section>
  </div>
</template>

<style scoped>
.title-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.picked-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 6px 2px 10px;
  color: var(--primary-ink);
  background: var(--primary-soft);
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 12px;
}

.picked-pill__clear {
  width: 16px;
  height: 16px;
  padding: 0;
  color: inherit;
  background: transparent;
  border: none;
  border-radius: 50%;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}

.shell-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 4px 0 16px;
}

.shell-pill {
  padding: 2px 10px;
  color: var(--ink-2);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 999px;
  font-size: 12px;
}

.cell-stack {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.cell-strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 500;
}

.cell-sub {
  color: var(--ink-3);
  font-size: 12px;
}

/* 状态胶囊：僵尸是「该处理了」的提示，用警示语义色；正常项保持中性。 */
.state-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  color: var(--ink-2);
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 8px;
  font-size: 12px;
  white-space: nowrap;
}

.state-pill--zombie {
  color: var(--warning-ink);
  background: var(--warning-soft);
  border-color: currentColor;
}

/* 图上点了气泡 → 表格里对号入座 */
:deep(.el-table__row.is-picked td) {
  background: var(--primary-soft);
}

.panel-hint {
  margin: 14px 0 0;
  color: var(--ink-3);
  font-size: 12px;
}
</style>

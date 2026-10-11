<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Coin, Files, Lock, PieChart, Sort, Warning } from '@element-plus/icons-vue'
import { startMigrate } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import { useHandoffTarget } from '../composables/useHandoff'
import HandoffNote from '../components/HandoffNote.vue'
import DataTable from '../components/DataTable.vue'
import JobProgress from '../components/JobProgress.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatCount } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const plan = useJob('migrate.plan')
const execute = useJob('migrate.execute')

const source = ref('')
/* 跨页接力：从别的页面带着目录过来时，直接填好这个框（不自动开跑） */
const { handed, from } = useHandoffTarget(source)
const target = ref('')
const mode = ref('move')
const createJunction = ref(false)
const planResult = ref(null)

const modeOptions = [
  { label: '移动（校验后删源）', value: 'move' },
  { label: '仅复制（保留源文件）', value: 'copy' },
]

const planSummary = computed(() => {
  if (!planResult.value) return []
  const data = planResult.value
  const items = [
    {
      title: '待迁移文件',
      value: formatCount(data.item_count),
      note: '将按计划移动到目标盘',
      icon: Files,
      tone: 'blue',
    },
    {
      title: '总体积',
      value: data.total_size_text,
      note: '移动前请确认目标盘放得下',
      icon: PieChart,
      tone: 'blue',
    },
    {
      title: '目标盘可用',
      value: data.target_free_text,
      note: '需不小于总体积',
      icon: Coin,
      tone: 'blue',
    },
    {
      title: '同名冲突',
      value: formatCount(data.conflicts),
      note: data.conflicts ? '执行时需逐个确认' : '无同名冲突',
      icon: Warning,
      tone: 'blue',
    },
  ]
  // 桌面 / .workbuddy / 项目自身等保护目录内的文件会被剔除，这里如实报出来
  if (data.skipped_protected) {
    items.push({
      title: '保护目录跳过',
      value: formatCount(data.skipped_protected),
      note: '受写保护，强制排除',
      icon: Lock,
      tone: 'blue',
    })
  }
  return items
})

const planColumns = [
  { prop: 'name', label: '文件名', minWidth: 200 },
  { prop: 'source', label: '源路径', minWidth: 300 },
  { prop: 'target', label: '目标路径', minWidth: 300 },
  { prop: 'size_text', label: '大小', width: 110, align: 'right' },
]

async function makePlan() {
  if (!source.value.trim() || !target.value.trim()) {
    ElMessage.warning('请填写源目录和目标目录')
    return
  }
  planResult.value = null
  await plan.run(() => startMigrate({
    source_dir: source.value.trim(),
    target_dir: target.value.trim(),
    excludes: excludes.value,
    dry_run: true,
    mode: mode.value,
    create_junction: createJunction.value,
  }))
}

async function runMigration() {
  if (!planResult.value?.plan?.length) return
  const items = planResult.value.plan.map(item => ({ source: item.source, target: item.target, size: item.size }))
  const job = await execute.run(() => startMigrate({
    source_dir: source.value.trim(),
    target_dir: target.value.trim(),
    dry_run: false,
    mode: mode.value,
    create_junction: createJunction.value,
    items,
  }))
  if (job) ElMessage.success('迁移任务已启动，复制与校验会同步显示进度')
}

watch(() => plan.job.value?.status, (status) => {
  if (status === 'done' && plan.job.value?.result) {
    planResult.value = plan.job.value.result
  }
})

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <HandoffNote v-if="handed" :path="handed" :from="from" @dismiss="handed = ''" />

      <TargetPicker
        v-model:path="source"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="源目录"
        placeholder="例如：C:\\Users\\me\\Videos"
      />

      <el-divider />

      <TargetPicker
        v-model:path="target"
        :drives="config?.drives || []"
        :show-excludes="false"
        label="目标目录"
        placeholder="例如：D:\\Archive"
      />

      <div class="row-actions">
        <div class="option-block">
          <span class="field-label">迁移方式</span>
          <el-segmented v-model="mode" :options="modeOptions" />
        </div>

        <div class="option-block">
          <span class="field-label">搬完后在原位置留一个目录联接</span>
          <el-switch v-model="createJunction" />
        </div>

        <el-button type="primary" size="large" :loading="plan.running.value" :icon="Sort" @click="makePlan">
          {{ plan.running.value ? '正在规划' : '生成迁移计划' }}
        </el-button>
      </div>

      <el-alert
        class="panel-note"
        type="warning"
        :closable="false"
        show-icon
        title="这一步只生成计划，不会动任何文件。确认清单之后再执行。"
      />

      <JobProgress v-if="plan.job.value" :job="plan.job.value" empty-title="先生成迁移计划" :rollbackable="false" @cancel="plan.cancel" />
    </section>

    <template v-if="planResult">
      <section class="stats-grid">
        <StatCard v-for="item in planSummary" :key="item.title" v-bind="item" />
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">迁移清单</h3>
            <p class="panel-subtitle">
              共 {{ formatCount(planResult.item_count) }} 项，清单已同时导出为 CSV 方便搬完对账。
            </p>
          </div>
          <el-button
            type="primary"
            :loading="execute.running.value"
            :disabled="!planResult.plan?.length"
            @click="runMigration"
          >
            确认执行迁移
          </el-button>
        </div>

        <DataTable
          :columns="planColumns"
          :data="planResult.plan || []"
          empty-text="没有需要迁移的文件"
          :empty-icon="Sort"
        />
      </section>
    </template>

    <section v-if="execute.job.value" class="app-card panel-card">
      <div class="panel-title-row">
        <div>
          <h3 class="panel-title">执行进度</h3>
        </div>
      </div>

      <JobProgress :job="execute.job.value" @cancel="execute.cancel" @rollback="execute.rollback" />
    </section>
  </div>
</template>

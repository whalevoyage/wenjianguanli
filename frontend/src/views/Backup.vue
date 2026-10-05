<script setup>
import { computed, onMounted, ref } from 'vue'
import { Box, Files, FolderDelete, FolderOpened } from '@element-plus/icons-vue'
import { startBackup } from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import JobProgress from '../components/JobProgress.vue'
import StatCard from '../components/StatCard.vue'
import TargetPicker from '../components/TargetPicker.vue'
import { formatCount } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const { job, running, run, cancel, rollback } = useJob('backup')

const source = ref('')
const dest = ref('')

const summary = computed(() => {
  const result = job.value?.result
  if (!result) return []
  const ratio = result.source_size ? Math.round((result.size / result.source_size) * 100) : 0
  const times = result.size ? (result.source_size / result.size).toFixed(1) : '—'
  return [
    {
      title: '压缩包文件数',
      value: formatCount(result.file_count),
      note: `已跳过 ${result.excludes?.length || 0} 个排除目录`,
      icon: Files,
      tone: 'blue',
    },
    {
      title: '原始体积',
      value: result.source_size_text,
      note: `是压缩包体积的 ${times} 倍`,
      icon: FolderOpened,
      tone: 'blue',
    },
    {
      title: '压缩包体积',
      value: result.size_text,
      note: `压缩到原来的 ${ratio}%`,
      icon: Box,
      tone: 'blue',
    },
    {
      title: '排除目录',
      value: `${result.excludes?.length || 0} 项`,
      note: '系统目录等保护路径',
      icon: FolderDelete,
      tone: 'blue',
    },
  ]
})

async function start() {
  if (!source.value.trim()) return
  await run(() => startBackup({
    source_dir: source.value.trim(),
    dest_dir: dest.value.trim(),
    excludes: excludes.value,
  }))
}

onMounted(async () => {
  await loadConfig().catch(() => {})
})
</script>

<template>
  <div class="section-stack form-page">

    <section class="app-card panel-card">
      <TargetPicker
        v-model:path="source"
        v-model:excludes="excludes"
        :drives="config?.drives || []"
        label="备份目录"
        placeholder="例如：D:\\MyProject"
      />

      <el-divider />

      <TargetPicker
        v-model:path="dest"
        :drives="config?.drives || []"
        :show-excludes="false"
        label="保存到"
        placeholder="留空则存到工具状态目录的 backups 下"
      />

      <div class="row-actions row-actions--end">
        <el-button type="primary" size="large" :loading="running" :icon="FolderOpened" @click="start">
          {{ running ? '正在备份' : '开始备份' }}
        </el-button>
      </div>

      <el-alert
        class="panel-note"
        type="info"
        :closable="false"
        show-icon
        title="备份前会先干跑清点文件并检查目标盘剩余空间，确认能放下才开始压缩。"
      />

      <JobProgress v-if="job" :job="job" empty-title="先做一次备份" @cancel="cancel" @rollback="rollback" />
    </section>

    <template v-if="job?.result">
      <section class="stats-grid">
        <StatCard v-for="item in summary" :key="item.title" v-bind="item" />
      </section>

      <section class="app-card panel-card">
        <div class="panel-title-row">
          <div>
            <h3 class="panel-title">输出位置</h3>
          </div>
        </div>

        <div class="path-box">
          <span class="path-text" :title="job.result.zip_path">{{ job.result.zip_path }}</span>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.path-box {
  padding: 14px 16px;
  background: var(--surface-2);
  border: 1px solid var(--hairline);
  border-radius: 12px;
}
</style>

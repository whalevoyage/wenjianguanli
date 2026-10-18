/**
 * Agent 的模型配置与权限档位（Phase 7 从 Agent.vue 抽出）。
 *
 * 为什么抽 composable 而不是组件：这部分的状态被模板、发送逻辑、任务卡多处引用，
 * 拆成子组件要么 props 巨长、要么样式重复；而抽成 composable **模板一行不用改**，
 * 只是把「数据从哪来、改完存哪」集中到一处。
 *
 * 留在页面里的是**与 DOM 相关的交互**（例如选完档位要主动收起弹层），
 * 那属于视图而不是数据，不搬。
 */
import { computed, nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  getAgentSettings,
  getAgentStatus,
  getSafetySettings,
  saveAgentSettings,
} from '../api/filepilot'

const DEFAULT_MODEL_NAME = 'deepseek-flash'

export function useAgentSettings() {
  const status = ref(null)
  const settings = ref(null)
  const safety = ref(null)
  const saving = ref(false)
  /* 表单是「未提交的输入」，与已生效的 settings 分开存：留空表示不改动，
     避免把掩码（sk-ab…cd）当成真 key 再存回去。 */
  const form = ref({ key: '', model: '' })
  /* 自定义模型输入默认收起，点「使用自定义模型」再展开（列表更干净） */
  const showCustomModel = ref(false)
  const customModelInput = ref(null)

  const modelLabel = computed(() => settings.value?.model || status.value?.model || DEFAULT_MODEL_NAME)

  const permissionMode = computed(() => safety.value?.mode || 'standard')
  const permissionLabel = computed(() => safety.value?.label || '标准')
  const permissionModes = computed(() => safety.value?.modes || [])
  const permissionGuards = computed(() => safety.value?.guards || [])
  const permissionTone = computed(
    () => permissionModes.value.find(item => item.id === permissionMode.value)?.tone || 'quiet',
  )

  async function loadSettings() {
    try {
      settings.value = await getAgentSettings()
    } catch {
      settings.value = null
    }
  }

  async function loadSafety() {
    try {
      safety.value = await getSafetySettings()
    } catch {
      safety.value = null
    }
  }

  /** 状态 + 设置 + 权限一起刷：三者互相影响（没配 key 时模型名也要回退） */
  async function reloadStatus() {
    try {
      status.value = await getAgentStatus()
    } catch {
      status.value = { configured: false }
    }
    await loadSettings()
    await loadSafety()
  }

  function openCustomModel() {
    showCustomModel.value = true
    nextTick(() => customModelInput.value?.focus?.())
  }

  /** 切模型：点一下就存，不给多余的确认步骤 */
  async function submitModel(model) {
    const value = (model || '').trim() || DEFAULT_MODEL_NAME
    if (value === modelLabel.value) return
    try {
      settings.value = await saveAgentSettings({ model: value })
      await reloadStatus()
      ElMessage.success(`已切换到 ${value}`)
      // 应用后收起自定义入口，弹层回到干净状态
      showCustomModel.value = false
      form.value.model = ''
    } catch (error) {
      ElMessage.error(error.message)
    }
  }

  /** 保存 key。留空 = 不改动（避免把掩码当成真 key 回传） */
  async function submitKey() {
    if (!form.value.key.trim()) {
      ElMessage.warning('请输入 API Key')
      return
    }
    saving.value = true
    try {
      settings.value = await saveAgentSettings({ api_key: form.value.key.trim() })
      await reloadStatus()
      form.value.key = ''
      ElMessage.success('API Key 已保存，立即生效')
    } catch (error) {
      ElMessage.error(error.message)
    } finally {
      saving.value = false
    }
  }

  /** 清除只动 settings.json：配方与任务记录都不受影响 */
  async function clearKey() {
    try {
      settings.value = await saveAgentSettings({ api_key: '' })
      await reloadStatus()
      ElMessage.success('已清除本地保存的 API Key')
    } catch (error) {
      ElMessage.error(error.message)
    }
  }

  return {
    status,
    settings,
    safety,
    saving,
    form,
    showCustomModel,
    customModelInput,
    modelLabel,
    permissionMode,
    permissionLabel,
    permissionModes,
    permissionGuards,
    permissionTone,
    loadSettings,
    loadSafety,
    reloadStatus,
    openCustomModel,
    submitModel,
    submitKey,
    clearKey,
  }
}

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown,
  ArrowRight,
  ArrowUpBold,
  ChatLineSquare,
  Check,
  DArrowLeft,
  DArrowRight,
  Lock,
  MoreFilled,
  Plus,
  Promotion,
  Star,
  View,
  WarningFilled,
} from '@element-plus/icons-vue'
import {
  agentChatStream,
  clearAgentMemory,
  deleteRecipe,
  getAgentMemory,
  getAgentSettings,
  getAgentStatus,
  getRecipes,
  listAgentConversations,
  renameAgentConversation,
  getSafetySettings,
  saveAgentSettings,
  saveRecipe,
  saveSafetySettings,
  startBackup,
  startClean,
  startDuplicateScan,
  startJunkScan,
  startMigrate,
  startScan,
  startTree,
} from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useJob } from '../composables/useJob'
import AgentGlyph from '../components/AgentGlyph.vue'
import JobProgress from '../components/JobProgress.vue'
import { renderMarkdown } from '../utils/miniMarkdown'
import { formatShortTime } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const job = useJob('agent')

const status = ref(null)
const messages = ref([])
const draft = ref('')
const streaming = ref(false)
const phase = ref('')
const recipes = ref([])
const lastPlan = ref(null)
const scroller = ref(null)
const composerInput = ref(null)
const hoverIndex = ref(-1)
const expanded = ref(new Set())
const showJump = ref(false)

/* 会话上下文（Codex 式多会话）：
   - conversation_id 存 localStorage，重开页面接着上次那条；
   - 首次没有记录时，自动接「最近一条历史会话」（兼容以前没有 id、都堆在 default 桶的老数据）；
   - 「新建对话」= 换一个新 id，旧的原地保留，可在「历史对话」抽屉里翻。 */
const CONVERSATION_KEY = 'filepilot.agent.conversationId'
const conversationId = ref('')
const memoryCount = ref(0)
const conversations = ref([])
/* 任务列表是否收起：窄屏默认收起（Codex 在这种宽度也会折起任务栏） */
const listHidden = ref(window.innerWidth < 1280)
/* 已展开的「工作块」消息下标（计划卡默认折叠） */
const workOpen = ref(new Set())

function readStoredConversation() {
  try {
    return localStorage.getItem(CONVERSATION_KEY) || ''
  } catch {
    return ''
  }
}

function persistConversation(id) {
  try {
    localStorage.setItem(CONVERSATION_KEY, id)
  } catch {
    /* 隐私模式等拿不到 localStorage，忽略即可 */
  }
}

function newConversationId() {
  return `conv-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
}

/* 模型配置：点右下角模型名切模型（内含 API Key），不进「+」菜单 */
const settings = ref(null)
const saving = ref(false)
const form = ref({ key: '', model: '' })
/* 自定义模型输入默认收起，点「使用自定义模型」再展开（列表更干净） */
const showCustomModel = ref(false)
const customModelInput = ref(null)
const openCustomModel = () => {
  showCustomModel.value = true
  nextTick(() => customModelInput.value?.focus?.())
}

/* 权限档位：显示在「+」后面，点开切换 */
const safety = ref(null)

const permissionMode = computed(() => safety.value?.mode || 'standard')
const permissionLabel = computed(() => safety.value?.label || '标准')
const permissionModes = computed(() => safety.value?.modes || [])
const permissionGuards = computed(() => safety.value?.guards || [])
const permissionTone = computed(
  () => permissionModes.value.find(item => item.id === permissionMode.value)?.tone || 'quiet',
)
/* 弹层 ref：选完档位要主动收起，否则它一直悬在输入框上方 */
const permPop = ref(null)
const closePermPopover = () => permPop.value?.hide?.()

const LONG_TEXT = 320
/* 后端 /api/settings/agent 不可用时的兜底显示 */
const DEFAULT_MODEL_NAME = 'deepseek-v4.1-flash'
const MODEL_OPTIONS_FALLBACK = ['deepseek-v4.1-flash', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner']

const modelLabel = computed(() => settings.value?.model || status.value?.model || DEFAULT_MODEL_NAME)
const modelOptions = computed(() => settings.value?.model_options || MODEL_OPTIONS_FALLBACK)

let stopStream = null

/* 示例胶囊：只留 4 句、正好排满一行。
   6 句会断成「5 + 1」（最后一句孤零零占第二行），既不协调又占掉两行高度。
   删掉的是最长的迁移示例与语义重复的那句——迁移在侧栏「跨盘迁移」里能进。 */
const EXAMPLES = [
  '清理 C 盘垃圾',
  '找出 D 盘重复文件并只留最新',
  '扫描 D 盘',
  '备份 D:\\资料 到 E:\\备份',
]

const driveRoots = computed(() => (config.value?.drives || []).map(letter => `${letter}:\\`))

const jobRunning = computed(() => {
  const item = job.job.value
  return item && ['pending', 'running', 'waiting'].includes(item.status) ? item : null
})

const jobSummary = computed(() => {
  const item = job.job.value
  if (!item || ['pending', 'running', 'waiting'].includes(item.status)) return ''
  if (item.status === 'done') return `已完成 · ${item.result?.summary || '任务结果已生成'}`
  if (item.status === 'cancelled') return '任务已取消'
  return '任务执行失败'
})

/* ------------------------------------------------------------ 滚动与折叠 */

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = scroller.value
    if (!el) return
    if (force) {
      el.scrollTop = el.scrollHeight
      showJump.value = false
      return
    }
    // 用户翻到上面看历史时不要把他拽回来
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) {
      el.scrollTop = el.scrollHeight
      showJump.value = false
    }
  })
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  showJump.value = el.scrollHeight - el.scrollTop - el.clientHeight > 160
}

function isLong(index) {
  return (messages.value[index]?.message || '').length > LONG_TEXT
}

function isCollapsed(index) {
  return isLong(index) && !expanded.value.has(index)
}

function toggleExpand(index) {
  const next = new Set(expanded.value)
  next.has(index) ? next.delete(index) : next.add(index)
  expanded.value = next
}

/* ------------------------------------------------------------------ 对话 */

function sourceLabel(msg) {
  if (msg.source === 'local') return '本地规则'
  if (msg.source === 'deepseek') return 'DeepSeek'
  if (msg.source === 'error') return '出错'
  return '提示'
}

function send(text) {
  const message = (text ?? draft.value).trim()
  if (!message || streaming.value) return

  messages.value.push({ role: 'user', text: message })
  draft.value = ''
  phase.value = ''
  lastPlan.value = null
  streaming.value = true
  scrollToBottom(true)

  messages.value.push({
    role: 'assistant',
    source: '',
    tokens: 0,
    message: '',
    plan: null,
    pending: true,
    stopped: false,
  })
  const index = messages.value.length - 1

  stopStream = agentChatStream(message, { excludes: excludes.value, conversation_id: conversationId.value }, {
    onPhase: text => {
      phase.value = text
    },
    onDelta: text => {
      const item = messages.value[index]
      if (!item) return
      item.message += text
      scrollToBottom()
    },
    onDone: payload => {
      const item = messages.value[index]
      if (!item) return
      Object.assign(item, payload, { pending: false })
      if (payload.plan && payload.plan.action !== 'none') lastPlan.value = payload.plan
      if (typeof payload.memory_count === 'number') memoryCount.value = payload.memory_count
      // 刷新任务列表，让标题/条数跟上（新建的任务这时才会出现在列表里）
      loadConversationList()
    },
    onError: text => {
      const item = messages.value[index]
      if (!item) return
      Object.assign(item, { source: 'error', message: text, plan: null, pending: false })
    },
    onClose: () => {
      streaming.value = false
      phase.value = ''
      stopStream = null
      scrollToBottom()
    },
  })
}

function stopGenerating() {
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant' && last.pending) {
    last.pending = false
    last.stopped = true
  }
  stopStream?.()
  stopStream = null
}

/* Enter 发送，但必须躲开中文输入法的组合态：
   打拼音时按回车是「确认候选词」，此时 isComposing=true（keyCode 229），
   不守护会把还没上屏的半截拼音直接发出去。 */
function onComposerEnter(event) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  send()
}

const currentConversation = computed(
  () => conversations.value.find(item => item.conversation_id === conversationId.value) || null,
)

/* 纯符号 / 标点 / 空白的标题视为无意义，回退到首条消息或「新任务」 */
function isTitleNoise(text) {
  const t = (text || '').trim()
  if (!t) return true
  const stripped = t.replace(/[\s\p{P}\p{S}]+/gu, '')
  return stripped.length < 2
}

/* 主区标题：优先列表里的标题（含重命名），否则按首条用户消息生成 */
const conversationTitle = computed(() => {
  const candidates = [
    currentConversation.value?.title,
    messages.value.find(item => item.role === 'user')?.text,
  ]
  for (const raw of candidates) {
    const text = (raw || '').trim().replace(/\s+/g, ' ')
    if (!isTitleNoise(text)) {
      return text.length > 24 ? `${text.slice(0, 24)}…` : text
    }
  }
  return '新任务'
})

/* 标题栏第二行：`记忆 8 条 · 15:02`（两者都可能没有 → 整行隐藏） */
const headSubtitle = computed(() => {
  const parts = []
  if (memoryCount.value) parts.push(`记忆 ${memoryCount.value} 条`)
  const time = formatClock(currentConversation.value?.updated_at || '')
  if (time) parts.push(time)
  return parts.join(' · ')
})

/* 当前会话若还没进列表（刚建、还没发消息），临时补一条「新任务」置顶 */
const displayConversations = computed(() => {
  const list = conversations.value
  if (conversationId.value && !list.some(item => item.conversation_id === conversationId.value)) {
    return [
      { conversation_id: conversationId.value, title: '新任务', count: memoryCount.value, updated_at: new Date().toISOString() },
      ...list,
    ]
  }
  return list
})

/* 任务列表按「今天 / 昨天 / 更早」分组 */
const taskGroups = computed(() => {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const yesterday = new Date(start)
  yesterday.setDate(start.getDate() - 1)
  const groups = [
    { label: '今天', items: [] },
    { label: '昨天', items: [] },
    { label: '更早', items: [] },
  ]
  for (const item of displayConversations.value) {
    const at = new Date(item.updated_at || item.created_at || 0)
    if (Number.isNaN(at.getTime()) || at >= start) groups[0].items.push(item)
    else if (at >= yesterday) groups[1].items.push(item)
    else groups[2].items.push(item)
  }
  return groups.filter(group => group.items.length)
})

/* 列表项第二行：`15:02 · 8 条`（今天显示时刻，更早显示「月-日」） */
function formatClock(value) {
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return ''
  const now = new Date()
  const pad = number => String(number).padStart(2, '0')
  if (at.toDateString() === now.toDateString()) {
    return `${pad(at.getHours())}:${pad(at.getMinutes())}`
  }
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
}

function taskMeta(item) {
  const parts = []
  const time = formatClock(item.updated_at || item.created_at)
  if (time) parts.push(time)
  if (item.count) parts.push(`${item.count} 条`)
  return parts.join(' · ')
}

function resetView() {
  messages.value = []
  expanded.value = new Set()
  workOpen.value = new Set()
  hoverIndex.value = -1
  lastPlan.value = null
  phase.value = ''
  draft.value = ''
  showJump.value = false
  memoryCount.value = 0
  resetWriteFlow()
}

function toggleList() {
  listHidden.value = !listHidden.value
}

/* 计划卡（工作块）默认折叠，点标题行展开参数 */
function toggleWork(index) {
  const next = new Set(workOpen.value)
  next.has(index) ? next.delete(index) : next.add(index)
  workOpen.value = next
}

/* 任务项「…」菜单：重命名 / 删除 */
async function onTaskCommand(command, item) {
  if (command === 'rename') {
    let value = ''
    try {
      const result = await ElMessageBox.prompt('给这条任务起个名字', '重命名任务', {
        inputValue: item.title === '新任务' ? '' : item.title,
        inputPlaceholder: '任务名',
        confirmButtonText: '保存',
        cancelButtonText: '取消',
        inputValidator: text => (text && text.trim() ? true : '名字不能为空'),
      })
      value = result.value.trim()
    } catch {
      return
    }
    try {
      await renameAgentConversation(item.conversation_id, value)
      await loadConversationList()
      ElMessage.success('已重命名')
    } catch (error) {
      ElMessage.error(error.message)
    }
    return
  }

  if (command === 'delete') {
    try {
      await ElMessageBox.confirm(`删除「${item.title}」？该任务的对话记忆会一并清掉。`, '删除任务', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      })
    } catch {
      return
    }
    await removeConversation(item.conversation_id)
  }
}

async function loadConversationList() {
  try {
    conversations.value = (await listAgentConversations()).conversations || []
  } catch {
    conversations.value = []
  }
}

/* 新建对话：换一个新 id（旧会话原地保留，可在「历史对话」里翻），并聚焦输入框 */
function newConversation() {
  if (streaming.value) stopGenerating()
  const id = newConversationId()
  conversationId.value = id
  persistConversation(id)
  resetView()
  nextTick(() => composerInput.value?.focus?.())
}

/* 切到某条历史会话：读它的记忆并渲染出来 */
async function switchConversation(id) {
  if (streaming.value) stopGenerating()
  conversationId.value = id
  persistConversation(id)
  resetView()
  await restoreConversation()
}

/* 删除某条历史会话 */
async function removeConversation(id) {
  try {
    await clearAgentMemory(id)
  } catch {
    /* 后端删不掉也先从列表里移除，别卡住用户 */
  }
  conversations.value = conversations.value.filter(item => item.conversation_id !== id)
  if (id !== conversationId.value) return
  const next = conversations.value[0]?.conversation_id
  if (next) await switchConversation(next)
  else newConversation()
}

/* 按 conversation_id 从后端记忆恢复对话 */
async function restoreConversation() {
  try {
    const data = await getAgentMemory(conversationId.value)
    const items = data?.messages || []
    memoryCount.value = items.length
    if (!items.length) return
    messages.value = items.map(item =>
      item.role === 'user'
        ? { role: 'user', text: item.content }
        : {
            role: 'assistant',
            source: '',
            tokens: 0,
            message: item.content,
            plan: null,
            pending: false,
            stopped: false,
            restored: true,
          },
    )
    scrollToBottom(true)
  } catch {
    memoryCount.value = 0
  }
}

/* 启动：先拉会话列表，再决定当前会话 ——
   沿用「上次那条且确有内容」的；否则接最近一条历史；都没有才新开。 */
async function initConversations() {
  await loadConversationList()
  const stored = readStoredConversation()
  const known = stored && conversations.value.some(item => item.conversation_id === stored)
  const id = known ? stored : conversations.value[0]?.conversation_id || stored || newConversationId()
  conversationId.value = id
  persistConversation(id)
  await restoreConversation()
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text || '')
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('拷贝失败，请手动选中复制')
  }
}

function regenerate(index) {
  if (streaming.value) return
  let start = -1
  for (let i = index; i >= 0; i -= 1) {
    if (messages.value[i].role === 'user') {
      start = i
      break
    }
  }
  if (start < 0) return

  const text = messages.value[start].text
  messages.value = messages.value.slice(0, start)
  expanded.value = new Set()
  send(text)
}

/* ------------------------------------------------------------------ 计划 */

/* 计划卡参数的展示名：等宽 code 里显示原始键，这里给人话 */
const PARAM_LABELS = {
  path: '目录',
  source_dir: '来源',
  target_dir: '去向',
  dest_dir: '备份到',
  min_size_mb: '体积阈值',
  extensions: '类型',
  keep: '保留策略',
}

function paramText(key, value) {
  if (key === 'min_size_mb') return `≥ ${value} MB`
  if (key === 'extensions') return `.${String(value).replace(/,/g, ' .')}`
  if (key === 'keep') return value === 'newest' ? '只保留最新一份' : '保留最旧一份为原份'
  return String(value)
}

function writeButtonLabel(plan) {
  if (!plan.write) return '执行这个计划'
  if (plan.action === 'backup') return '开始备份'
  return '出清单待确认'
}

/* 写类动作 = 会真正动文件。执行分两段：
   「准备」跑一次只读预检（垃圾扫描 / 重复扫描 / 迁移 dry-run），
   结果出来后弹「写入确认」卡，用户点确认才真正动文件。 */
const writeConfirm = ref(null)
const prepAction = ref(null)

function resetWriteFlow() {
  writeConfirm.value = null
  prepAction.value = null
}

function buildJunkItems(result) {
  const items = []
  for (const level of result?.levels || []) {
    if (level.level !== 'safe') continue       // Agent 只清安全级，谨慎级必须去对应页手动挑
    for (const item of level.items || []) {
      items.push({ path: item.path, is_dir: item.is_dir, rule: item.rule, size: item.size })
    }
  }
  return items
}

function matchExtensions(name, extensions) {
  if (!extensions?.length) return true
  return extensions.some(ext => name.toLowerCase().endsWith(`.${ext}`))
}

function buildDuplicateItems(result, plan) {
  const { min_size_mb: minSize, extensions, keep } = plan.params || {}
  const minBytes = minSize ? minSize * 1024 * 1024 : 0
  const items = []
  for (const group of result?.groups || []) {
    let redundant = group.redundant_files || []
    if (keep === 'newest' && group.files?.length > 1) {
      // 「只保留最新」：每组排除修改时间最新的那份（modified_time 是 YYYY-MM-DD HH:MM，字典序可比）
      let newest = null
      for (const file of group.files) {
        if (!newest || String(file.modified_time) > String(newest.modified_time)) newest = file
      }
      redundant = redundant.filter(file => file.path !== newest?.path)
    }
    for (const file of redundant) {
      if (minBytes && file.size < minBytes) continue
      if (!matchExtensions(file.name || file.path, extensions)) continue
      items.push({ path: file.path, is_dir: false, rule: 'duplicate', size: file.size })
    }
  }
  return items
}

function buildMigrateItems(result) {
  return (result?.plan || []).map(item => ({ source: item.source, target: item.target, size: item.size }))
}

function totalSizeText(items) {
  const total = items.reduce((sum, item) => sum + (item.size || 0), 0)
  return total > 1024 ** 3 ? `${(total / 1024 ** 3).toFixed(2)} GB` : `${(total / 1024 ** 2).toFixed(1)} MB`
}

/* 预检任务完成后按 kind 组装确认卡 */
function resolvePrep(kind, plan) {
  const result = job.job.value?.result
  if (!result) return
  let items = []
  let title = ''
  let starter = null
  let destructive = true

  if (kind === 'junk_clean') {
    items = buildJunkItems(result)
    title = `清理垃圾文件 · ${items.length} 项 · 约 ${totalSizeText(items)}`
    starter = () => startClean({ items, mode: 'isolate' })
  } else if (kind === 'duplicate_clean') {
    items = buildDuplicateItems(result, plan)
    title = `清理重复文件 · ${items.length} 个副本 · 约 ${totalSizeText(items)}`
    starter = () => startClean({ items, mode: 'isolate' })
  } else if (kind === 'migrate_execute') {
    items = buildMigrateItems(result)
    title = `执行搬移 · ${items.length} 项 · 约 ${totalSizeText(items)}`
    destructive = false                              // 有完整校验与隔离区兜底，但仍是真搬
    starter = () => startMigrate({
      source_dir: plan.params.source_dir,
      target_dir: plan.params.target_dir,
      dry_run: false,
      mode: 'move',
      items,
      excludes: excludes.value,
    })
  }

  if (!items.length || !starter) {
    ElMessage.warning('预检没有选出可处理的项目，什么都不会动')
    prepAction.value = null
    return
  }
  writeConfirm.value = { kind, title, destructive, items, starter }
  scrollToBottom(true)
}

/* 预检任务结束（成功/失败/取消）都收掉 prep 状态 */
watch(() => job.job.value?.status, (status, prev) => {
  if (!prepAction.value) return
  if (['done', 'failed', 'cancelled'].includes(status) && !['done', 'failed', 'cancelled'].includes(prev)) {
    const { kind, plan } = prepAction.value
    prepAction.value = null
    if (status === 'done') resolvePrep(kind, plan)
  }
})

function confirmWrite() {
  const confirm = writeConfirm.value
  if (!confirm) return
  writeConfirm.value = null
  job.run(confirm.starter)
}

function cancelWrite() {
  writeConfirm.value = null
  ElMessage.info('已取消，没有动任何文件')
}

async function executePlan(plan) {
  const params = plan.params || {}
  const base = { excludes: excludes.value }

  // 只读档拦写类计划：在入口说明，不等任务跑到一半才失败
  if (plan.write && permissionMode.value === 'readonly') {
    ElMessage.warning('当前是只读档，写类操作不可用；切到「标准」档再试')
    return
  }

  let starter = null
  if (plan.action === 'scan') starter = () => startScan({ ...base, path: params.path })
  if (plan.action === 'tree') starter = () => startTree({ ...base, path: params.path, depth: 2 })
  if (plan.action === 'duplicate_scan') starter = () => startDuplicateScan({ ...base, path: params.path })
  if (plan.action === 'junk_scan') starter = () => startJunkScan({ ...base, path: params.path })
  if (plan.action === 'migrate_plan') {
    starter = () => startMigrate({
      source_dir: params.source_dir,
      target_dir: params.target_dir,
      dry_run: true,
      mode: 'move',
      min_size: params.min_size_mb ? params.min_size_mb * 1024 * 1024 : 0,
      include_ext: params.extensions,
      ...base,
    })
  }

  // 写类：先跑只读预检，出清单再二次确认
  if (plan.action === 'junk_clean' || plan.action === 'duplicate_clean' || plan.action === 'migrate_execute') {
    const kind = plan.action
    if (jobRunning.value) {
      ElMessage.warning('有任务正在运行，等它结束再下计划')
      return
    }
    prepAction.value = { kind, plan }
    const prepStarter = kind === 'migrate_execute'
      ? () => startMigrate({
          source_dir: params.source_dir,
          target_dir: params.target_dir,
          dry_run: true,
          mode: 'move',
          min_size: params.min_size_mb ? params.min_size_mb * 1024 * 1024 : 0,
          include_ext: params.extensions,
          ...base,
        })
      : kind === 'junk_clean'
        ? () => startJunkScan({ ...base, path: params.path })
        : () => startDuplicateScan({ ...base, path: params.path })
    const started = await job.run(prepStarter)
    if (!started) prepAction.value = null
    else ElMessage.success('预检已启动，出清单后确认才会动文件')
    return
  }

  // 备份是纯复制、不删任何东西，计划卡上的「执行」就是确认
  if (plan.action === 'backup') {
    starter = () => startBackup({ source_dir: params.source_dir, dest_dir: params.dest_dir, ...base })
  }

  if (!starter) {
    ElMessage.warning('这个计划暂时不能直接执行')
    return
  }

  const started = await job.run(starter)
  if (started) {
    ElMessage.success('已在本地创建任务')
    scrollToBottom(true)
  }
}

async function saveAsRecipe(msg) {
  const plan = msg?.plan || lastPlan.value
  if (!plan) return

  const index = messages.value.indexOf(msg)
  let name = plan.label || '未命名配方'
  for (let i = (index < 0 ? messages.value.length - 1 : index); i >= 0; i -= 1) {
    if (messages.value[i].role === 'user') {
      name = messages.value[i].text
      break
    }
  }

  try {
    await saveRecipe({ name, message: name, action: plan })
    ElMessage.success('已保存为配方')
    loadRecipes()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function removeRecipe(index) {
  try {
    await deleteRecipe(index)
    loadRecipes()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function loadRecipes() {
  try {
    recipes.value = (await getRecipes()).recipes || []
  } catch {
    recipes.value = []
  }
}

/* ------------------------------------------------------------ 模型配置 */

async function loadSettings() {
  try {
    settings.value = await getAgentSettings()
  } catch {
    settings.value = null
  }
}

async function reloadStatus() {
  try {
    status.value = await getAgentStatus()
  } catch {
    status.value = { configured: false }
  }
  await loadSettings()
  await loadSafety()
}

async function loadSafety() {
  try {
    safety.value = await getSafetySettings()
  } catch {
    safety.value = null
  }
}

/** 切档位。「完全访问」必须过一道确认，并把 confirm 传给后端（两边都拦）。 */
async function choosePermission(item) {
  // 点当前档位：给一句提示并收起弹层，别让用户以为「点了没反应」
  if (item.id === permissionMode.value) {
    closePermPopover()
    ElMessage.info(`当前已经是「${permissionLabel.value}」档`)
    return
  }

  if (item.id === 'full') {
    try {
      await ElMessageBox.confirm(
        '完全访问允许改动桌面与系统目录。删除仍先进隔离区，任务记录里会留下当时的档位。确认切换？',
        '切换到「完全访问」',
        { type: 'warning', confirmButtonText: '确认切换', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  try {
    safety.value = await saveSafetySettings({ mode: item.id, confirm: item.id === 'full' })
    ElMessage.success(`已切换到「${safety.value.label}」`)
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    // 无论成败都收起弹层：留在原地会挡住输入框，也让人以为没生效
    closePermPopover()
  }
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
    ElMessage.success('已清除 API Key，配方与任务记录不受影响')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

/* 点配方只填入输入框，不直接发送——给用户一次确认机会 */
function useRecipe(recipe) {
  draft.value = recipe.message || recipe.name || ''
}

function insertDrive(root) {
  draft.value = root
}

/* 窄屏自动收起任务列表（不自动展开，尊重用户手动展开） */
function onResize() {
  if (window.innerWidth < 1280) listHidden.value = true
}

onMounted(async () => {
  onResize()
  window.addEventListener('resize', onResize)
  await loadConfig().catch(() => {})
  await reloadStatus()
  loadRecipes()
  initConversations()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="agent-page">
    <div class="agent-workspace">
      <!-- 任务列表：常驻左栏，可收起（窄屏自动收起） -->
      <aside v-show="!listHidden" class="task-panel">
        <div class="task-panel__head">
          <span class="task-panel__title">任务</span>
          <span v-if="conversations.length" class="task-panel__count">{{ conversations.length }}</span>
          <span class="task-panel__spacer"></span>
          <button type="button" class="task-panel__collapse" title="收起列表" @click="toggleList">
            <el-icon :size="15"><DArrowLeft /></el-icon>
          </button>
        </div>

        <button type="button" class="task-new" @click="newConversation">
          <el-icon :size="14"><Plus /></el-icon>新建任务
        </button>

        <div class="task-list">
          <template v-for="group in taskGroups" :key="group.label">
            <div class="task-group__label">{{ group.label }}</div>
            <div
              v-for="item in group.items"
              :key="item.conversation_id"
              class="task-item"
              :class="{ 'is-active': item.conversation_id === conversationId }"
              @click="switchConversation(item.conversation_id)"
            >
              <div class="task-item__body">
                <span class="task-item__title">{{ item.title }}</span>
                <span class="task-item__meta">{{ taskMeta(item) }}</span>
              </div>
              <el-dropdown trigger="click" placement="bottom-end" @command="command => onTaskCommand(command, item)">
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
          <div v-if="!taskGroups.length" class="task-list__empty">
            <el-icon :size="22"><ChatLineSquare /></el-icon>
            <span>还没有任务</span>
          </div>
        </div>
      </aside>

      <!-- 对话主区 -->
      <section class="agent-shell">
        <header class="agent-head">
          <button v-if="listHidden" type="button" class="agent-head__icon" title="展开任务列表" @click="toggleList">
            <el-icon :size="16"><DArrowRight /></el-icon>
          </button>
          <div class="agent-head__meta">
            <span class="agent-head__title" :title="conversationTitle">{{ conversationTitle }}</span>
            <span v-if="headSubtitle" class="agent-head__sub">{{ headSubtitle }}</span>
          </div>
        </header>

        <!-- 空态：标记 + 大标题居中，输入框落在下方 -->
        <div v-if="!messages.length" class="agent-hero">
          <AgentGlyph class="agent-hero__mark" :size="64" />
          <h1 class="agent-hero__title">你想让 <span class="agent-hero__brand">Atlas</span> 做什么？</h1>
        </div>

      <!-- 对话态 -->
      <div v-else ref="scroller" class="chat-scroll" @scroll="onScroll">
        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="message"
          :class="`is-${msg.role}`"
          @mouseenter="hoverIndex = index"
          @mouseleave="hoverIndex = -1"
        >
          <template v-if="msg.role === 'user'">
            <div class="message-text">{{ msg.text }}</div>
          </template>

          <template v-else>
            <div
              class="message-text markdown"
              :class="{ 'is-collapsed': isCollapsed(index) }"
              v-html="renderMarkdown(msg.message)"
            ></div>
            <button v-if="isLong(index)" type="button" class="expand-btn" @click="toggleExpand(index)">
              {{ isCollapsed(index) ? '展开全文' : '收起' }}
            </button>

            <div v-if="msg.plan && msg.plan.action !== 'none'" class="work" :class="{ 'is-write': msg.plan.write }">
              <button type="button" class="work__head" @click="toggleWork(index)">
                <el-icon :size="13" class="work__caret" :class="{ 'is-open': workOpen.has(index) }">
                  <ArrowRight />
                </el-icon>
                <strong>{{ msg.plan.label || msg.plan.action }}</strong>
                <code>{{ msg.plan.action }}</code>
                <span v-if="msg.plan.write" class="work__write-tag">写入</span>
                <span class="work__spacer"></span>
                <span class="work__hint">{{ workOpen.has(index) ? '收起' : '展开' }}</span>
              </button>
              <div v-show="workOpen.has(index)" class="plan-params">
                <span v-for="(value, key) in msg.plan.params || {}" :key="key">
                  {{ PARAM_LABELS[key] || key }}：{{ paramText(key, value) }}
                </span>
              </div>
              <div class="plan-actions">
                <el-button
                  size="small"
                  :type="msg.plan.write ? 'warning' : 'primary'"
                  :icon="Promotion"
                  :disabled="msg.plan.write && permissionMode === 'readonly'"
                  @click="executePlan(msg.plan)"
                >
                  {{ writeButtonLabel(msg.plan) }}
                </el-button>
                <el-button size="small" plain :icon="Star" @click="saveAsRecipe(msg)">存成配方</el-button>
                <span v-if="msg.plan.write && permissionMode === 'readonly'" class="plan-actions__note">
                  只读档不可执行写操作
                </span>
              </div>
            </div>

            <div v-if="msg.pending" class="message-phase">
              <span class="message-caret"></span>{{ phase || '正在生成…' }}
            </div>
          </template>

          <!-- 消息操作行：hover 才出现（含来源 / token 元信息） -->
          <div class="message-actions" :class="{ 'is-visible': hoverIndex === index && !msg.pending }">
            <span v-if="msg.role === 'assistant'" class="message-actions__meta">
              <template v-if="msg.restored">来自记忆</template>
              <template v-else>{{ sourceLabel(msg) }} · {{ msg.tokens ?? 0 }} token</template>
              <template v-if="msg.stopped"> · 已停止</template>
            </span>
            <button type="button" @click="copyText(msg.role === 'user' ? msg.text : msg.message)">复制</button>
            <template v-if="msg.role === 'assistant'">
              <button type="button" :disabled="streaming" @click="regenerate(index)">重新生成</button>
              <button
                v-if="msg.plan && msg.plan.action !== 'none'"
                type="button"
                @click="saveAsRecipe(msg)"
              >存成配方</button>
            </template>
          </div>
        </div>

        <!-- 运行中的任务：内联进度卡（左侧主色竖条，与对话气泡区分） -->
        <div v-if="jobRunning" class="job-card">
          <JobProgress :job="jobRunning" @cancel="job.cancel" @rollback="job.rollback" />
        </div>
        <div v-else-if="jobSummary" class="job-summary">
          {{ jobSummary }}
          <button type="button" @click="$router.push('/jobs')">查看 →</button>
        </div>

        <!-- 写入确认卡：写类计划预检出清单后出现，点确认才真正动文件 -->
        <div v-if="writeConfirm" class="write-confirm">
          <div class="write-confirm__head">
            <el-icon :size="15"><WarningFilled /></el-icon>
            <strong>{{ writeConfirm.title }}</strong>
          </div>
          <ul class="write-confirm__list">
            <li v-for="item in writeConfirm.items.slice(0, 4)" :key="item.path" :title="item.path">
              {{ item.path }}
            </li>
            <li v-if="writeConfirm.items.length > 4" class="is-more">…等共 {{ writeConfirm.items.length }} 项</li>
          </ul>
          <p class="write-confirm__note">
            {{ writeConfirm.destructive
              ? '所有删除项先进隔离区，可随时还原；谨慎级垃圾不会被碰。'
              : '复制校验通过后才删源；系统只读区与自保护目录不会触碰。' }}
          </p>
          <div class="write-confirm__actions">
            <el-button type="warning" size="small" @click="confirmWrite">确认执行</el-button>
            <el-button size="small" plain @click="cancelWrite">取消</el-button>
          </div>
        </div>
      </div>

      <!-- 输入区：空态贴底、对话态吸底，两种形态共用同一份结构。
           「回到底部」锚在输入区上沿（bottom: 100%），高度随输入框自适应，不再用魔法数。 -->
      <div class="composer-zone">
        <button v-if="messages.length && showJump" type="button" class="jump-bottom" @click="scrollToBottom(true)">
          <el-icon :size="14"><ArrowDown /></el-icon>回到底部
        </button>

        <div v-if="status && !status.configured" class="degraded-note">
          未配置 DEEPSEEK_API_KEY，当前仅本地规则可用
        </div>

        <!-- 示例胶囊放在输入框上方当引导条 -->
        <div v-if="!messages.length" class="examples">
          <button v-for="item in EXAMPLES" :key="item" type="button" class="example" @click="send(item)">
            {{ item }}
          </button>
        </div>

        <div class="composer">
          <el-input
            v-model="draft"
            ref="composerInput"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            resize="none"
            placeholder="随心输入"
            @keydown.enter.exact="onComposerEnter"
          />

          <div class="composer__row">
            <el-popover placement="top-start" :width="280" trigger="click">
              <template #reference>
                <button type="button" class="composer__icon" aria-label="添加配方或盘符">
                  <el-icon :size="18"><Plus /></el-icon>
                </button>
              </template>

              <div class="menu">
                <div class="menu__title">配方</div>
                <div v-if="!recipes.length" class="menu__empty">暂无配方</div>
                <div v-for="(recipe, index) in recipes" :key="index" class="menu__row">
                  <button type="button" class="menu__item" @click="useRecipe(recipe)">
                    <strong>{{ recipe.name }}</strong>
                    <small>{{ recipe.action?.action }} · {{ formatShortTime(recipe.created_at) }}</small>
                  </button>
                  <button type="button" class="menu__del" @click="removeRecipe(index)">删除</button>
                </div>

                <template v-if="driveRoots.length">
                  <div class="menu__title">盘符</div>
                  <div class="menu__drives">
                    <button
                      v-for="root in driveRoots"
                      :key="root"
                      type="button"
                      class="menu__drive"
                      @click="insertDrive(root)"
                    >{{ root }}</button>
                  </div>
                </template>
              </div>
            </el-popover>

            <!-- 当前权限档位：点开切换（「完全访问」需二次确认）
                 开合不用 v-model：受控模式下 EP 的 trigger 不再自己切换 visible，
                 会让弹层彻底打不开。改用 ref 主动收起（见 closePermPopover）。 -->
            <el-popover
              ref="permPop"
              placement="top-start"
              :width="260"
              :fallback-placements="['bottom-start', 'top-end']"
              :offset="12"
              popper-class="perm-popper"
              trigger="click"
            >
              <template #reference>
                <button type="button" class="perm-pill" :class="'is-' + permissionTone">
                  <el-icon :size="13">
                    <WarningFilled v-if="permissionMode === 'full'" />
                    <View v-else-if="permissionMode === 'readonly'" />
                    <Lock v-else />
                  </el-icon>
                  {{ permissionLabel }}
                </button>
              </template>

              <div class="panel">
                <div class="panel__title">权限档位</div>
                <button
                  v-for="item in permissionModes"
                  :key="item.id"
                  type="button"
                  class="panel__option"
                  :class="{ 'is-active': item.id === permissionMode }"
                  @click="choosePermission(item)"
                >
                  <span class="panel__option-main">
                    <span class="panel__option-label">{{ item.label }}</span>
                    <span class="panel__option-desc">{{ item.desc }}</span>
                  </span>
                  <el-icon v-if="item.id === permissionMode" :size="13"><Check /></el-icon>
                </button>
                <ul class="panel__guards">
                  <li v-for="line in permissionGuards" :key="line">{{ line }}</li>
                </ul>
              </div>
            </el-popover>

            <span class="composer__spacer"></span>

            <!-- 模型：点名字直接切 -->
            <el-popover placement="top-end" :width="300" trigger="click">
              <template #reference>
                <button
                  type="button"
                  class="model-trigger"
                  :class="{ 'is-quiet': !status?.configured }"
                  aria-label="切换模型"
                >
                  {{ modelLabel }}<span v-if="!status?.configured" class="model-dot"></span>
                  <el-icon :size="12"><ArrowDown /></el-icon>
                </button>
              </template>

              <div class="panel panel--model">
                <!-- 段一：模型 -->
                <div class="panel__section">
                  <div class="panel__title">模型</div>
                  <div class="panel__options">
                    <button
                      v-for="item in modelOptions"
                      :key="item"
                      type="button"
                      class="panel__option"
                      :class="{ 'is-active': item === modelLabel }"
                      @click="submitModel(item)"
                    >
                      <span class="panel__option-name">{{ item }}</span>
                      <el-icon v-if="item === modelLabel" :size="13"><Check /></el-icon>
                    </button>
                  </div>

                  <button
                    v-if="!showCustomModel"
                    type="button"
                    class="panel__entry"
                    @click="openCustomModel"
                  >
                    <el-icon :size="13"><Plus /></el-icon>使用自定义模型
                  </button>
                  <el-input
                    v-else
                    ref="customModelInput"
                    v-model="form.model"
                    size="small"
                    autocomplete="off"
                    placeholder="输入模型名，回车应用"
                    @keydown.enter="submitModel(form.model)"
                  />
                  <div v-if="!status?.configured" class="panel__hint">
                    未配置 API Key，切了模型也只会走本地规则
                  </div>
                </div>

                <div class="panel__divider"></div>

                <!-- 段二：API Key -->
                <div class="panel__section">
                  <div class="panel__title">API Key</div>
                  <div class="panel__status">
                    <span class="panel__badge" :class="status?.configured ? 'is-ok' : 'is-off'">
                      {{ status?.configured ? '已配置' : '未配置' }}
                    </span>
                    <code v-if="status?.configured" class="panel__mask">{{ settings?.masked || '••••••' }}</code>
                    <small v-if="settings?.source === 'env'" class="panel__src">环境变量</small>
                    <small v-else-if="settings?.source === 'settings'" class="panel__src">本地设置</small>
                  </div>
                  <div class="panel__keyrow">
                    <el-input
                      v-model="form.key"
                      type="password"
                      show-password
                      size="small"
                      autocomplete="new-password"
                      placeholder="sk-…"
                      @keydown.enter="submitKey"
                    />
                    <el-button size="small" type="primary" :loading="saving" @click="submitKey">保存</el-button>
                  </div>
                  <div v-if="settings?.source === 'settings'" class="panel__actions">
                    <el-button size="small" text @click="clearKey">清除 API Key</el-button>
                  </div>
                </div>
              </div>
            </el-popover>

            <el-button
              v-if="streaming"
              circle
              class="send-button stop-button"
              aria-label="停止生成"
              @click="stopGenerating"
            >
              <span class="stop-square"></span>
            </el-button>
            <el-button
              v-else
              circle
              type="primary"
              class="send-button"
              :icon="ArrowUpBold"
              :disabled="!draft.trim()"
              aria-label="发送"
              @click="send()"
            />
          </div>
        </div>
      </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 整页：左「任务列表」+ 右「对话主区」。外层 .workspace-content 在 Agent 页是 is-flush（无内边距），
   所以这里直接铺满视口高度 */
.agent-workspace {
  /* 局部尺寸 token：值保持与 v22 审查前完全一致。 */
  --agent-radius-xs: 5px;
  --agent-radius-sm: 6px;
  --agent-radius-md: 7px;
  --agent-radius-control: 8px;
  --agent-radius-card: 9px;
  --agent-radius-panel: 10px;
  --agent-radius-lg: 14px;
  --agent-fs-xs: 10.5px;
  --agent-fs-sm: 11px;
  --agent-fs-md: 11.5px;
  --agent-fs-body-tight: 12.5px;
  --agent-fs-body-wide: 13.5px;
  --agent-fs-body: 14px;
  --agent-composer-bottom-space: 90px;
  --agent-caret-pulse: 1.1s;
  display: flex;
  height: 100vh;
}

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
  color: var(--ink-2);
  font-size: var(--fs-title);
  font-weight: 600;
}

.task-panel__count {
  color: var(--ink-4);
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
.task-new {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  width: 100%;
  height: 34px;
  margin: 8px 0 4px;
  padding: 0 10px;                 /* 与列表项文字同一左边缘 */
  color: var(--ink-2);
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
  color: var(--ink);
  background: var(--inset);        /* 白底之后，悬停才给一层极淡的底提示可点 */
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
  color: var(--ink-4);
  font-size: var(--fs-micro);
  letter-spacing: 0.02em;
}

/* 第二组起：一条极淡的分节线，把「今天 / 昨天 / 更早」分成明显的段 */
.task-group__label:not(:first-child) {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline);
}

/* 列表项：不描边、不加灰底（用户要求「这个灰底都换成白底」），当前项只用标题加深 + 加粗区分 */
.task-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: var(--agent-radius-card);
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease);
}

.task-item:hover {
  background: var(--inset);        /* 悬停才出底，静止时是纯白 */
}

/* 当前项：一层很淡的主色底（用户 2026-10-04 指定），标题同时加深加粗 */
.task-item.is-active {
  background: var(--primary-soft);
}

.task-item__body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}

.task-item__title {
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--fs-note);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-item.is-active .task-item__title {
  color: var(--ink);
  font-weight: 600;
}

.task-item__meta {
  color: var(--ink-4);
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

/* ------------------------------------------------------- 对话主区（右） */
/* 主区白、左栏灰 —— 两栏才分得开（否则面板和主区同色，只剩一根线） */
.agent-shell {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
}

/* ------------------------------------------------------------------ 空态 */
.agent-hero {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;           /* 居中对齐 */
  justify-content: flex-end;     /* 标题贴近输入框，而不是居中在一大片空白天花板 */
  gap: 20px;
  padding-bottom: var(--agent-composer-bottom-space);          /* 输入框 + composer-zone 上间距 + 透气留白 */
  min-height: 0;
}

.agent-hero__mark {
  color: var(--ink-2);
  animation: hero-rise var(--duration-slow) var(--ease) both;
}

.agent-hero__title {
  margin: 0;
  color: var(--ink);
  font-size: clamp(30px, 3vw, 42px);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.2;
  text-align: center;
  animation: hero-rise var(--duration-slow) var(--ease) var(--duration-micro) both;
}

/* 标题统一成一个颜色（此前「Atlas」单独用品牌色，深色主题下和白标题撞色）。
   想恢复品牌色点染，把下面的 color 改回 var(--primary) 即可。 */
.agent-hero__brand {
  color: inherit;
  font-weight: 600;
}

@keyframes hero-rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}

/* ---------------------------------------------------------------- 输入区 */
.composer-zone {
  position: relative;            /* 「回到底部」以此为锚点上浮 */
  width: min(824px, 100%);
  margin: 0 auto;
  padding-top: 20px;
}

.composer {
  display: grid;
  gap: 8px;
  padding: 16px 16px 14px;
  background: var(--surface);
  border: 1px solid var(--hairline-strong);
  border-radius: 26px;
  box-shadow: var(--sh-2);
}

/* 悬停不再改投影：原先从 --sh-2 跳到 --sh-3 是模态级的重量，一颗常驻输入框承受不起。
   静止的 --sh-2 已经把它从页面上托起来了。 */

/* 聚焦态不做任何视觉变化：不给主色描边、不给光晕、也不加深边框。
   光标本身就是「已聚焦」的反馈。 */

/* 内层 textarea 必须彻底隐形：可视的输入面是整颗胶囊。
   global.css 里 Element Plus 的聚焦样式带 !important（`box-shadow: var(--sh-focus) !important`），
   普通覆写压不住，这里必须同样用 !important，否则聚焦时会出现「框里还有一个框」。 */
.composer :deep(.el-textarea__inner),
.composer :deep(.el-textarea__inner:hover),
.composer :deep(.el-textarea__inner:focus) {
  padding: 2px 6px;
  color: var(--ink);
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
  font-size: var(--agent-fs-body);
  line-height: 1.6;
}

.composer__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.composer__icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  padding: 0;
  color: var(--ink-2);
  background: transparent;
  border: 0;
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease);
}

.composer__icon:hover {
  color: var(--ink);
}

.composer__spacer {
  flex: 1;
}

/* 键盘可达性：默认的 UA 描边（点击后经 Escape 返回焦点时会露出来）与本站控件不搭，
   统一换成 1px 主色细环。只在键盘导航时出现，鼠标点击不触发。 */
.composer__icon:focus-visible,
.perm-pill:focus-visible,
.model-trigger:focus-visible,
.send-button:focus-visible,
.jump-bottom:focus-visible {
  outline: 1px solid var(--primary);
  outline-offset: 2px;
}

/* 权限档位胶囊：和「+」同一行，位置对齐参考图 */
.perm-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  margin-left: 2px;
  color: var(--ink-3);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  font-size: var(--agent-fs-body-tight);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease), border-color var(--duration-quick) var(--ease);
}

.perm-pill:hover {
  background: var(--surface-2);
}

/* 标准：透明底 + 细边，日常态不再像标签一样抢眼 */
.perm-pill.is-quiet {
  color: var(--ink-3);
  border-color: var(--hairline);
}

.perm-pill.is-quiet:hover {
  background: var(--surface-2);
}

/* 只读：用中性灰底 + 细边，和「标准」拉开一档（否则两档看起来一样） */
.perm-pill.is-locked {
  color: var(--ink-2);
  background: var(--surface-2);
}

.perm-pill.is-locked:hover {
  background: var(--inset);
}

/* 完全访问：整条胶囊用警告色，和参考图一样醒目 */
.perm-pill.is-warning {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

.perm-pill.is-warning:hover {
  background: var(--warning-soft);
  filter: brightness(0.97);
}

/* 模型名：现在是个按钮，点开切模型。
   文字用 --ink-3 而不是 --ink-4 —— 它承载信息（当前用的是哪个模型），
   按全站规则「承载信息的文字最低降到 --ink-3」。 */
.model-trigger {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 30px;
  padding: 0 8px 0 10px;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: var(--radius-pill);
  font-size: var(--agent-fs-md);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.model-trigger:hover {
  color: var(--ink-2);
  background: var(--surface-2);
}

/* 未配置 API Key：模型名右侧加一个小灰点提示，不再整体变灰 */
.model-dot {
  width: 5px;
  height: 5px;
  margin-left: 3px;
  border-radius: 50%;
  background: var(--ink-4);
  flex: none;
}

.send-button {
  flex: none;
  width: 32px;
  height: 32px;
}

/* 生成中：发送键变停止键 */
.stop-button {
  color: var(--ink-2);
  background: var(--inset);
  border-color: transparent;
}

.stop-square {
  display: block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: currentColor;
}

/* 发送按钮禁用态：从浅蓝换成中性灰，否则空输入时几乎看不见 */
.composer :deep(.el-button.is-disabled),
.composer :deep(.el-button.is-disabled:hover) {
  color: var(--surface);
  background: var(--ink-4);
  border-color: transparent;
}

.degraded-note {
  margin-bottom: 8px;
  padding: 7px 12px;
  color: var(--warning-ink);
  background: var(--warning-soft);
  border-radius: var(--agent-radius-panel);
  font-size: 12px;
  text-align: center;
}

.examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  padding-bottom: 10px;
}

.example {
  padding: 8px 14px;
  color: var(--ink-2);
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  font-size: 13px;
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), border-color var(--duration-quick) var(--ease);
}

.example:hover {
  color: var(--primary);
  border-color: var(--primary);
}

/* ------------------------------------------------------------ 主区标题栏 */
/* 极简无框：不画分隔线，靠留白与对话区分隔；标题 + 一行小字元信息，右侧只一个圆形＋ */
.agent-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  width: min(824px, 100%);
  margin: 0 auto;
  padding: 18px 0 8px;
}

.agent-head__icon {
  flex: none;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-control);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.agent-head__icon:hover {
  color: var(--ink);
  background: var(--inset);
}

.agent-head__meta {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.agent-head__title {
  overflow: hidden;
  color: var(--ink);
  font-size: var(--fs-title);
  font-weight: 600;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 元信息（记忆条数 · 更新时间）降级到标题下方，不再和标题平级抢位 */
.agent-head__sub {
  color: var(--ink-3);
  font-size: var(--fs-micro);
  font-variant-numeric: tabular-nums;
}

/* --------------------------------------------- 计划卡（可折叠「工作块」） */
.work {
  margin-top: 12px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--agent-radius-lg);
  box-shadow: var(--sh-1);
}

.work__head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 2px 0;
  background: transparent;
  border: 0;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.work__caret {
  flex: none;
  color: var(--ink-3);
  transition: transform var(--duration-quick) var(--ease);
}

.work__caret.is-open {
  transform: rotate(90deg);
}

.work__head strong {
  color: var(--ink);
  font-size: var(--agent-fs-body-wide);
  font-weight: 600;
}

.work__head code {
  padding: 2px 8px;
  color: var(--ink-3);
  background: var(--inset);
  border-radius: var(--agent-radius-sm);
  font-family: var(--font-mono);
  font-size: var(--agent-fs-sm);
}

.work__spacer {
  flex: 1;
}

.work__hint {
  flex: none;
  color: var(--ink-4);
  font-size: var(--fs-micro);
}

/* ---------------------------------------------------------------- 对话区 */
/* 滚动条：v14 给导航定的「彻底隐藏」不适用于聊天区 —— 长对话需要知道自己在哪。
   这里保留 6px 悬浮胶囊：轨道透明，静止低调、悬停加深，不占视觉。 */
.chat-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: min(824px, 100%);
  margin: 0 auto;
  padding: 8px 0 26px;              /* 底部留出悬浮操作行的位置 */
  scrollbar-width: thin;
  scrollbar-color: var(--hairline-strong) transparent;
}

.chat-scroll::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.chat-scroll::-webkit-scrollbar-track {
  background: transparent;
}

.chat-scroll::-webkit-scrollbar-thumb {
  background: var(--hairline-strong);
  border-radius: 999px;
}

.chat-scroll::-webkit-scrollbar-thumb:hover {
  background: var(--ink-4);
}

.message {
  position: relative;              /* 操作行以它为基准做绝对定位 */
  max-width: 92%;
  color: var(--ink);
  font-size: var(--agent-fs-body);
  line-height: 1.7;
}

/* 间距按「轮次」组织：用户 → 它的回复贴紧（同一组），换新的一问才拉开 */
.message + .message,
.message + .job-card,
.job-card + .message,
.message + .job-summary {
  margin-top: 28px;
}

.message.is-user + .message.is-assistant,
.message.is-user + .message.is-user,
.message.is-assistant + .message.is-assistant {
  margin-top: 10px;
}

/* 用户消息：中性灰气泡、右对齐（参照 Codex / ChatGPT 的用户消息；不用蓝色，避免抢戏） */
.message.is-user {
  width: fit-content;
  max-width: 80%;
  margin-left: auto;
  padding: 8px 14px;
  background: var(--inset);
  border-radius: 16px;             /* 四角统一圆角（参照用户给的气泡图，不做右下小尖角） */
  color: var(--ink);
}

.message-text {
  word-break: break-word;
  white-space: pre-wrap;
}

/* 助手的富文本回复（自绘极简 markdown） */
.markdown {
  white-space: normal;
}

.markdown :deep(p) {
  margin: 0 0 8px;
}

.markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown :deep(ul),
.markdown :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}

.markdown :deep(li) {
  margin: 3px 0;
}

.markdown :deep(strong) {
  font-weight: 600;
}

.markdown :deep(code) {
  padding: 1px 5px;
  background: var(--inset);
  border-radius: var(--agent-radius-xs);
  font-family: var(--font-mono);
  font-size: var(--agent-fs-body-tight);
}

.markdown :deep(pre) {
  overflow-x: auto;
  margin: 8px 0;
  padding: 10px 12px;
  background: var(--inset);
  border-radius: var(--agent-radius-panel);
}

.markdown :deep(pre code) {
  padding: 0;
  background: transparent;
  font-size: var(--agent-fs-body-tight);
  line-height: 1.6;
}

.markdown.is-collapsed {
  position: relative;
  max-height: 9.8em;
  overflow: hidden;
}

.markdown.is-collapsed::after {
  content: '';
  position: absolute;
  inset: auto 0 0 0;
  height: 3.2em;
  background: linear-gradient(to bottom, transparent, var(--bg));
}

.expand-btn {
  margin-top: 6px;
  padding: 0;
  color: var(--primary);
  background: transparent;
  border: 0;
  font-size: var(--agent-fs-body-tight);
  cursor: pointer;
}

.message-phase {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  color: var(--ink-3);
  font-size: var(--agent-fs-sm);
}

.message-caret {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  animation: caret-pulse var(--agent-caret-pulse) var(--ease) infinite;
}

@keyframes caret-pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.25;
  }
}

/* 元信息（来源 · token）已并入 hover 操作行，不再常驻 */
.message-actions__meta {
  color: var(--ink-3);
  font-size: var(--agent-fs-sm);
}

/* 消息操作行：hover 才出现，且脱离布局 ——
   以前它 opacity:0 却仍占 20px，每条消息凭空变高，整屏显得很空。 */
.message-actions {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 2;
  display: flex;
  gap: 14px;
  margin-top: 4px;
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--duration-quick) var(--ease);
}

.message-actions.is-visible {
  opacity: 1;
  pointer-events: auto;
}

.message.is-user .message-actions {
  left: auto;
  right: 0;
  justify-content: flex-end;
}

.message-actions button {
  padding: 0;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  font-size: 12px;
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease);
}

.message-actions button:hover:not(:disabled) {
  color: var(--primary);
}

.message-actions button:disabled {
  color: var(--ink-4);
  cursor: default;
}

@media (prefers-reduced-motion: reduce) {
  .message-caret,
  .agent-hero__mark,
  .agent-hero__title {
    animation: none;
  }
}

/* 工作块展开后的参数与操作（外壳见上方 .work） */
.plan-params {
  display: grid;
  gap: 4px;
  margin: 10px 0 12px;
}

.plan-params span {
  color: var(--ink-2);
  font-size: var(--agent-fs-body-tight);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
}

.plan-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 写类计划的「写入」徽标：语义色只出现在真的表达状态的地方 */
.work__write-tag {
  padding: 1px 7px;
  color: var(--warning-ink);
  background: var(--warning-soft);
  border-radius: var(--radius-pill);
  font-size: var(--agent-fs-xs);
}

.work.is-write {
  border-left: 2px solid var(--warning);
}

.plan-actions__note {
  color: var(--warning-ink);
  font-size: var(--agent-fs-md);
}

/* 写入确认卡：预检完成后的最后一道闸。
   警示但克制——语义色只上在边线与按钮上，正文仍是中性灰阶。 */
.write-confirm {
  max-width: 92%;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-left: 2px solid var(--warning);
  border-radius: 16px;
  box-shadow: var(--sh-1);
}

.write-confirm__head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--warning-ink);
}

.write-confirm__head strong {
  color: var(--ink);
  font-size: var(--agent-fs-body);
}

.write-confirm__list {
  display: grid;
  gap: 3px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.write-confirm__list li {
  overflow: hidden;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.write-confirm__list li.is-more {
  color: var(--ink-3);
}

.write-confirm__note {
  margin: 10px 0 0;
  color: var(--ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.write-confirm__actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

/* 进度卡与结果摘要 */
.job-card {
  padding: 14px 16px 14px 18px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-left: 2px solid var(--primary);
  border-radius: 16px;
  box-shadow: var(--sh-1);
}

.job-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ink-2);
  font-size: 13px;
}

.job-summary button {
  padding: 0;
  color: var(--primary);
  background: transparent;
  border: 0;
  font-size: 13px;
  cursor: pointer;
}

/* 回到底部 */
/* 「回到底部」挂在输入区上沿（父级 .composer-zone 是锚点）。
   原先写死 bottom: 132px，输入框 autosize 撑高后按钮会压到输入框上。 */
.jump-bottom {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 12px);
  transform: translateX(-50%);
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  color: var(--ink-2);
  background: var(--surface);
  border: 1px solid var(--hairline-strong);
  border-radius: var(--radius-pill);
  box-shadow: var(--sh-2);
  font-size: var(--agent-fs-body-tight);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), border-color var(--duration-quick) var(--ease);
}

.jump-bottom:hover {
  color: var(--primary);
  border-color: var(--primary);
}

/* ------------------------------------------------------------ 配方弹层 */
.menu {
  display: grid;
  gap: 4px;
}

.menu__title {
  color: var(--ink-4);
  font-size: var(--agent-fs-sm);
  letter-spacing: 0.06em;
}

.menu__title:not(:first-child) {
  margin-top: 8px;
}

.menu__empty {
  color: var(--ink-3);
  font-size: var(--agent-fs-body-tight);
}

/* 齿轮 / 模型名弹出的面板（沿用菜单的排版习惯） */
.panel {
  display: grid;
  gap: 8px;
  /* 弹层内容自己带一层底，不依赖 EP 的 .el-popper.is-light（少一层变量依赖） */
  background: var(--surface);
  border-radius: var(--agent-radius-panel);
}

.panel__title {
  color: var(--ink-4);
  font-size: var(--agent-fs-sm);
  letter-spacing: 0.06em;
}

.panel__status {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink-2);
  font-size: var(--agent-fs-body-tight);
  font-family: var(--font-mono);
}

.panel__status small {
  color: var(--ink-3);
  font-family: inherit;
}

.panel__option {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  color: var(--ink-2);
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-control);
  font-family: var(--font-mono);
  font-size: var(--agent-fs-body-tight);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease), color var(--duration-quick) var(--ease);
}

/* 悬停底色统一走 --inset：权限弹层与模型弹层原来一个 surface-2、一个 inset，
   同一页两套手感。现在基线就是 inset，模型弹层不必再单独覆写。 */
.panel__option:hover {
  background: var(--inset);
}

/* 选中：不用主色、不用蓝底（用户要求去蓝），改为中性浅灰底 + 文字加深，靠右侧对勾标明当前项 */
.panel__option.is-active {
  color: var(--ink);
  font-weight: 500;
  background: var(--inset);
}

.panel__option-main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.panel__option-label {
  font-family: var(--font-mono);
  font-size: var(--agent-fs-body-tight);
}

.panel__option-desc {
  font-family: var(--font-sans);
  color: var(--ink-4);
  font-size: var(--agent-fs-sm);
  line-height: 1.45;
}

.panel__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel__hint {
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  line-height: 1.5;
}

/* 权限弹层的三条底限：原先挤成一行（`；` 串起来的两行密排），读不出是三条规则。
   改为每条形排列，前置一个小圆点，间距拉开。 */
.panel__guards {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.panel__guards li {
  position: relative;
  padding-left: 11px;
  color: var(--ink-3);
  font-size: var(--agent-fs-sm);
  line-height: 1.5;
}

.panel__guards li::before {
  content: '';
  position: absolute;
  top: 7px;
  left: 0;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--ink-4);
}

/* 弹层内的分区线：模型列表与 API Key 两段之间 */
.panel__divider {
  height: 1px;
  margin: 2px 0;
  background: var(--hairline);
}

/* ------------------------------------------------ 模型弹层（.panel--model）
   改动一律收敛在这个修饰类下，避免动到复用 .panel 的权限弹层。 */
.panel--model {
  gap: 12px;
}

.panel__section {
  display: grid;
  gap: 8px;
}

.panel__options {
  display: grid;
  gap: 2px;
}

/* 选项三态：默认透明 / 悬停中性灰 / 选中蓝底蓝字——悬停不再与选中撞色 */
.panel--model .panel__option {
  align-items: center;
  min-height: 34px;
  padding: 8px 10px;
}

.panel--model .panel__option:hover {
  background: var(--inset);
}

/* 选中态统一走基线（中性灰底 + 对勾），此处不再覆盖成主色 */

.panel__option-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 自定义模型：折叠入口（次级、低权重，点了才展开输入行） */
.panel__entry {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  align-self: flex-start;
  padding: 5px 7px;
  color: var(--ink-3);
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-control);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.panel__entry:hover {
  color: var(--primary);
  background: var(--primary-soft);
}

/* API Key 状态行：状态徽标 + 掩码 + 来源 */
.panel--model .panel__status {
  gap: 8px;
  color: var(--ink-2);
}

.panel--model .panel__badge {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  font-family: var(--font-sans);
  font-size: var(--agent-fs-sm);
  line-height: 1.5;
}

.panel--model .panel__badge.is-ok {
  color: var(--success-ink);
  background: var(--success-soft);
}

.panel--model .panel__badge.is-off {
  color: var(--ink-3);
  background: var(--inset);
}

.panel--model .panel__mask {
  overflow: hidden;
  color: var(--ink-2);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.panel--model .panel__src {
  flex: none;
  margin-left: auto;
  padding: 1px 7px;
  color: var(--ink-3);
  background: var(--surface-2);
  border-radius: var(--radius-pill);
  font-family: var(--font-sans);
  font-size: var(--agent-fs-xs);
}

/* 输入 + 保存 同一行 */
.panel__keyrow {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel__keyrow :deep(.el-input) {
  flex: 1;
  min-width: 0;
}

/* 弹层里的输入框（自定义模型 / API Key）扁平化：
   去掉 global.css 的 EP 蓝色聚焦光环（3.5px 光晕），否则它看起来像「第二个选中行」。
   聚焦只留 1px 细边 + 白底，和选项行的「蓝底选中」彻底区分开。 */
.panel--model :deep(.el-input__wrapper) {
  min-height: 34px;
  border-radius: var(--agent-radius-control);
  background: var(--inset);
  box-shadow: none !important;
}

.panel--model :deep(.el-input__wrapper:hover) {
  background: var(--inset);
}

.panel--model :deep(.el-input__wrapper.is-focus) {
  background: var(--surface);
  border-color: transparent !important;
  box-shadow: none !important;
}

.menu__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.menu__item {
  flex: 1;
  min-width: 0;
  padding: 7px 9px;
  text-align: left;
  background: transparent;
  border: 0;
  border-radius: var(--agent-radius-control);
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease);
}

.menu__item:hover {
  background: var(--surface-2);
}

.menu__item strong {
  display: block;
  overflow: hidden;
  color: var(--ink);
  font-size: var(--agent-fs-body-tight);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu__item small {
  display: block;
  margin-top: 3px;
  color: var(--ink-3);
  font-size: var(--agent-fs-sm);
}

.menu__del {
  flex: none;
  padding: 4px 6px;
  color: var(--danger-ink);
  background: transparent;
  border: 0;
  font-size: 12px;
  cursor: pointer;
}

.menu__del:hover {
  text-decoration: underline;
}

.menu__drives {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.menu__drive {
  padding: 5px 11px;
  color: var(--ink-2);
  background: var(--surface-2);
  border: 0;
  border-radius: var(--radius-pill);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.menu__drive:hover {
  color: var(--primary);
  background: var(--primary-soft);
}

/* ---------------------------------------------------------------- 移动端 */
@media (max-width: 960px) {
  /* 移动端有顶栏，而顶栏高度随内容换行变化，用固定 calc 减不干净（会把输入框底部裁掉）。
     改成：页面自然滚动 + 输入区 sticky 贴底。任务列表在窄屏由 onResize 自动收起。 */
  .agent-workspace,
  .agent-shell {
    height: auto;
  }

  .agent-hero {
    justify-content: flex-end;
    padding-bottom: 60px;
    min-height: calc(100vh - 320px);
  }

  .chat-scroll {
    flex: none;
    overflow-y: visible;
  }

  .composer-zone {
    position: sticky;
    bottom: 0;
    padding: 20px 0 12px;
    background: var(--bg);
  }

  .message,
  .message.is-user {
    max-width: 88%;
  }

  /* 「回到底部」不再需要单独的移动端定位：它挂在 .composer-zone 上沿，
     而输入区在窄屏本身就是 sticky 贴底的，跟着走就对。 */
}
</style>

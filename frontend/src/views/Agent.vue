<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
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
  Setting,
  Star,
  View,
  WarningFilled,
} from '@element-plus/icons-vue'
import {
  agentChatStream,
  approveAgentTask,
  cancelJob,
  clearAgentMemory,
  deleteRecipe,
  getAgentContext,
  getAgentMemory,
  getAgentPrefs,
  getAgentSettings,
  getAgentStatus,
  getRecipes,
  getTrash,
  listAgentConversations,
  listJobs,
  observeAgentJob,
  renameAgentConversation,
  restoreTrash,
  reviseAgentPlan,
  touchAgentConversation,
  reviseAgentTask,
  rollbackJob,
  saveAgentPrefs,
  getSafetySettings,
  saveAgentSettings,
  saveRecipe,
  saveSafetySettings,
  startAgentTask,
  startBackup,
  startClean,
  startDuplicateScan,
  startJunkScan,
  startMigrate,
  startScan,
  startTree,
  startVirtual,
  startProjectScan,
} from '../api/filepilot'
import { useConfig } from '../composables/useConfig'
import { useAgentSettings } from '../composables/useAgentSettings'
import { useJob } from '../composables/useJob'
import AgentGlyph from '../components/AgentGlyph.vue'
import JobProgress from '../components/JobProgress.vue'
import PlanTree from '../components/PlanTree.vue'
import AgentTaskPanel from '../components/AgentTaskPanel.vue'
import NextSteps from '../components/NextSteps.vue'
import { escapeHtml, renderMarkdown } from '../utils/miniMarkdown'
import { formatShortTime } from '../utils/formatters'

const { config, excludes, load: loadConfig } = useConfig()
const job = useJob(() => conversationId.value)
const router = useRouter()

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
/* 「正在从记忆里恢复会话」闸门。
   欢迎空态的条件是 !messages.length，而恢复是异步的 —— 不闸住的话，
   每次进这一页都会先闪一张「你想让 Atlas 做什么？」再被真实对话替换掉。
   初始为 true：首屏在拿到结论（有历史 / 真没有）之前一律不渲染空态。
   点「新建任务」不受闸门影响 —— 那是用户主动要一张空白页。 */
const hydrating = ref(true)

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


/* 模型配置 + 权限档位的数据与持久化都在 useAgentSettings（Phase 7）。
   模板里的 settings / status / permissionLabel 等名字保持不变，
   所以这次抽取**一行模板都不用改**。 */
const {
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
  providerOptions,
  providerId,
  needsBaseUrl,
  providerError,
  baseUrlDraft,
  switchProvider,
  saveBaseUrl,
} = useAgentSettings()

/* 权限档位：显示在「+」后面，点开切换 */
/* 弹层 ref：选完档位要主动收起，否则它一直悬在输入框上方 */
const permPop = ref(null)
const closePermPopover = () => permPop.value?.hide?.()

/* ------------------------------------------------------------------ 状态感知
   第六阶段：Agent 侧栏显示「现在处于什么状态」——有几个任务在跑、当前档位、
   模型配没配、磁盘还剩多少、有没有可回滚的任务。全部本地读，零 token。 */
const agentContext = ref(null)

async function loadAgentContext() {
  try {
    agentContext.value = await getAgentContext()
  } catch {
    agentContext.value = null
  }
}

const contextChips = computed(() => {
  const ctx = agentContext.value
  if (!ctx) return []
  const chips = []
  if (ctx.running_count) {
    // 「在跑」是陈述不是请求，用中性灰；只有「待确认」才配拿主色催你
    chips.push({ id: 'running', text: `${ctx.running_count} 个任务在跑`, tone: 'quiet' })
  } else {
    chips.push({ id: 'idle', text: '空闲', tone: 'quiet' })
  }
  if (ctx.waiting_count) chips.push({ id: 'waiting', text: `${ctx.waiting_count} 个待确认`, tone: 'active' })
  if (ctx.rollback_count) chips.push({ id: 'rollback', text: `${ctx.rollback_count} 个可回滚`, tone: 'quiet' })
  const tight = (ctx.disks || []).filter(disk => disk.level !== 'ok').slice(0, 2)
  for (const disk of tight) {
    chips.push({ id: `disk-${disk.root}`, text: `${disk.root} 剩 ${disk.free_text}`, tone: 'warn' })
  }
  if (ctx.trash?.count) chips.push({ id: 'trash', text: `隔离区 ${ctx.trash.size_text}`, tone: 'quiet' })
  return chips
})

/* ---------------------------------------------------------------- 偏好记忆
   第五阶段：常用路径 / 排除标签 / 永久约束 / 常用策略。用户可直接改。 */
const prefs = ref(null)
const excludeTags = ref({})
const prefsPop = ref(null)
const prefsSaving = ref(false)
const newExcludePath = ref('')
const newHardRule = ref('')
/* 本会话「用过的目录」——自动记的，跟着会话走，不写全局偏好。
   与 prefs.common_paths（用户手设的长期常用路径）性质不同，展示时合并、来源分开。 */
const recentPaths = ref([])

async function loadPrefs() {
  try {
    const data = await getAgentPrefs(conversationId.value)
    prefs.value = data.prefs || null
    excludeTags.value = data.tags || {}
    recentPaths.value = data.recent_paths || []
  } catch {
    prefs.value = null
    recentPaths.value = []
  }
}

/* 展示用的路径清单：本会话用过的排前面，再接全局手设的，按路径去重。
   这样"这个对话去过哪"和"我长期设了哪些"合起来看，但不会互相冒充。 */
const knownPaths = computed(() => {
  const seen = new Set()
  const out = []
  const push = (path, note, session) => {
    const p = typeof path === 'string' ? path : (path?.path || '')
    if (!p || seen.has(p)) return
    seen.add(p)
    out.push({ path: p, note: note || '', session })
  }
  for (const item of recentPaths.value || []) push(item, item?.note, true)
  for (const item of prefs.value?.common_paths || []) push(item, item?.note, false)
  return out
})

const tagOptions = computed(() =>
  Object.entries(excludeTags.value).map(([name, spec]) => ({ name, note: spec.note || '' })),
)

function hasTag(name) {
  return (prefs.value?.exclude_tags || []).includes(name)
}

async function persistPrefs(patch) {
  prefsSaving.value = true
  try {
    const data = await saveAgentPrefs(patch)
    prefs.value = data.prefs || prefs.value
    ElMessage.success('偏好已保存，下次对话立即生效')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    prefsSaving.value = false
  }
}

function toggleTag(name) {
  const current = prefs.value?.exclude_tags || []
  const next = current.includes(name) ? current.filter(item => item !== name) : [...current, name]
  persistPrefs({ exclude_tags: next })
}

function addExcludePath() {
  const value = newExcludePath.value.trim()
  if (!value) return
  const current = prefs.value?.exclude_paths || []
  if (current.includes(value)) {
    ElMessage.info('这条已经在列表里了')
    return
  }
  newExcludePath.value = ''
  persistPrefs({ exclude_paths: [...current, value] })
}

function removeExcludePath(value) {
  persistPrefs({ exclude_paths: (prefs.value?.exclude_paths || []).filter(item => item !== value) })
}

function addHardRule() {
  const value = newHardRule.value.trim()
  if (!value) return
  const current = prefs.value?.hard_rules || []
  newHardRule.value = ''
  persistPrefs({ hard_rules: [...current, { text: value, path: '', action: '*' }] })
}

function removeHardRule(index) {
  persistPrefs({ hard_rules: (prefs.value?.hard_rules || []).filter((_, i) => i !== index) })
}

function clearAllPrefs() {
  persistPrefs({ common_paths: [], exclude_tags: [], exclude_paths: [], hard_rules: [], clean_strategies: [] })
}

/** 点常用策略：把当时那句话放回输入框，给用户一次确认机会再发 */
function useStrategy(item) {
  draft.value = item.message || item.action || ''
  prefsPop.value?.hide?.()
  nextTick(() => composerInput.value?.focus?.())
}

const LONG_TEXT = 320
/* 后端 /api/settings/agent 不可用时的兜底显示 */
const MODEL_OPTIONS_FALLBACK = ['deepseek-flash', 'deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat', 'deepseek-reasoner']

// 只在后端**没给这个字段**时兜底（旧进程）。
// 后端明确返回空数组 = 当前服务商的模型名未知（自定义网关），
// 这时不该硬塞一排 deepseek 的名字 —— 用户点了必然 404。
const modelOptions = computed(() => {
  const fromServer = settings.value?.model_options
  return Array.isArray(fromServer) ? fromServer : MODEL_OPTIONS_FALLBACK
})

let stopStream = null

/* 示例胶囊：只留 4 句、正好排满一行。
   6 句会断成「5 + 1」（最后一句孤零零占第二行），既不协调又占掉两行高度。
   删掉的是最长的迁移示例与语义重复的那句——迁移在侧栏「跨盘迁移」里能进。 */
/* 空态的四个入口。给「第一次进来不知道该干什么」一个具体起点，
   note 写的是**点下去会发生什么**，不是宣传语 —— 后面那句"只读"要让用户先看到。 */
const HERO_ACTIONS = [
  { label: '清理垃圾', note: '先列清单确认，确认后即彻底删除', prompt: '清理 C 盘垃圾' },
  { label: '找重复文件', note: '按内容分组，每组只留一份', prompt: '找出 D 盘重复文件并只留最新' },
  { label: '看看磁盘占用', note: '只读扫描，不删任何东西', prompt: '扫描 D 盘' },
  { label: '备份目录', note: '打成 ZIP，跳过依赖与构建产物', prompt: '备份 D:\\资料 到 E:\\备份' },
]

const driveRoots = computed(() => (config.value?.drives || []).map(letter => `${letter}:\\`))

const jobRunning = computed(() => {
  const item = job.job.value
  return item && ['pending', 'running', 'waiting'].includes(item.status) ? item : null
})

const jobSummary = computed(() => {
  const item = job.job.value
  if (!item || ['pending', 'running', 'waiting'].includes(item.status)) return ''
  const raw = item.result?.summary
  // summary 可能是字符串（旧任务）也可能是对象（agent_task 的执行总结）
  const summary = typeof raw === 'string' ? raw : raw?.message || ''
  if (item.status === 'done') return summary ? `已完成 · ${summary}` : '任务结果已生成'
  if (item.status === 'cancelled') return '任务已取消'
  return '任务执行失败'
})

/* ------------------------------------------------------------ 多步骤计划树
   第三 / 七阶段：目标型指令走 agent_task，一个任务卡从分析一路走到执行。
   「等待确认」不是终态——同一个 job 在确认后继续跑，撤销也是回滚同一个 job。 */

const jobIsAgentTask = computed(() => job.job.value?.type === 'agent_task')
const treeOpen = ref(true)

/** 正在重算的是哪一条草稿（-1 表示没有）。草稿还没建任务，重算走 /agent/plan。 */
const draftRevising = ref(-1)
const treeSteps = computed(() => job.job.value?.steps || [])
const treeProposal = computed(() => job.job.value?.proposal || {})
const treeIntent = computed(() => job.job.value?.intent || {})
const treeEstimate = computed(() => job.job.value?.result?.proposal || {})
const treeBusy = computed(() => Boolean(jobRunning.value))
const treeWaiting = computed(() => job.job.value?.status === 'waiting')

/** 消息里带的计划树：还没开始跑，只是先让用户看到会走哪几步 */
function stepsOf(msg) {
  return msg.steps || []
}

/** 这条消息是不是多步骤方案（只有多步骤才显示计划树，单步仍用原来的计划卡） */
function isMultiStep(msg) {
  if (!msg.plan || msg.plan.action === 'none') return false
  return stepsOf(msg).length > 1
}

function intentOf(msg) {
  return msg.intent || msg.plan?.intent || {}
}

function toggleTreeOpen() {
  treeOpen.value = !treeOpen.value
}

/**
 * 找后端「正在跑的 Agent 任务」，并带出它属于哪个对话。
 *
 * 为什么要查后端、而不是只看本会话的 `jobRunning`：
 * 任务卡按会话显示，但**「一次只跑一个 Agent 任务」是全局限制** ——
 * 只看本会话的话，切个会话就能再起一个，限制等于没有。
 * 走 `/jobs` 还能查到**不是本页启动**的任务（比如刷新过页面），
 * 并且能拿到它的 `params.conversation_id`，把警告说清楚是谁在跑。
 */
/**
 * 认领「本会话停在等待确认的 Agent 任务」。
 *
 * `useJob` 的 `ACTIVE_JOBS` 只是内存里的一个 Map —— **刷新页面 / 重开窗口就空了**。
 * 而一个停在 `waiting` 的任务在后端还活着，前端却再也接不回来：
 * 表现就是「任务从界面上消失了，也没法点继续」（2026-10-11 用户报的）。
 * 所以槽位为空时主动问一次后端：本会话有没有 waiting 的 agent_task，有就接回来。
 */
async function adoptWaitingTask() {
  if (job.job.value) return // 手上已经有任务了，别抢
  const cid = conversationId.value
  if (!cid) return
  try {
    const data = await listJobs(50)
    const hit = (data?.jobs || []).find(item => item.type === 'agent_task'
      && item.status === 'waiting'
      && (item.params?.conversation_id || '') === cid)
    if (hit) job.watch(hit.id)
  } catch {
    /* 后端没起来就算了，别拦着页面 */
  }
}

async function findRunningAgentTask() {
  try {
    const data = await listJobs(50)
    const list = data?.jobs || []
    const hit = list.find(item => item.type === 'agent_task'
      && ['pending', 'running', 'waiting'].includes(item.status))
    if (!hit) return null
    const cid = hit.params?.conversation_id || ''
    const conv = conversations.value.find(item => item.conversation_id === cid)
    // 会话标题就是用户那条原话，可能是整段路径 —— 提示里只留一截，够认出来就行
    const raw = (conv?.title || '').trim()
    const name = raw ? (raw.length > 16 ? `${raw.slice(0, 16)}…` : raw) : '另一个对话'
    return { name, conversationId: cid, isCurrent: cid === conversationId.value }
  } catch {
    return null // 查不到就别拦着用户
  }
}

/** 启动多步骤任务：先只跑只读分析段 */
async function startPlanTree(msg) {
  const busy = await findRunningAgentTask()
  if (busy) {
    ElMessage.warning(busy.isCurrent
      ? '本对话有任务正在运行，等它结束再下新计划'
      : `「${busy.name}」里有个任务正在运行，等它结束再下新计划`)
    return
  }
  const plan = msg.plan || {}
  await job.run(() => startAgentTask({
    intent: plan.intent || {},
    params: plan.params || {},
    steps: stepsOf(msg),
    title: plan.goal || plan.label || 'Agent 任务',
    // 带上会话 id：后端要把「用过的目录」记进本会话的最近路径，
    // 而不是全局偏好 —— 否则新对话会"记得"别的对话逛过哪儿。
    conversation_id: conversationId.value,
  }))
  loadAgentContext()
}

/** 永久删除确认里最多列几条路径 —— 再多弹窗就装不下了，其余只报总数 */
const CONFIRM_PREVIEW_LIMIT = 5

/** 拼「永久删除」确认框的正文：数量 / 体积 / 前几条清单 / 不可恢复。全部转义后拼。 */
function permanentConfirmBody(items, count, sizeText, intro) {
  const list = items.slice(0, CONFIRM_PREVIEW_LIMIT)
    .map(item => `<div class="atlas-confirm__item">${escapeHtml(item.path || item.name || '')}</div>`)
    .join('')
  const more = items.length > CONFIRM_PREVIEW_LIMIT
    ? `<div class="atlas-confirm__item">…等共 ${items.length} 项</div>`
    : ''
  const listing = list
    ? `<div class="atlas-confirm__list">${list}${more}</div>`
    : ''

  return `<div class="atlas-confirm">`
    + `<div>${escapeHtml(intro)}</div>`
    + `<div class="atlas-confirm__meta">共 ${count} 项 · ${escapeHtml(sizeText)}</div>`
    + listing
    + `<div class="atlas-confirm__danger">此操作不可恢复，删了就没有了。</div>`
    + `</div>`
}

/** 确认方案：在同一个任务里继续执行段（写权限在后端 approve 处再拦一次） */
async function approvePlanTree() {
  const current = job.job.value
  if (!current?.id) return
  const proposal = current.proposal || {}
  const action = current.action || proposal.action || ''
  const isClean = ['junk_clean', 'duplicate_clean', 'similar_clean', 'large_clean'].includes(action)
  const isPurge = action === 'trash_purge'
  const items = proposal.items || []
  const count = proposal.item_count || items.length || 0
  const size = proposal.freed_text || '未知'

  let body
  let title = '确认执行方案'
  let confirmText = '确认执行'

  if (isPurge || isClean) {
    // ★ 这两类的执行结果都是**物理删除**（先进隔离区，随即立刻清空同一批）。
    //   所以按钮必须写「永久删除」，不能只写「确认执行」——用户点的是不可逆的那一下。
    title = '永久删除（不可恢复）'
    confirmText = count ? `永久删除 ${count} 项` : '永久删除'
    body = permanentConfirmBody(
      items,
      count,
      size,
      isPurge
        ? '将从隔离区永久删除以下条目：'
        : '将清理以下文件（先进隔离区，随即立刻清空同一批）：',
    )
  } else {
    body = `即将处理 ${count} 项，预计释放 ${size}。将复制并校验后删除源文件，全程有兜底。确认继续？`
  }

  try {
    await ElMessageBox.confirm(body, title, {
      type: 'warning',
      confirmButtonText: confirmText,
      cancelButtonText: '取消',
      // 只有拼好的 HTML 才开；里面每一段都过了 escapeHtml
      dangerouslyUseHTMLString: Boolean(isPurge || isClean),
    })
  } catch {
    return
  }
  await job.run(() => approveAgentTask(current.id))
  loadAgentContext()
}

/** 只执行安全项：把清单里非安全级的项剔掉再确认 */
async function approveSafeOnly() {
  const current = job.job.value
  if (!current?.id) return
  const proposal = current.proposal || {}
  const safe = (proposal.items || []).filter(item => !item.level || item.level === 'safe')
  if (!safe.length) {
    ElMessage.info('这份清单里没有安全级项目，需要去对应页面手动挑')
    return
  }
  job.job.value = {
    ...current,
    proposal: { ...proposal, items: safe, item_count: safe.length, safe_only: true },
  }
  await approvePlanTree()
}

/** 修改方案（就地改条件）：换条件重算，零 token，仍在同一个任务里。 */
const reviseBusy = ref(false)

/** 草稿就地改条件：方案还没建任务，所以走 /agent/plan 的重算分支，零 token。
    只替换方案本身，保留这条消息原本的来源与 token 数——重算没花 token，
    但原话那次花过，抹掉就看不出这条一共花了多少。 */
async function reviseDraft(index, patch) {
  const msg = messages.value[index]
  // 必须用 plan.intent（完整意图）：消息上的 msg.intent 是 intent_view 的精简视图，
  // 它不含 action，拿去重算会被后端判成「没有可修改的意图」。
  const intent = msg?.plan?.intent
  if (!intent?.action) {
    modifyPlan()
    return
  }
  draftRevising.value = index
  try {
    const message = [...messages.value.slice(0, index)]
      .reverse()
      .find(item => item.role === 'user')?.text || ''
    const data = await reviseAgentPlan(message, intent, patch)
    Object.assign(msg, {
      plan: data.plan,
      intent: data.intent,
      steps: data.steps || [],
      message: data.message || msg.message,
      revised: true,
    })
    ElMessage.success('已按新条件重算，仍然是只读预览')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    draftRevising.value = -1
  }
}

async function revisePlan(patch) {
  const current = job.job.value
  if (!current?.id) return
  reviseBusy.value = true
  try {
    const started = await job.run(() => reviseAgentTask(current.id, patch))
    if (started) ElMessage.success('已按新条件重算，跑完还要你确认')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    reviseBusy.value = false
  }
}

/** 修改方案：把原话放回输入框，让用户改条件重来 */
function modifyPlan() {
  const source = [...messages.value].reverse().find(item => item.role === 'user')
  draft.value = source?.text || ''
  ElMessage.info('在输入框里改条件再发一次，我会重新出一版方案')
  nextTick(() => composerInput.value?.focus?.())
}

/** 撤销本次计划：停掉正在挂起/运行的任务 */
async function discardPlan() {
  try {
    await ElMessageBox.confirm(
      '撤销本次计划会停掉这个任务。计划还没开始动文件，撤销后什么都不影响。',
      '撤销本次计划',
      { type: 'warning', confirmButtonText: '撤销', cancelButtonText: '继续执行' },
    )
  } catch {
    return
  }
  await job.cancel()
  loadAgentContext()
}

function showPlanDetails() {
  const current = job.job.value
  if (!current?.id) return
  router.push({ path: '/jobs', query: { job: current.id } })
}

/** 任务落终态后，用本地模板把它读成一条人话总结，追加进对话 */
async function reportObservation(item) {
  if (!item?.id || item.type !== 'agent_task') return
  try {
    const observation = await observeAgentJob(item.id)
    messages.value.push({
      role: 'assistant',
      source: 'local',
      tokens: 0,
      message: observation.message || '任务已结束',
      plan: null,
      pending: false,
      stopped: false,
    })
    scrollToBottom()
  } catch {
    /* 观察失败不打断主流程 */
  }
  loadAgentContext()
}

/* 任务状态变化：分析段跑完 → 刷新状态条；执行段跑完 → 追加一条总结 */
watch(() => job.job.value?.status, (next, prev) => {
  if (!next || next === prev) return
  if (next === 'waiting') {
    treeOpen.value = true
    ElMessage.success('分析完成，方案已生成，等你确认')
  }
  if (['done', 'failed', 'cancelled'].includes(next)) {
    loadAgentContext()
    reportObservation(job.job.value)
  }
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

/* 侧栏数据：分组与副标题都在父组件算好，AgentTaskPanel 只负责渲染（Phase 7）。
   这样「任务按什么分组」这条规则始终只有一份，改的时候不用两边找。 */
const sidebarGroups = computed(() => taskGroups.value.map(group => ({
  label: group.label,
  items: group.items.map(item => ({
    id: item.conversation_id,
    title: item.title,
    meta: taskMeta(item),
    active: item.conversation_id === conversationId.value,
  })),
})))

/** 侧栏下拉命令：子组件只回 id，原 item 在这里找回来（重命名要用到旧标题） */
function onSidebarCommand(command, id) {
  const item = conversations.value.find(entry => entry.conversation_id === id)
  if (item) onTaskCommand(command, item)
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
  // 空会话也立刻落库：以前要等第一条消息才进列表，「新建任务」后切走再回来就找不到了
  touchAgentConversation(id).catch(() => {})
  resetView()
  // 新对话是空槽位：把上一个对话的任务卡清掉（任务本身照旧在后台跑）
  job.restore()
  // 新对话没有任何"用过的目录"：重新拉一次，把上一个会话的最近路径换掉
  loadPrefs()
  nextTick(() => composerInput.value?.focus?.())
}

/* 切到某条历史会话：读它的记忆并渲染出来 */
async function switchConversation(id) {
  if (streaming.value) stopGenerating()
  conversationId.value = id
  persistConversation(id)
  resetView()
  /* 任务卡跟着会话走：切过去就把那个会话的任务接回来；没有就清掉 ——
     别让上一个对话的任务留在屏幕上（2026-10-09 用户报的就是这个）。
     注意 `job` 的槽位是 `() => conversationId.value`，所以这一行必须在改完 id 之后。 */
  job.restore()
  adoptWaitingTask()
  // 最近路径跟着会话走，切换后必须重新拉，否则显示的还是上一个对话的
  loadPrefs()
  await loadConversationMemory()
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
    messages.value = items.map(item => {
      if (item.role === 'user') return { role: 'user', text: item.content }
      // 计划、意图、步骤这些「这条回复长什么样」的信息存在 payload 里。
      // 老记录没有这一列（那时还没存），那就只剩一句话——这是数据本身的限制，
      // 不拿正文去反推一个假计划出来。
      const saved = item.payload || {}
      return {
        role: 'assistant',
        source: saved.source || '',
        tokens: saved.tokens ?? 0,
        message: item.content,
        type: saved.type || '',
        plan: saved.plan || null,
        intent: saved.intent || null,
        steps: saved.steps || [],
        hint: saved.hint || '',
        question: saved.question || '',
        missing: saved.missing || [],
        suggestions: saved.suggestions || [],
        options: saved.options || [],
        fields: saved.fields || [],
        pending: false,
        stopped: false,
        restored: true,
      }
    })
    scrollToBottom(true)
  } catch {
    memoryCount.value = 0
  }
}

/* 恢复会话 + 开合闸门：切换会话和首次进入都走这里 */
async function loadConversationMemory() {
  hydrating.value = true
  try {
    await restoreConversation()
  } finally {
    hydrating.value = false
  }
}

/* 「本次启动」标记：sessionStorage 跟着 WebView / 标签页的生命周期走，应用一关就没了 ——
   正好用来区分「新一次启动」与「同一次运行内切走再切回」。 */
const BOOT_KEY = 'filepilot.agent.booted'

function isFirstEntryOfThisRun() {
  try {
    if (sessionStorage.getItem(BOOT_KEY) === '1') return false
    sessionStorage.setItem(BOOT_KEY, '1')
    return true
  } catch {
    /* 隐私模式等拿不到 sessionStorage：退回原来的「接着上次那条」，不擅自重置 */
    return false
  }
}

/* 启动：先拉会话列表，再决定当前会话 ——
   1. 本次启动后的**首次**进入：一律从「新任务」开始，不读也不恢复 localStorage 里那条，
      直接放行 hydrating 闸门，让欢迎页稳稳渲染（用户 2026-10-07 要求：进来就是这张空态）；
   2. 同一次运行内再进来：沿用当前会话，切走再切回不打断；
   3. 旧会话一条没删，仍在左栏任务列表里可翻。
   注意：第 1 条与 v23「压掉欢迎页闪现」的初衷方向相反 —— 那里要消除的是
   「闪一下欢迎页又被旧对话顶掉」，这里要的是「稳定停在欢迎页」。
   两者都不希望欢迎页一闪而过，所以 hydrating 闸门照旧保留。 */
async function initConversations() {
  await loadConversationList()

  if (isFirstEntryOfThisRun()) {
    newConversation()
    hydrating.value = false
    return
  }

  /* 同一次运行内再进来：只认 localStorage 里那条。
     不能再用「stored 不在后端列表里就退回最近一条历史」的老 fallback ——
     刚新建的空会话在发出第一条消息之前不会落库、也就不在列表里，
     一 fallback，用户切去别的选项卡再回来就会莫名看到一条毫不相干的旧对话。
     （2026-10-07 实测翻车：hero 被顶掉、最近那条历史会话冒出来。） */
  const stored = readStoredConversation()
  const id = stored || conversations.value[0]?.conversation_id || newConversationId()
  conversationId.value = id
  persistConversation(id)
  // 会话定下来之后再认领任务卡：本会话有在跑的任务就接回来，没有就保持空
  job.restore()
  // restore 只认内存里的槽位；刷新过页面的话槽位是空的，得去后端把「等待确认」的任务接回来
  adoptWaitingTask()
  await loadConversationMemory()
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
  if (plan.action === 'trash_restore') return '恢复这个文件'
  if (plan.action === 'job_rollback') return '回滚这个任务'
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

/** 计划里带的目录（如果有）—— 给"去处理这个目录"的接力出口取路径。 */
function planFocusPath(plan) {
  const params = plan?.params || {}
  const raw = params.path || params.source_dir || (Array.isArray(params.paths) ? params.paths[0] : '')
  return String(raw || '').trim()
}

/** Agent 的"结果 → 去处理"出口：与其它页保持同一套。 */
const agentNextSteps = [
  { name: 'duplicates', label: '查重' },
  { name: 'junk', label: '清理' },
  { name: 'backup', label: '备份' },
  { name: 'space', label: '空间地图' },
]

async function executePlan(plan) {
  const params = plan.params || {}
  const base = { excludes: excludes.value }

  const tabActions = {
    open_tab: params.route,
    overview: '/',
    jobs_list: '/jobs',
    trash_list: '/trash',
    logs_list: '/logs',
  }
  if (tabActions[plan.action]) {
    // open_tab 若带了目录，就按「接力」跳过去（目标页会预填好）——"打开扫描分析 D:\X" 落地即就绪
    const focus = plan.action === 'open_tab' ? String(params.path || '').trim() : ''
    router.push(focus ? { path: tabActions[plan.action], query: { focus, from: 'agent' } } : tabActions[plan.action])
    return
  }

  // 只读档拦写类计划：在入口说明，不等任务跑到一半才失败
  if (plan.write && permissionMode.value === 'readonly') {
    ElMessage.warning('当前是只读档，写类操作不可用；切到「标准」档再试')
    return
  }

  // ---- 任务控制与隔离区恢复：不新起任务，直接作用于已经存在的东西 ----
  // 这三个动作在工具注册表里（执行工具 9 条含取消/回滚/恢复），
  // 所以计划卡上必须有对应的落点，不能点了只说「暂时不能执行」。
  if (plan.action === 'trash_restore') {
    const wanted = String(params.path || '').toLowerCase()
    const data = await getTrash().catch(() => ({ entries: [] }))
    const entries = data.entries || []
    const hit = (wanted && entries.find(item => String(item.original || '').toLowerCase() === wanted)) || entries[0]
    if (!hit) {
      ElMessage.warning('隔离区里没有可恢复的项目')
      return
    }
    try {
      const result = await restoreTrash([hit.id])
      ElMessage.success(`已恢复 ${result.restored_count ?? 1} 项到原位置`)
      loadAgentContext()
    } catch (error) {
      ElMessage.error(error.message)
    }
    return
  }

  if (plan.action === 'job_cancel' || plan.action === 'job_rollback') {
    const ctx = agentContext.value || {}
    const tracked = job.job.value
    if (plan.action === 'job_cancel') {
      const runningTracked = tracked && ['running', 'pending', 'waiting'].includes(tracked.status)
      const targetId = (runningTracked && tracked.id) || (ctx.running || [])[0]?.id || ''
      if (!targetId) {
        ElMessage.info('现在没有正在运行的任务')
        return
      }
      try {
        await cancelJob(targetId, 'Agent 计划')
        ElMessage.success('已请求中断该任务')
        loadAgentContext()
      } catch (error) {
        ElMessage.error(error.message)
      }
      return
    }
    const targetId = (ctx.rollbackable || [])[0]?.id || ''
    if (!targetId) {
      ElMessage.info('没有可回滚的任务')
      return
    }
    try {
      const result = await rollbackJob(targetId)
      ElMessage.success(`已回滚 ${result.restored_count ?? 0} 项`)
      loadAgentContext()
    } catch (error) {
      ElMessage.error(error.message)
    }
    return
  }

  let starter = null
  if (plan.action === 'scan') starter = () => startScan({ ...base, path: params.path })
  if (plan.action === 'tree') starter = () => startTree({ ...base, path: params.path, depth: 2 })
  if (plan.action === 'virtual_preview') {
    starter = () => startVirtual({ ...base, path: params.path, group_by: params.group_by || 'type', min_size: params.min_size_mb ? params.min_size_mb * 1024 * 1024 : 0 })
  }
  if (plan.action === 'projects_scan') {
    starter = () => startProjectScan({ ...base, path: params.path, zombie_days: params.zombie_days || 180, max_depth: params.max_depth || 0 })
  }
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
        '完全访问允许改动桌面与系统目录，删除会真实生效（清理类确认后不可恢复），任务记录里会留下当时的档位。确认切换？',
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
  /* 会话恢复不依赖 config / status，放最前面并行跑。
     串在后面的话，后端刚启动时要等两轮请求才轮到它 —— 那段时间用户看着的就是空页面。 */
  initConversations().catch(() => {
    /* 后端还没起来时列表和记忆都会失败，闸门必须放行，否则一直空着 */
    hydrating.value = false
  })
  await loadConfig().catch(() => {})
  await reloadStatus()
  loadRecipes()
  loadPrefs()
  loadAgentContext()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})
</script>

<template>
  <div class="agent-page">
    <div class="agent-workspace">
      <!-- 任务列表：常驻左栏，可收起（窄屏自动收起） -->
      <!-- 任务列表：常驻左栏，可收起（窄屏自动收起）—— 已拆到 AgentTaskPanel（Phase 7） -->
      <AgentTaskPanel
        :visible="!listHidden"
        :groups="sidebarGroups"
        @toggle="toggleList"
        @create="newConversation"
        @select="switchConversation"
        @command="onSidebarCommand"
      />

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

          <span class="agent-head__spacer"></span>

          <!-- 状态感知条：任务 / 待确认 / 可回滚 / 磁盘吃紧 / 隔离区占用 -->
          <div v-if="contextChips.length" class="agent-chips">
            <span
              v-for="chip in contextChips"
              :key="chip.id"
              class="agent-chip"
              :class="`is-${chip.tone}`"
            >{{ chip.text }}</span>
          </div>

          <!-- 偏好记忆：Agent 长期记住的东西，用户可直接增删 -->
          <el-popover ref="prefsPop" placement="bottom-end" :width="360" trigger="click">
            <template #reference>
              <button type="button" class="agent-head__icon" title="偏好记忆">
                <el-icon :size="15"><Setting /></el-icon>
              </button>
            </template>
            <div class="prefs">
              <div class="prefs__title">偏好记忆</div>
              <p class="prefs__note">这些结论会随每次对话一起交给 Agent，改完立即生效。</p>

              <div class="prefs__section">
                <div class="prefs__label">长期排除标签</div>
                <div class="prefs__tags">
                  <button
                    v-for="tag in tagOptions"
                    :key="tag.name"
                    type="button"
                    class="prefs__tag"
                    :class="{ 'is-on': hasTag(tag.name) }"
                    :disabled="prefsSaving"
                    :title="tag.note"
                    @click="toggleTag(tag.name)"
                  >{{ tag.name }}</button>
                </div>
              </div>

              <div class="prefs__section">
                <div class="prefs__label">长期排除路径</div>
                <ul v-if="prefs?.exclude_paths?.length" class="prefs__list">
                  <li v-for="item in prefs.exclude_paths" :key="item">
                    <span class="prefs__path" :title="item">{{ item }}</span>
                    <button type="button" @click="removeExcludePath(item)">移除</button>
                  </li>
                </ul>
                <div v-else class="prefs__empty">还没有，加一条就不会再被 Agent 碰到</div>
                <div class="prefs__add">
                  <el-input
                    v-model="newExcludePath"
                    size="small"
                    placeholder="例如 D:\素材"
                    @keyup.enter="addExcludePath"
                  />
                  <el-button size="small" plain :disabled="prefsSaving" @click="addExcludePath">添加</el-button>
                </div>
              </div>

              <div class="prefs__section">
                <div class="prefs__label">永久约束</div>
                <ul v-if="prefs?.hard_rules?.length" class="prefs__list">
                  <li v-for="(rule, index) in prefs.hard_rules" :key="`${rule.text}-${index}`">
                    <span class="prefs__path">{{ rule.text || rule.path }}</span>
                    <button type="button" @click="removeHardRule(index)">移除</button>
                  </li>
                </ul>
                <div v-else class="prefs__empty">例如「项目都不要动」，之后 Agent 会一直遵守</div>
                <div class="prefs__add">
                  <el-input
                    v-model="newHardRule"
                    size="small"
                    placeholder="例如 项目都不要动"
                    @keyup.enter="addHardRule"
                  />
                  <el-button size="small" plain :disabled="prefsSaving" @click="addHardRule">添加</el-button>
                </div>
              </div>

              <div class="prefs__section" v-if="prefs?.clean_strategies?.length">
                <div class="prefs__label">常用清理策略</div>
                <ul class="prefs__list">
                  <li v-for="item in prefs.clean_strategies.slice(0, 5)" :key="`${item.action}-${item.path}`">
                    <button type="button" class="prefs__replay" :title="item.path" @click="useStrategy(item)">
                      {{ item.message || item.action }}
                    </button>
                    <span class="prefs__meta">{{ item.at ? formatShortTime(item.at) : '' }}</span>
                  </li>
                </ul>
              </div>

              <div class="prefs__section" v-if="knownPaths.length">
                <div class="prefs__label">常用路径</div>
                <div class="prefs__chips">
                  <button
                    v-for="item in knownPaths.slice(0, 8)"
                    :key="item.path"
                    type="button"
                    :title="item.session ? `${item.path}（本次对话用过）` : item.path"
                    @click="draft = item.path"
                  >{{ item.path }}</button>
                </div>
              </div>

              <div class="prefs__footer">
                <span class="prefs__meta">
                  {{ prefs?.updated_at ? `更新于 ${formatShortTime(prefs.updated_at)}` : '尚未记录' }}
                </span>
                <button type="button" class="prefs__clear" :disabled="prefsSaving" @click="clearAllPrefs">
                  清空记忆
                </button>
              </div>
            </div>
          </el-popover>
        </header>

        <!-- 空态：不再是「一句话 + 一大片留白」。
             标记 → 标题 → 四个起点 → 常用路径，纵向居中在对话区里。 -->
        <div v-if="!messages.length && !hydrating" class="agent-hero">
          <AgentGlyph class="agent-hero__mark" :size="56" />
          <h1 class="agent-hero__title">你想让 <span class="agent-hero__brand">Atlas</span> 做什么？</h1>

          <div class="hero-grid">
            <button
              v-for="item in HERO_ACTIONS"
              :key="item.label"
              type="button"
              class="hero-card"
              @click="send(item.prompt)"
            >
              <span class="hero-card__title">{{ item.label }}</span>
              <span class="hero-card__note">{{ item.note }}</span>
            </button>
          </div>

          <div v-if="knownPaths.length" class="hero-paths">
            <span class="hero-paths__label">常用路径</span>
            <button
              v-for="item in knownPaths.slice(0, 4)"
              :key="item.path"
              type="button"
              class="hero-path"
              :title="item.session ? `${item.path}（本次对话用过）` : item.path"
              @click="draft = item.path"
            >{{ item.path }}</button>
          </div>
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
            <!-- 身份锚：纯段落文本滚几轮就分不清哪句是谁说的。
                 整块（方章 + 名字）横跨两列占第一行，下面的正文落到右列，
                 不用再包一层 div，条件渲染的兄弟节点也不会错位。 -->
            <div class="message__mark">
              <span class="message__mark-box"><AgentGlyph :size="12" /></span>
              <span class="message__who">Atlas</span>
            </div>
            <div
              class="message-text markdown"
              :class="{ 'is-collapsed': isCollapsed(index) }"
              v-html="renderMarkdown(msg.message)"
            ></div>
            <button v-if="isLong(index)" type="button" class="expand-btn" @click="toggleExpand(index)">
              {{ isCollapsed(index) ? '展开全文' : '收起' }}
            </button>

            <div v-if="msg.type === 'clarify'" class="agent-clarify">
              <strong>{{ msg.question || '还需要补充一些信息' }}</strong>
              <div v-if="msg.missing?.length" class="agent-clarify__missing">
                缺少：{{ msg.missing.join('、') }}
              </div>
              <div v-if="msg.options?.length" class="agent-clarify__options">
                <button
                  v-for="option in msg.options"
                  :key="option.id"
                  type="button"
                  @click="send(option.label)"
                >
                  <span class="agent-clarify__option-label">{{ option.label }}</span>
                  <span v-if="option.note" class="agent-clarify__option-note">{{ option.note }}</span>
                </button>
              </div>
              <ul v-if="msg.fields?.length" class="agent-clarify__fields">
                <li v-for="field in msg.fields" :key="field.id">{{ field.question }}</li>
              </ul>
              <div v-if="msg.suggestions?.length" class="agent-clarify__suggestions">
                <button
                  v-for="suggestion in msg.suggestions"
                  :key="suggestion"
                  type="button"
                  @click="send(suggestion)"
                >
                  {{ suggestion }}
                </button>
              </div>
            </div>

            <!-- 多步骤方案：先把计划树摊开，点了才真正开始分析 -->
            <div v-if="isMultiStep(msg)" class="plan-draft">
              <PlanTree
                :steps="stepsOf(msg)"
                :intent="intentOf(msg)"
                :constraints="msg.plan.constraints || {}"
                :estimate="msg.plan.estimate || {}"
                :proposal="{}"
                :tag-options="tagOptions"
                :busy="!!jobRunning"
                :waiting="false"
                :editable="true"
                :revise-busy="draftRevising === index"
                :permission-mode="permissionMode"
                @revise="patch => reviseDraft(index, patch)"
                @modify="modifyPlan"
                @details="showPlanDetails"
              />
              <div class="plan-draft__actions">
                <el-button
                  size="small"
                  type="primary"
                  :icon="Promotion"
                  :disabled="!!jobRunning"
                  @click="startPlanTree(msg)"
                >
                  开始分析（{{ stepsOf(msg).length }} 步）
                </el-button>
                <el-button size="small" plain :icon="Star" @click="saveAsRecipe(msg)">存成配方</el-button>
                <span class="plan-draft__note">
                  <template v-if="msg.revised">已按新条件重算 · 零 token；</template>分析全程只读，出方案后还要你确认
                </span>
              </div>
            </div>

            <div
              v-else-if="msg.plan && msg.plan.action !== 'none'"
              class="work"
              :class="{ 'is-write': msg.plan.write }"
            >
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
              <NextSteps
                v-if="planFocusPath(msg.plan)"
                :path="planFocusPath(msg.plan)"
                origin="agent"
                :steps="agentNextSteps"
              />
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

        <!-- 运行中的任务：内联进度卡（左侧主色竖条，与对话气泡区分）。
             多步骤方案走计划树——同一个任务卡从分析一路显示到执行。 -->
        <div v-if="jobRunning && jobIsAgentTask" class="job-card is-plan">
          <PlanTree
            :steps="treeSteps"
            :intent="treeIntent"
            :proposal="treeProposal"
            :estimate="treeEstimate"
            :constraints="treeIntent.constraints || {}"
            :tag-options="tagOptions"
            :busy="treeBusy"
            :waiting="treeWaiting"
            :editable="treeWaiting"
            :revise-busy="reviseBusy"
            :permission-mode="permissionMode"
            :open="treeOpen"
            @toggle="value => (treeOpen = value)"
            @execute="approvePlanTree"
            @safe-only="approveSafeOnly"
            @modify="modifyPlan"
            @revise="revisePlan"
            @discard="discardPlan"
            @details="showPlanDetails"
          />
          <p v-if="treeWaiting" class="job-card__hint">
            <template v-if="lastPlan?.action === 'trash_purge'">
              方案已生成。确认后会永久删除隔离区里的这些条目，不可恢复——你确认前什么都不会动。
            </template>
            <template v-else-if="['junk_clean','duplicate_clean','similar_clean','large_clean'].includes(lastPlan?.action)">
              方案已生成。清理类确认后即彻底删除，不可恢复——你确认前什么都不会动。
            </template>
            <template v-else>
              方案已生成。删除一律先进隔离区，可随时还原——你确认前什么都不会动。
            </template>
          </p>
        </div>
        <div v-else-if="jobRunning" class="job-card">
          <JobProgress :job="jobRunning" @cancel="job.cancel" @rollback="job.rollback" />
        </div>
        <div v-else-if="jobSummary && !jobIsAgentTask" class="job-summary">
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

        <!-- 示例胶囊已并入空态的入口卡（.hero-grid），输入框上方不再重复摆一排 -->
        <div class="composer">
          <el-input
            v-model="draft"
            ref="composerInput"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
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
                <!-- 段零：服务商（Phase 12）。放在模型上面 —— 换服务商会清掉已保存的 Key，
                     先选服务商再填 Key，顺序才对得上。 -->
                <div class="panel__section">
                  <div class="panel__title">服务商</div>
                  <el-select
                    :model-value="providerId"
                    size="small"
                    style="width: 100%"
                    @change="switchProvider"
                  >
                    <el-option
                      v-for="item in providerOptions"
                      :key="item.id"
                      :label="item.label"
                      :value="item.id"
                    />
                  </el-select>

                  <template v-if="needsBaseUrl">
                    <div class="panel__keyrow" style="margin-top: 8px">
                      <el-input
                        v-model="baseUrlDraft"
                        size="small"
                        autocomplete="off"
                        placeholder="https://你的网关/v1"
                        @keydown.enter="saveBaseUrl"
                      />
                      <el-button size="small" type="primary" @click="saveBaseUrl">保存</el-button>
                    </div>
                    <small v-if="settings?.llm_base_url" class="panel__src">
                      当前：{{ settings.llm_base_url }}
                    </small>
                    <!-- 风险提示是需求明确要求的：用户得知道对话内容和工具结果都会发到这个地址 -->
                    <small class="panel__warn">
                      对话内容与工具结果都会发往你填写的地址。请确认它可信 ——
                      换成不熟悉的网关等于把数据交给对方。
                    </small>
                  </template>

                  <small v-if="providerError" class="panel__warn">{{ providerError }}</small>
                </div>

                <div class="panel__divider"></div>

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
                  <!-- 存量明文 / 开发模式明文：如实说，不假装已加密 -->
                  <small v-if="settings?.storage_warning" class="panel__warn">
                    {{ settings.storage_warning }}
                  </small>
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
  --agent-composer-bottom-space: 76px;
  --agent-caret-pulse: 1.1s;
  /* 任务列表的状态点：只在「确实知道状态」时上色，见 task-item__dot */
  --agent-status-dot: 7px;
  /* 助手消息的身份锚方章 */
  --agent-mark-size: 22px;
  display: flex;
  height: 100vh;
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
/* 从前是「一个标记 + 一行大标题 + 一大片留白」，进来不知道该干什么。
   现在排成：标记 → 标题 → 四个起点 → 常用路径，纵向居中，宽度与阅读栏对齐。 */
.agent-hero {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-5);
  width: min(var(--reading-width), 100%);
  margin: 0 auto;
  padding: var(--space-5) var(--space-5) var(--space-4);
  min-height: 0;
}

.agent-hero__mark {
  color: var(--ink-2);
  animation: hero-rise var(--duration-slow) var(--ease) both;
}

.agent-hero__title {
  margin: 0;
  color: var(--ink);
  font-size: clamp(26px, 2.6vw, 34px);
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.2;
  text-align: center;
  animation: hero-rise var(--duration-slow) var(--ease) var(--duration-micro) both;
}

/* 四个起点：两列、只描边不填充。
   空态要保持轻 —— 一上来就摆四块灰底卡片，页面立刻变沉。 */
.hero-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
  width: 100%;
}

.hero-card {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3);
  text-align: left;
  background: transparent;
  border: 1px solid var(--hairline);
  border-radius: var(--agent-radius-control);
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease), border-color var(--duration-quick) var(--ease);
}

.hero-card:hover {
  background: var(--inset);
  border-color: var(--hairline-strong);
}

.hero-card__title {
  color: var(--ink);
  font-size: var(--fs-card-title);
  font-weight: 500;
}

.hero-card__note {
  color: var(--ink-3);
  font-size: var(--fs-micro);
}

.hero-paths {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  justify-content: center;
}

.hero-paths__label {
  color: var(--ink-4);
  font-size: var(--fs-micro);
}

.hero-path {
  max-width: 220px;
  overflow: hidden;
  padding: 2px var(--space-2);
  color: var(--ink-3);
  font-size: var(--fs-micro);
  text-overflow: ellipsis;
  white-space: nowrap;
  background: transparent;
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), border-color var(--duration-quick) var(--ease);
}

.hero-path:hover {
  color: var(--ink);
  border-color: var(--hairline-strong);
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
/* 版心内的分隔线：页眉下缘与输入区上缘各一条 760px 的 hairline，
   上下把对话围成一个「文件」的区间。两条线宽度一致，看起来才像一个系统。 */
.composer-zone {
  position: relative;            /* 「回到底部」以此为锚点上浮 */
  flex: none;
  width: min(var(--reading-width), 100%);
  margin: 0 auto;
  padding: var(--space-3) var(--space-5) var(--space-4);
  border-top: 1px solid var(--hairline);
}

/* 命令栏，不是评论框：圆角收紧、投影降一档、内边距压紧。
   原来 26px 圆角 + sh-2 更像移动端的输入胶囊，撑不起「工具」的重量。 */
.composer {
  display: grid;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4) var(--space-2);
  background: var(--surface);
  border: 1px solid var(--hairline-strong);
  border-radius: var(--agent-radius-lg);
  box-shadow: var(--sh-1);
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

/* ------------------------------------------------------------ 主区标题栏 */
/* 标题 + 一行小字元信息，右侧是状态条与设置入口。
   下缘一条 hairline 与对话区作别 —— 这一步让标题区从「一行字」变成「页眉」。 */
.agent-head {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: min(var(--reading-width), 100%);
  margin: 0 auto;
  padding: var(--space-4) var(--space-5) var(--space-3);
  /* 收口线：标题区与对话区之间拉一条 hairline，内容从此有了「页眉」的身份，
     也给滚动的内容一个起点。宽度与对话阅读栏一致，两栏左右对齐。 */
  border-bottom: 1px solid var(--hairline);
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

.agent-head__spacer {
  flex: 1;
}

/* ------------------------------------------- 状态感知条（第六阶段）
   任务 / 待确认 / 可回滚 / 磁盘吃紧 / 隔离区占用，全部本地读、零 token。 */
.agent-chips {
  display: flex;
  flex: none;
  gap: 6px;
  align-items: center;
  max-width: 60%;
  overflow: hidden;
}

.agent-chip {
  flex: none;
  padding: 2px var(--space-2);
  color: var(--ink-3);
  font-size: var(--fs-micro);
  white-space: nowrap;
  background: var(--inset);
  border-radius: var(--radius-pill);
}

/* 状态条的颜色只留给「需要你动手」的那一项：
   待确认 = 主色，磁盘吃紧 = 告警。其余一律中性灰 ——
   三项都上色会把标题的注意力摊平，看着就吵。 */
.agent-chip.is-active {
  color: var(--primary-ink);
  background: var(--primary-soft);
}

.agent-chip.is-warn {
  color: var(--warning-ink);
  background: var(--warning-soft);
}

/* --------------------------------------------- 偏好记忆面板（第五阶段） */
.prefs {
  display: grid;
  gap: 12px;
}

.prefs__title {
  color: var(--ink);
  font-size: var(--agent-fs-body-tight);
  font-weight: 600;
}

.prefs__note {
  margin: 0;
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  line-height: 1.6;
}

.prefs__section {
  display: grid;
  gap: 6px;
}

.prefs__label {
  color: var(--ink-2);
  font-size: var(--agent-fs-md);
}

.prefs__tags,
.prefs__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.prefs__tag {
  padding: 3px 10px;
  color: var(--ink-2);
  font-size: var(--agent-fs-md);
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-pill);
  cursor: pointer;
  transition: color var(--duration-quick) var(--ease), background-color var(--duration-quick) var(--ease);
}

.prefs__tag:hover {
  color: var(--ink);
  background: var(--inset);
}

.prefs__tag.is-on {
  color: var(--primary-ink);
  background: var(--primary-soft);
  border-color: var(--primary-soft);
}

.prefs__list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.prefs__list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--agent-fs-md);
}

.prefs__path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ink-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prefs__list button,
.prefs__clear {
  padding: 0;
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  background: transparent;
  border: 0;
  cursor: pointer;
}

.prefs__list button:hover,
.prefs__clear:hover {
  color: var(--danger-ink);
}

.prefs__empty {
  color: var(--ink-4);
  font-size: var(--agent-fs-md);
}

.prefs__add {
  display: flex;
  gap: 6px;
}

.prefs__chips button {
  max-width: 100%;
  padding: 2px 8px;
  overflow: hidden;
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  text-overflow: ellipsis;
  white-space: nowrap;
  background: var(--inset);
  border: 0;
  border-radius: var(--radius-pill);
  cursor: pointer;
}

.prefs__chips button:hover {
  color: var(--ink);
}

.prefs__replay {
  flex: 1;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  color: var(--ink-2);
  font-size: var(--agent-fs-md);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.prefs__replay:hover {
  color: var(--primary-ink);
}

.prefs__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.prefs__meta {
  color: var(--ink-4);
  font-size: var(--agent-fs-md);
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
  /* 阅读栏：限宽居中。长文本横跨整个宽屏会读不下去——两侧留白是从这里
     「有意留出来的」，而不是没填满。宽度与顶部标题栏共用同一个 token。 */
  width: min(var(--reading-width), 100%);
  margin: 0 auto;
  padding: var(--space-2) var(--space-5) var(--space-5);
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
  max-width: 100%;
  color: var(--ink);
  font-size: var(--agent-fs-body-wide);
  line-height: 1.65;
}

/* 助手消息：左侧立一个身份锚（山脊方章 + 名字），正文落右列。
   用两列网格而不是再包一层 div —— 这一层里有五六个条件渲染的兄弟节点，
   包一层就得整块重排缩进，网格只改样式、不动结构。 */
.message.is-assistant {
  display: grid;
  grid-template-columns: var(--agent-mark-size) minmax(0, 1fr);
  column-gap: var(--space-3);
  row-gap: 0;
  align-items: start;
}

.message.is-assistant > *:not(.message__mark) {
  grid-column: 2;
}

.message__mark {
  grid-column: 1 / -1;
  grid-row: 1;
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-bottom: var(--space-2);
}

.message__mark-box {
  display: grid;
  place-items: center;
  width: var(--agent-mark-size);
  height: var(--agent-mark-size);
  color: var(--primary-ink);
  background: var(--primary-soft);
  border-radius: var(--agent-radius-sm);
}

.message__who {
  color: var(--ink-4);
  font-size: var(--fs-micro);
  font-weight: 600;
  letter-spacing: 0.01em;
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
  margin-top: var(--space-3);
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
   以前它 opacity:0 却仍占 20px，每条消息凭空变高，整屏显得很空。

   两个坑（2026-10-06 实测复现，都会让人"点不到"）：
   1. 用 margin-top 挪 4px，会在消息底边和操作行顶边之间留出 4px 死区 ——
      鼠标往下跨过它时既不在消息里也不在行里，父级 mouseleave 立刻触发，行当着你面消失。
      改用 padding-top，行的盒子从消息底边**紧贴**开始，没有死区。
   2. 行的盒子会往下伸约 23px，而相邻两条助手消息只隔 12px ——
      伸出去的部分被**下一条消息盖在上面**（z-index 高的后来者居上）。
      所以父级 hover 时要抬一层，再给行一层与主区同色的底，叠上去才干净。 */
.message-actions {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 2;
  display: flex;
  gap: 14px;
  padding-top: 4px;
  background: var(--surface);     /* 与主区同色：与下一条消息重叠时不留脏底 */
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--duration-quick) var(--ease);
}

.message-actions.is-visible {
  opacity: 1;
  pointer-events: auto;
}

/* 悬停（或键盘进入）时把整条消息抬到邻条之上，
   否则伸出去的操作行会被下一条消息盖住，按钮看得见点不着。 */
.message:hover,
.message:focus-within {
  z-index: 3;
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

.agent-clarify {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding: 12px 14px;
  background: var(--inset);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-ctl);
}

.agent-clarify__missing {
  color: var(--ink-3);
  font-size: var(--fs-note);
}

.agent-clarify__suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.agent-clarify__suggestions button {
  padding: 5px 9px;
  color: var(--primary-ink);
  background: var(--surface);
  border: 1px solid var(--hairline-strong);
  border-radius: var(--radius-pill);
  cursor: pointer;
}

.agent-clarify__suggestions button:hover {
  background: var(--primary-soft);
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

/* 分区标题（「模型」/「API Key」）。
   用 --ink-3 而不是 --ink-4：深色下 --ink-4(#636366) 落在 --surface(#2c2c2e) 上
   只有 2.3:1，分区标题几乎看不清；--ink-3 提到 4.2:1，浅色下也顺带从 2.6:1 提到 3.5:1。
   它本来就该是"安静但读得见"的一层，不是"接近背景色"。 */
.panel__title {
  color: var(--ink-3);
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

/* 密钥存储形态警告（存量明文 / 开发模式明文）——只提示，不拦保存 */
.panel--model .panel__warn {
  display: block;
  margin: 8px 0 0;
  padding: 5px 8px;
  color: var(--warning-ink);
  background: var(--warning-soft);
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  font-size: var(--agent-fs-xs);
  line-height: 1.45;
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

/* --------------------------------------- 澄清卡的选项与字段（第四阶段）
   选项式追问：把选择摆出来，点一下就能继续，不用手打一遍。 */
.agent-clarify__options {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.agent-clarify__options button {
  display: grid;
  gap: 1px;
  padding: 8px 10px;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: var(--agent-radius-control);
  cursor: pointer;
  transition: background-color var(--duration-quick) var(--ease);
}

.agent-clarify__options button:hover {
  background: var(--inset);
}

.agent-clarify__option-label {
  color: var(--ink);
  font-size: var(--agent-fs-body-tight);
}

.agent-clarify__option-note {
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
}

.agent-clarify__fields {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  line-height: 1.7;
}

/* ------------------------------------------- 计划树：草稿态与任务态 */
.plan-draft {
  margin-top: 12px;
}

.plan-draft__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}

.plan-draft__note {
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
}

/* 计划树自带边框，任务态去掉原来的蓝边与投影，避免两层框叠在一起 */
.job-card.is-plan {
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
}

.job-card__hint {
  margin: 8px 0 0;
  color: var(--ink-3);
  font-size: var(--agent-fs-md);
  line-height: 1.6;
}
</style>

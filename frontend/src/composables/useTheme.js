/**
 * 主题引擎 · light / dark / auto 三态
 *
 * 设计要点：
 * - `data-theme` 由本模块与 index.html 内联脚本共同维护，内联脚本负责首屏防闪白。
 * - Element Plus 官方深色变量依赖 `html.dark` 类，所以两个标记同时打。
 * - `auto` 跟随系统，监听 prefers-color-scheme 变化。
 */
import { computed, ref } from 'vue'

const STORAGE_KEY = 'filepilot-theme'
export const THEME_MODES = ['auto', 'light', 'dark']

const MODE_LABEL = {
  auto: '跟随系统',
  light: '浅色',
  dark: '深色',
}

const media =
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

function readStored() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return THEME_MODES.includes(value) ? value : 'auto'
  } catch {
    return 'auto'
  }
}

const mode = ref(readStored())
const systemDark = ref(Boolean(media && media.matches))
const resolved = computed(() => {
  if (mode.value === 'auto') return systemDark.value ? 'dark' : 'light'
  return mode.value
})

function paint() {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  const value = resolved.value
  root.dataset.theme = value
  root.classList.toggle('dark', value === 'dark')
  root.style.colorScheme = value
}

function setMode(next) {
  if (!THEME_MODES.includes(next)) return
  mode.value = next
  try {
    window.localStorage.setItem(STORAGE_KEY, next)
  } catch {
    /* 隐私模式下忽略 */
  }
  paint()
}

/**
 * 点击语义：总是切到「与当前实际显示相反」的模式。
 *
 * 为什么不做三态循环：模式是 auto 而系统恰好是浅色时，
 * auto → light 这一步在屏幕上毫无变化，用户会以为点击没反应，
 * 必须点两下才能变暗。改成「切成相反」之后，一次点击必定生效。
 *
 * auto 只作为初始默认值；一旦手动切换，就进入显式的浅 / 深。
 */
function toggleMode() {
  setMode(resolved.value === 'dark' ? 'light' : 'dark')
}

if (media) {
  const onSystemChange = (event) => {
    systemDark.value = event.matches
    if (mode.value === 'auto') paint()
  }
  if (media.addEventListener) media.addEventListener('change', onSystemChange)
  else if (media.addListener) media.addListener(onSystemChange)
}

paint()

export function useTheme() {
  return {
    mode,
    resolved,
    setMode,
    toggleMode,
    label: computed(() => MODE_LABEL[mode.value] || '跟随系统'),
  }
}

export function initTheme() {
  paint()
  return useTheme()
}

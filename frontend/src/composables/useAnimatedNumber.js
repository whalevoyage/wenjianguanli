/**
 * 把统计卡片里的字符串数值（如 "14 B" / "0" / "1.2 GB"）做成「计数滚动 + 放大回弹」动画。
 *
 * 用法：
 *   const { displayValue, bumpKey } = useAnimatedNumber(() => props.value)
 *   <div class="stat-card__value" :key="bumpKey" :class="{ 'is-bumping': bumpKey > 0 }">{{ displayValue }}</div>
 *
 * - 挂载时从 0 滚到目标值，并弹一下；目标值变化时从当前值继续滚到新值。
 * - 只滚动数字部分，单位/后缀原样保留。
 * - 尊重 prefers-reduced-motion：直接跳变，不播动画。
 * - 纯原生 rAF + CSS 动画，不引第三方库。
 */
import { onBeforeUnmount, ref, watch } from 'vue'

const REDUCED =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

function easeOutQuint(t) {
  return 1 - Math.pow(1 - t, 5)
}

/** 把 "14 B" / "1.2 GB" / "3 个项目" 拆成 { num, suffix, decimals }；非数字原样返回。
   suffix 保留数字后的原始字符（含空格），避免把 "14 B" 显示成 "14B"。 */
function parse(str) {
  const s = String(str ?? '')
  const m = s.match(/^([\d.,]+)(.*)$/)
  if (!m) return { num: null, suffix: s, decimals: 0 }
  const raw = m[1].replace(/,/g, '')
  const num = Number.parseFloat(raw)
  if (!Number.isFinite(num)) return { num: null, suffix: s, decimals: 0 }
  const decimals = (raw.split('.')[1] || '').length
  return { num, suffix: m[2], decimals }
}

function format(v, decimals) {
  if (decimals > 0) return v.toFixed(decimals)
  return String(Math.round(v))
}

function resolve(source) {
  if (typeof source === 'function') return source()
  if (source && typeof source === 'object' && 'value' in source) return source.value
  return source
}

export function useAnimatedNumber(source, options = {}) {
  const duration = options.duration ?? 700

  const initial = String(resolve(source) ?? '')
  const p0 = parse(initial)
  const initialDisplay =
    p0.num === null ? initial : format(0, p0.decimals) + p0.suffix

  const displayValue = ref(initialDisplay)
  const bumpKey = ref(0)
  let raf = null
  let current = null

  function stop() {
    if (raf) {
      cancelAnimationFrame(raf)
      raf = null
    }
  }

  function setInstant(val) {
    const { num, suffix, decimals } = parse(val)
    displayValue.value = num === null ? val : format(num, decimals) + suffix
  }

  function animateTo(val) {
    const { num, suffix, decimals } = parse(val)
    if (num === null) {
      displayValue.value = val
      if (!REDUCED) bumpKey.value += 1
      return
    }
    if (REDUCED) {
      displayValue.value = format(num, decimals) + suffix
      current = num
      return
    }
    const start = current === null ? 0 : current
    const end = num
    const t0 = performance.now()
    stop()
    const tick = now => {
      const p = Math.min(1, (now - t0) / duration)
      const v = start + (end - start) * easeOutQuint(p)
      current = v
      displayValue.value = format(v, decimals) + suffix
      if (p < 1) {
        raf = requestAnimationFrame(tick)
      } else {
        current = end
        displayValue.value = format(end, decimals) + suffix
        raf = null
      }
    }
    raf = requestAnimationFrame(tick)
    bumpKey.value += 1
  }

  watch(
    () => resolve(source),
    (newVal, oldVal) => {
      if (newVal === oldVal) return
      animateTo(newVal)
    },
    { immediate: true },
  )

  onBeforeUnmount(stop)

  return { displayValue, bumpKey }
}

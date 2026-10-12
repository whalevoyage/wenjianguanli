import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * 跨页「带着目录过去」。
 *
 * 13 个选项卡原来各管各的：在空间地图看到一个大目录，想去查重 / 清理 / 迁移，
 * 得自己切过去、再把同一个目录选一遍 —— 同一件事用户做两次。
 *
 * 这里只做最小的接力：源页面把目录放进路由 query（`?focus=<目录>`），
 * 目标页进来时取走、填进输入框。
 *
 * ★ 刻意**不自动开跑**。项目一贯的规矩是"先出清单、等用户确认"，
 *   自动开跑会绕过那一步。目标页只把目录填好，按钮还是要用户点。
 */

const ORIGIN_LABEL = {
  space: '空间地图',
  scan: '扫描分析',
  duplicates: '重复文件',
  junk: '垃圾清理',
  projects: '项目雷达',
  home: '首页',
  agent: 'Agent 助手',
}

/** 源页面用：跳到某个选项卡，并把当前目录带过去。 */
export function useHandoff() {
  const router = useRouter()

  function handoffTo(name, path, origin = '') {
    const target = String(path || '').trim()
    if (!target) return
    router.push({ name, query: { focus: target, ...(origin ? { from: origin } : {}) } })
  }

  return { handoffTo }
}

/**
 * 目标页用：进来时把带过来的目录填进输入框。
 *
 * 返回 `handed`（带过来的目录，空串表示这次不是被带过来的）与 `from`（来源页面的中文名），
 * 页面拿它们出一条提示，让用户知道"这个框为什么已经填好了"。
 */
export function useHandoffTarget(targetRef, options = {}) {
  const multiple = !!options.multiple
  const route = useRoute()
  const handed = ref('')
  const from = ref('')

  onMounted(() => {
    const focus = typeof route.query.focus === 'string' ? route.query.focus : ''
    if (!focus) return
    // 多源模式：把带过来的目录**追加**进列表，而不是覆盖已有的选择
    if (multiple && Array.isArray(targetRef.value)) {
      targetRef.value = Array.from(new Set([...targetRef.value, focus]))
    } else {
      targetRef.value = focus
    }
    handed.value = focus
    const origin = typeof route.query.from === 'string' ? route.query.from : ''
    from.value = ORIGIN_LABEL[origin] || ''
  })

  return { handed, from }
}

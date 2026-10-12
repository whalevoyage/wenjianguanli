import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { startClean } from '../api/filepilot'
import { useJob } from './useJob'

/**
 * 结果页通用：「勾选 → 送进隔离区 / 送进系统回收站」。
 *
 * ★ 复用既有的 `POST /api/clean`，**不新增"直接删文件"的接口** ——
 *   两种动作都可还原（隔离区 / Windows 回收站），与全站「删除先进隔离区」的承诺一致；
 *   真正的物理删除仍只在回收站页的「彻底删除」。
 *
 * 用法：`const sel = useSelectionClean('scan.clean')`，
 * 然后 `v-model` 到 `sel.selected`（一个 path 的 Set）、`sel.cleanMode`，
 * 勾选变化用 `sel.toggle(path)`，提交用 `sel.submit(items)`。
 */

export const CLEAN_MODE_OPTIONS = [
  { label: '送进隔离区（可还原）', value: 'isolate' },
  { label: '送进系统回收站', value: 'recycle' },
]

export function useSelectionClean(jobKey) {
  const selected = ref(new Set())
  const cleanMode = ref('isolate')
  const clean = useJob(jobKey)

  function toggle(key) {
    const next = new Set(selected.value)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    selected.value = next
  }

  function setAll(keys) {
    selected.value = new Set(keys)
  }

  function clear() {
    selected.value = new Set()
  }

  /** items: [{path, size, is_dir?, rule?}]。跑完清空选择；返回 job（供调用方判断是否成功）。 */
  async function submit(items) {
    if (!items.length) return null
    const job = await clean.run(() => startClean({ items, mode: cleanMode.value }))
    if (job) {
      const where = cleanMode.value === 'recycle' ? '系统回收站' : '隔离区'
      ElMessage.success(`已把 ${items.length} 项送进${where}，可随时还原`)
      clear()
    }
    return job
  }

  return {
    selected,
    cleanMode,
    clean,
    modeOptions: CLEAN_MODE_OPTIONS,
    toggle,
    setAll,
    clear,
    submit,
  }
}

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { cancelJob, rollbackJob, subscribeJob } from '../api/filepilot'

/**
 * 任务订阅：所有耗时操作都通过它拿进度。
 *
 *   const { job, running, run, cancel, rollback } = useJob('scan')
 *
 * **key 很重要**：它决定这个订阅的身份。切换选项卡时组件会被卸载，
 * 但任务在后端仍在跑——有了 key，回到这一页时会自动把上次的进度接回来，
 * 不会再出现「切走再回来，进度没了，看起来像任务被取消」的错觉。
 */
const ACTIVE_JOBS = new Map()

const TERMINAL = ['done', 'failed', 'cancelled']

export function useJob(key = '') {
  const job = ref(null)
  const running = ref(false)
  let closeStream = null

  const finished = computed(() => TERMINAL.includes(job.value?.status || ''))

  function attach(jobId, restarted = false) {
    if (key) ACTIVE_JOBS.set(key, jobId)
    closeStream?.()
    running.value = true
    closeStream = subscribeJob(jobId, next => {
      if (!next) return
      job.value = next
      // 恢复进来的任务如果已经跑完了，就别停在"进行中"
      if (TERMINAL.includes(next.status)) {
        running.value = false
        closeStream?.()
        closeStream = null
      }
    })
    return restarted
  }

  /** 回到页面时，把上次那个还在跑的任务接回来 */
  function restore() {
    const saved = key ? ACTIVE_JOBS.get(key) : null
    if (!saved || job.value?.id === saved) return
    attach(saved)
  }

  async function run(starter) {
    try {
      const payload = await starter()
      job.value = payload.job
      attach(payload.job_id)
      return payload.job
    } catch (error) {
      ElMessage.error(error.message)
      running.value = false
      return null
    }
  }

  /** 直接拿一个已存在的任务（例如从任务列表点进来） */
  function watch(jobId) {
    attach(jobId)
  }

  async function cancel() {
    if (!job.value?.id) return
    try {
      job.value = await cancelJob(job.value.id, key ? `useJob(${key})` : 'useJob')
      ElMessage.success('已请求中断任务')
    } catch (error) {
      ElMessage.error(error.message)
    }
  }

  async function rollback() {
    if (!job.value?.id) return null
    try {
      const result = await rollbackJob(job.value.id)
      const text = result.errors?.length
        ? `回滚完成，但有 ${result.errors.length} 项失败`
        : `已回滚 ${result.restored_count} 项`
      result.errors?.length ? ElMessage.warning(text) : ElMessage.success(text)
      return result
    } catch (error) {
      ElMessage.error(error.message)
      return null
    }
  }

  /* 卸载只断开订阅，绝不取消任务 */
  onBeforeUnmount(() => {
    closeStream?.()
    closeStream = null
  })

  onMounted(restore)

  return { job, running, finished, run, watch, cancel, rollback, restore }
}

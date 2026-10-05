import { ref } from 'vue'
import { getConfig } from '../api/filepilot'

/** 全局共享的后端配置（盘符、排除规则、垃圾规则）与用户当前的排除设置。 */
const config = ref(null)
const excludes = ref([])
let pending = null

export function useConfig() {
  async function load() {
    if (config.value) return config.value
    if (!pending) {
      pending = getConfig()
        .then((data) => {
          config.value = data
          if (!excludes.value.length) excludes.value = [...(data.excludes || [])]
          return data
        })
        .finally(() => {
          pending = null
        })
    }
    return pending
  }

  return { config, excludes, load }
}

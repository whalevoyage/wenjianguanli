import { createApp } from 'vue'
// 组件样式由 ElementPlusResolver 按需注入；这里只保留主题变量。
import 'element-plus/theme-chalk/dark/css-vars.css'
// ⚠️ 例外：ElMessage / ElMessageBox 是**命令式调用**的（ElMessageBox.confirm(...)），
// 模板里没有对应标签，ElementPlusResolver 扫模板时扫不到 → 样式永远不注入，
// 表现为「确认框裸着跑到左上角、吐司没有底色圆角」。
// 所以这两份样式必须在这里手动引，且是全局的（哪个页面用都生效）。
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'
import 'element-plus/theme-chalk/el-overlay.css'
import App from './App.vue'
import router, { collectViewLoaders } from './router'
import { initTheme } from './composables/useTheme'
import './styles/global.css'

// 主题必须在挂载前就确定，避免首屏闪一下浅色
initTheme()

const app = createApp(App)

app.use(router)

/* 首帧原子化：等初始路由（含它那个懒加载 chunk）解析完再挂载。
   直接 mount 会先画出一帧「侧栏已经在、内容区还空着」的半成品 ——
   实测启动时有 3 帧（≈50ms）就是这样，观感就是开机闪一下。
   isReady() 一直等到初始导航 + chunk 就绪，所以首帧就是一张完整页面。
   catch 兜底：万一初始导航出错（比如 hash 写坏了），也必须挂载，不能白屏不给进。 */
router
  .isReady()
  .catch(() => {})
  .then(() => app.mount('#app'))

/* 路由 chunk 预热：等首屏画完（load）之后分批把 13 个页面的模块拉下来。
   间隔 160ms 是为了不在同一帧里塞 13 个模块转换，那反而会把主线程堵住。
   任何一步失败都无所谓 —— 真正导航时还会再加载一次，这里只是提前。 */
function warmUpViews() {
  collectViewLoaders().forEach((load, index) => {
    window.setTimeout(() => {
      try {
        const pending = load()
        if (pending && typeof pending.catch === 'function') pending.catch(() => {})
      } catch {
        /* 预热失败不影响正常导航 */
      }
    }, 500 + index * 160)
  })
}

if (document.readyState === 'complete') warmUpViews()
else window.addEventListener('load', warmUpViews, { once: true })

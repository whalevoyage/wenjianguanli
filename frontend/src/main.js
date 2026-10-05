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
import router from './router'
import { initTheme } from './composables/useTheme'
import './styles/global.css'

// 主题必须在挂载前就确定，避免首屏闪一下浅色
initTheme()

const app = createApp(App)

app.use(router).mount('#app')

import { createApp } from 'vue'
import {
  ElAlert,
  ElButton,
  ElCollapse,
  ElCollapseItem,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElInput,
  ElOption,
  ElProgress,
  ElSegmented,
  ElSelect,
  ElSkeleton,
  ElTable,
  ElTableColumn,
  ElTag,
  vLoading,
} from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './styles/global.css'

const app = createApp(App)

;[
  ElAlert,
  ElButton,
  ElCollapse,
  ElCollapseItem,
  ElDrawer,
  ElEmpty,
  ElIcon,
  ElInput,
  ElOption,
  ElProgress,
  ElSegmented,
  ElSelect,
  ElSkeleton,
  ElTable,
  ElTableColumn,
  ElTag,
].forEach((component) => {
  app.component(component.name, component)
})

app.directive('loading', vLoading)
app.use(router).mount('#app')

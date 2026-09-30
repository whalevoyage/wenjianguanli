import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/Dashboard.vue'),
    meta: {
      title: '本地文件总览',
      description: '把空间状态、最近扫描、备份和操作记录收在一个工作台里。',
    },
  },
  {
    path: '/scan',
    name: 'scan',
    component: () => import('../views/Scan.vue'),
    meta: {
      title: '扫描分析',
      description: '一次扫描内直接查看类型分布、大文件和 DeepSeek 建议。',
    },
  },
  {
    path: '/duplicates',
    name: 'duplicates',
    component: () => import('../views/DuplicateFiles.vue'),
    meta: {
      title: '重复文件',
      description: '用真实本地数据识别重复副本，并移动到恢复区。',
    },
  },
  {
    path: '/organizer',
    name: 'organizer',
    component: () => import('../views/Organizer.vue'),
    meta: {
      title: '文件整理',
      description: '先预览真实整理结果，再执行和撤销。',
    },
  },
  {
    path: '/backup',
    name: 'backup',
    component: () => import('../views/Backup.vue'),
    meta: {
      title: '项目备份',
      description: '给关键目录快速留下一个可回看的 ZIP 快照。',
    },
  },
  {
    path: '/reports',
    name: 'reports',
    component: () => import('../views/Reports.vue'),
    meta: {
      title: '报告导出',
      description: '把分析结果沉淀成更适合留档的文本报告。',
    },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('../views/Logs.vue'),
    meta: {
      title: '操作日志',
      description: '按时间回看每一次真实本地操作。',
    },
  },
  { path: '/large-files', redirect: () => ({ path: '/scan', query: { focus: 'large-files' } }) },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

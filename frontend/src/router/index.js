import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/Home.vue'),
    meta: {
      group: '总览',
      title: '首页',
      description: '先看全局：各盘容量、可释放空间、最近任务，再决定下一步做什么。',
    },
  },
  {
    path: '/agent',
    name: 'agent',
    component: () => import('../views/Agent.vue'),
    meta: {
      group: '总览',
      title: 'Atlas 智能助手',
      description: '用大白话下命令，翻译成计划后再交给本地功能执行。',
    },
  },
  {
    path: '/space',
    name: 'space',
    component: () => import('../views/SpaceMap.vue'),
    meta: {
      group: '分析',
      title: '空间地图',
      description: '用矩形树图看清是哪个目录在占空间，点进去逐层钻取。',
    },
  },
  {
    path: '/virtual',
    name: 'virtual',
    component: () => import('../views/Virtual.vue'),
    meta: {
      group: '分析',
      title: '整理预览',
      description: '先看「整理后会是什么样」：按类型、时间、体积或项目聚拢，全程不动文件。',
    },
  },
  {
    path: '/projects',
    name: 'projects',
    component: () => import('../views/Projects.vue'),
    meta: {
      group: '分析',
      title: '项目雷达',
      description: '把全盘的项目一个个拎出来：类型、体积、最后活跃，顺手揪出僵尸项目。',
    },
  },
  {
    path: '/scan',
    name: 'scan',
    component: () => import('../views/Scan.vue'),
    meta: {
      group: '分析',
      title: '扫描分析',
      description: '扫描整块盘或任意目录，统计数量、体积、类型分布和大文件。',
    },
  },
  {
    path: '/duplicates',
    name: 'duplicates',
    component: () => import('../views/Duplicates.vue'),
    meta: {
      group: '分析',
      title: '重复文件',
      description: '三层过滤找出内容完全相同的文件，确认后送进隔离区。',
    },
  },
  {
    path: '/junk',
    name: 'junk',
    component: () => import('../views/Junk.vue'),
    meta: {
      group: '分析',
      title: '垃圾文件',
      description: '按三级安全分级找出临时文件、缓存、日志和空文件夹。',
    },
  },
  {
    path: '/migrate',
    name: 'migrate',
    component: () => import('../views/Migrate.vue'),
    meta: {
      group: '执行',
      title: '跨盘迁移',
      description: '复制 → 逐文件校验 → 校验通过才删源，可选保留目录联接。',
    },
  },
  {
    path: '/backup',
    name: 'backup',
    component: () => import('../views/Backup.vue'),
    meta: {
      group: '执行',
      title: '项目备份',
      description: '给目录留一个 ZIP 快照，排除依赖与构建产物。',
    },
  },
  {
    path: '/jobs',
    name: 'jobs',
    component: () => import('../views/Jobs.vue'),
    meta: {
      group: '执行',
      title: '任务中心',
      description: '每个任务一条流程图，实时进度，可中断、可回滚。',
    },
  },
  {
    path: '/trash',
    name: 'trash',
    component: () => import('../views/Trash.vue'),
    meta: {
      group: '记录',
      title: '回收站',
      description: '清理掉的东西先关在这里，可以还原到原位。',
    },
  },
  {
    path: '/logs',
    name: 'logs',
    component: () => import('../views/Logs.vue'),
    meta: {
      group: '记录',
      title: '操作日志',
      description: '完整审计流水，按类型和结果筛选，可导出对账。',
    },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

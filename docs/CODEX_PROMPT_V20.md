# FilePilot Lite · 全站 UI 优化（v20）执行提示词

你在 `D:\FilePilotLite`（Vue 3 + Vite + Element Plus 前端，FastAPI 后端）工作。
本轮**只改前端**，不改任何后端 Python 文件。目标：修掉全站八个通病，保持既有设计语言不变。

## 一、项目事实（先读再动手）

- 设计令牌**唯一事实源是 `frontend/src/styles/global.css`**；视图与组件里**不允许出现字面量颜色和字面量时长**，只写 `var(--*)`。
  - 豁免：`utils/miniMarkdown.js` 不含颜色；`AppSidebar.vue` 里 mask 的 `black` 是 alpha 通道，保留。
- Element Plus 是**手动注册**的（`main.js` 里逐个 `app.component(...)`）。用到没注册的组件，标签会原样渲染成裸 DOM 且不报错——用新组件前先看注册名单。
- `global.css` 里 Element Plus 的输入聚焦样式**带 `!important`**，覆写它必须同样用 `!important`。
- 后端分层：`main.py`(路由，只校验+建任务) → `jobs.py`(任务/SSE) → `services/` → `fsutil.py` → `store.py`。状态存 `.filepilot/`（已在 .gitignore）。
- 端口约定：**8000 是用户正在跑的实例，绝对不要动它**；验证一律用独立实例 8001。
- 侧栏导航是 `App.vue` 里硬编码的 `navGroups`（与路由是两套东西）；本轮不新增页面，不要动它。
- 日志页的 Job 类型中文名在 `views/Logs.vue` 的 `TYPE_LABELS`。

## 二、已定稿、禁止回退的决策（碰到就停）

1. **全站无页头**：12 个视图的 `PageHeader` 已全部移除（组件文件还在但零引用）。不要加回任何大号 H1/eyebrow/meta 胶囊。
2. 页面级按钮**一律并入既有的一行**（筛选行右端或面板标题行右侧），**永远不要为按钮新开一行**。
3. 侧栏：贴边通高、无分组标题、13 项 + 底部「外观/收起」两行、滚动条隐藏、底部渐隐用 `mask-image`（不是覆盖式渐变）。
4. Agent 页（`views/Agent.vue`）是 v19 定稿形态：居中大标题 + 输入框贴底、空态恰好 4 枚示例胶囊排满一行、
   内层 textarea 样式带 `!important`、**聚焦态零视觉变化**（没有 `:focus-within` 规则，别加任何聚焦反馈）、
   右下角 = `Enter 发送` · 齿轮(API Key) · 模型名(点开切模型) · 发送键，`+` 菜单只放 配方/盘符。本轮除小修外**不动 Agent 页**。
5. 灰底白卡（`--bg`/`--surface`），层次只来自「卡片比页面底白」；不引图表库、不新增第三方依赖、不做毛玻璃。
6. 表格规范沿用 `DataTable`（sticky 表头、`rowClassName` 行高亮）；时间戳见下条。

## 三、八项改动（按此顺序做）

### 1. 空态统一：删掉整句文案

`components/JobProgress.vue` 的空态现在是「还没有任务。填好上面的路径后点击开始。」——它在 6+ 个页面重复出现。

- 给 `JobProgress` 加 prop `emptyTitle`（默认「暂无任务」），空态只显示这一个名词短语；
- 各业务页传入各自的短标题：空间地图「先生成一次空间地图」、扫描「先运行一次扫描」、垃圾「先做一次垃圾检查」、
  查重「先跑一次查重」、迁移「先生成迁移计划」、备份「先做一次备份」；
- `views/Virtual.vue` 与 `views/Projects.vue` 结果区的 `EmptyState`：**删掉说明句**，只留名词标题
  （「先生成一次预览」「先扫描一次项目」）+ 图标 +（可选）一个动作按钮。

### 2. 空的「执行进度 / 任务进度」卡整卡隐藏

`Scan.vue` / `Migrate.vue` / `Junk.vue` / `Backup.vue` / `SpaceMap.vue` 里包着 `JobProgress` 的第二张卡：
当前没有任务时整卡不渲染（`v-if` 绑定 `useJob()` 的 job 是否存在），有任务才出现。

### 3. 时间戳全站统一为短格式

- `utils/formatters.js` 新增 `formatShortTime(value, fallback = '—')`：输出 `MM-DD HH:mm`
  （解析 `YYYY-MM-DD HH:MM(:SS)(+08:00)` 与 ISO `T` 两种形态；解析失败原样返回）。
- 应用三处：`Home.vue`（删除本地 `shortTime()`，接口调用列表改用全局函数）、
  `Jobs.vue` 任务列表项、`Logs.vue` 表格「操作时间」列；
- 完整时间放 `title` tooltip（用现有 `formatDateTime`）。

### 4. 表单型页面内容区收窄

`SpaceMap / Scan / Junk / Duplicates / Migrate / Backup / Virtual / Projects` 八个视图：
页面内容根容器 `max-width: 920px; margin: 0 auto`（建议在 `global.css` 加一个 `.form-page` 类统一收口）。
`Jobs / Logs / Trash` 三个表格页**保持全宽**。改完逐页截图，确认没有出现横向滚动或挤压。

### 5. 全宽 el-alert 降级为面板内注脚

`Scan / Backup / Virtual / Projects`（灰色 info）与 `Migrate`（黄色 warning）：
- 新增全局类 `.panel-note`：`display:flex; gap:6px; align-items:center; color:var(--ink-3); font-size:var(--fs-note);`，
  图标 14px；**不套色带、不占整行**；
- Migrate 的危险提示保留 `--warning-soft` 底色，但**收成一行、随内容宽度**，不再是整宽色带；
- 文案一字不改（安全信息必须保留）。

### 6. 任务中心（`Jobs.vue`）

- 任务列表项标题行尾加**状态点**：沿用 `global.css` 里 `.status-dot` 规范（6px 圆点 + 文字，
  success/warning/danger 三色已有），数据取 job.status（done/cancelled/failed/running…），颜色映射与现有语义一致；
- 列表时间用 `formatShortTime`，`title` 给完整时间；
- 两栏比例 5:7 → **4:8**；
- 详情卡充实为三段（用 `--hairline` 分隔，空段不渲染）：**阶段条（现有）/ 参数（键值对，取 job.params）/ 结果摘要或错误信息**。

### 7. 操作日志（`Logs.vue`）

- 「备注」列不再拼接路径（只保留动作说明；数据里如果 note 本身含路径，前端截掉或显示「—」）；
- 「操作时间」列用 `formatShortTime` + tooltip 完整时间。

### 8. 首页（`Home.vue`）

- 「接口调用」从 TOP 8 改为 **TOP 5**（`data.api_stats.slice(0, 5)`），「查看全部 →」保留；
- 删除本地 `shortTime()`（改用全局 `formatShortTime`）。

## 四、验证（全部通过才算完成）

1. 语法/构建：`cd frontend && npx vite build --outDir .build_check --emptyOutDir`（完成后删除 `.build_check` 目录）。
   逐个改动过的 `.vue` 也可以先用 `@vue/compiler-sfc` 的 parse+compileScript+compileTemplate 快速校验。
2. 冒烟测试（**必须用 8001 独立实例，别碰 8000**）：
   - 起 `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001`（后台）；
   - `FILEPILOT_API=http://127.0.0.1:8001/api python tests/smoke_test.py`，要求 **73/73 全绿**；测完关掉 8001。
3. 令牌自检：`grep -rnE '#[0-9a-fA-F]{3,6}|rgba?\(' frontend/src/views frontend/src/components` 应无新增命中（既有豁免除外）；
   同样检查 `transition:[^;]*[0-9]+(s|ms)` 的字面量时长。
4. 视觉自查：用本机现成 headless Chromium 逐页截图（13 个路由：
   `/`、`/agent`、`/space`、`/virtual`、`/projects`、`/scan`、`/duplicates`、`/junk`、`/migrate`、`/backup`、`/jobs`、`/trash`、`/logs`）：
   `"C:\Users\faker\AppData\Local\ms-playwright\chromium_headless_shell-1234\chrome-headless-shell-win64\chrome-headless-shell.exe"
   --headless --disable-gpu --no-sandbox --hide-scrollbars --window-size=1385,860 --screenshot=输出.png --virtual-time-budget=5000 "http://127.0.0.1:5173/#/路由"`
   - 深浅**两态**都要看（用侧栏「外观」切换主题）；
   - 重点核对：空态无整句文案、无空进度卡、时间戳短格式、表单页宽度、任务中心状态点、首页 TOP 5；
   - 临时 seed/调试代码（如强制展开弹层的 `:visible="true"`）**用完必须删除**，截图文件不要留在仓库根目录。

## 五、交付

- 改动代码 + `docs/UI_REVAMP.md` 追加「## v20」章节（改动清单 + 新踩的坑，风格沿用既有章节）；
- 一个 commit（中文消息，概括本轮八项），**只 commit 不 push**；
- 最终回复里给出：改动文件清单、冒烟测试结果、深浅两态自查结论、以及你发现的但没动的问题（如有）。

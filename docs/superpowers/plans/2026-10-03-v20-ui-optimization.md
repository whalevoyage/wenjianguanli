# v20 UI 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/CODEX_PROMPT_V20.md` 完成全站八项前端 UI 优化，并通过构建、冒烟、令牌自检与视觉检查。

**Architecture:** 保持现有 Vue 3 + Element Plus 结构，优先复用 `JobProgress`、`EmptyState`、`useJob` 和全局 CSS 令牌。新增的时间格式、表单页宽度和面板注脚采用小型共享工具/类，页面只负责传递语义数据。

**Tech Stack:** Vue 3、Vite、Element Plus、CSS custom properties、FastAPI smoke test。

**Spec:** `docs/CODEX_PROMPT_V20.md`

## Global Constraints

- 只改前端与 `docs/UI_REVAMP.md`，不改后端 Python 文件。
- 8000 端口不动，冒烟验证使用 8001。
- 不新增第三方依赖；视图与组件中的颜色、时长使用 CSS 令牌。
- 不恢复 PageHeader，不新增页面，不改侧栏导航，不改 Agent 页既定形态。
- 最终只创建一个中文 commit，不 push。

---

### Task 1: 共享空态、时间格式与全局样式

**Files:** `frontend/src/components/JobProgress.vue`, `frontend/src/utils/formatters.js`, `frontend/src/styles/global.css`

- [ ] 增加 `JobProgress.emptyTitle`，默认“暂无任务”，空态只渲染标题。
- [ ] 增加并导出 `formatShortTime`，覆盖空值、两种时间格式、解析失败原样返回。
- [ ] 增加 `.form-page`、`.panel-note` 和任务详情分段所需的共享样式；将任务双栏比例改为 4:8。
- [ ] 运行前端构建确认共享改动可编译。

### Task 2: 表单页空态、进度卡和提示条

**Files:** `SpaceMap.vue`, `Scan.vue`, `Junk.vue`, `Duplicates.vue`, `Migrate.vue`, `Backup.vue`, `Virtual.vue`, `Projects.vue`

- [ ] 为业务页传入对应 `emptyTitle`，并将表单根容器加入 `.form-page`。
- [ ] 无任务时隐藏第二张进度卡；有任务时保持现有操作能力。
- [ ] 将指定 `el-alert` 降为 `.panel-note`，保留文案和 Migrate 警告语义。
- [ ] 删除 Virtual/Projects 空结果说明句，只保留标题、图标和可选动作。

### Task 3: 首页、任务中心和操作日志

**Files:** `Home.vue`, `Jobs.vue`, `Logs.vue`

- [ ] 首页删除本地时间函数，接口调用改 TOP 5 并使用共享短时间格式与 tooltip。
- [ ] 任务中心加入状态点、短时间、4:8 布局及条件渲染的参数/结果/错误详情段。
- [ ] 日志备注移除路径拼接，操作时间改短格式并添加完整时间 tooltip。

### Task 4: 文档与质量检查

**Files:** `docs/UI_REVAMP.md`

- [ ] 追加 `## v20`，记录改动清单与本轮踩坑。
- [ ] 执行 Vite build 并删除 `.build_check`。
- [ ] 启动 8001，执行 73 项 smoke test，结束 8001。
- [ ] 执行颜色/时长字面量扫描，检查 Git diff 仅包含允许文件。
- [ ] 使用现成 Chromium 对 13 个路由完成亮/暗态截图检查，不将截图留在仓库根目录。
- [ ] 创建一个中文 commit，不 push。

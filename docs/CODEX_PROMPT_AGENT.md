# 给 Codex 的实施提示词（Agent 助手页重设计）

> 使用方法：把下面「提示词正文」整段复制给 Codex。它面向的仓库是 `D:\FilePilotLite`（显示名 Atlas）。

---

## 提示词正文

你要重做 `frontend/src/views/Agent.vue` 这一个页面的表现层，目标是**主流 Agent 对话产品的极简形态**。先完整读这两份文件再动手：

1. `docs/UI_REVAMP.md` —— 全站视觉规范与禁用清单（铁律）。
2. `docs/AGENT_UI_REDESIGN.md` —— 本次设计规格，逐项照做，结构、尺寸、令牌都在里面。

### 范围与边界

- **只改 `Agent.vue` 一个文件**（模板 + 样式）。不碰 `JobProgress.vue` 内部、不碰 `PageHeader.vue`、不碰任何后端接口与 `agentChat` / 配方 / 任务启动逻辑——它们是现成能力，只改它们在页面上的呈现方式。
- 用户已确认的三项决策：配方 = 输入框旁按钮 + 弹层；空态 = 输入框垂直居中；PageHeader 保留但删 meta 胶囊。

### 必做清单（对应设计文档章节号）

1. **§1 空态**：无消息时输入框垂直居中（约视口 45% 处），下方 4 枚示例胶囊（沿用 EXAMPLES），无引导句；发第一条消息后过渡到顶部消息流（过渡用 `--duration-medium` + `--ease`，禁字面量时长）。
2. **§2 消息流**：max-width 760px 居中；用户消息右对齐 `--primary-soft` 气泡（圆角 18px / 右下 6px，去掉"你"标签）；助手消息左对齐**无气泡纯文本**（`--ink`，14px/1.7），来源与 token 收成末尾一行 11px `--ink-3`（如 `本地规则 · 0 token`）；消息区独立滚动且**保留可见滚动条**。
3. **§3 计划卡**：保留现有信息结构（计划名 + code 胶囊 + 参数 + 执行/存配方双按钮），样式换新皮（`--surface` + `--hairline` + `--sh-1`，圆角 16px），内联在助手消息下方左对齐。
4. **§4 进度卡**：任务启动后 `JobProgress` 作为一张卡**内联插入对话流末尾**（左侧 2px `--primary` 竖条）；任务结束后收成一条摘要行（结果名词短语 + 「查看 →」跳 `/jobs`）。右侧「执行进度」面板删除。
5. **§5 输入区**：吸底、760px 居中、上方 24px 渐隐遮罩；一体式输入框（圆角 20px，`--surface` + `--hairline`，聚焦 `--sh-focus`）；左侧星形配方按钮、右侧主色圆形发送按钮内嵌右下；**Enter 发送 / Shift+Enter 换行**（替换现有 Ctrl+Enter）；输入框下方居中一行 11px `--ink-3` 模型状态（`providerText` 改写为短句，如 `本地规则优先 · DeepSeek 已就绪`）。
6. **§6 配方弹层**：`el-popover` 从配方按钮弹出；列表项 = 名称 + 小字（动作 · 时间），点击把原句**填入输入框**（不直接发送）；行尾 danger 文字按钮删除配方；空态一行 `暂无配方`。右侧「配方」面板删除。
7. **§7 降级提示**：未配置 API Key 时，整行 `el-alert` 换成输入框上方一条内联细提示（`--warning-soft` 底 + `--warning-ink` 字，圆角 10px，一行），文案 `未配置 DEEPSEEK_API_KEY，当前仅本地规则可用`。
8. **§8 删除**：右侧栏 `.agent-side` 整块（执行进度 / 配方 / 本地能理解的动作 三张卡）、页头两个 meta 胶囊、`.agent-layout` 双栏网格改单栏。
9. **令牌换新**：文件内所有旧令牌替换——`--text-muted → --ink-3`、`--text-secondary → --ink-2`、`--text-primary → --ink`、`--border → --hairline`、`--border-strong → --hairline-strong`；`.example` / `.recipe` / `.brief-item` 等旧样式随之重写或删除。

### 硬约束（违反任何一条都算失败）

- 只允许 `var(--*)`，无字面量颜色；完工自检 `grep -nE '#[0-9a-fA-F]{3,6}|rgba?\(' frontend/src/views/Agent.vue` 为空。
- 禁 `transition: all`、禁字面量时长（全走 `--duration-*` / `--ease-*`）；自检 `grep -noE 'transition:[^;]*[0-9]+(\.[0-9]+)?(s|ms)' frontend/src/views/Agent.vue` 为空。
- 不加 `backdrop-filter` 到卡片（弹层 popover 除外）；不新增色相；金色只留给 Agent 品牌标识的既有用法，本页不新增金色元素。
- 文案准则：无第二人称、无引导句、无口语；弹层与提示只用名词短语。
- 响应式：<960px 消息流取消 max-width、左右 16px，输入框贴底（§9）。

### 验收

1. 两条 grep 自检为空。
2. `cd frontend && npm run build` 通过（`vite build` 清空 `dist/` 若被环境拦截，用 `--outDir` 换新目录）。
3. 起前后端实际过一遍：空态居中 → 点示例胶囊发消息 → 出计划卡 → 执行 → 进度卡内联出现 → 存配方 → 从弹层填入 → 删除配方；浅色 + 深色两态各看一遍。
4. 完成后在 `docs/UI_REVAMP.md` 追加 v19-a 章节记录改动清单。

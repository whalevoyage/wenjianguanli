# UI 审查报告 + Codex 提示词（v22）

审查范围：`frontend/src` 全部 13 个视图 + 13 个组件 + `styles/global.css`（2026-10-05）。

## 总体评价

颜色纪律执行得很好：全部视图/组件仅 Home.vue 有 1 处内联语义色（磁盘等级色，有意为之），其余全部走 `var(--*)`。
问题集中在四个层面：**魔法尺寸绕过 token、工具类重复造轮子、死代码/静默失效规则、跨页一致性与可访问性**。

---

## 问题清单

### P0 · 真实 bug（规则失效 / 误导 / 溢出）

1. **SpaceMap.vue:204** — scoped 样式 `.space-map-page > .app-card:first-child .job-progress--empty` 试图穿透子组件 JobProgress 的内部类，但没写 `:deep()`，**整条规则静默失效**（scoped 的 data-attribute 加不上子组件内部元素）。
2. **Scan.vue:211** — DataTable 的 `empty-text="先运行一次扫描"` 写在 `v-if="result"` 内部，表格只在有结果时渲染，空态文案**永远不可见**，是误导性死属性。
3. **窄屏横向溢出** — Duplicates.vue:236 `min-width: 620px`、Trash.vue:172 `min-width: 560px`、Home.vue:602 最近任务表 `min-width: 460px`，在窄窗口/移动断点下靠 `overflow-x` 硬撑，出现卡片内横向滚动条。
4. **SpaceMap treemap 不可下钻的块仍渲染为 `<button>`** — 可聚焦、有点击样式，但点了没反应，违反「控件必须言行一致」。
5. **ProjectRadar.vue** — 气泡用 `<circle @click>`，无 `role="button"` / `tabindex`，键盘与读屏完全不可达。

### P1 · 跨页一致性

6. **`.status-dot` 状态圆点至少 4 处各自实现**：Home.vue:631、Logs.vue、Duplicates.vue、JobProgress.vue:340，样式近乎逐字重复，global.css 没有公共版。改一处忘三处是必然结果。
7. **表单工具类逐字重复**：`.row-actions`（7 处）、`.option-block`（3 处）、`.field-label`（5 处）散在各视图 scoped 里，应像 `.metric-strip` / `.form-surface` 一样提升为全局类。
8. **「选一个」控件混用无规则**：el-segmented（Scan 阈值、Virtual 分组、SpaceMap 层数） vs el-select（Projects 僵尸线、Virtual 最小体积、Migrate 模式），同样是 3-4 个短选项却两种控件。
9. **开始按钮排布不统一**：Duplicates 用 `justify-content: flex-end`（按钮独占右侧），其余表单页用 `space-between`（左控件右按钮）。
10. **内联尺寸**：`style="width: 148px"` 出现在 Projects.vue:149、156 和 Virtual.vue:237，应改 class。

### P2 · token 纪律（魔法数字）

11. **Agent.vue 是重灾区，30+ 处字面量**：
    - 圆角 9 种散落值：5/6/7/8/9/10/14/16/26px（1174/1197/1242/1292/1388/1570/1621/1660/1697/1773/1812/1822）
    - 字号 7 种：11/11.5/12/12.5/13/13.5/14px（1409/1463/1516/1571/1589/1689/1699/1748/1814/1828/1852/1862/1887/1921/1952）
    - 魔法时长 `caret-pulse 1.1s`（1870）、魔法间距 `padding-bottom: 90px`（1340）
    - ⚠️ 例外：气泡 `border-radius: 16px`（1773）与 `padding: 8px 14px` 是用户拍板的规格，**值不许动**，只允许 token 化引用。
12. **JobProgress.vue** 硬值：`border-radius: 14px`（178）、`11px`（330）、`box-shadow: 0 0 0 4px`（267）、`height: 8px/2px`（279/252）。
13. **DataTable.vue** 行高魔法值：th 46px / td 54px（64-78）。
14. **global.css 自身重复**：`.job-detail-section` / `.job-detail-section__title` / `.job-params` 在 675-708 和 1419-1452 **定义了两遍**，后一份覆盖前一份，纯冗余。
15. **兼容别名尾大不掉**：`--primary-strong / --accent / --page-bg / --card-bg / --text-* / --border* / --shadow-*` 这套旧别名仍被 11 个文件使用（Backup/Duplicates/Junk/Migrate/Projects/Scan/SpaceMap/Trash/Virtual/JobProgress/TargetPicker），「唯一事实源」实际上有两套词汇表。

### P3 · 死代码

16. **PageHeader.vue 全站零引用**（已 grep 确认），按「全站无 PageHeader」约定应删除。
17. **Scan.vue:245-295** `.chart-item` / `.chart-item__head` / `.chart-bar` / `.chart-bar__fill` 整段死 CSS——类型分布已改用 DataTable 渲染，模板里没有任何元素用这些类。

### 可选 · 设计层面（非 bug，供参考）

18. App.vue 主题切换的 View Transition 在 JS 里硬编码 `duration: 480` 和 easing 字面量，与「动效 token 化」原则相悖（CSS token 到不了 JS，建议抽成模块常量并注释对齐 `--duration-*`）。
19. `.aurora` 72s 无限动画 + `will-change: transform` 常驻一个 `inset: -30%` 的超大合成层，GPU 开销持续存在；可考虑仅在深色主题保留（浅色下 `--aur-a/b` 透明度为 0，动画纯属空转）。

---

## Codex 提示词（直接复制给 Codex）

````
你是 Vue 3 + Element Plus 前端工程师。项目：D:\FilePilotLite（本地文件管理台 Atlas，个人自用）。
只改 frontend/src 下的文件，不碰 backend，不新增依赖，不跑 vite build（会撞守卫）。

## 铁律（违反任何一条都视为失败）
1. 颜色只能用 var(--*)，禁止任何新的字面量颜色。
2. 禁止 transition: all；时长/缓动走 --duration-* / --ease*。
3. 本次是「视觉零变化」重构：所有字面量换成等价 token 或新增 token 时，**计算后的渲染值必须与原值完全一致**。唯一例外是 P0 列出的明确 bug。
4. Agent.vue 的气泡 border-radius: 16px、padding: 8px 14px、用户消息右对齐灰气泡是既定规格，值不许动。
5. 不恢复侧栏分组标题；不给任何页面加 PageHeader；不为按钮新开一行。
6. 侧栏、首页、Agent 页的布局骨架不动。

## 任务（按优先级）

### P0 修 bug
1. SpaceMap.vue:204 的 `.space-map-page > .app-card:first-child .job-progress--empty` 选择器穿透失败（scoped 未加 :deep），规则静默失效。用 :deep() 修正，或更稳妥地给 JobProgress 加 prop/修饰类由父级控制。
2. Scan.vue 删除 DataTable 上永不可见的 empty-text（在 v-if="result" 内），或把空态移到 v-else 分支用 EmptyState 组件（与全站一致）。
3. SpaceMap treemap 中不可下钻的块：若是 <button> 且无点击行为，改为 <div> 或加 disabled，去掉按钮观感。
4. ProjectRadar.vue 的气泡 <circle @click> 补键盘可达：role="button" tabindex="0" @keydown.enter/space 触发同一逻辑，focus-visible 态加描边（走 --sh-focus 或 hairline-strong，与全局 :focus-visible 风格一致）。
5. Duplicates.vue / Trash.vue / Home.vue 最近任务表的固定 min-width（620/560/460px）在 640px 断点下给出降级方案：允许换行、隐藏次要列或收窄 min-width，消灭卡片内横向滚动。

### P1 一致性收敛
6. 把 .status-dot（含 ::before 圆点与 success/warning/danger 变体）提取到 global.css 作为公共类，删除 Home.vue、Logs.vue、Duplicates.vue、JobProgress.vue 里的 4 份私有实现，全部改引用公共类。视觉值以 Home.vue 版本为准。
7. 把 .row-actions / .option-block / .field-label 三件套去重后提升到 global.css（先 diff 各视图版本，取最通用的那份；有差异的保留 scoped 覆盖），删除各视图重复声明。
8. 「选一个」控件定规则并统一：选项 ≤4 且文案 ≤4 字一律 el-segmented，其余 el-select。按此改造 Projects.vue 僵尸线、Virtual.vue 最小体积、Migrate.vue 模式（若选项符合 segmented 条件）。
9. 开始按钮排布统一为「左侧控件组 + 右侧主按钮」的 space-between 模式，改造 Duplicates.vue 的 flex-end 特例。
10. Projects.vue:149,156、Virtual.vue:237 的内联 style="width:148px" 改成语义 class（如 .filter-select），宽度值不变。

### P2 token 化（视觉零变化）
11. Agent.vue 的全部字面量尺寸 token 化：
    - 圆角值收敛映射到 --radius-sm(10) / --radius-ctl(12) / --radius(20) / --radius-lg(26) / --radius-pill；无法等价映射的（5/6/7/8/9/14/16px）在 :root 新增 --radius-xs 等档位或在 Agent scoped 顶部定义局部变量，**值不变**。
    - 字号同理：能映射 --fs-micro/note/body/title 的映射，不能的（12.5/11.5px）定义局部变量。
    - caret-pulse 1.1s、padding-bottom: 90px 抽为局部变量并注释用途。
12. JobProgress.vue 的 14px/11px 圆角、0 0 0 4px 光晕、8px/2px 高度抽为局部变量或映射 token，值不变。
13. DataTable.vue 的 th 46px / td 54px 行高抽为组件顶部局部变量（--dt-row-h 等），值不变。
14. global.css 删除 1419-1452 行重复的 .job-detail-section / .job-params 定义块（与 675-708 重复）。
15. 别名迁移：把 11 个文件里的旧别名（--primary-strong/--accent/--page-bg/--card-bg/--text-primary/--text-secondary/--text-muted/--border/--border-strong/--shadow-sm/--shadow-lg）逐个替换为对应新 token（映射表就在 global.css 175-186 行），全部替换完后删除别名定义块。

### P3 死代码清理
16. 删除 components/PageHeader.vue（全站零引用，已确认）。
17. 删除 Scan.vue 245-295 行的 .chart-item/.chart-bar 死 CSS。
18. App.vue 主题切换 JS 里的 duration: 480 与 easing 字面量抽为模块顶部常量，注释对齐 --duration-slow / --ease-slide。
19. App.vue .aurora 在浅色主题下（--aur-a/b 全透明）暂停动画：html:not([data-theme="dark"]) .aurora { animation: none; }。

## 验收（完成后逐项自查并汇报结果）
- grep -rn "#[0-9a-fA-F]\{3,8\}" frontend/src/views frontend/src/components → 除 Home.vue 磁盘等级色外无新增。
- grep -rn "transition: all" frontend/src → 空。
- grep -rn "status-dot" frontend/src → 只剩 global.css 定义 + 各文件引用，无 scoped 重定义。
- grep -rn "PageHeader" frontend/src → 空。
- grep -rn -- "--primary-strong\|--accent\|--page-bg\|--card-bg" frontend/src → 空。
- 肉眼对照：浅色/深色两主题下，首页、Agent、Scan、SpaceMap、Duplicates、Trash 六个页面与改动前截图对比，除 P0 修复点外无任何像素级差异。
````

---

## 执行建议

- 任务量较大，建议让 Codex **按 P0 → P3 分四个 commit** 提交，每个 commit 单独验收。
- P2（token 化）是最容易引入视觉回归的部分，务必要求「值不变」自查 + 截图对比。
- 15（别名迁移）收益主要是可维护性，如果想控制风险可以单独放最后一批。

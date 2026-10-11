# Agent 助手架构升级（七阶段）

> 落地时间：2026-10-06。本文记录**已经实现**的协议与决策，不是提案。
> 上游背景：`FEATURE_REVAMP_PLAN.md`（Agent 是唯一允许花 token 的模块）。

---

## 一、七阶段完成度（实施后）

| 阶段 | 内容 | 升级前 | 升级后 |
|---|---|---|---|
| 一 | 统一意图结构、参数理解、主动澄清 | ~30% | **完成** |
| 二 | 查询 / 执行 / 辅助三组工具 | ~45% | **完成**（19 条动作全部有前端落点） |
| 三 | 多步骤任务自动编排 | ~5% | **完成**（释放空间 / 迁移 / 整理三条流水线均已端到端跑通） |
| 四 | 主动追问与澄清 | ~50% | **完成** |
| 五 | 上下文记忆 / 偏好 / 常用路径 | ~20% | **完成** |
| 六 | 主动状态感知 | ~25% | **完成** |
| 七 | 计划树 UI、风险、估算、修改与复盘 | ~20% | **完成** |

> 「修改方案」**两处都已支持就地编辑参数**（改体积阈值 / 排除标签 / 排除路径 /
> 保留策略 / 迁移方式后零 token 重算），见下文第十二节：
> - 任务停在 `waiting` 时走 `revise_task()`，沿用同一个 `job_id` 重算；
> - 还没点「开始分析」的草稿走 `revise_plan_preview()`，不创建任务。
>
> 只有「运行中（非 `waiting`）」的计划树仍保留「改条件重说」这条退路——
> 那一刻任务正在跑，改条件应该等它停下来，不该边跑边换约束。
>
> *（本节此前写的是「不是就地编辑参数，属于已知取舍」，与 `PlanTree.vue` 里
> 早已存在的 `revise` 表单不符，已按实际行为改正。）*

---

## 二、分层架构

```
意图层  services/agent_intent.py     AgentIntent：goal / scope / constraints / risk_level
工具层  services/agent_tools.py      查询 12 · 执行 9 · 辅助 6，白名单 + 权限门禁
编排层  services/agent_planner.py    一句话拆多步流水线
        services/agent_task.py       agent_task 任务的执行体
记忆层  .filepilot/prefs.json        常用路径 / 排除标签 / 永久约束 / 常用策略
        .filepilot/agent_state.json  每个会话的「最近一次意图」快照
感知层  agent.status_context()       任务 / 权限 / 磁盘 / 确认卡 / 可回滚 / 隔离区
执行层  jobs.py + services/*         既有 Job、权限三档、隔离区（未改动）
```

三条原则贯穿全链路：

1. **意图结构只描述「要做什么」，不负责执行。**
2. **risk_level / needs_confirmation 一律本地确定性计算**，模型给的值不采信。
3. **语义排除在这里解析成目录名剪枝 + 真实路径前缀**，模型绕不过。

---

## 三、AgentIntent 协议

本地规则与模型输出都经 `agent_intent.normalize()` 收敛成同一形状：

```json
{
  "goal": "释放 C 盘空间",
  "action": "junk_clean",
  "scope": { "path": "C:\\", "target": "", "label": "C 盘" },
  "constraints": {
    "safe_only": true,
    "min_size_mb": null, "extensions": [], "keep": null,
    "min_free_space_mb": 20480, "space_goal": true,
    "exclude_tags": ["项目文件", "照片"],
    "exclude_names": ["node_modules", "Pictures"],
    "exclude_paths": ["D:\\素材"],
    "overwrite": null, "keep_structure": null, "delete_source": null,
    "blocked_paths": []
  },
  "risk_level": "medium",
  "risk_reason": "会删除文件，但先进隔离区、可恢复",
  "needs_confirmation": true,
  "missing": [],
  "confidence": 0.9
}
```

**向后兼容**：`normalize()` 的返回值保留旧的 `action / reason / params / label / write /
needs_path` 字段，前端可以逐段迁移，不会一次性崩掉。

### 风险分级（本地计算）

| 等级 | 判定 |
|---|---|
| low | 只读动作；从隔离区恢复；单步查询 |
| medium | 会移动/删除文件（先进隔离区、可恢复）；取消/回滚任务 |
| high | 涉及盘符根目录、系统只读区、受保护目录，或批量对象 ≥ 500 项 |

### 同义表达与上下文指代

- `SYNONYMS` 替换表 + `SPACE_GOAL_RE` 正则，把「腾出 20G 空间」「瘦身」「盘满了」
  统一归一成「清理垃圾」。
- `resolve_reference()` 处理「就刚才那个目录」——从 `agent_state.json` 取上一轮
  `last_scope` 还原成真实路径；解析不到就走追问，**绝不猜一个路径出来**。
- `CONTEXT_REF_ACTION_RE` + `observe_job()` 处理「刚才的迁移怎么样了」。

### 路径抽取的坑

中文紧贴在路径后是常见写法（「把 D:\素材 腾出空间」）。正则分不清
「路径里的中文」和「路径后的中文」，所以 `_trim_path()` 用存在性判：
整段存在就用整段，否则从第一个中文字符处切开，前缀存在就用前缀。

---

## 四、工具层（第二阶段）

| 组 | 数量 | 内容 |
|---|---|---|
| 查询 | 12 | 磁盘状态 / 目录大小 / 目录树 / 类型分布 / 最近修改 / 大文件 / 重复文件 / 垃圾文件 / 项目目录 / 任务状态 / 回收站内容 / 历史操作 |
| 执行 | 9 | 整理预览 / 迁移计划 / 执行迁移 / 清理垃圾 / 清理重复 / 创建备份 / 恢复隔离区 / 取消任务 / 回滚任务 |
| 辅助 | 6 | 检查磁盘空间 / 路径是否存在 / 是否受保护 / 目标是否冲突 / 用户权限 / 估算耗时与释放空间 |

**查询工具刻意做成「有界」的**（`_bounded_walk`）：条目数 + 墙钟时间双上限，
超限在结果里标 `truncated`，由 Agent 说明「这是抽样估算」，绝不假装精确。
这样「这个目录多大」不会退化成一次全盘重扫。

**未注册的工具一律拒绝**：`run_tool()` 只认注册表里的名字，模型不能凭空发明工具。

---

## 五、多步骤编排（第三阶段）

### 流水线

```
分析盘占用 → 找大文件 → 找重复 → 找垃圾 → 套用约束 → 汇总方案
  → 等待确认（挂起）→ 分批执行 → 汇报结果
```

迁移与整理各有自己的流水线（`PIPELINES`）。只有目标型意图才拆多步，
纯查询走单步。

### 挂起语义

新增 `jobs.JobPaused`：任务停在 `waiting`，**不是终态**。
用户点「继续执行」后，同一个 `job_id` 再 `start()` 一次，保留已完成的分析步骤、
补上执行段——任务卡不会分裂成两条，撤销也只需回滚同一个 job。

`JobManager.start()` 在 `waiting → running` 时会换一条干净的取消信号，
否则上一轮点过取消的话，续跑会立刻被判定为已取消。

### 安全边界（关键决策）

**`agent_task` 只跑只读分析段。真正的写入仍走 clean / migrate 任务。**

理由：「Agent 会拆任务」和「谁能动文件」是两件事。前者可以放开做，
后者必须继续锁在权限层里，隔离区、分批、回滚这些已在生产里验证过的
保障一条都不能少。执行段只是把既有的 `clean.run_clean` / `migrate.run_migrate`
排进了计划树，没有新开写通道。

`approve` 接口再拦一次只读档。

---

## 六、记忆（第五阶段）

| 文件 | 内容 | 可见性 |
|---|---|---|
| `filepilot.db` memory 表 | 逐条对话历史 | 会话列表 |
| `.filepilot/prefs.json` | 提炼后的长期结论 | Agent 页可直接增删 |
| `.filepilot/agent_state.json` | 每个会话最近一次意图快照 | 不展示 |

prefs 的四类内容：常用路径 / 排除标签 / 排除路径 / 永久约束 / 常用策略。

**自动积累（两条写入路径）**：
- `main.py::_launch()`：任何页面启动的任务都会把目标目录记进常用路径
  （盘符根不记，重复不叠加）。用副作用的形式做，异常绝不影响任务本身。
- `agent_task`：分析段跑完记常用路径；执行段**无错误**完成时记一条常用清理策略，
  在偏好面板里可以点一下重跑。

`apply_hard_rules()` 让「项目都不要动」这类约束在所有路径上强制生效，
被挡下的路径会出现在 `constraints.blocked_paths`，用户在计划阶段就能看到。

**修复了一处历史断链**：前端一直把全局 `excludes` 传过来，
但早期版本从未读过它，导致「排除目录」对 Agent 完全无效。
现在 `_runtime_context()` 会把它与 prefs 合并后一起注入。

---

## 七、状态感知（第六阶段）

`status_context()` 一次读齐：在跑/待确认/可回滚的任务、权限档位、模型是否配置、
各盘剩余空间、最近一次任务结果、隔离区占用。全部本地读，零 token。

注意区分 **「正在干活」与「停着等人」**：`PHASE_ACTIVE` 只含 running/pending，
待确认的任务单独统计。否则状态条会一直显示「有任务在跑」，
用户以为机器在忙，其实只是在等他点确认。

提问命中时（「现在忙不忙」「刚才的迁移怎么样了」）直接由本地规则回答，
不消耗 token，也不让模型去猜。

---

## 八、UI（第七阶段）

`components/PlanTree.vue` 一个组件承担两种密度：

- **单步计划**：只有一步，渲染出来就是原来那张计划卡的样子
- **多步方案**：完整 timeline + 每步产出 + 预计数字 + 决策按钮

元素：阶段状态图标（pending/running/waiting/done/failed）、风险徽章、
每步的输入与产出、预计释放空间 / 文件数 / 耗时 / 分批数、约束摘要，
以及五个按钮：**继续执行 · 只执行安全项 · 修改方案 · 查看详细清单 · 撤销本次计划**。

澄清卡新增两种形态：

- **选项式**（`options`）：整理方式 4 选 1，点一下就能继续
- **字段式**（`fields`）：迁移缺来源/去向时列出还差哪几条

**落空时不给死胡同**：无论这句话是本地规则没认出，还是模型回了 `action=none`，
统一走 `none_payload()` —— 保留模型那句更有针对性的 reason 当正文，
但一定附上 4 个**可点的完整指令**（带路径时用真实路径拼，不带时给盘符级别的候选）。
单说一句「无法执行」是死路，摆出可点的选项才是第四阶段要的追问。

偏好记忆面板挂在标题栏右侧齿轮里，五个分区：长期排除标签（可点选）、
长期排除路径、永久约束、常用清理策略（可点重跑）、常用路径（可点填入输入框）。

---

## 九、涉及文件

新增：

- `backend/services/agent_intent.py`
- `backend/services/agent_tools.py`
- `backend/services/agent_planner.py`
- `backend/services/agent_task.py`
- `frontend/src/components/PlanTree.vue`
- `docs/AGENT_UPGRADE.md`（本文）

改动：

- `backend/config.py`：prefs / 排除标签 / 风险阈值 / 查询上限常量
- `backend/store.py`：prefs 与会话快照读写
- `backend/jobs.py`：`agent_task` 类型、`JobPaused`、续跑时换取消信号
- `backend/services/agent.py`：收敛到 Intent、`status_context`、`summarize_job`、
  `observe_job`、`plan_preview`、`revise_task`、`revise_plan_preview`、
  prefs 接口、`_trim_path`、`_looks_like_action`
- `backend/main.py`：`/api/agent/{tools,context,prefs,plan,task,observe}`，
  以及 `/api/agent/task/{job_id}/revise`；`/api/agent/plan` 另有 `revise` 重算分支
- `frontend/src/api/filepilot.js`：对应 API（含 `reviseAgentTask` / `reviseAgentPlan`）
- `frontend/src/views/Agent.vue`：计划树、状态条、偏好面板、澄清选项、结果复盘、
  就地改条件（`revisePlan` / `reviseDraft`）

---

## 十、验证方式

- 后端自检：`_agent_test/batch_a_check.py`、`batch_b_check.py`、`batch_b_execute.py`、`batch_c_check.py`
- 界面验证：`_agent_test/ui_verify.js`、`ui_verify_extra.js`（Playwright 真实渲染，截图在 `_agent_test/shots/`）

端到端验过：一句话 → 7 步计划树 → 开始分析 → 挂起等待确认 →
确认执行 → 分批清理进隔离区 → 本地模板复盘总结。

---

## 十一、复检时补掉的问题（当初没做透）

### 第一轮

1. **「没识别清楚就主动问」只做了一半**——原本只在「没配 Key 且本地规则落空」时追问，
   配上 Key 走模型时，模型回 `action=none` 仍然只丢一句「无法执行」。
   现在两条路都过 `none_payload()`，一律给可点选项。
2. **常用路径 / 常用清理策略只建了存储位，没有写入方**——
   `add_common_path()` 定义了却从没被调用，`clean_strategies` 更是死字段。
   现在 `_launch()` 与 `agent_task` 都会写，前端也能点着重跑。
3. **盘符根会污染常用路径**——`C:\` 这类路径没有记忆价值，写之前先过滤。

### 第二轮（只写了没跑过的路径，实测全挂）

4. **迁移流水线的执行段是死的**。`parse_local` 对「把 X 搬到 Y」产出的是只读的
   `migrate_plan`，而 `build_proposal()` 只在 `action == "migrate_execute"` 时收集清单 →
   items 恒为空、`executable` 恒为 false、**用户永远确认不了搬移**。
   修法：加 `MIGRATE_UPGRADE`，把 `migrate_plan` 一律按 `migrate_execute` 折算
   （整条 migrate 流水线的终点本来就是执行，清单仍要用户点确认）。
5. **整理流水线挂在一个点不动的确认卡上**。`virtual_preview` 是纯只读动作，
   没有「确认后才执行」的下一步，但模板里却有 `confirm` 步骤 → 任务永久停在 waiting。
   修法：`STEP_ORGANIZE` 去掉 `confirm`，跑完直接 `done`。
6. **前端有三个动作点了等于没点**。`trash_restore` / `job_cancel` / `job_rollback`
   在工具注册表里（执行工具 9 条含这三个），但 `executePlan()` 没有分支 →
   点执行只会弹「这个计划暂时不能直接执行」。修法：补上三条落点
   （恢复走 `restoreTrash`，取消/回滚走 `cancelJob` / `rollbackJob`）。
   顺带把 `job_cancel` 移出 `WRITE_ACTIONS`——它只是让任务停下来，不碰文件，
   而且「只读档也想停掉一个跑疯了的任务」完全合理，不该被拦。
7. **迁移的措辞是错的**：文案写「预计释放 3.0 MB」，但迁移是换地方放、不是释放。
   修法：proposal 加 `freed_label`，迁移显示「搬运体积」；
   执行总结也分开——迁移报「搬移 N 项 / 校验通过 N 项 / 已删源 N 项」。
8. **整理的第二轮追问会打转**：用户点了「按类型」之后，追问又把那四个方式选项摆一遍，
   用户再点还是原地。修法：方式已定时只问路径，并把常用路径当建议推过去。

**附带发现的坑**：
- `database.replace_jobs()` 是 upsert + 按时间裁剪，**不是真删除**，
  所以「从列表里删掉」清不掉库里的行；要真删必须显式 `DELETE`。
- 清理数据前必须先停掉后端进程，否则常驻进程的 `_persist()` 会把删掉的行写回来。
  （正是靠这个现象定位到 8001 上还挂着一个旧进程，也解释了为什么 UI 里看到的是旧行为。）
- 子进程用 `cmd &` 起，父命令结束不一定回收——验证完要按端口确认真的退了。

**教训**：写完了 ≠ 跑过了。三条流水线里只有「释放空间」那条真跑过，
另外两条（迁移、整理）都是这次复检才第一次执行，全挂。
新写的分支一定要**至少各跑一次**，尤其是「目标动作 → 方案 → 确认 → 执行」这种多段链路，
断在任何一段都表现为「点了没反应」。

---

## 十二、草稿阶段的就地改条件（2026-10-06 补齐）

第一轮把「修改方案」做成了就地编辑，但只挂在**任务停在 `waiting`** 那一刻
（`PlanTree` 的 `editable` ← `treeWaiting`）。**还没点「开始分析」的草稿**
仍然退化成 `modifyPlan()`——把原话塞回输入框让你重说一遍。

缺口的原因是重算入口不存在：`/api/agent/plan` 只收 `message` 重新解析，
收不下条件补丁。

### 改法

| 文件 | 改动 |
|---|---|
| `services/agent.py` | 新增 `revise_plan_preview(intent, patch, context)`：复用 `sanitize_constraints` + `revise_payload` + `normalize`，零 token、不建任务、不落记忆、不调模型 |
| `main.py` | `AgentPlanRequest` 加可选 `revise`（intent + constraints）；路由在 `revise` 非空时改走重算分支 |
| `api/filepilot.js` | 新增 `reviseAgentPlan(message, intent, constraints, context)` |
| `views/Agent.vue` | 新增 `reviseDraft(index, patch)` 与 `draftRevising`；草稿的 `PlanTree` 传 `editable` / `revise-busy` / `tag-options`，接 `@revise` |

### 三个必须守住的点

1. **runtime 与 `revise_task()` 逐字一致**（只给 `prefs` 与 `revise` 两个键）。
   两处若喂不同上下文，同一个补丁会算出不同约束——「任务里改」和「草稿里改」
   给出不一样的结果，比不支持就地编辑更糟。
2. **补丁只能在服务端注入**。`_runtime_context()` 会主动 `pop` 掉上下文里的
   `revise`，否则一次普通对话就能伪造条件，绕过模型侧「不许改排除项」的限制。
3. **前端必须传 `plan.intent`，不能传消息上的 `msg.intent`**。后者是
   `intent_view()` 的精简视图，不含 `action`，拿去重算会被判成「没有可修改的意图」。
   这个坑只有真跑才发现——精简视图看起来「字段都在」。

### 验证

接口层（`--port 8011` 独立实例 + 临时 `FILEPILOT_DATA_DIR`，不碰真实状态目录）：

- 同一条意图 + 补丁 → `min_size_mb` / `safe_only` / `exclude_tags` / `exclude_paths`
  全部落到新方案上；标签还按 `resolve_excludes` 展开成了真实路径
- 重算前后 `jobs` 与 `conversations` **都是 0 条**——零副作用坐实
- 边界：空补丁 → 400「没有需要修改的条件」；无意图 → 400「这份方案没有可修改的意图」
- 白名单：`exclude_tags: ["不存在的标签"]` 被丢弃、`exclude_tags` 保持空
  （自由文本会变成「看起来排除了其实没排除」，比不排除更危险）
- 正常预览 `扫描 D 盘` 未受影响

界面层（Playwright 真实渲染，Chromium-1234 + `--disable-web-security` 跨端口）：

- 草稿按钮从「改条件重说」变成「修改方案」，`hasLegacyBtn = false`
- 编辑器 6 个排除标签 + 3 行（体积阈值 / 不动这些 / 排除路径）正常渲染
- 改「体积阈值 50 + 勾上照片」后重算：7 步与步骤标题不变，约束从
  「只取安全级」变成「不动 照片 · 排除 1 个目录 · 仅 ≥ 50 MB · 只取安全级」
- 备注变成「已按新条件重算 · 零 token；…」，编辑器自动收起，token 元信息仍是 `0 token`
- 后端 10 个前端用到的接口全 200；唯一 404 是 `favicon.ico`
  （`index.html` 里没有 icon 链接，浏览器默认请求，属已知无害项）

截图：`agent_revise_before_check.png` / `agent_revise_editor_check.png` / `agent_revise_after_check.png`

### 刻意没做的

- **没给单步计划卡加「修改方案」**。单步动作（扫描 / 整理预览这类）的参数就是
  路径加一个阈值，重说一句比开表单更快；而 `PlanTree` 的 revise 表单是按多步方案
  的约束集设计的，套到单步上会露出一堆用不上的字段。
- **没让「运行中」的计划树可编辑**。任务正跑着，改条件应该等它停下来，
  不该边跑边换约束。
- **没动 `/api/agent/plan` 的 `message` 必填**。重算分支不读它，但保持必填能让
  两条路径的请求形状一致，也免得前端在两种情况下传不同结构。

---

## 十三、对话记忆带上计划（2026-10-06）

### 问题

重开会话／刷新页面后，助手那一条只剩孤零零一句话，**计划卡全没了**。
根因在 `restoreConversation()`：恢复历史时把 `plan` 写死成 `null`。

再往下追是数据层面的：`agent_memory` 表只存 `role / content / created_at`，
**计划从来没落过库**。前端的 `null` 不是偷懒，是真没东西可填。

### 改法

| 文件 | 改动 |
|---|---|
| `database.py` | `agent_memory` 加 `payload TEXT NOT NULL DEFAULT ''`；`append_memory` / `read_memory` 支持它 |
| `database.py` | `_ensure_memory_payload()`：老库走 `PRAGMA table_info` + `ALTER TABLE` 补列 |
| `services/agent.py` | `save_memory(..., payload=)` + `_memory_payload()` 白名单；`chat()` / `stream_chat()` 两处写入点都传完整 payload |
| `views/Agent.vue` | `restoreConversation()` 读 payload 还原 plan / intent / steps / clarify 等 |

### 三个必须守住的点

1. **只存白名单字段。** `MEMORY_PAYLOAD_KEYS` 里是「重建这条回复长什么样」需要的
   东西。存整份 payload 会把状态快照、磁盘剩余空间这类一次性数据也冻进历史——
   那些数字第二天就是错的，还原出来反倒是误导。
2. **正文与 payload 分开存，模型上下文不受影响。** `build_context_memory()` 只读
   `role` / `content`，所以 payload 再大也不会挤占 token 预算。
3. **迁移必须显式做。** `CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不干，
   光改建表语句**补不上老库的列**。`_ensure_memory_payload()` 每次连接时查一次
   `PRAGMA table_info`，缺了才 ALTER。

### 验证

`--port 8011` 独立实例 + 临时 `FILEPILOT_DATA_DIR`（全新库走新建表路径），
Playwright 真实渲染：发一句 → 出计划卡（`.plan` = 1、7 步）→ **刷新页面** →
`.plan` 仍是 1、步骤仍是 7、消息元信息显示「来自记忆」。
另用一份**旧 schema 的库**（不带 payload 列）单独验过 ALTER 迁移。

### 取舍

老记录恢复出来仍然只有文字——那时没有存计划，不拿正文去反推一个假计划出来。
前端 `saved.payload || {}` 走空对象分支，退化成 v25 之前的样子。

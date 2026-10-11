# 同类开源项目调研（2026-10-11）

> 目的：找出与 FilePilotLite / Atlas 同类的开源项目，看看**哪些做法值得搬过来**。
> **本次只调研，未改任何代码。**
> 方法：用 GitHub 检索接口实搜（关键词 + topic），逐个读 README，再回代码里核对"我到底有没有"。

---

## 一、结论先说

**同一条赛道上，真正值得对标的是 4 个项目**：

| 项目 | Star | 技术栈 | 定位 | 与我的关系 |
|---|---|---|---|---|
| [qarmin/czkawka](https://github.com/qarmin/czkawka) | 34.2k | Rust + GTK | 查重 / 空文件夹 / 相似图片视频 | **查重维度比我宽**（我只做精确哈希） |
| [harry0703/MangoDisk](https://github.com/harry0703/MangoDisk) | 3.9k | Tauri2 + Rust + **Vue3** | 磁盘清理 + 空间分析 + 系统工具 | **功能面最像**（清理/分析/历史），且它也用 Vue |
| [windirstat/windirstat](https://github.com/windirstat/windirstat) | 4.3k | C++ | 空间分析（treemap/sunburst）+ 查重 | **空间地图的正统参考** |
| [cuiheng511/filepilot-ai](https://github.com/cuiheng511/filepilot-ai) | 18 | Python + PySide6 | 本地文件智能：桌面 + CLI + **MCP server** | **定位最像**（本地优先 + 计划先行 + 回退），多了 CLI 和 MCP |

另外两个值得单独提：

- [qup1010/FilePilot](https://github.com/qup1010/FilePilot)（26★，中文）—— **和我几乎同名同定位**："本地优先的 Windows 文件整理工作台 / AI 辅助分析 / 清晰规划 / 可回退执行"，Tauri + Next.js + Python，而且用词都是"方案 → 预检 → 执行 → 回退"。
- [Eul45/omni-search](https://github.com/Eul45/omni-search)（607★）—— 用 **NTFS MFT/USN 直读**做秒级全盘检索 + 查重。

### 一句话判断

**功能宽度上我在第一梯队**（清理 + 查重 + 迁移 + 空间图 + Agent 规划确认回退，这四项凑齐的开源项目很少）；
**短板集中在三块**：① 没有全文检索/索引；② 查重只认"完全一样"；③ 只有 GUI，没有 CLI 与对外接口（MCP）。

---

## 二、可以抄的清单

### A 档 · 立刻能做（成本低、收益直接）

**A1. 发布包附 SHA256 校验文件**
来源：`filepilot-ai`（"Release assets include checksum sidecars and CI verification"）、`build_release.ps1` 现在**没有**校验步骤。
做法：打包完同时产出 `Atlas-2.0.0-win64-portable.zip.sha256`，内容一行 `哈希  文件名`。
落到：`packaging/build_release.ps1` 末尾。
成本：10 行。收益：以后拿到包先验一次，不怕传坏。

**A2. README / 文档里补一张「当前限制」表**
来源：`qup1010/FilePilot` 的 README 有一节「当前限制」，列得很难看但很诚实（只 Windows、每次重扫无差异扫描、回退只对最近一次、预检不能保证发现所有问题）。
做法：把我散落在 `docs/GOTCHAS.md` 里的已知限制，抽一张表放到对外文档里。
成本：纯文档。收益：自己隔两周回来也知道边界在哪。

**A3. 把"已知遗留"固化成可见的 Issue 清单**
来源：几个成熟项目的 `docs/ROADMAP.md` / `TODO`。
做法：现在已知遗留有：百分比跨阶段倒退、折叠目录体积未统计、空会话不落库、只读档回滚门禁。
成本：纯文档。

**A4. `pyproject.toml` + 可选依赖组**
来源：`filepilot-ai` 的 `pip install "filepilot-ai[desktop]" / [mcp] / [test]`。
做法：把 `.venv-packaging` 那套隐式依赖显式化成 `[packaging]`，测试依赖成 `[test]`。
成本：一个新文件 + 调整脚本。收益：换机器/重装环境时不用回忆装过什么。

---

### B 档 · 值得做（中等成本，能力缺口）

**B1. 全文检索 + 增量索引（最大的功能缺口）**
来源：`filepilot-ai`（Whoosh + SQLite metadata + 增量索引）。
现状核实：**代码里完全没有**（`whoosh` / `fts5` / `index_folder` 全无命中）。
做法：给已扫描过的目录建一个 SQLite FTS5 索引（只索引文件名 + 路径 + mtime + 大小，先不碰内容），
再逐步加"按需抽取内容片段"（pdf/docx/txt）。
落到：新 `backend/services/index.py` + 新 Job 类型 + 新选项卡。
成本：中（一个完整功能）。收益：这是当前最缺的一块 —— 现在"找文件"只能靠系统资源管理器。

**B2. 相似图片查重（感知哈希）**
来源：`czkawka`（34k★ 的核心卖点：similar images / similar videos）。
现状核实：**只有精确哈希**（`backend/services/duplicates.py` 三层 SHA 比对），语义是"字节完全一样"。
做法：在现有流程后加一层 —— 对图片算 pHash/dHash，汉明距离 < 阈值就归为"相似"，
**单独出一类结果**（不并进"完全重复"，避免误删）。
依赖：`Pillow`（或 `imagehash`）。
落到：`duplicates.py` 加第三类结果 + 前端查重页加分组。
成本：中。收益：截图、二次压缩、缩放后的副本现在完全查不出来，这是真实痛点。

**B3. 清理项加「AI 解释一句」**
来源：`MangoDisk` 的 "AI Explanations"（用条目描述 + 扫描结果解释这是什么、删了会怎样）。
做法：垃圾清单里每一项旁边加一个小问号，点了让模型解释。**复用 Agent 页已有的模型通道**。
落到：`views/Junk.vue` + `services/agent.py` 加一个只读的 explain 动作。
成本：小（模型通道和 key 都是现成的）。收益：观感提升明显，而且**符合"零 token 纯本地"的例外条款**（只在用户主动点时才调）。

**B4. 清理规则声明式化**
来源：`MangoDisk` —— "Routine cleanup coverage should use **build-validated, declarative TOML rules**"，
规则库放在仓库里公开可查，每条规则必须"安全边界明确 + 真机验证"才收录。
现状：我的垃圾规则是**硬编码在 `backend/config.py`** 里的。
做法：抽成 `rules/junk/*.toml`（或 JSON），每条带 `name` / `match` / `reason` / `risk` / `safe_by_default` 字段，
加载时校验 schema。
落到：`config.py` + 新 `backend/rules/`。
成本：中。收益：**用户能自己加规则**（不用改代码），规则可单独测试，也自然带上了"为什么这条能删"的说明。

**B5. 操作历史突出「这次回收了多少空间」**
来源：`MangoDisk`（Operation History 第一眼就是 reclaimable space）。
现状：我有日志（`store.write_log`），但没有"回收空间"这个**汇总数字**。
做法：清理/迁移任务结束时，结果里带上 `freed_bytes`；日志页与首页各显示"累计回收"。
落到：`services/clean.py` 结果 + `views/Logs.vue` / `views/Home.vue`。
成本：小。收益：自用工具最有成就感的那个数字。

**B6. 加一个薄 CLI**
来源：`MangoDisk`（`mangodisk clean --apply --dry-run --format json`）、`filepilot-ai`（`python -m filepilot.cli scan/search/duplicates/export`）。
现状核实：**没有 CLI**（只有 `main.py` 起服务）。
做法：`python -m backend.cli scan/duplicates/junk/tree`，复用现有 `services/*`（都是纯函数 + JobContext），
**默认 dry-run，要 `--yes` 才真删**。
落到：新 `backend/cli.py`。
成本：中偏小。收益：批量/自动化场景（也是给自己用的）。

**B7. 空间地图给一个「视图切换」**
来源：`MangoDisk` 的 "Switch between a treemap and a sunburst chart, and choose how many levels to display"。
现状：刚按你的要求砍掉方块图，现在是「3D 数据城 + 磁贴卡片」。
做法：保留卡片为默认，**另加一个切换按钮**（卡片 / 层级条 / 环形），不改默认。
成本：中（三套渲染都写过草稿，能复用）。
说明：**不是回退**，是"不同场景不同视图" —— 概览用环形、比较用卡片、看层级用堆叠条。

**B8. 支持一次选多个来源**
来源：`qup1010/FilePilot`（"一次选择多个文件或文件夹作为任务来源"）。
现状：`TargetPicker` 是单路径。
做法：允许加入多个根，任务对每个根分别出清单再合并。
成本：中。收益：清理"下载 + 桌面 + 某个项目目录"这种组合时不用跑三次。

---

### C 档 · 大改造 / 慎重

**C1. MCP server（我认为最值得的一条）**
来源：`cuiheng511/filepilot-ai` 的核心差异点 —— 把文件能力做成 MCP 工具，
给 Claude Code / Codex / Cursor 用，带**目录白名单、默认只读、读取上限、写操作需显式开关 + JSONL 审计**。
为什么适合我：**我已经有全部前置条件** —— 服务层是纯函数、权限门禁在服务层、删除先进隔离区、任务可回滚。
做法：新增 `backend/mcp_server.py`，把现有 `services/*` 里**只读的那部分**（scan / duplicates / junk / tree / overview）
暴露成 MCP 工具，写操作先不开。
成本：中。收益：我自己就用 AI 写代码，"让 AI 通过受限工具看我指定目录"是真实需求，
而且**这是我的项目相对 `filepilot-ai` 唯一能反超的功能点**。

**C2. Tauri 替代 pywebview**
来源：`MangoDisk`（Tauri 2 + Rust + Vue3）、`qup1010/FilePilot`（Tauri + Next.js）。
现状：`pywebview` + PyInstaller，产物 **37 MB / 压缩后 18.3 MB**。
收益：产物体积能降到 ~10 MB 以内、启动更快、原生能力（托盘/通知/单实例）更稳。
代价：**要把 Python 后端一起塞进去**（sidecar），打包链要重做，风险不小。
判断：**收益真实但不紧急**，等有别的理由动打包链时一起做。

**C3. NTFS MFT / USN 直读**
来源：`Eul45/omni-search`（607★）、以及一批 Rust 项目（`everyfind` / `rustFind` / `PulseFS`）。
做法：直接读 NTFS 的 MFT + 监听 USN Journal，整盘枚举从"走目录"变成"读表"，秒级出全盘文件表。
代价：**需要管理员权限**、仅 NTFS、要写底层解析（或依赖第三方库）。
判断：我的扫描已经做过四轮优化（`docs/GOTCHAS.md` 有完整基准），再往上是另一个数量级 ——
但**风险与维护成本都高**，且需要管理员权限（当前环境拿不到）。列为"想清楚再说"。

**C4. 插件 SDK（extractor plugins）**
来源：`filepilot-ai` 的 `extractors/` 插件注册表 + 远程插件的 SHA-256 校验。
判断：**自用项目不需要**。等 B1（内容索引）真做了再谈。

---

## 三、不建议抄的（明确列出来，免得以后手痒）

| 项目 | 不抄的东西 | 原因 |
|---|---|---|
| MangoDisk | **系统优化 / 隐私清理 / 启动项管理 / 系统维护 / 卸载器** | 这些要改注册表、删浏览器数据、动启动项。它自己是"内置校验过的设置 + 改完读回验证"，但**风险级别和我的自用工具定位不匹配**；我已有"只读档 / 隔离区 / 服务层门禁"三条安全线，不该为了多几个功能去动系统 |
| MangoDisk | 多语言 README、官网、Homebrew/winget 分发、自动更新 | 面向开源运营，我是自用 |
| czkawka | 扫描"损坏文件 / 无扩展名 / 错误扩展名" | 误报率高，价值低 |
| 通用 | 注册表清理类功能 | 同上，且与"扫描永不遍历系统只读区"的既有原则冲突 |

---

## 四、一个命名冲突要提醒

GitHub 上**至少有 7 个叫 FilePilot 的项目**，其中：

- `qup1010/FilePilot`（26★）—— 中文，定位与我几乎一致
- `cuiheng511/filepilot-ai`（18★）—— 定位也几乎一致
- `ChrisTs8920/FilePilot`（67★）—— Python/Tkinter 文件管理器

我的**对外显示名已经改成 Atlas**（侧栏、标题、面包屑），但**仓库名与目录名仍是 `FilePilotLite`**。
如果以后要开源，建议一并改掉，避免和这三个撞名。

---

## 五、建议的推进顺序

1. **A1–A4**（半天内，纯文档 + 10 行脚本）
2. **B5 + B3**（都是小的，且立刻能感觉到：回收空间数字 + AI 解释一句）
3. **B2 相似图片查重**（真实痛点，且我有现成的三层比对流程可以挂）
4. **B4 规则声明式化** → 顺带把 B1 的索引做成"用户可加规则"的同一套机制
5. **C1 MCP server**（最有战略价值，但等前面稳了再做）
6. **B1 全文索引**、**B6 CLI**、**B7 视图切换**、**B8 多来源**（按需）
7. **C2 / C3** 只在有明确理由时碰

---

# 已实施（2026-10-11，用户："按你的想法先改吧" → "直到把推荐的都改完"）

| 项 | 来源 | 落地 |
|---|---|---|
| **A1 发布包附 SHA256** | `filepilot-ai` 的 checksum sidecars | ✅ 出包产出 `…zip.sha256`；`build_release.ps1` 也补了这一步 |
| **A4 pyproject.toml + 可选依赖组** | `filepilot-ai` 的 `[desktop]` / `[mcp]` 分组 | ✅ 必需依赖只留 fastapi/uvicorn，pywebview / pillow / pyinstaller 全部可选 |
| **A2/A3 限制表 + 遗留清单** | `qup1010/FilePilot` 的「当前限制」 | ✅ `docs/LIMITS.md`（平台 / 扫描 / 写入 / 跨页 / 联网 + 6 条已知遗留 + 明确不做的） |
| **B2 相似图片查重（感知哈希）** | `czkawka`(34k★) 的招牌功能 | ✅ `services/similar.py`，dHash + 并查集聚类；与精确查重**分开呈现** |
| **B3 清理项 AI 解释** | `MangoDisk` 的 AI Explanations | ✅ `services/explain.py` + 垃圾清单每项一个「?」，**必须手动点**才联网 |
| **B5 回收空间正反馈** | `MangoDisk` 的 Operation History | ✅ 任务日志带 `reclaimed`，`/api/stats/reclaimed`，日志页「累计腾出」 |
| **C1 MCP server** | `cuiheng511/filepilot-ai` 的核心差异点 | ✅ `backend/mcp_server.py`，手写 JSON-RPC；**7 个只读工具 + 目录白名单 + 失败关闭** |

## 与对标项目相比，我们守住的差异

- **一个 exe 两种形态**：`Atlas.exe` 不带参数起图形界面，带 `--mcp` 就是 MCP server。不用打包两个可执行文件。
- **零新增运行时依赖**：MCP 用手写 JSON-RPC（没用官方 `mcp` SDK）；Pillow 是唯一的可选新增（只给相似图查重，不装就那一项功能不可用）。
- **安全模型比对标项目更严**：MCP **默认一个目录都看不见**（不是"默认全开"），且**只暴露只读工具** —— 没有任何删除/移动/写入工具。

## 仍未做（留给以后）

- **B1 全文检索 + 增量索引** —— 目前完全没有内容检索（对标 `filepilot-ai` 的 Whoosh 索引）
- **B4 清理规则声明式化** —— 规则仍硬编码在 `config.py`（对标 MangoDisk 的可校验 TOML 规则库）
- **B6 CLI** —— 目前只有 GUI + MCP，没有命令行
- **B7 空间地图多视图切换**、**B8 多来源选择**
- **C2 Tauri 替代 pywebview**、**C3 NTFS MFT 直读**
- **反向跨页接力**（目前只有 空间地图 → 其它页 这一向）

---

# 第二轮实施（2026-10-11，用户："接着做剩下的"）

| 项 | 来源 | 落地 |
|---|---|---|
| **B6 CLI** | `MangoDisk` / `filepilot-ai` 都有 CLI | ✅ `backend/cli.py`：`scan` / `duplicates` / `similar` / `junk` / `tree` / `overview` / `reclaimed`，`--json` 输出。**刻意只读** —— 不提供任何删除/移动命令（写操作绕不过"先出清单等确认"） |
| **B4 清理规则声明式化** | `MangoDisk` 的 TOML 规则库 | ✅ 数据目录放 `junk_rules.json` 即可**覆盖/追加/停用**规则；逐条校验，坏的那条跳过并给出**人话原因**（垃圾页会列出来）。内置表永不失效 |
| **B7 空间地图多视图** | `MangoDisk` 的 treemap ↔ sunburst | ✅ 「卡片 / 环形」切换（**刻意不加层级条** —— 横向条形那族被否过两次） |
| **反向跨页接力** | ——（补第一轮的缺口） | ✅ 抽出 `components/NextSteps.vue`；查重页 / 垃圾页 / 扫描页都能把当前目录带去别的页面 |

## 顺带统一的抽象

- `backend/services/headless.py`：CLI 和 MCP 共用同一个「无界面 JobContext」，
  不用为了绕开 JobManager 去改服务层。
- `components/NextSteps.vue`：跨页接力那一行只有一个来源，样式不会各写各的。

## 仍未做

- **B1 全文检索 + 增量索引**（最大的一块，值得单独一轮）
- **B8 一次选多个来源**
- **C2 Tauri 替代 pywebview** / **C3 NTFS MFT 直读** —— 报告里就标了"不紧急 / 想清楚再说"，
  前者要重做整条打包链，后者需要管理员权限且仅限 NTFS

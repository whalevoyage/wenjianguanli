# B1 全文检索 + 增量索引 · 实现方案

> 状态：**已实现（P0 + P1）** —— 方案已确认并落地，见 §十三「实际落地」。
> 坑与实测数据见 `docs/GOTCHAS.md` 十八；能力边界见 `docs/LIMITS.md` 三。
> 来源：`docs/OSS_RESEARCH.md` B1 —— 同类项目调研核出的**最大功能缺口**。
> 硬约束：**零 token、纯本地、可断网**；不新增第三方依赖。

---

## 一、一句话目标

给 Atlas 加一个**搜索选项卡**：输入关键词，**毫秒级**列出命中的文件（文件名 + 正文），
结果继续遵守「看 → 挑 → 执行」契约，能接力到查重 / 清理 / 迁移。

现在 Atlas 能扫、能查重、能清垃圾、能搬家，**唯独不能"找内容"** ——
"找文件"只能回系统资源管理器，而且是按文件名找。

---

## 二、已核实的事实（不是推测）

### 2.1 代码里现在完全没有

`grep -rn "whoosh|fts5|index_folder"` 全无命中；`backend/services/` 下 17 个服务，
没有一个负责索引或检索。

### 2.2 SQLite FTS5 可用，**零新增依赖**

打包环境 `.venv-packaging` 的 SQLite 是 **3.45.1**，`CREATE VIRTUAL TABLE ... USING fts5` 实测可用：

```
sqlite 3.45.1
FTS5 OK
```

项目**本来就在用 SQLite**（`backend/database.py` 的 `filepilot.db`，已开 WAL），
索引直接并进这个库即可，不新建 db 文件、不引 whoosh/jieba。

### 2.3 ★ 中文分词：默认分词器**搜不了 2 字词**（这是本方案最关键的实测）

用真实中文串 `自然语言驱动的玉米三维表型智能测量系统` 实测三种方案：

| 方案 | `表型` | `玉米` | `测量系统` | 结论 |
|---|---|---|---|---|
| 默认 `unicode61` | ✗ 0 | ✗ 0 | ✗ 0 | **整段中文被当成 1 个 token**，只有整串能命中 —— 不可用 |
| `trigram` | ✗ 0 | ✗ 0 | ✓ 1 | 只对 **≥3 字**的查询有效；`表型`/`玉米` 这些常见的 2 字词全丢 |
| **unigram 预切 + 相邻短语** | **✓ 1** | **✓ 1** | **✓ 1** | **全部命中** ✅ |

**做法**：写入与查询前，把 CJK 逐字用空格分隔（`玉米表型` → `玉米 表型` 存成 `玉 米 表 型`），
查询时拼成**相邻短语** `"表 型"`：

```
AND 单字  "表" AND "型"  → [玉米表型测量, 表中型态]   ← 只要两个字都在就命中（过宽）
相邻短语  "表 型"        → [玉米表型测量]            ← 只命中相邻的（正确）
```

- 零依赖，纯字符串处理，几十行代码。
- 代价：索引比按词切大（每个汉字都是 token）。**可接受** —— 和项目"不假装精确"的一贯取舍一致，
  第一版宁可索引大一点，也不引 jieba 破坏"零新增依赖"。
- `snippet()` 高亮 API 可用（实测返回 `玉 米 [表] 型 测 量`），
  **展示时把字间空格去掉**即可（存的是预切文本，所以拿回来要还原）。

---

## 三、架构：两阶段，与既有分层对齐

```
索引阶段（Job，耗时长、可中断、带进度）
  fsutil.iter_entries(root)            ← 复用唯一遍历入口
   → 比对 search_file 里的 (size, mtime) ← 没变直接跳过（增量）
   → 只对"新的/变过的"抽正文（白名单 + 上限）
   → CJK 逐字预切 → 写入 FTS5

查询阶段（普通 GET，要求即时返回，不做成 Job）
  关键词 → 预切 → FTS5 MATCH → JOIN 元数据 → 结果清单
   → 看 → 挑 → 执行（接力到查重 / 清理 / 迁移 / Agent）
```

**为什么查询不做成 Job**：Job 是为"耗时长、要进度、要中断"准备的（`jobs.py` 注释）；
检索必须是敲完回车就出结果的即时反馈，套 Job 反而多一层 SSE 延迟。与既有"扫描类走 Job、
`/api/overview` 这类即时聚合走 GET"的分工一致。

---

## 四、存储设计（并进现有 `filepilot.db`）

在 `backend/database.py` 的 `connect()` 建表脚本里追加（**全是 `IF NOT EXISTS`，老库不受影响**）：

```sql
-- 索引根目录（允许多个，各自记录上次索引时间）
CREATE TABLE IF NOT EXISTS search_root (
    id          TEXT PRIMARY KEY,      -- 规范化后的根路径
    path        TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    indexed_at  TEXT,                  -- 上次索引完成时间（用于"索引陈旧"提示）
    file_count  INTEGER NOT NULL DEFAULT 0
);

-- 每个被索引文件一行（元数据 + 增量比对基准）
CREATE TABLE IF NOT EXISTS search_file (
    id         INTEGER PRIMARY KEY,    -- rowid，与 FTS 表对齐
    root_id    TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,   -- 绝对路径（不 resolve，用 str()）
    name       TEXT NOT NULL,
    ext        TEXT NOT NULL DEFAULT '',
    size       INTEGER NOT NULL DEFAULT 0,
    mtime      REAL    NOT NULL DEFAULT 0,
    indexed_at TEXT    NOT NULL,
    is_text    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_search_file_root ON search_file(root_id);
CREATE INDEX IF NOT EXISTS idx_search_file_mtime ON search_file(mtime);

-- 全文索引（普通 FTS5，非 contentless —— 这样 snippet() 高亮才可用）
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    name, body,
    tokenize = 'unicode61'
);
-- 对齐方式：search_fts.rowid === search_file.id
```

**规格说明**
- `search_fts.name / body` 存的是**预切过**的文本（CJK 逐字空格分隔），`snippet()` 结果展示时去空格。
- 路径不進 FTS（会让每个文件的每个父目录名重复一遍，体积翻几倍）；
  "按路径找"用 `search_file.path LIKE` 的次级过滤（结果集通常很小，可接受），
  或者第一版先只按"文件名 + 正文"搜 —— **见待拍板 ⑤**。
- 增量比对的基准是 `(size, mtime)`，两者都没变就**不重读文件**、不动 FTS 行。

---

## 五、增量算法（核心）

```
for entry in iter_entries(root, progress=ctx.tick, cancelled=ctx.cancelled):
    stat = entry.stat()                      # DirEntry.stat()，零系统调用
    row  = index.get(path)                   # 一次批量预取，非逐条查库
    if row and row.size == stat.st_size and row.mtime == stat.st_mtime:
        continue                             # ★ 没变，跳过 —— 绝大多数字符都在这里
    if row:                                  # 变了 → 更新元数据 + 重写 FTS 行
    else:                                    # 新的 → 插入
        ...
# 收尾：清掉本轮没见到的 path（文件被删/移走）→ 从 search_file 与 search_fts 删除
```

三个实现要点：
1. **批量预取**：先把该 root 下所有 `(path, size, mtime)` 一次性读进内存 dict，
   遍历时内存比对 —— 避免"每个文件一次 SQL 查询"（那是 O(n) 次 I/O，正好是项目踩过的坑，
   见 GOTCHAS「哈希缓存别每文件读一遍写一遍」）。
2. **删除检测**：遍历结束后用集合差找出"索引里有、本轮没见到"的 path，逐条删。
3. **写入按批提交**（如每 500 行 `executemany` + commit），并在每批后 `ctx.check_cancel()`。

---

## 六、正文抽取：白名单 + 三层护栏

**只抽"明确是文本"的文件**，其余只索引文件名（`is_text=0`）。

| 护栏 | 规则 | 理由 |
|---|---|---|
| 扩展名白名单 | `.txt .md .markdown .log .csv .tsv .json .yaml .yml .xml .ini .toml .cfg .py .js .ts .vue .java .c .cpp .h .cs .go .rs .sql .html .css .sh .bat .ps1 …` | 白名单而非黑名单 —— 宁可漏，不误读二进制 |
| 单文件上限 | **默认 2 MB**（`SEARCH_MAX_FILE_BYTES`） | 一个 2GB 日志能把索引撑爆；超限只索引文件名 |
| 二进制嗅探 | 读前 8KB，含 `\x00` 即判非文本 | 有些文本内容顶着 `.log` 之类的扩展名，但其实是二进制 |
| 编码 | `utf-8` → `utf-8-sig` → `gbk`，全失败则只索引文件名 | 中文 Windows 上 GBK 文本很常见 |
| 单次上限 | 一次索引最多处理 `SEARCH_FILE_LIMIT`（默认 20 万）个"新/变"文件，超出如实标 `truncated` | 同 `_bounded_walk` 的"不假装精确" |

**不做**（明确写进 `docs/LIMITS.md`）：PDF / docx / xlsx 正文抽取 —— 要引 `pypdf`/`python-docx`，
**违背"可选依赖只有两个"的硬约束**。第一版靠"文件名 + 纯文本正文"已经覆盖绝大多数自用场景。

---

## 七、后端接线（严格照「加新功能 checklist」）

| 步骤 | 文件 | 内容 |
|---|---|---|
| 1 | `backend/config.py` | 新增常量 `SEARCH_MAX_FILE_BYTES` / `SEARCH_FILE_LIMIT` / `SEARCH_TEXT_EXTS` / `SEARCH_ROOT_LIMIT` |
| 2 | `backend/database.py` | `connect()` 里追加 §四 的建表语句（+ `_ensure_*` 查缺补列不需要，因为都是新表） |
| 3 | **新** `backend/services/index.py` | `run_index(ctx)`（建/更新索引）+ `query(keyword, ...)`（即时检索，非 Job） |
| 4 | `backend/jobs.py` | `JOB_TYPES` 加 `"index": "内容索引"`；**不加进** `WRITE_JOB_TYPES`（只读用户文件，不是写任务） |
| 5 | `backend/main.py` | `POST /api/search/index`（下发索引 Job，走 `_launch`）+ `GET /api/search`（即时查询）<br>+ `GET /api/search/roots`（列已索引根）+ `DELETE /api/search/roots/{id}`（移除索引） |
| 6 | `frontend/src/api/filepilot.js` | `startIndex` / `searchFiles` / `listSearchRoots` / `removeSearchRoot` |
| 7 | `frontend/src/router/index.js` | 加 `/search` 路由（懒加载） |
| 8 | **新** `frontend/src/views/Search.vue` | 见 §八 |
| 9 | `frontend/src/App.vue` | `navGroups` 的**「分析」组**加一项 `{ path:'/search', label:'全文检索', hint:'文件名 · 正文', icon: Search }` |
| 10 | `frontend/src/views/Logs.vue` | `TYPE_LABELS` 加 `index: '内容索引'` |

---

## 八、前端：新选项卡 `Search.vue`（遵守既有约定）

- **接跨页接力**：`useHandoffTarget(searchRoot)` + 模板放 `<HandoffNote>`
  —— 否则这个新页面立刻就是"新的孤岛"（MEMORY 硬规）。
- **遵守「结果 → 执行」契约**：结果出来后在结果区上方放 `<NextSteps :path="..." origin="search" :steps="[...]" />`
  （查重 / 清理 / 迁移），让"看"和"动"之间有衔接。
- **进度**：索引 Job 用 `useJob('search.index')` 订阅 SSE；预热统计阶段必然没有分母，
  直接复用 `JobProgress.vue` 已有的**不确定态**（`total=None` → 来回扫动 + 实时秒数），不编假百分比。
- **无结果 / 未建索引**：走 `EmptyState`，并明确写"先建一次索引"，
  **主按钮禁用时必须说明原因**（例如"索引里还没有这个目录"）。
- 视觉全部用 `var(--*)` token，不写字面量颜色；间距只从 `--space-1..6` 取。

**结果行长什么样**：`文件名（高亮）· 大小 · 修改时间 · 命中片段（高亮，去字间空格）· 路径`。

---

## 九、验收清单

- **零 token / 可断网**：整条链路纯本地 SQLite，断网必须完全可用（这是硬验收标准）。
- **只读性**：索引阶段**只读用户文件**；`readonly` 权限档下**必须放行**（同 scan），
  且绝不触发 `permissions.require_write()`（索引库写在 `STATE_DIR`，属自保护内部状态，非用户文件）。
- **遍历合规**：只用 `fsutil.iter_entries`，不新写 `os.walk`；不调 `path.resolve()`。
- **实机验证**：`vite build` + `_agent_test/ui_verify_search.js`（playwright-core，chromium-1234），
  真实点一次"建索引 → 搜索 → 命中高亮"，抓 `pageerror`。
- **测试**：新增 `tests/search_test.py`（建索引 → 增量（改一个文件只重索引 1 个）→ 删除检测 →
  中文 2 字词命中 → 白名单/上限生效），并接进 `tests/smoke_test.py` 总闸。

---

## 十、分阶段落地（建议）

| 阶段 | 内容 | 价值 |
|---|---|---|
| **P0** | 建表 + `index.py` 只索引**文件名/大小/mtime**（不抽正文）+ Job + 查询接口 + Search 选项卡 + 接力 | 先跑通"建索引 → 搜 → 看→挑→执行"整条链路，**一天内可交付** |
| **P1** | 正文抽取（白名单 + 上限 + 编码 + 二进制嗅探）+ CJK 逐字预切 + `snippet` 高亮 | 功能真正完整 |
| **P2** | 索引陈旧提示、被删文件清理、结果行"在资源管理器打开"、接进 Agent 工具（只读查询） | 打磨 |

**P0 先合入**的用意：链路通了之后再堆抽取逻辑，避免"正文抽取调半天、链路本身还没验证过"。

---

## 十一、需要你拍板的点

| # | 决策 | 我的建议（默认按此实现，除非你改） |
|---|---|---|
| ① | 索引**哪些目录** | **只索引你手动指定的根目录**（可多个），不做全盘自动 —— 全盘耗时且索引库会很大 |
| ② | 建索引**时机** | **手动**"建立 / 更新索引"按钮；进页面时若索引超过 N 天，提示"索引可能已过期，建议更新"。不做后台自动 |
| ③ | 中文分词 | **unigram 逐字预切 + 相邻短语查询**（零依赖，实测 2 字词可命中）—— 代价是索引略大，接受吗 |
| ④ | 正文抽取范围 | **白名单 + 单文件 2MB 上限 + 二进制嗅探**；PDF/docx 不抽（要引依赖，破硬约束） |
| ⑤ | 要不要"按路径搜" | P0 只搜**文件名 + 正文**；路径搜做次级 `LIKE` 过滤（P1 再说） |
| ⑥ | 索引陈旧后 | 只**提示**不自动重建（自用项目，避免意外长任务） |

---

## 十二、明确不做（写进 `docs/LIMITS.md`）

- PDF / docx / xlsx / 图片 OCR 的正文抽取 —— 要新增依赖，破"可选依赖只有两个"。
- 后台常驻监听文件变化（`ReadDirectoryChangesW`）—— 复杂且会一直占资源；用"手动增量"替代。
- 按词（而非按字）的中文分词 —— 要 jieba；第一版不引。

---

## 十三、实际落地（2026-10-11）

方案**原样**实现，无偏离。

| 层 | 文件 | 内容 |
|---|---|---|
| 存储 | `backend/database.py` | 新增 `search_root` / `search_file` / `search_fts`（FTS5）+ 一组访问函数；FTS5 缺失时**降级不报错** |
| 服务 | `backend/services/index.py`（新） | `run_index`（增量）/ `query`（即时）/ `status` / `remove_root`；CJK 逐字预切 + 相邻短语 |
| 配置 | `backend/config.py` | `SEARCH_TEXT_EXTS` 白名单 + 三个上限常量 |
| 任务 | `backend/jobs.py` | `JOB_TYPES["index"]="内容索引"`；**不进** `WRITE_JOB_TYPES`（只读用户文件） |
| 路由 | `backend/main.py` | `POST /api/search/index`、`GET /api/search`、`GET /api/search/status`、`POST /api/search/remove` |
| 前端 | `api/filepilot.js` · `router` · `App.vue` · `Logs.vue` · `views/Search.vue`（新） | 新选项卡（分析组）；接接力 + `NextSteps`；高亮用控制字符分段，不走 v-html |

### 验证结果

- `tests/search_test.py`（新）：**22/22** —— 建索引 / 增量（无变化全跳过、改一个只更一条）/ 删除检测 /
  中文 2 字词命中 / 不相邻不误命中 / 白名单 + 二进制嗅探 / 上限 / 移除索引。
- `tests/smoke_test.py` 总闸：**73/73 全过**（无回归）。
- `vite build`：通过（新增 `Search-*.js` chunk）。
- 实机（playwright-core + 真实 Chromium）：`_agent_test/ui_verify_search.js` ——
  建索引 3 文件（含正文 2）→ 搜「表型」命中 2 条、高亮 3 处、耗时 2.9 ms → 搜「report」命中 2 →
  「zzzzzz」如实提示无结果 → 接力出口「查重 / 清理 / 空间地图」→ 移除索引归零；**页面无 JS 错误**。

### 顺带修掉的两个既有小问题

- `Logs.vue` 的 `TYPE_LABELS` 漏了 `similar`，日志页会把「相似图片」显示成原始英文 `similar`；已补。
- 索引跑完**自动刷新状态**（`watch(status === "done")`），不沿用扫描页那种"跑完给个按钮手动载入结果"的做法 ——
  索引结果不是一份"清单"，没有"要不要看"的选择。

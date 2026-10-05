# FilePilot Lite

个人自用的本地文件管理台。看清整块盘的占用、找出重复和垃圾文件、安全地把文件跨盘搬家，
整个过程都看得见、可中断、可回滚。

**除「Agent 助手」一页外，全部功能纯本地运行，零 token、可断网。**

---

## 功能

| 分组 | 页面 | 干什么 |
|---|---|---|
| 总览 | 首页 | 各盘容量、可释放空间、最近任务 |
| 总览 | Agent 助手 | 大白话下命令，翻译成计划后交给本地执行（唯一可能消耗 token） |
| 分析 | 空间地图 | 矩形树图看占用，点进去逐层钻取 |
| 分析 | 扫描分析 | 扫整盘或任意目录，统计数量/体积/类型/大文件/长期未动文件 |
| 分析 | 重复文件 | 三层过滤找出内容相同的文件 |
| 分析 | 垃圾文件 | 三级安全分级，找临时文件、缓存、日志、空目录 |
| 执行 | 跨盘迁移 | 复制 → 逐文件校验 → 校验通过才删源，可选保留目录联接 |
| 执行 | 项目备份 | 打成 ZIP 快照，自动排除依赖与构建产物 |
| 执行 | 任务中心 | 每个任务一条流程图，实时进度，可中断可回滚 |
| 记录 | 回收站 | 清理掉的东西先关在这里，可还原到原位 |
| 记录 | 操作日志 | 完整审计流水，可导出对账 |

---

## 架构

```
浏览器（Vue 3 + Vite + Element Plus）
        │  HTTP JSON
        ▼
FastAPI 路由层（backend/main.py）        只做校验入参 + 创建任务
        │
        ▼
任务管理器（backend/jobs.py）             Job 状态机 + 线程执行 + SSE 进度
        │
        ▼
业务服务层（backend/services/）           scan / duplicates / junk / migrate / clean / backup / agent / overview
        │
        ▼
文件系统工具（backend/fsutil.py）         安全拦截、遍历、哈希、容量、目录树、隔离区
        │
        ▼
JSON 持久层（backend/store.py）           .filepilot/ 下的 jobs.json / logs.json / trash.json / recipes.json
```

### 关键设计

- **一切耗时操作都是任务。** 提交后立刻返回 `job_id`，前端用 SSE（`/api/jobs/{id}/stream`）订阅进度。
  任务状态机：`扫描 → 分析 → 生成清单 → 等待确认 → 执行 → 校验 → 完成`，任何阶段都能中断。
- **一切危险操作先干跑。** 迁移、清理都先生成清单给你看，确认后才真执行。
- **一切可回滚。** 每个任务记录它做过的动作（`move` / `copy` / `trash` / `rmdir`），一键逆序还原。
- **删除默认进隔离区。** 隔离区跟着源文件所在盘走，还原就是一次同盘改名，不会跨盘搬运。
- **Agent 省钱机制。** 先用本地关键词规则识别意图（零 token）；认不出且配置了 API Key 才调 DeepSeek。
  识别成功的意图可存成「配方」，下次直接跑，依然零 token。

---

## 安全设计

| 机制 | 说明 |
|---|---|
| 保护目录 | 桌面、`.workbuddy`、项目自身目录，**任何情况下不可改动** |
| 系统只读区 | `Windows`、`Program Files`、`ProgramData`、`$Recycle.Bin` 等只允许扫描 |
| 默认排除 | 12 项（`node_modules`、`.git`、`__pycache__`、`dist`、`build` 等），界面上可增减 |
| 空间预检 | 迁移 / 备份前检查目标盘剩余空间，不够直接拒绝启动，不动任何文件 |
| 扫描限流 | 深度限制 + 条目上限 + 可中断，避免把磁盘 IO 打满 |
| 同名不覆盖 | 目标已存在时自动追加序号 |

---

## 目录结构

```text
FilePilotLite/
├─ backend/
│  ├─ main.py            路由层
│  ├─ config.py          规则库：分类、排除、黑名单、垃圾规则
│  ├─ fsutil.py          文件系统工具（安全拦截 / 遍历 / 哈希 / 目录树）
│  ├─ jobs.py            任务模型 + 线程执行 + SSE + 回滚
│  ├─ store.py           JSON 持久层
│  └─ services/          各功能实现
├─ frontend/             Vue 3 前端
├─ tests/smoke_test.py   端到端冒烟测试（44 项）
├─ docs/                 设计文档
├─ start_all.bat         一键启动前后端
└─ requirements.txt
```

---

## 启动

```powershell
# 后端依赖
python -m pip install -r requirements.txt

# 前端依赖
cd frontend
npm install
cd ..
```

双击 `start_all.bat`，或者分别运行：

```powershell
# 终端一
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 终端二
cd frontend
npm run dev
```

- 前端：<http://127.0.0.1:5173>
- 后端 API：<http://127.0.0.1:8000/api>
- API 文档：<http://127.0.0.1:8000/docs>

---

## 测试

后端起来之后：

```powershell
python tests/smoke_test.py
```

会造一个临时样本目录，覆盖扫描、目录树、查重、垃圾、迁移规划、迁移执行、回滚、
隔离区还原、日志、Agent 本地意图，共 44 项断言。

---

## 可选配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 启用 Agent 的 DeepSeek 兜底（不配则只用本地规则） | 未设置 |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-chat` |
| `FILEPILOT_TRASH` | 隔离区兜底位置 | `~/FilePilotTrash` |
| `FILEPILOT_SCAN_LIMIT` | 单次扫描条目上限 | `400000` |

```powershell
$env:DEEPSEEK_API_KEY = "your-api-key"
```

不要把真实密钥写入代码或提交到仓库。

---

## 环境要求

- Python 3.10+（实测 3.12）
- Node.js 18+
- Windows（隔离区与系统回收站能力依赖 Windows API）

---

## 注意事项

清理、迁移和备份都会真实影响磁盘内容。执行批量操作前先看预览清单，确认目标目录有足够空间。
所有删除默认先进隔离区，可以在「回收站」页还原。

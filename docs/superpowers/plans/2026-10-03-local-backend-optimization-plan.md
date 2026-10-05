# FilePilot Lite 本地后端优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将后端优化为可靠、安全、可分发的本地桌面服务。

**Architecture:** FastAPI 仅绑定 localhost；SQLite 保存任务、日志、隔离区；固定线程池运行本地文件任务；fsutil 统一执行路径和安全检查；JSON 保留设置与配方。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLite、pytest、现有 Vue 前端。

**Spec:** `docs/superpowers/specs/2026-10-03-local-backend-optimization-design.md`

## Global Constraints

- 不实现账号、云端、多租户或远程访问。
- 不修改用户 Unity 项目。
- 保持现有 API 响应字段兼容，新增字段不得破坏前端。
- 文件删除默认进入隔离区。
- 所有生产代码改动先有失败测试。

---

### Task 1: 建立测试基础和安全边界

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_fs_security.py`
- Modify: `backend/fsutil.py`

- [ ] 为保护目录、只读区、符号链接和迁移边界写失败测试。
- [ ] 运行 `python -m pytest tests/test_fs_security.py -q`，确认测试因缺少新校验而失败。
- [ ] 实现 `assert_within`, `assert_no_symlink`, `assert_migration_paths` 等最小安全接口。
- [ ] 再次运行该测试并通过。

### Task 2: SQLite 持久化与旧数据迁移

**Files:**
- Create: `backend/database.py`
- Create: `tests/test_database.py`
- Modify: `backend/store.py`
- Modify: `backend/config.py`

- [ ] 测试 SQLite 初始化、事务、任务/日志/隔离区 CRUD 和 JSON 幂等迁移。
- [ ] 运行测试确认失败。
- [ ] 实现 SQLite schema、WAL、线程安全连接和迁移函数。
- [ ] 保留 settings/recipes JSON 接口。
- [ ] 运行数据库测试通过。

### Task 3: 任务线程池和状态可靠性

**Files:**
- Create: `tests/test_jobs.py`
- Modify: `backend/jobs.py`

- [ ] 测试最大并发数、取消、失败、partial 和终态事件。
- [ ] 运行测试确认失败。
- [ ] 用固定 `ThreadPoolExecutor` 替代无限制裸线程。
- [ ] 增加任务状态转换校验、partial 状态和事件序号。
- [ ] 运行任务测试通过。

### Task 4: API 参数、CORS 和错误响应

**Files:**
- Create: `tests/test_api_contract.py`
- Modify: `backend/main.py`

- [ ] 测试限制 depth/limit/max_items/rate_limit/message，测试本地 CORS 和错误状态码。
- [ ] 运行测试确认失败。
- [ ] 增加 Pydantic Field 约束，收紧 CORS，增加统一错误结构。
- [ ] 确保应用默认只声明 localhost 启动配置。
- [ ] 运行 API 测试通过。

### Task 5: 迁移和备份可靠性

**Files:**
- Create: `tests/test_migrate_backup.py`
- Modify: `backend/services/migrate.py`
- Modify: `backend/services/backup.py`

- [ ] 测试源目标嵌套、伪造 items、备份取消和 partial ZIP 清理。
- [ ] 运行测试确认失败。
- [ ] 实现迁移前后校验、冲突策略安全处理、CSV 标准写入、partial ZIP 原子完成。
- [ ] 运行测试通过。

### Task 6: 性能、启动和回归验证

**Files:**
- Modify: `backend/services/overview.py`
- Modify: `backend/services/duplicates.py`
- Modify: `README.md`
- Modify: `tests/smoke_test.py`

- [ ] 添加哈希缓存/overview 缓存行为测试。
- [ ] 运行完整测试，记录现有失败。
- [ ] 实现低风险缓存和大任务持久化优化。
- [ ] 运行完整 pytest、compileall、应用导入和 health 检查。
- [ ] 检查前端调用接口兼容性。

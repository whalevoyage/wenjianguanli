# 参与贡献

这是一个个人项目，但欢迎 issue 和 PR。

## 报告问题

用 [issue 模板](.github/ISSUE_TEMPLATE/bug_report.md)，里面已经列了需要的信息。
模板里写了隐私提醒——**报问题时请把真实路径改成占位**，这个工具处理的是你本机的文件。

**安全问题不要开公开 issue**，见 [SECURITY.md](SECURITY.md)。

## 提 PR 之前

先说一句：这个项目的交互细节是反复打磨过的（禁用清单、口径、文案），
**大改动建议先开 issue 聊一下**，避免你写完发现方向不对。

小修小补（错别字、明显 bug、兼容性）直接提就行。

## 本地跑起来

```bash
# 后端
pip install -r requirements.lock
python -m pytest                    # 单元测试（362 项）
python -m uvicorn backend.main:app --port 8000

# 前端
cd frontend && npm ci && npm run dev
```

端到端冒烟需要**另起一个测试后端**（它会把样本建在仓库根，别对着打包版跑）：

```bash
FILEPILOT_DATA_DIR=/tmp/atlas-test python -m uvicorn backend.main:app --port 8236
FILEPILOT_API=http://127.0.0.1:8236/api python tests/smoke_test.py
```

## 硬规则

这几条不是风格偏好，是**会破坏安全边界**的：

1. **用户文件的写操作必须走 `backend/execution.py`。**
   直接写 `shutil.move` / `.unlink()` / `rmtree()` 会被 `tests/test_execution_guard.py`
   的静态守卫拦下，CI 直接红。原因：所有门禁（权限档、写保护、链接逃逸、目录越界）
   都收在那一个地方，绕过它就等于绕过全部。
2. **模型不能直接碰文件。** 它只产出计划，工具名要在 `tool_specs.py` 里注册过，
   写动作前由本地强制插确认步骤。
3. **删除文案不能说"可恢复"。** 隔离区的能恢复，`trash_purge` 和 Agent 清理不能。
   文案要跟实际行为一致，这类错误比 bug 更伤人。
4. `ruff check backend/ tests/` 和 `pytest` 必须过（CI 就这么验的）。

## 代码风格

- Python 3.11+，行宽 120，`ruff` 会管
- 注释写「为什么」，不写「做了什么」——代码本身能看出做了什么
- 前端沿用现有设计令牌（`frontend/src/styles/global.css`），不要引入新的色值/字号

## 不要提交

`_local_shots/`、`_local_docs/`、`.filepilot/`、`release/`、`dist/`、`node_modules/`
都已在 `.gitignore` 里。另外**绝对不要提交 API Key**，哪怕是测试用的真 key。

# Security

## 报告问题

如果发现安全问题，请**不要开公开 issue**。用仓库的 Security → Report a vulnerability
私下提交，或在 issue 里只写「有安全问题，请私聊」并留下联系方式。

个人项目，响应不保证 SLA，但会认真看。

## 这个工具能碰什么

Atlas 是一个**能真的删你文件**的本地工具，所以它的安全边界值得先说清楚：

| 层 | 约束 | 说明 |
|---|---|---|
| 网络 | 只监听 `127.0.0.1` | 没有对外端口，没有账号体系 |
| 权限档 | `readonly` / `standard` / `full` | 门禁在**服务层**，不是只写在路由上；只读档下任何写操作都拒 |
| 写路径 | 全部经过 `backend/execution.py` | 检查顺序固定：权限档 → 目标写保护 → 链接/junction 逃逸 → 目录越界 |
| 保护目录 | 自保护 + 软保护 + 系统只读区 | 自保护（工具自身数据/隔离区）任何档位不可写；桌面在 standard 下不可写 |
| 删除 | 默认进隔离区 | 唯一物理删除口是 `trash_purge`（清空隔离区），风险等级固定 `high` |
| Agent | 模型只产计划 | 工具名不在册即丢弃；写动作前**本地强制**插确认步骤，模型绕不过 |
| MCP | 只读工具，白名单默认空 | 详见 [`docs/MCP_SECURITY.md`](docs/MCP_SECURITY.md) |

更完整的限制清单在 [`docs/LIMITS.md`](docs/LIMITS.md)。

## 已知的非目标

- **不防本地攻击者。** 能读你用户目录的程序本来就能读你的文件，Atlas 不提供沙箱。
- **不防恶意文件内容。** 扫描只读文件名/大小/哈希；但把文件名原样显示出来时，
  前端一律转义（见 `frontend/src/utils/miniMarkdown.js` 的 `escapeHtml`）。
- **API Key 的保护上限是 Windows DPAPI。** 加密后存在设置文件里；同一台机器同一用户
  能解密 —— 这是 DPAPI 的模型，不是 Atlas 的额外承诺。DPAPI 不可用时**拒绝保存**，
  除非显式设置 `FILEPILOT_ALLOW_PLAINTEXT_SECRETS=1`（开发用，会在界面上提示明文）。

## 提交代码时

- 不要提交任何 API Key、个人路径、`_local_shots/`、`.filepilot/`。
- 新增会改动用户文件的代码，必须走 `backend.execution`；`tests/test_execution_guard.py`
  里有一条静态守卫会扫描源码，直接写 `shutil.move` / `.unlink()` 会让 CI 变红。

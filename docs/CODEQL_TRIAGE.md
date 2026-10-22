# CodeQL 发布前复核

复核日期：2026-10-22。工作流成功仅表示分析完成，告警还需逐项判断。

## 修复的告警

- #3、#4（`py/path-injection`）：CSV 报表导出把未知 `group_by` 原样拼进文件名。改为只接受已注册的分组模式，在创建目录或写文件前拒绝无效值；新增越界输入和正常导出测试。
- #14（`py/polynomial-redos`）：体积正则可从每个数字重新尝试长匹配。限制数字起点并使用 Python 3.11+ possessive quantifier；超大体积先钳制再转整数。
- #15、#16、#17（`py/polynomial-redos`）：空间目标正则中的相邻可空重复存在回溯。改为 possessive quantifier，保留已支持的数字、空白和单位表达。
- #18（`py/polynomial-redos`）：解释意图的关键词与 `.*` 组合可多次扫描后缀。改为按行检查固定关键词顺序。

恶意长文本回归测试在子进程内运行并设超时；普通中文空间表达和解释语义同时受测试保护。

## 经复核的误报

- #1（`tests/test_providers.py`）：`api.deepseek.com` 子串检查用于扫描业务源文件、禁止重新硬编码服务 URL。该测试没有接受或净化网络 URL，真实 URL 校验位于 `providers.validate_base_url`。
- #2（`fsutil.normalize`）：这是文件管理工具解析用户选择的任意路径的函数，自身不执行 IO，也不是授权边界。读操作允许用户选择来源；文件写入仍通过 `backend.execution` 的权限、保护目录和链接/越界检查，既有安全测试覆盖这些边界。
- #5–#12（`py/stack-trace-exposure`）：FastAPI 以默认 `debug=False` 运行，只序列化固定的 HTTP detail 或用户可见的校验/DPAPI 错误；异常上下文和 Python traceback 没有序列化。新增 API 测试确认内部 KeyError 内容和调用栈不会进入响应。保留有用的密钥保存失败与权限错误提示。
- #13（`store.save_json`）：通用 JSON 写入器接收的是 `agent_settings` 已调用 `secrets.protect()` 的结果。Windows 发行版必须先经 DPAPI，失败不落盘；非 Windows 的明文开发模式需显式环境开关并带 `plain:` 标记。密钥存储和失败关闭已有回归测试，不将通用 JSON 层改为再次加密全部状态。

误报仅按以上具体告警编号记录和处理，不禁用整类规则或目录。

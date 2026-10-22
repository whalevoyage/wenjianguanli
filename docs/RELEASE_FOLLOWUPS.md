# 发布后续工作

状态：2026-10-22。v2.1.1 交付工程整改；自动更新与 MFT/USN 留待后续版本。

## Windows 代码签名方案

建议优先申请 [SignPath Foundation 的开源项目签名服务](https://signpath.org/apply.html)。目前尚未提交申请、开通服务或取得证书，Atlas 的发行文件仍然未签名。

该服务需要审核项目；免费服务并非自动获批。维护者需确认符合 [开源项目条件](https://signpath.org/terms.html)，开启账户 MFA，并为每次签名请求设置人工批准流程。获批后再公布正式 Code signing policy 和服务署名，不能提前声称签名由 SignPath 提供。

申请资料草稿：

- 项目：Atlas；维护者 GitHub 账户：`whalevoyage`。
- 仓库：https://github.com/whalevoyage/wenjianguanli
- 许可证：MIT；已提供 Windows portable ZIP 发行形式。
- 产品：本地优先的 Windows 文件管理与磁盘整理工具；文件写入经本地执行层和用户确认。
- 构建：GitHub Actions Windows runner、CPython 3.12、Node.js；版本一致性检查、便携 ZIP、SHA256 sidecar 和 packaged smoke。
- 隐私政策：[PRIVACY.md](../PRIVACY.md)。模型请求仅发往用户配置的 Provider；可能包含所需路径、结果摘要和用户要求的搜索片段。
- 作者/审查者/签名批准者：由维护者确认；不得在申请中虚构独立审查人员或 MFA 状态。

获批后的接入步骤：为自有 `Atlas.exe` 设置一致的产品名和文件版本元数据；从 CI 构建 artifact 发起签名请求；人工批准后下载签名产物；验证签名和时间戳；再生成 ZIP、SHA256 并运行 packaged smoke。上游 DLL 不冒用 Atlas 的证书签名。

若免费服务未获批，再评估商业 OV 证书或符合地区/身份要求的云签名服务。Microsoft 的 [Artifact Signing 前置条件](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) 对申请地区和个人/组织身份有限制，应在购买前核对。

## 自动更新：后续版本

实现前确定 portable 目录的替换策略、安装版更新路径和用户确认方式。验收范围至少包括 HTTPS 下载、可信签名/清单验证、更新前退出、失败回滚、数据目录保留、降级与重放防护，以及断网/下载中断场景。当前版本不自动联网检查更新。

## MFT/USN：后续版本

保持现有目录遍历作为默认和回退路径。首先实现只读 NTFS 枚举原型，明确卷权限、链接/重解析点处理、路径白名单和排除规则。USN 增量阶段必须覆盖 journal 重建/轮转、游标失效、重命名及删除事件；不能让快速枚举绕过现有权限和路径安全边界。采用标准用户和提升权限场景分别测试后，再决定是否进入发行版。

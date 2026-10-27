# Windows 发布签名

## 当前状态

本项目当前发布的 Windows 安装程序没有 Authenticode 代码签名。GitHub Actions 可以为安装程序和源码 ZIP 生成构建来源证明，并发布 SHA-256 校验值；这些证明帮助确认文件来自本仓库的指定提交和构建工作流，但**不等同于 Windows 代码签名，也不会保证 SmartScreen 不再提示**。

每次 `v<版本号>` 标签触发构建后，工作流会创建一个草稿 Release。发布者应先检查构建结果、文件和说明，再手动发布。签名证书尚未配置时，不应将安装包描述为“已签名”。

## 开源签名服务

可申请 SignPath Foundation 的开源代码签名服务。Lyrics 原创代码现采用 OSI 批准的 GPL-3.0-or-later；预设1按固定 Auto-LRC Apache-2.0 仓库中的 BDR 检查点及其 Git LFS 说明单独采用 Apache-2.0。项目许可证选择本身不能证明整个发布物符合资格：SignPath 还要求所有组件均无专有代码或商业双重许可，且项目已发布并持续维护；是否获批由其审核决定。

Auto-LRC、依赖、模型及其他第三方组件仍各自受其许可约束。更多理由和边界见 LICENSE-RATIONALE.md。

## 启用 Authenticode 时

- 只允许可信的 Release 构建流程签署从受审查提交构建出的文件；不要在仓库中保存私钥或证书密码。
- 对外分发的安装程序应在上传 Release 前签署，并在签署之后计算 SHA-256。
- 如需签署 Inno Setup 卸载程序，配置 Inno Setup 的 `SignTool` 与 `SignedUninstaller`。
- 签名后不得再修改文件；否则签名验证会失败。
- 持续使用同一个受信任的发布身份。签名有助于验证发布者和文件完整性，但新文件仍可能触发 SmartScreen 提示。


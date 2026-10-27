# GitHub Release 操作

1. 更新根目录 `VERSION`，并将改动提交到 `main`。
2. 创建并推送与版本一致的标签，例如 `v1.0.1`。GitHub Actions 会检查标签与 `VERSION` 一致，在 Windows runner 上构建安装程序和源码 ZIP，生成 SHA-256 与构建来源证明，并创建一个**草稿** Release。
3. 检查草稿中的 `Lyrics-Setup-<版本>-Windows-x64.exe`、源码 ZIP、`SHA256SUMS.txt` 和自动生成的发布说明，再手动发布。
4. 安装程序需要 64 位 Python 3.10 或 3.11；安装时需要联网下载 CPU 运行环境，首次打轴时下载 Auto-LRC 模型。
5. 保留 `LICENSE`、`LICENSE-RATIONALE.md`、README 和 `THIRD-PARTY-NOTICES.md`。Lyrics 原创代码与项目文档采用 GNU GPL-3.0-or-later；`model-presets/预设1.pt` 根据固定 Auto-LRC 提交的 Apache-2.0 根许可证单独按 Apache-2.0 分发，并保留 `model-presets/LICENSE`。其他依赖和模型仍遵循各自适用的第三方条款。
6. 发布前核对新增依赖、模型或数据是否带有单独条款；若上游为 Auto-LRC checkpoint 发布比仓库根许可证更具体的条款，应更新预设许可说明和发布物。

当前安装程序没有 Authenticode 签名。构建来源证明与 SHA-256 不能代替代码签名；请先阅读 [`CODE-SIGNING.md`](CODE-SIGNING.md)。

GitHub Actions 使用固定版本的 Inno Setup 7.1.0，并先验证其 GitHub Release 来源证明和 Authenticode 签名。按 Inno Setup 当前说明，非商业用户无需购买；营利组织或个人达到其商业用户条件后，维护者请求购买许可证。商业用途下，无人值守 CI 中每台可并行调用编译器的机器计为一个用户；请按[当前许可说明](https://jrsoftware.org/isorder.php)核对项目适用情况。

也可以在本地手动构建：安装 Inno Setup 6.3 或 7，然后依次运行 `powershell -ExecutionPolicy Bypass -File .\tools\Build-Installer.ps1`、`powershell -ExecutionPolicy Bypass -File .\tools\Build-ReleaseZip.ps1` 和 `powershell -ExecutionPolicy Bypass -File .\tools\Write-Checksums.ps1`。

安装程序默认安装到当前用户的 `%LOCALAPPDATA%\Programs\Lyrics`，不申请管理员权限。它不包含 Python 虚拟环境、上游模型缓存或 Demucs 预训练权重；它保留小型 `model-presets/预设1.pt`。安装会运行 CPU 依赖准备流程，模型仍在首次打轴时按固定版本下载和校验。

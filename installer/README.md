# 构建 Windows 安装程序

GitHub Release 的主要安装文件为 `Lyrics-Setup-<版本>-Windows-x64.exe`。安装器只复制应用文件和轻量预设到 `%LOCALAPPDATA%\Programs\Lyrics`，创建开始菜单快捷方式；不检查 Python、不联网，也不下载或安装依赖、运行环境或模型。用户应先阅读安装目录中的 `README.md`，再从开始菜单手动运行 CPU/CUDA 运行环境安装入口和 Auto-LRC 模型下载入口。

## 构建步骤

1. 从 [jrsoftware.org/isinfo.php](https://jrsoftware.org/isinfo.php) 安装 Inno Setup 6.3 或 7。
2. 在 `Lyrics repo` 文件夹运行：`powershell -ExecutionPolicy Bypass -File .\tools\Build-Installer.ps1`。
3. 将 `dist\Lyrics-Setup-<版本>-Windows-x64.exe` 附加到对应的 GitHub Release。

构建脚本从 VERSION 读取版本号，检查安装器输入文件，并将生成文件写入 dist。安装程序不包含 Python 虚拟环境、用户音频、模型缓存、Auto-LRC checkpoint 或 Demucs 权重；会包含小型本地边界模型预设 model-presets\预设1.pt，以及该预设单独适用的 Apache-2.0 许可证。安装目录同时附带项目 GPL 许可证和第三方通知。它不会在安装期间运行安装脚本或进行网络连接。

安装按当前用户进行，不需要管理员权限，也不要求系统已经安装 Python。用户阅读随附 README 后，手动安装 64 位 Python 3.10/3.11，再从开始菜单明确运行 CPU/CUDA 运行环境安装入口和模型下载入口。依赖安装和模型下载均需要网络；Auto-LRC 模型在单独下载并校验后可离线打轴。

若构建失败，可在 Inno Setup Compiler 中打开 `.iss` 文件查看诊断。发布前需按 `THIRD-PARTY-NOTICES.md` 和项目 README 中列出的适用许可审核项目与随包内容。

Inno Setup 是构建时工具，不随安装程序分发。其维护者对商业使用请求购买商业许可证；若通过无人值守 CI 调用编译器，或 Lyrics 用于商业场景，请查看其当前许可说明。详见 [Inno Setup 商业许可证说明](https://jrsoftware.org/isorder.php)。

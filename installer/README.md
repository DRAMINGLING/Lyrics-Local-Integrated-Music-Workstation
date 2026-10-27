# 构建 Windows 安装程序

GitHub Release 的主要安装文件为 `Lyrics-Setup-<版本>-Windows-x64.exe`。程序按当前用户安装到 `%LOCALAPPDATA%\Programs\Lyrics`，创建开始菜单快捷方式，并在安装过程中准备 CPU 运行环境。Python 及固定版本依赖在安装时下载；Auto-LRC 模型留到首次打轴时下载并校验，以保持安装程序轻量。

## 构建步骤

1. 安装 64 位 Python 3.10 或 3.11，并启用 Python Launcher（`py`）。
2. 从 [jrsoftware.org/isinfo.php](https://jrsoftware.org/isinfo.php) 安装 Inno Setup 6.3 或 7。
3. 在 `Lyrics repo` 文件夹运行：`powershell -ExecutionPolicy Bypass -File .\tools\Build-Installer.ps1`。
4. 将 `dist\Lyrics-Setup-<版本>-Windows-x64.exe` 附加到对应的 GitHub Release。

构建脚本从 VERSION 读取版本号，检查安装器输入文件，并将生成文件写入 dist。安装程序不包含 Python 虚拟环境、用户音频、模型缓存、Auto-LRC checkpoint 或 Demucs 权重；会包含小型本地边界模型预设 model-presets\预设1.pt，以及该预设单独适用的 Apache-2.0 许可证。安装目录同时附带项目 GPL 许可证和第三方通知。

安装按当前用户进行，不需要管理员权限；安装 Python 依赖时需要联网。安装后可从开始菜单使用 **安装或修复 CUDA 运行环境**，这需要兼容的 NVIDIA 驱动并会下载较大的 PyTorch 包。Auto-LRC 模型仍在首次打轴时下载。

若构建失败，可在 Inno Setup Compiler 中打开 `.iss` 文件查看诊断。发布前需按 `THIRD-PARTY-NOTICES.md` 和项目 README 中列出的适用许可审核项目与随包内容。

Inno Setup 是构建时工具，不随安装程序分发。其维护者对商业使用请求购买商业许可证；若通过无人值守 CI 调用编译器，或 Lyrics 用于商业场景，请查看其当前许可说明。详见 [Inno Setup 商业许可证说明](https://jrsoftware.org/isorder.php)。

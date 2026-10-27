# Auto-LRC 引擎（Lyrics 集成）

此目录包含 Lyrics 桌面程序的 Auto-LRC 适配层、边界模型训练器和固定版本的 Auto-LRC 上游源码。上游提交记录于 `UPSTREAM_VERSION.txt`；上游许可证保留在 `upstream/LICENSE`。

## 安装和资产

从项目根目录运行 `InstallAutoLrc.cmd`（CPU 默认）或 `InstallAutoLrc.cmd --cuda`。Python 3.10/3.11 及其 Launcher 是前置条件。虚拟环境位于 `auto-lrc-engine/.venv/`，只用于当前项目。

Auto-LRC 的 MTL、BDR 和语言识别文件不会放在 Release ZIP 中。安装运行环境后，用户应明确运行项目根目录的 `DownloadAutoLrcModels.cmd`，从固定上游提交下载文件。下载器检查固定字节数和 SHA-256 后，原子写入 `%LOCALAPPDATA%\Lyrics\models\auto-lrc\<commit>\`。如果旧模型仍在 `upstream/`，应用会验证后直接从原位置加载，不会复制或移动模型。TLS、大小和摘要校验失败时文件不会加载。打轴和训练只验证及读取本地文件，不会自动联网下载模型。上游加载器通过环境变量读取当前有效模型路径，而不会把模型写进只读的程序目录。

人声分离是可选功能。Demucs 代码包随运行依赖安装；其较大的预训练权重不随包分发，也不会默认下载。界面先显示权重许可说明，用户确认并启用后，Demucs 才会向上游请求模型。权重限制详见根目录 README 和 `THIRD-PARTY-NOTICES.md`。

## 文件

- `adapter.py`：将 UI 输入交给上游 Auto-LRC，并复用已加载模型。
- `model_manager.py`：显式下载、校验和缓存固定版本模型；推理和训练阶段通过 `verify_auto_lrc_assets()` 仅验证本地缓存。
- `download_auto_lrc_models.py`：由用户手动触发的模型下载命令行入口。
- `train_boundary.py`：使用人工检查时间微调 BDR 边界检测模型。
- `audio_metadata.py` / `embedded_lyrics.py`：读取和写入本地音频标签。Auto-LRC 页可将编辑歌词原子覆盖到当前音频；保存后会重新打开文件读取歌词标签，确认后续读取到新内容。
- `online_lyrics.py`：向公开歌词 API 搜索候选和获取歌词文本。
- `upstream/`：固定提交的上游源代码和许可证。
- `UPSTREAM-MODIFICATIONS.md`：说明为缓存路径和安全加载新增的上游集成改动。

## 训练模型

反馈仅保存在 `%LOCALAPPDATA%\Lyrics\feedback\`。至少需要两首不同歌曲并留出验证集。人工作品的时间戳用于边界检测微调，不会训练 Auto-LRC 的声学模型。应用只有在验证损失改善后才启用新边界权重；模型预设保存在项目 `model-presets/` 目录。

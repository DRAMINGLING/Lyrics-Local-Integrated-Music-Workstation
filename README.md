# Lyrics 本地集成音乐工作站

Lyrics 是一个由 Chatgpt 6 Luna 主力开发的 Windows 本地桌面工作站，提供歌词导入与打轴、音频标签和内嵌歌词编辑、在线歌词检索、试听检查，以及基于人工反馈的边界模型微调。程序以 Auto-LRC 上游模型生成为时间轴草稿；最终时间建议试听确认。

## 选择的发布与运行方式

安装包只复制程序文件、安装说明和轻量的本地最佳模型预设。它不会检查或安装 Python，也不会联网下载 Python 运行环境、依赖包或模型。用户先阅读本说明，再从开始菜单明确选择要安装的运行环境和模型；这样安装行为由用户控制，也避免将数百 MB 的运行库和模型放进 GitHub Release。

CPU 运行环境是推荐选项，兼容面广。安装包本身不会安装 CPU 或 CUDA 依赖。阅读本 README 后，可从开始菜单手动运行“手动安装或修复 CPU 运行环境”；NVIDIA GPU 用户可选择“手动安装或修复 CUDA 运行环境”，安装 CUDA 12.1 版 PyTorch。CUDA 包更大，脚本会先检查 NVIDIA 驱动和 GPU，失败会明确报错，不会静默改装另一种运行方式。设置里的“自动”会在当前 PyTorch 可用时使用 GPU，否则使用 CPU。

## 窗口与实现语言评估

这版继续使用 Python + Tkinter。界面耗时操作已有后台线程；模型推理由 PyTorch 原生 CPU / CUDA 内核执行，因此只换窗口语言不会直接加速打轴。源码中更明确的候选热点是 Auto-LRC 的纯 Python `alignment_bdr` 动态规划循环。若以后实测打轴较慢，优先评估该计算段的 JIT / 原生加速；若主要目标改为成熟的 Windows 原生窗口，再考虑 C# WPF 前端并保留 Python 模型引擎。完整方案比较见 [`TECH_STACK_EVALUATION.md`](TECH_STACK_EVALUATION.md)。

### 模型与预设

- **Auto-LRC 原始模型**：上游固定版本为 [`2c475cc62dda50572d7a292fd88cc39e8aa2ae2c`](auto-lrc-engine/UPSTREAM_VERSION.txt)。安装运行环境后，用户从开始菜单手动运行“手动下载 Auto-LRC 模型”，获取本应用需要的 MTL 声学权重、BDR 边界权重和语言识别文件，存入 `%LOCALAPPDATA%\Lyrics\models\auto-lrc\`。下载器只取固定版本，并逐个验证文件大小和 SHA-256；缺少模型时应用不会在打轴过程中自行联网下载。
- **预设1**：`model-presets/预设1.pt` 是发布者本地训练得到的最佳边界模型，保留为可选择的本地预设。它只替换 BDR 边界权重，MTL 声学模型仍使用 Auto-LRC 上游原始权重。
- **恢复上游模型**：设置页“恢复预训练模型”会停用本地微调模型，恢复使用上游的 MTL + BDR。源代码仍保存在 `auto-lrc-engine/upstream/`；原始大权重由用户从开始菜单的模型下载入口取回，不塞进 Release ZIP。
- **人声分离**：默认关闭。Demucs 程序代码与其预训练权重适用不同条款。维护者说明相关权重仅供科学用途，所以 Lyrics 不分发这些权重；用户在界面勾选人声分离时会先看到许可提示，并在确认后由 Demucs 上游下载。也可以直接使用未分离音频打轴。

## 安装和启动

1. 安装器不会联网，也不要求先安装 Python。下载并运行 `Lyrics-Setup-<版本>-Windows-x64.exe`，程序将按当前用户安装到 `%LOCALAPPDATA%\Programs\Lyrics`，不需要管理员权限。
2. 安装后先从开始菜单打开“阅读安装说明”，或查看安装目录中的 `README.md`。然后安装 64 位 Python 3.10 或 3.11，并启用 Python Launcher（`py`）。Python 安装程序请从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/)获取。
3. 运行开始菜单中的“手动安装或修复 CPU 运行环境”，下载并安装本项目固定版本的 PyTorch 和依赖。该操作由用户主动启动，需要网络。若需 CUDA，请先确认 NVIDIA 驱动和 GPU，再运行“手动安装或修复 CUDA 运行环境”。
4. 运行开始菜单中的“手动下载 Auto-LRC 模型”。约 68 MiB 的上游模型会下载到 `%LOCALAPPDATA%\Lyrics\models\auto-lrc\` 并校验 SHA-256。模型准备好后可离线打轴；在线歌词搜索始终需要联网。
5. 从开始菜单启动 Lyrics。首次打轴前若未手动准备模型，程序会提示用户运行模型下载入口，不会自动联网下载。

人声分离模型仅在用户确认许可并启用该功能后由 Demucs 上游下载，体积较大。源码开发者也可以直接从可写目录运行 `InstallAutoLrc.cmd`，再运行 `DownloadAutoLrcModels.cmd` 和 `AutoLrcAligner.pyw`；GitHub Release 的安装器只复制应用文件，不执行任何依赖或模型下载。

## 使用功能

### 歌词和时间轴

1. 选择音频，然后导入 `.lrc` / `.txt`、读取内嵌歌词、粘贴歌词，或在线搜索歌词。
2. 点击“生成时间轴草稿”。音频和歌词在本机处理；请先通过开始菜单下载并校验 Auto-LRC 模型，打轴时程序只读取本地缓存。
3. 播放、拖动进度条，双击歌词行试听，双击时间戳修正。按鼠标中键标记问题行，可只重打标记段。
4. 检查后可导出 LRC 或导出带内嵌歌词的音频副本。也可导入人工 LRC 对照模型时间。
5. “确认检查并学习”保存本地反馈；在设置页启动训练时，程序按歌曲留出验证集，仅在验证损失改善时替换当前本地边界模型。它不会训练 Auto-LRC 的 MTL 声学模型。

时间轴是草稿。翻唱、混响、和声、歌词文本版本不一致或混合文字系统仍需人工检查。

### 音频标签

音频编辑页可以查看和修改标题、歌手、专辑、封面、年份、流派、作曲、备注和内嵌歌词。支持常见 MP3、M4A、FLAC、Ogg、Opus 标签；写入采用临时文件再替换。支持格式及标签能力以界面为准。

### 在线歌词

在线搜索会向网易云、QQ 音乐、酷狗和 LRCLIB 发送歌曲名称、歌手、专辑或用户输入的搜索关键字，用于查询歌词候选；不会上传音频、歌词草稿或本地文件路径。选中候选后再获取歌词，由用户预览并手动导入。各服务接口及可用性可能变化，使用时应遵守对应平台条款。

歌词搜索网络接口的来源和多来源检索思路参考了 [MusicTag 项目](https://github.com/zsxink/MusicTag)公开介绍的外部接口调用方向。Lyrics 的网络客户端和界面由本项目独立实现。在 Lyrics 的开发过程中，**没有把 MusicTag 的任何源代码作为提示提供给 AI，也没有要求 AI 改写、优化或改进 MusicTag 的源代码**。

## Windows 安装器签名状态

当前提供的 Windows 安装器没有 Microsoft Authenticode 数字签名。首次下载或运行时，Windows 可能将它显示为未知发布者，或出现 SmartScreen 提示；请仅从项目的官方 GitHub Release 下载，并核对同一 Release 提供的 SHA-256 校验值。GitHub 构建来源证明和 SHA-256 可帮助核验文件来源与完整性，但不等同于 Authenticode 签名，也不能保证消除 SmartScreen 提示。后续若启用可信签名，会在发布说明中公布签名身份和验证方式。

更多签名状态和后续方案见 [CODE-SIGNING.md](CODE-SIGNING.md)。

## 本机文件和数据位置

程序内的默认文件夹按 Lyrics 项目结构设置，而不是依赖开发者电脑上的媒体路径：

- `input/`：音频、歌词和对照 LRC 文件选择器的默认目录。
- `output/`：LRC 和带内嵌歌词音频副本的默认导出目录。
- `covers/`：封面图片选择器的默认目录。
- `model-presets/`：可选择的边界模型预设，发布包包含 `预设1.pt`。

应用数据、设置、训练反馈和缓存位于 `%LOCALAPPDATA%\Lyrics\`；用户音频和在线搜索内容不会被程序上传。每次训练使用用户本机的反馈音频和时间戳，不会发送到 Lyrics 作者或外部训练服务。

## 目录说明

- `AutoLrcAligner.pyw`：桌面窗口。
- `InstallAutoLrc.cmd`：CPU 安装/更新入口；`--cuda` 可选择 CUDA 运行时。
- `DownloadAutoLrcModels.cmd`：用户手动下载并校验固定版本 Auto-LRC 模型的入口。
- `auto-lrc-engine/adapter.py`：桌面应用与 Auto-LRC 的适配层。
- `auto-lrc-engine/model_manager.py`：固定上游文件下载、缓存和 SHA-256 校验；应用推理时只验证本地模型，不联网下载。
- `auto-lrc-engine/train_boundary.py`：人工反馈边界微调。
- `auto-lrc-engine/upstream/`：固定提交版本的上游源码及其许可证，集成差异列于 `UPSTREAM-MODIFICATIONS.md`。
- `model-presets/预设1.pt`：本地最佳边界模型预设。
- `tools/Build-ReleaseZip.ps1`：生成不含虚拟环境、模型缓存、样本媒体和用户文件的 Windows Release ZIP。
- `tools/Build-Installer.ps1`、`installer/Lyrics.iss`：生成只复制应用文件、不自动安装依赖的 Windows 安装器；构建说明见 [`installer/README.md`](installer/README.md)。
- `tools/Write-Checksums.ps1`：为安装器和源码 ZIP 生成 SHA-256 校验清单。
- `.github/workflows/release-windows.yml`：推送版本标签后自动构建并创建草稿 GitHub Release。

## 许可与来源

Auto-LRC 上游代码及该仓库随附的文件保留其 [Apache License 2.0](auto-lrc-engine/upstream/LICENSE)、版权与来源说明。本项目固定上游提交，并在 [`UPSTREAM-MODIFICATIONS.md`](auto-lrc-engine/UPSTREAM-MODIFICATIONS.md) 记录对上游文件所做的集成修改。Auto-LRC checkpoint 及 fastText 文件在该固定提交中随仓库发布；本项目不把它们放进 Release ZIP，而是从该固定上游地址获取并校验。若上游另行发布适用于模型文件的条款，应以该条款为准。

Demucs 的代码许可为 MIT，但预训练权重不受该代码许可覆盖。Demucs 维护者在[上游许可讨论](https://github.com/facebookresearch/demucs/issues/327)中说明，相关预训练权重仅供科学用途。因此 Lyrics 不随包分发 Demucs 权重，默认关闭该功能，并要求用户确认后才触发上游下载。`pykakasi` 依赖为 GPL-3.0-or-later；依赖详情和来源链接见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md) 与 `auto-lrc-engine/requirements.txt`。

Lyrics 原创代码和项目文档采用 **GNU GPL-3.0-or-later**，完整文本见 [LICENSE](LICENSE)，选择理由和覆盖范围见 [LICENSE-RATIONALE.md](LICENSE-RATIONALE.md)。这一选择主要考虑到固定 Auto-LRC 版本直接调用 GPL-3.0-or-later 的 pykakasi；Apache-2.0 的 Auto-LRC 文件仍保留自己的许可证及来源说明，其他依赖和模型也各自遵循其条款。应用内“关于 / 软件许可”入口说明版权、无担保和许可证查看方式。

model-presets/预设1.pt 从固定 Auto-LRC 提交 `2c475cc62dda50572d7a292fd88cc39e8aa2ae2c` 的 BDR 检查点微调而来。该提交的根目录采用 Apache-2.0，README 将检查点列为 Git LFS 模型文件，未找到更具体的检查点许可证；因此预设1单独按 Apache-2.0 分发，许可文本随附于 `model-presets/LICENSE`。根目录 GPL-3.0-or-later 用于 Lyrics 原创代码与文档。详细依据见 [许可证说明](LICENSE-RATIONALE.md) 和 [第三方通知](THIRD-PARTY-NOTICES.md)。

## 从本地工作台同步

开发工作目录中的 `Lyrics` 与此发布项目 `Lyrics repo` 是并列目录。后续修缮本地程序后，请从 `Lyrics repo` 运行 `.\sync-from-workstation.ps1`，将程序改动同步过来；该脚本会跳过虚拟环境、缓存、上游权重和输入/输出媒体文件，保留此发布仓库的 Git 元数据。

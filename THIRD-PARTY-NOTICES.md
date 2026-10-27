# 第三方代码与模型说明

本文件记录 Lyrics 发布包中直接使用或按需下载的主要第三方组件。依赖包的完整名称和固定版本见 `auto-lrc-engine/requirements.txt`；每个上游组件仍受其自身许可证约束。

| 组件 | 用途 | 许可/分发说明 | 来源 |
|---|---|---|---|
| Auto-LRC (`liu-xiaoran/auto-lrc`) | 多语言歌词与音频对齐；原始 MTL/BDR 与 fastText 文件按需下载 | 随上游仓库提供 Apache-2.0；源码、许可证和来源说明保留。原始模型文件不打包，下载固定提交并做 SHA-256 校验。 | [固定上游提交](https://github.com/liu-xiaoran/auto-lrc/tree/2c475cc62dda50572d7a292fd88cc39e8aa2ae2c)、`auto-lrc-engine/upstream/LICENSE` |
| `model-presets/预设1.pt` | 本机反馈训练得到的 BDR 边界模型预设 | 从固定 Auto-LRC Apache-2.0 仓库中由 Git LFS 管理的 `checkpoint_BDR` 微调；该提交未见单独的 checkpoint 许可文件，因此预设按 Apache-2.0 分发。随包附带 `model-presets/LICENSE` 和来源说明。 | [固定上游 LICENSE](https://github.com/liu-xiaoran/auto-lrc/blob/2c475cc62dda50572d7a292fd88cc39e8aa2ae2c/LICENSE)、[上游 README](https://github.com/liu-xiaoran/auto-lrc/blob/2c475cc62dda50572d7a292fd88cc39e8aa2ae2c/README.md) |
| Demucs | 用户可选的人声分离程序代码 | 代码为 MIT。预训练权重不受 MIT 代码许可覆盖；维护者说明相关权重仅供科学用途。Lyrics 不分发权重，默认不启用，用户确认后才允许上游下载。 | [上游仓库](https://github.com/facebookresearch/demucs)、[权重许可讨论](https://github.com/facebookresearch/demucs/issues/327) |
| pykakasi 2.2.1 | Auto-LRC 日语音素转写 | GPL-3.0-or-later；上游代码直接导入该依赖，随 Python 依赖安装，不作为本项目源码复制。它是本项目选择 GPL-3.0-or-later 的关键兼容性因素。 | [固定版本 PyPI 元数据](https://pypi.org/project/pykakasi/2.2.1/)、[上游源码](https://github.com/miurahr/pykakasi/tree/v2.2.1) |
| PyTorch / torchaudio | 本地声学模型推理和训练 | 依其各自许可证；CPU 或 CUDA wheel 由安装器从官方 PyTorch 索引获取。 | [PyTorch](https://github.com/pytorch/pytorch)、[torchaudio](https://github.com/pytorch/audio) |
| MusicTag | 公开歌词搜索 API 的接口方向参考 | Lyrics 的网络请求代码独立实现；开发中未将 MusicTag 源代码提供给 AI 作为改写或改进提示。未复制 MusicTag 源码。 | [MusicTag 项目](https://github.com/zsxink/MusicTag) |

Lyrics 原创代码和项目文档采用 GPL-3.0-or-later；该许可证不更改上述第三方代码、预训练模型或模型预设各自适用的许可。使用第三方歌词 API 前应自行遵守相应服务条款。

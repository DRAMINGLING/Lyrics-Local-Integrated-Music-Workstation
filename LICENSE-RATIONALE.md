# Lyrics 许可证选择说明

## 选择结果

Lyrics 新增的程序源码和项目文档采用 **GNU GPL-3.0-or-later**，完整标准文本见根目录 LICENSE。此次版权署名使用 GitHub 项目所有者 DRAMINGLING；如果该账号并非原创代码的实际权利人，应在公开发布前更正署名并由真正的权利人确认授权。

## 选择依据

- 固定版本的 Auto-LRC 源码采用 Apache License 2.0；Lyrics 保留了上游许可证、来源和修改记录。
- Auto-LRC 的 t2l/phonetic.py 直接导入 pykakasi，并注明该依赖采用 GPL。Lyrics 固定安装 pykakasi 2.2.1，该版本的 PyPI 元数据将其标为 GPL v3 或更高版本。它在同一 Python 运行环境中被上游歌词对齐代码直接调用，因此使用 GPL 系列比给整个集成程序选宽松的 MIT/Apache 许可证更符合已选定依赖的要求。
- GPLv3 与 Apache-2.0 的组合兼容；GPLv2-only 则不是合适的共同版本。因此采用 GPLv3 并允许后续版本，即 SPDX 标识 GPL-3.0-or-later。分发时仍保留 Auto-LRC 自己的 Apache-2.0 许可证和修改说明；根目录许可证不会抹掉上游文件的署名或许可证。
- Auto-LRC 的 MTL 子目录另附 MIT 许可证；Demucs 程序代码为 MIT。它们继续保留各自的许可证。Demucs 预训练权重不受代码的 MIT 许可证自动授权，仍按维护者对权重的限制处理。

## 许可证适用范围

根目录许可证用于 Lyrics 原创代码、界面和项目文档，以及按适用版权规则可在 GPL 条件下分发的集成程序。明确复制自 Auto-LRC 或其他项目的文件继续遵循文件中或其上游目录所带的原许可证。完整第三方清单见 THIRD-PARTY-NOTICES.md。

model-presets/预设1.pt 是从固定 Auto-LRC 提交 `2c475cc62dda50572d7a292fd88cc39e8aa2ae2c` 的 `checkpoint_BDR` 继续微调的权重。该提交根目录的 Apache-2.0 许可证覆盖上游 Work；同一提交的 README 将检查点列为 Git LFS 管理的模型文件，且没有单独的检查点许可声明。因此预设1按 Apache-2.0 作为上游模型的派生文件分发，并在 `model-presets/LICENSE` 保留许可证文本。根目录 GPL-3.0-or-later 仍适用于 Lyrics 原创程序和文档，不会取代预设自身的 Apache 条件。若上游以后为检查点发布专门条款，应重新核查并按更具体条款处理。fastText 模型和其他下载权重也各依其自身条款。

## 对再发布的影响

使用者可以按 GPL-3.0-or-later 的条件使用、研究、修改和再发布 Lyrics；公开再发布覆盖程序的修改版本时，通常需要提供相应源代码，并保留许可证与修改告知。许可证不禁止收费，但不能把 GPL 覆盖的整体集成程序重新发布为仅限专有使用。软件内“关于 / 软件许可”入口提供版权、无担保和查看完整许可证的提示。

许可证选择以仓库中固定版本的依赖、上游许可证和模型文件说明为依据，并非律师意见或全量传递依赖审计。发布每个版本时仍应检查新增依赖、模型权重和第三方条款。


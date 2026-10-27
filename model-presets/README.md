# 模型预设

`预设1.pt` 是本机人工反馈训练所得的最佳边界检测（BDR）权重，大小约 1.78 MiB，SHA-256：`b5657935c81144bcf5159144591ea6b2dc219ec6b5fc3ba78251a7a3472592fb`。

该预设只替换 Auto-LRC 的 BDR 边界子模型。它不包含音频或歌词文本，MTL 声学模型继续使用上游 Auto-LRC 固定版本。项目设置页可选择、另存和管理预设；“恢复预训练模型”会切回上游原始 MTL + BDR。

该权重从固定 Auto-LRC 提交 `2c475cc62dda50572d7a292fd88cc39e8aa2ae2c` 的 BDR 检查点继续微调而来。该上游仓库以 Apache-2.0 发布，README 说明 checkpoint 由 Git LFS 管理；固定提交中没有单独的 checkpoint 许可证。因此本预设按 Apache-2.0 分发，并在本目录随附完整 `LICENSE` 文本。根目录 GPL-3.0-or-later 适用于 Lyrics 原创代码，不替代本预设适用的 Apache 条款。若上游以后为 checkpoint 发布更具体条款，应重新核查并按该条款处理。

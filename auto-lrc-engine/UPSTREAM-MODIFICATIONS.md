# Auto-LRC 集成修改记录

上游来源：`liu-xiaoran/auto-lrc`，提交 `2c475cc62dda50572d7a292fd88cc39e8aa2ae2c`。上游 `LICENSE` 和来源文档保留在 `upstream/`。

Lyrics 为了在只读安装目录中运行并避免不受限的 pickle 加载，对以下上游文件增加了小范围适配；其他上游文件按固定提交保留。

| 上游文件 | Lyrics 集成修改 |
|---|---|
| `upstream/t2l/mtl/utils.py` | 从 `AUTO_LRC_CHECKPOINT_DIR` 读取已校验的用户缓存权重；将映射到 CPU 的权重限定为 `weights_only=True` 后再加载。 |
| `upstream/t2l/phonetic.py` | 从 `AUTO_LRC_FASTTEXT_PATH` 读取已校验的 fastText 模型文件。 |

修改均在对应源码旁标注，并遵守随上游代码保留的 Apache License 2.0。原始 Auto-LRC 模型权重不在 Release ZIP 内；`model_manager.py` 只从记录的固定提交下载并逐文件校验。
# Auto-LRC checkpoint cache

大型原始 checkpoint 不放在源代码仓库中。阅读 Lyrics 根目录 README 后，用户可运行 `DownloadAutoLrcModels.cmd`，从 `UPSTREAM_VERSION.txt` 指定的固定上游提交下载所需文件，并写入 `%LOCALAPPDATA%\Lyrics\models\auto-lrc\`。下载器按文件大小和 SHA-256 校验；打轴和训练不会自动联网下载缺失文件。

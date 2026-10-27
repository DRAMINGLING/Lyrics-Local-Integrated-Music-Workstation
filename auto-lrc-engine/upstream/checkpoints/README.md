# Auto-LRC checkpoint cache

大型原始 checkpoint 不放在源代码仓库中。Lyrics 在首次打轴或训练时，从 `UPSTREAM_VERSION.txt` 指定的上游提交下载所需文件，并写入 `%LOCALAPPDATA%\Lyrics\models\auto-lrc\`，经文件大小和 SHA-256 校验后再加载。

"""Explicitly download and verify the pinned Auto-LRC model files."""

from __future__ import annotations

import sys

from model_manager import download_auto_lrc_assets


def main() -> int:
    try:
        assets = download_auto_lrc_assets()
    except Exception as exc:
        print(f"Auto-LRC 模型下载或校验失败：{exc}", file=sys.stderr)
        return 1
    print("Auto-LRC 模型已下载并通过 SHA-256 校验。")
    print(f"模型目录：{assets.checkpoint_dir.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

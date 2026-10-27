"""Download and verify the pinned Auto-LRC assets in the per-user cache."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


UPSTREAM_REPOSITORY = "liu-xiaoran/auto-lrc"
UPSTREAM_COMMIT = "2c475cc62dda50572d7a292fd88cc39e8aa2ae2c"
_BASE_URL = (
    "https://media.githubusercontent.com/media/"
    f"{UPSTREAM_REPOSITORY}/{UPSTREAM_COMMIT}"
)
_ASSETS = {
    "checkpoint_MTL": {
        "relative_path": "checkpoints/checkpoint_MTL",
        "size": 68_900_020,
        "sha256": "826559e7e810bf8f90223d8fd5c66525c01ad4dd9cdc19d019f39f784e7ed3c1",
    },
    "checkpoint_BDR": {
        "relative_path": "checkpoints/checkpoint_BDR",
        "size": 1_858_932,
        "sha256": "a3955b290311bec16f04253eac328147d444f275f263315490ec5faf1a668fc9",
    },
    "lid.176.ftz": {
        "relative_path": "lid.176.ftz",
        "size": 938_013,
        "sha256": "8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83",
    },
}
_MAX_BYTES = max(asset["size"] for asset in _ASSETS.values())
_LOCK = threading.Lock()
_VERIFIED: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class AutoLrcAssets:
    checkpoint_dir: Path
    fasttext_model_path: Path


def app_data_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data).expanduser() if local_app_data else Path.home() / ".local" / "share"
    return base / "Lyrics"


def model_cache_directory() -> Path:
    return app_data_directory() / "models" / "auto-lrc" / UPSTREAM_COMMIT


def configure_model_paths() -> AutoLrcAssets:
    """Use verified cache files, or keep valid legacy upstream files in place."""
    cache_dir = model_cache_directory()
    legacy_dir = Path(__file__).resolve().parent / "upstream"
    checkpoint_assets = {
        name: details for name, details in _ASSETS.items()
        if name in ("checkpoint_MTL", "checkpoint_BDR")
    }
    checkpoints = cache_dir / "checkpoints"
    for candidate in (checkpoints, legacy_dir / "checkpoints"):
        candidate_valid = True
        for name, details in checkpoint_assets.items():
            asset_path = candidate / name
            if not _valid_file(
                asset_path,
                expected_size=details["size"],
                expected_sha256=details["sha256"],
            ):
                candidate_valid = False
                break
            _VERIFIED.add((name, str(asset_path)))
        if candidate_valid:
            checkpoints = candidate
            break

    fasttext_model_path = cache_dir / "lid.176.ftz"
    fasttext_details = _ASSETS["lid.176.ftz"]
    legacy_fasttext_path = legacy_dir / fasttext_details["relative_path"]
    for candidate in (fasttext_model_path, legacy_fasttext_path):
        if _valid_file(
            candidate,
            expected_size=fasttext_details["size"],
            expected_sha256=fasttext_details["sha256"],
        ):
            fasttext_model_path = candidate
            _VERIFIED.add(("lid.176.ftz", str(candidate)))
            break

    os.environ["AUTO_LRC_CHECKPOINT_DIR"] = str(checkpoints)
    os.environ["AUTO_LRC_FASTTEXT_PATH"] = str(fasttext_model_path)
    os.environ.setdefault("TORCH_HOME", str(app_data_directory() / "torch"))
    return AutoLrcAssets(checkpoints, fasttext_model_path)


def _valid_file(path: Path, *, expected_size: int, expected_sha256: str) -> bool:
    try:
        if path.stat().st_size != expected_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == expected_sha256
    except OSError:
        return False


def _download_asset(name: str, destination: Path, details: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_BASE_URL}/{details['relative_path']}"
    request = Request(url, headers={"User-Agent": "Lyrics-Desktop/1.0"})
    temporary_path: Path | None = None
    try:
        with urlopen(request, timeout=45) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise RuntimeError(f"上游模型 {name} 返回无效的文件大小。") from exc
                if declared_size != details["size"]:
                    raise RuntimeError(
                        f"上游模型 {name} 的文件大小与固定版本不符，已停止下载。"
                    )

            digest = hashlib.sha256()
            received = 0
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{name}.", suffix=".download",
                dir=destination.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > _MAX_BYTES:
                        raise RuntimeError(f"上游模型 {name} 超出安全下载大小限制。")
                    digest.update(block)
                    temporary.write(block)
                temporary.flush()
                os.fsync(temporary.fileno())

        if received != details["size"] or digest.hexdigest() != details["sha256"]:
            raise RuntimeError(
                f"上游模型 {name} 未通过 SHA-256 校验；临时文件不会启用。"
            )
        os.replace(temporary_path, destination)
        temporary_path = None
    except (OSError, URLError) as exc:
        raise RuntimeError(
            f"无法从固定的 Auto-LRC 上游版本下载 {name}。请检查网络后重试。\n{exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def verify_auto_lrc_assets() -> AutoLrcAssets:
    """Verify manually downloaded pinned assets without making network requests."""
    assets = configure_model_paths()
    with _LOCK:
        for name, details in _ASSETS.items():
            if name == "lid.176.ftz":
                destination = assets.fasttext_model_path
            else:
                destination = assets.checkpoint_dir / name
            verified_key = (name, str(destination))
            if verified_key in _VERIFIED and destination.is_file():
                continue
            if not _valid_file(
                destination,
                expected_size=details["size"],
                expected_sha256=details["sha256"],
            ):
                raise RuntimeError(
                    f"未找到通过 SHA-256 校验的 Auto-LRC 模型文件：\n{destination}\n"
                    "请先阅读 README.md，然后在开始菜单运行“下载 Auto-LRC 模型”。"
                )
            _VERIFIED.add(verified_key)
    return assets


def download_auto_lrc_assets() -> AutoLrcAssets:
    """Use valid existing assets in place; download only any missing pinned assets."""
    assets = configure_model_paths()
    with _LOCK:
        for name, details in _ASSETS.items():
            if name == "lid.176.ftz":
                destination = assets.fasttext_model_path
            else:
                destination = assets.checkpoint_dir / name
            verified_key = (name, str(destination))
            if verified_key in _VERIFIED and destination.is_file():
                continue
            if _valid_file(
                destination,
                expected_size=details["size"],
                expected_sha256=details["sha256"],
            ):
                _VERIFIED.add(verified_key)
                continue
            print(f"正在下载并校验 Auto-LRC 模型文件：{name}")
            _download_asset(name, destination, details)
            _VERIFIED.add(verified_key)
    return assets

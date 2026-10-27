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
_VERIFIED: set[str] = set()


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
    """Point the vendored upstream loader at per-user storage without changing cwd."""
    cache_dir = model_cache_directory()
    checkpoints = cache_dir / "checkpoints"
    os.environ["AUTO_LRC_CHECKPOINT_DIR"] = str(checkpoints)
    os.environ["AUTO_LRC_FASTTEXT_PATH"] = str(cache_dir / "lid.176.ftz")
    os.environ.setdefault("TORCH_HOME", str(app_data_directory() / "torch"))
    return AutoLrcAssets(checkpoints, cache_dir / "lid.176.ftz")


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


def ensure_auto_lrc_assets() -> AutoLrcAssets:
    """Fetch missing pinned assets and reject truncated or altered cache files."""
    assets = configure_model_paths()
    cache_dir = assets.fasttext_model_path.parent
    with _LOCK:
        for name, details in _ASSETS.items():
            if name == "lid.176.ftz":
                destination = assets.fasttext_model_path
            else:
                destination = assets.checkpoint_dir / name
            if name in _VERIFIED and destination.is_file():
                continue
            if not _valid_file(
                destination,
                expected_size=details["size"],
                expected_sha256=details["sha256"],
            ):
                _download_asset(name, destination, details)
            _VERIFIED.add(name)
    return AutoLrcAssets(assets.checkpoint_dir, cache_dir / "lid.176.ftz")

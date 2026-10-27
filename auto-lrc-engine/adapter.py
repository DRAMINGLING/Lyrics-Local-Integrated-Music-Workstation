"""Desktop-friendly adapter around the vendored Auto-LRC engine."""

from __future__ import annotations

import logging
import gc
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from model_manager import verify_auto_lrc_assets


MAX_AUDIO_BYTES = 200 * 1024 * 1024
ENGINE_ROOT = Path(__file__).resolve().parent
UPSTREAM_ROOT = ENGINE_ROOT / "upstream"
logger = logging.getLogger("auto-lrc-desktop")
if str(UPSTREAM_ROOT) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_ROOT))

_ALIGNABLE = re.compile(
    r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]"
)
_TIME_LINE = re.compile(r"^\s*\[(\d{1,4}):([0-5]\d)[.:](\d{1,3})\]")
_PHONETIC_NON_SPEECH_MARKS = str.maketrans(
    "",
    "",
    '「」『』｢｣“”‘’«»‹›〝〟〞《》〈〉【】〔〕〖〗﹁﹂﹃﹄＂"',
)
_META_LINE = re.compile(
    r"^\s*\[(?:ar|al|ti|by|re|ve|la|length|offset|kana):.*\]\s*$",
    re.IGNORECASE,
)
_CREDIT_LINE = re.compile(
    r"^\s*(作词|作詞|作曲|编曲|編曲|原曲作者|原唱|人声翻唱|人聲翻唱|歌手|演唱|翻唱|"
    r"和声|和聲|混音|母带|母帶|"
    r"制作人|製作人|监制|監製|配唱|录音|錄音|词曲|詞曲|PV)\s*[:：]",
    re.IGNORECASE,
)
_MODEL_LOCK = threading.RLock()
_ACOUSTIC_MODEL = None
_DEMUCS_MODEL = None
_MODEL_DEVICE: str | None = None
_MODEL_BOUNDARY_CHECKPOINT: str | None = None


class VocalModelUnavailableError(RuntimeError):
    """Raised when Demucs cannot load its optional pretrained model."""


def _load_boundary_weights(torch, model, checkpoint_path: str, device: str) -> None:
    checkpoint = torch.load(
        checkpoint_path,
        map_location=device,
        weights_only=True,
    )
    if not isinstance(checkpoint, dict) or not isinstance(
        checkpoint.get("model_state_dict"), dict
    ):
        raise ValueError("模型文件缺少有效的权重数据。")
    model.load_state_dict(checkpoint["model_state_dict"])


@contextmanager
def _upstream_working_directory() -> Iterator[None]:
    if not UPSTREAM_ROOT.is_dir():
        raise RuntimeError(
            "Auto-LRC 引擎文件不完整。请重新下载本工具，或检查 auto-lrc-engine/upstream 目录。"
        )
    previous_directory = Path.cwd()
    os.chdir(UPSTREAM_ROOT)
    try:
        yield
    finally:
        os.chdir(previous_directory)


def _get_models(vocalize: bool, device_preference: str, boundary_checkpoint: str | Path | None = None):
    global _ACOUSTIC_MODEL, _DEMUCS_MODEL, _MODEL_DEVICE, _MODEL_BOUNDARY_CHECKPOINT
    with _MODEL_LOCK, _upstream_working_directory():
        try:
            import torch
            from t2l.mtl.wrapper import load_mtl_model
            from t2l.t2l import load_demucs_model
        except ImportError as exc:
            raise RuntimeError(
                "Auto-LRC 依赖尚未安装。请先阅读 README.md，然后从开始菜单手动安装 CPU 或 CUDA 运行环境。"
            ) from exc

        verify_auto_lrc_assets()

        cuda_available = torch.cuda.is_available()
        if device_preference == "cuda":
            if not cuda_available:
                raise RuntimeError(
                    "当前环境没有可用的 CUDA GPU。请确认电脑有 NVIDIA GPU，"
                    "并已从开始菜单手动安装 CUDA 版运行环境；也可以改选“自动”或“CPU”。"
                )
            selected_device = "cuda"
        elif device_preference == "cpu":
            selected_device = "cpu"
        elif device_preference == "auto":
            selected_device = "cuda" if cuda_available else "cpu"
        else:
            raise ValueError(f"未知的运行设备选项：{device_preference}")

        checkpoint_path = str(Path(boundary_checkpoint).resolve()) if boundary_checkpoint else None
        if checkpoint_path and not Path(checkpoint_path).is_file():
            raise RuntimeError(
                "找不到本地训练的边界模型。请重新训练，或在界面中恢复预训练模型。"
            )
        checkpoint_key = None
        if checkpoint_path:
            checkpoint_stat = Path(checkpoint_path).stat()
            checkpoint_key = f"{checkpoint_path}|{checkpoint_stat.st_mtime_ns}|{checkpoint_stat.st_size}"

        if _MODEL_DEVICE != selected_device or _MODEL_BOUNDARY_CHECKPOINT != checkpoint_key:
            _ACOUSTIC_MODEL = None
            _DEMUCS_MODEL = None
            gc.collect()
            if cuda_available:
                torch.cuda.empty_cache()
            use_cuda = selected_device == "cuda"
            if checkpoint_path:
                from t2l.mtl.model import BoundaryDetection

                acoustic_model, _, model_type, _, model_device = load_mtl_model(
                    "MTL", cuda=use_cuda, verbose=False
                )
                boundary_model = BoundaryDetection(
                    n_cnn_layers=1,
                    rnn_dim=32,
                    n_class=1,
                    n_feats=32,
                    stride=1,
                    dropout=0.1,
                ).to(selected_device)
                try:
                    _load_boundary_weights(torch, boundary_model, checkpoint_path, selected_device)
                except Exception as exc:
                    raise RuntimeError(f"无法加载本地训练的边界模型：{exc}") from exc
                boundary_model.eval()
                _ACOUSTIC_MODEL = (
                    acoustic_model,
                    boundary_model,
                    model_type,
                    True,
                    model_device,
                )
            else:
                _ACOUSTIC_MODEL = load_mtl_model(
                    "MTL_BDR", cuda=use_cuda, verbose=False
                )
            _MODEL_DEVICE = selected_device
            _MODEL_BOUNDARY_CHECKPOINT = checkpoint_key

        if vocalize and _DEMUCS_MODEL is None:
            try:
                _DEMUCS_MODEL = load_demucs_model(
                    "mdx_extra", -1, device=selected_device, verbose=False
                )
            except Exception as exc:
                raise VocalModelUnavailableError(
                    "人声分离模型尚未下载或加载失败。请确认已阅读并接受该模型的适用条款，"
                    "检查网络后重试；也可以取消“人声分离”选项，直接使用 Auto-LRC 对齐。\n\n"
                    f"详细信息：{exc}"
                ) from exc
        return _ACOUSTIC_MODEL, _DEMUCS_MODEL, selected_device


def _script_groups(line: str) -> set[str]:
    groups: set[str] = set()
    if re.search(r"[\u3040-\u30ff]", line):
        groups.add("ja")
    if re.search(r"[\uac00-\ud7af]", line):
        groups.add("ko")
    if re.search(r"[\u0400-\u04ff]", line):
        groups.add("ru")
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", line):
        groups.add("zh")
    if re.search(r"[A-Za-z]", line):
        groups.add("en")
    # Han characters combined with kana are normal Japanese orthography.
    if "ja" in groups:
        groups.discard("zh")
    return groups


def _parse_output_times(content: str) -> list[str]:
    parsed: list[str] = []
    for line in content.splitlines():
        match = _TIME_LINE.match(line)
        if not match:
            continue
        minutes, seconds, fraction = match.groups()
        milliseconds = int(fraction.ljust(3, "0"))
        parsed.append(f"{int(minutes):02d}:{int(seconds):02d}.{milliseconds:03d}")
    return parsed


def _add_time_offset(timestamp: str, offset_ms: int) -> str:
    match = re.fullmatch(r"(\d+):(\d{2})\.(\d{3})", timestamp)
    if not match or offset_ms == 0:
        return timestamp
    minutes, seconds, milliseconds = (int(part) for part in match.groups())
    total_ms = ((minutes * 60) + seconds) * 1_000 + milliseconds + offset_ms
    total_minutes, remainder = divmod(total_ms, 60_000)
    total_seconds, total_milliseconds = divmod(remainder, 1_000)
    return f"{total_minutes:02d}:{total_seconds:02d}.{total_milliseconds:03d}"


def run_alignment(
    audio_bytes: bytes,
    suffix: str,
    lyrics: list[str],
    current_times: list[str],
    *,
    vocalize: bool = True,
    device: str = "auto",
    audio_window_ms: tuple[int, int | None] | None = None,
    boundary_checkpoint: str | Path | None = None,
) -> dict:
    """Align eligible lyric lines and map Auto-LRC timestamps back to the UI rows."""
    if not audio_bytes:
        raise ValueError("音频文件为空。")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("音频超过 200 MB，请先压缩或裁剪。")
    if not lyrics:
        raise ValueError("请输入逐行歌词。")

    existing = [
        current_times[index] if index < len(current_times) else "00:00.000"
        for index in range(len(lyrics))
    ]
    rows: list[dict] = []
    eligible_indices: list[int] = []
    eligible_lines: list[str] = []

    for index, line in enumerate(lyrics):
        if _META_LINE.match(line):
            rows.append(
                {
                    "index": index,
                    "status": "skipped",
                    "reason": "LRC 元数据未送入模型，保留原时间。",
                }
            )
            continue
        if _CREDIT_LINE.match(line):
            rows.append(
                {
                    "index": index,
                    "status": "skipped",
                    "reason": "检测到制作信息，未送入模型，保留原时间。",
                }
            )
            continue
        if not _ALIGNABLE.search(line):
            rows.append(
                {
                    "index": index,
                    "status": "skipped",
                    "reason": "空行、标点或不支持的文字，保留原时间。",
                }
            )
            continue

        groups = _script_groups(line)
        mixed = len(groups) > 1
        rows.append(
            {
                "index": index,
                "status": "review" if mixed else "aligned",
                "reason": "检测到多种文字系统，请重点核对这一行。" if mixed else "",
            }
        )
        eligible_indices.append(index)
        # Auto-LRC's English phonemizer expects lowercase ASCII input. Quote
        # marks are not spoken; some Japanese converters return an empty phone
        # for a standalone opening quote, producing an invalid empty word span.
        # Keep the original line for display/export and clean only model input.
        model_line = line.strip().lower().translate(_PHONETIC_NON_SPEECH_MARKS)
        eligible_lines.append(model_line)

    if not eligible_lines:
        raise ValueError(
            "没有找到可对齐的歌词行。Auto-LRC 支持中文、日文、韩文、英文和俄文。"
        )

    safe_suffix = suffix.lower() if re.fullmatch(r"\.[a-zA-Z0-9]{1,8}", suffix) else ".audio"
    acoustic_model, demucs_model, selected_device = _get_models(
        vocalize, device, boundary_checkpoint
    )
    time_offset_ms = 0

    try:
        from t2l.t2l import preprocess_audio, process
    except ImportError as exc:
        raise RuntimeError(
            "Auto-LRC 依赖尚未安装。请双击 InstallAutoLrc.cmd 完成一次安装。"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="auto-lrc-") as temp_dir:
        audio_path = Path(temp_dir) / f"audio{safe_suffix}"
        audio_path.write_bytes(audio_bytes)
        if audio_window_ms is not None:
            window_start_ms, window_end_ms = audio_window_ms
            if window_start_ms < 0 or (window_end_ms is not None and window_end_ms <= window_start_ms):
                raise ValueError("问题段音频范围无效，请先检查时间轴。")
            if window_start_ms > 0 or window_end_ms is not None:
                import soundfile as sf

                waveform, sample_rate = preprocess_audio(str(audio_path))
                start_sample = min(waveform.shape[1], window_start_ms * sample_rate // 1_000)
                end_sample = (
                    waveform.shape[1]
                    if window_end_ms is None
                    else min(waveform.shape[1], window_end_ms * sample_rate // 1_000)
                )
                if end_sample <= start_sample:
                    raise ValueError("问题段时间范围超出音频长度，无法重新打轴。")
                window_path = Path(temp_dir) / "audio-window.wav"
                cropped = waveform[:, start_sample:end_sample].detach().cpu().numpy().T
                sf.write(str(window_path), cropped, sample_rate, subtype="PCM_16")
                audio_path = window_path
                time_offset_ms = window_start_ms
        try:
            with _MODEL_LOCK, _upstream_working_directory():
                lrc_content = process(
                    eligible_lines,
                    str(audio_path),
                    mtl_model=acoustic_model,
                    demucs_model=demucs_model if vocalize else "mdx_extra",
                    line_only=True,
                    verbose=False,
                    vocalize=vocalize,
                    format="lrc",
                )
        except Exception as exc:
            logger.exception("Auto-LRC alignment failed")
            raise RuntimeError(
                "Auto-LRC 没有完成对齐。请核对音频与歌词语言，并查看错误详情。\n\n"
                f"详细信息：{exc}"
            ) from exc

    aligned_times = _parse_output_times(lrc_content)
    if time_offset_ms:
        aligned_times = [_add_time_offset(timestamp, time_offset_ms) for timestamp in aligned_times]
    if len(aligned_times) != len(eligible_indices):
        raise RuntimeError(
            "Auto-LRC 输出的时间行数与输入歌词不一致，已停止应用结果，避免错位。"
            f"（输入 {len(eligible_indices)} 行，输出 {len(aligned_times)} 行）"
        )

    times = list(existing)
    for index, timestamp in zip(eligible_indices, aligned_times):
        times[index] = timestamp

    return {
        "times": times,
        "rows": rows,
        "aligned_count": sum(row["status"] != "skipped" for row in rows),
        "skipped_count": sum(row["status"] == "skipped" for row in rows),
        "review_count": sum(row["status"] == "review" for row in rows),
        "review_required": True,
        "device": "GPU（CUDA）" if selected_device == "cuda" else "CPU",
    }

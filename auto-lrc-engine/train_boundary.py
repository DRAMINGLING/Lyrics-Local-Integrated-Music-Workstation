"""Fine-tune Auto-LRC's line-boundary detector from paired audio and timed LRC."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from embedded_lyrics import read_embedded_lyrics
from model_manager import configure_model_paths, verify_auto_lrc_assets


ENGINE_ROOT = Path(__file__).resolve().parent
UPSTREAM_ROOT = ENGINE_ROOT / "upstream"
if str(UPSTREAM_ROOT) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_ROOT))

TIME_TAG = re.compile(r"\[(\d{1,4}):([0-5]\d)(?:[.:](\d{1,3}))?\]")
CREDIT_LINE = re.compile(
    r"^\s*(?:作词|作詞|作曲|编曲|編曲|原曲作者|原唱|人声翻唱|人聲翻唱|歌手|演唱|翻唱|"
    r"和声|和聲|混音|母带|母帶|制作人|製作人|监制|監製|配唱|录音|錄音|词曲|詞曲|PV|"
    r"lyrics?|composer|music|arranger|artist|singer|vocal(?:ist)?|producer)\s*[:：]",
    re.IGNORECASE,
)
META_LINE = re.compile(r"^\s*\[(?:ar|al|ti|by|re|ve|la|length|offset|kana):.*\]\s*$", re.IGNORECASE)
BRACKETED_TITLE = re.compile(r"^\s*(?:【[^】]+】.+|《[^》]+》.+)\s*$")
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus"}
SAMPLE_RATE = 22_050
MEL_HOP = 256
FRAME_RESOLUTION = MEL_HOP * 3 / SAMPLE_RATE
WINDOW_FRAMES = 484
WINDOW_STEP = WINDOW_FRAMES // 2
NEGATIVE_FEEDBACK_WEIGHT = 4.0


@dataclass(frozen=True)
class Track:
    audio_path: Path
    lrc_path: Path | None
    line_starts: tuple[float, ...]
    vocalize: bool | None = None
    ignored_regions: tuple[tuple[float, float], ...] = ()
    negative_regions: tuple[tuple[float, float], ...] = ()

    @property
    def annotation_name(self) -> str:
        return self.lrc_path.name if self.lrc_path else f"{self.audio_path.name}（内嵌 LRC）"


def _parse_lrc_content(content: str) -> tuple[float, ...]:
    starts: list[float] = []
    for line in content.splitlines():
        matches = list(TIME_TAG.finditer(line))
        if not matches:
            continue
        lyric = TIME_TAG.sub("", line).strip()
        if (
            not lyric
            or META_LINE.match(lyric)
            or CREDIT_LINE.match(lyric)
            or BRACKETED_TITLE.match(lyric)
        ):
            continue
        for match in matches:
            minutes, seconds, fraction = match.groups()
            milliseconds = int((fraction or "0").ljust(3, "0"))
            starts.append(int(minutes) * 60 + int(seconds) + milliseconds / 1_000)
    return tuple(sorted(starts))


def _parse_lrc(path: Path) -> tuple[float, ...]:
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    return _parse_lrc_content(content)


def _pair_key(path: Path) -> tuple[str, str]:
    stem = path.stem.casefold()
    for suffix in (".aligned", ".manual", ".human", ".reference"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return str(path.parent.resolve()).casefold(), stem


def discover_tracks(root: Path) -> list[Track]:
    audio_by_key: dict[tuple[str, str], Path] = {}
    lrc_paths = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file():
            continue
        if path.suffix.casefold() in AUDIO_EXTENSIONS:
            audio_by_key.setdefault(_pair_key(path), path)
        elif path.suffix.casefold() == ".lrc":
            lrc_paths.append(path)

    lrc_candidates: dict[tuple[str, str], list[Path]] = {}
    for path in lrc_paths:
        lrc_candidates.setdefault(_pair_key(path), []).append(path)

    tracks: list[Track] = []
    for key, audio_path in audio_by_key.items():
        candidates = lrc_candidates.get(key, [])
        candidates.sort(
            key=lambda path: (
                0 if path.stem.casefold().endswith((".aligned", ".manual", ".human", ".reference")) else 1,
                path.name.casefold(),
            )
        )
        lrc_path: Path | None = None
        line_starts: tuple[float, ...] = ()
        for candidate in candidates:
            try:
                candidate_starts = _parse_lrc(candidate)
            except OSError as exc:
                print(f"无法读取 {candidate.name}：{exc}", flush=True)
                continue
            if len(candidate_starts) >= 5:
                lrc_path, line_starts = candidate, candidate_starts
                break
        if not line_starts:
            for content in read_embedded_lyrics(audio_path):
                candidate_starts = _parse_lrc_content(content)
                if len(candidate_starts) > len(line_starts):
                    line_starts = candidate_starts
        if len(line_starts) < 5:
            source = candidates[0].name if candidates else audio_path.name
            print(f"跳过 {source}：没有至少 5 个有效的外部或内嵌 LRC 时间。", flush=True)
            continue
        feedback_metadata_path = audio_path.with_name(f"{audio_path.stem}.feedback.json")
        feedback_vocalize = None
        ignored_regions: list[tuple[float, float]] = []
        negative_regions: list[tuple[float, float]] = []
        if feedback_metadata_path.is_file():
            try:
                feedback_metadata = json.loads(feedback_metadata_path.read_text(encoding="utf-8"))
                if isinstance(feedback_metadata, dict):
                    if isinstance(feedback_metadata.get("vocal_separation"), bool):
                        feedback_vocalize = feedback_metadata["vocal_separation"]
                    for field_name, destination in (
                        ("ignored_regions", ignored_regions),
                        ("negative_regions", negative_regions),
                    ):
                        raw_regions = feedback_metadata.get(field_name, [])
                        if not isinstance(raw_regions, list):
                            continue
                        for region in raw_regions:
                            if not isinstance(region, (list, tuple)) or len(region) != 2:
                                continue
                            try:
                                start, end = (float(value) for value in region)
                            except (TypeError, ValueError):
                                continue
                            if math.isfinite(start) and math.isfinite(end) and end > start:
                                destination.append((max(0.0, start), end))
            except (OSError, json.JSONDecodeError):
                pass
        tracks.append(
            Track(
                audio_path,
                lrc_path,
                line_starts,
                feedback_vocalize,
                tuple(ignored_regions),
                tuple(negative_regions),
            )
        )
    return sorted(tracks, key=lambda track: str(track.audio_path).casefold())


def _audio_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as audio_file:
        for block in iter(lambda: audio_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _boundary_model(torch, device):
    from t2l.mtl.model import BoundaryDetection

    model = BoundaryDetection(
        n_cnn_layers=1,
        rnn_dim=32,
        n_class=1,
        n_feats=32,
        stride=1,
        dropout=0.1,
    ).to(device)
    return model


def _window_starts(frame_count: int) -> list[int]:
    if frame_count <= WINDOW_FRAMES:
        return [0]
    starts = list(range(0, frame_count - WINDOW_FRAMES + 1, WINDOW_STEP))
    final_start = frame_count - WINDOW_FRAMES
    if starts[-1] != final_start:
        starts.append(final_start)
    return starts


def _make_target(torch, line_starts: tuple[float, ...], frame_start: int, output_frames: int):
    target = torch.zeros(output_frames, dtype=torch.float32)
    frame_ids = torch.arange(output_frames, dtype=torch.float32)
    for seconds in line_starts:
        local_frame = int(round((seconds * SAMPLE_RATE / MEL_HOP - frame_start) / 3))
        if local_frame < -5 or local_frame >= output_frames + 5:
            continue
        begin = max(0, local_frame - 5)
        end = min(output_frames, local_frame + 6)
        if begin >= end:
            continue
        gaussian = torch.exp(-0.5 * ((frame_ids[begin:end] - local_frame) / 1.5) ** 2)
        target[begin:end] = torch.maximum(target[begin:end], gaussian)
    return target


def _make_loss_weights(
    np,
    ignored_regions: tuple[tuple[float, float], ...],
    negative_regions: tuple[tuple[float, float], ...],
    frame_start: int,
    output_frames: int,
):
    """Weight confirmed bad predictions while masking unknown spans."""
    loss_weights = np.ones(output_frames, dtype=np.float32)
    for start_seconds, end_seconds in ignored_regions:
        start_frame = int(math.floor((start_seconds * SAMPLE_RATE / MEL_HOP - frame_start) / 3))
        end_frame = int(math.ceil((end_seconds * SAMPLE_RATE / MEL_HOP - frame_start) / 3))
        begin = max(0, start_frame)
        end = min(output_frames, end_frame)
        if begin < end:
            loss_weights[begin:end] = 0.0
    for start_seconds, end_seconds in negative_regions:
        start_frame = int(math.floor((start_seconds * SAMPLE_RATE / MEL_HOP - frame_start) / 3))
        end_frame = int(math.ceil((end_seconds * SAMPLE_RATE / MEL_HOP - frame_start) / 3))
        begin = max(0, start_frame)
        end = min(output_frames, end_frame)
        if begin < end:
            loss_weights[begin:end] = NEGATIVE_FEEDBACK_WEIGHT
    return loss_weights


def _batch_tensors(torch, np, cache_paths, tracks, examples, device):
    features = []
    targets = []
    loss_weights = []
    output_frames = WINDOW_FRAMES // 3
    for track_index, frame_start in examples:
        spec = np.load(cache_paths[track_index], mmap_mode="r")
        chunk = np.asarray(spec[:, frame_start : frame_start + WINDOW_FRAMES], dtype=np.float32)
        if chunk.shape[1] < WINDOW_FRAMES:
            chunk = np.pad(chunk, ((0, 0), (0, WINDOW_FRAMES - chunk.shape[1])))
        features.append(chunk)
        targets.append(
            _make_target(torch, tracks[track_index].line_starts, frame_start, output_frames).numpy()
        )
        loss_weights.append(
            _make_loss_weights(
                np,
                tracks[track_index].ignored_regions,
                tracks[track_index].negative_regions,
                frame_start,
                output_frames,
            )
        )
    feature_tensor = torch.from_numpy(np.stack(features)).unsqueeze(1).to(device)
    target_tensor = torch.from_numpy(np.stack(targets)).to(device)
    weight_tensor = torch.from_numpy(np.stack(loss_weights)).to(device)
    return feature_tensor, target_tensor, weight_tensor


def _run_epoch(torch, np, model, optimizer, cache_paths, tracks, indices, device, batch_size, training):
    model.train(training)
    examples = [
        (track_index, start)
        for track_index in indices
        for start in _window_starts(np.load(cache_paths[track_index], mmap_mode="r").shape[1])
    ]
    if training:
        random.shuffle(examples)
    total_loss = 0.0
    total_batches = 0
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for offset in range(0, len(examples), batch_size):
            batch = examples[offset : offset + batch_size]
            inputs, targets, loss_weights = _batch_tensors(torch, np, cache_paths, tracks, batch, device)
            outputs = model(inputs).squeeze(-1).clamp(1e-6, 1 - 1e-6)
            frame_count = min(targets.shape[1], loss_weights.shape[1], outputs.shape[1])
            targets = targets[:, :frame_count]
            loss_weights = loss_weights[:, :frame_count]
            outputs = outputs[:, :frame_count]
            element_loss = torch.nn.functional.binary_cross_entropy(outputs, targets, reduction="none")
            class_weights = 1.0 + targets * 39.0
            valid_weight = loss_weights.sum()
            if valid_weight.item() <= 0:
                continue
            loss = (element_loss * class_weights * loss_weights).sum() / valid_weight
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()
            total_loss += float(loss.detach().cpu())
            total_batches += 1
    if total_batches == 0:
        raise RuntimeError("没有可供训练的音频窗口。请检查音频长度和排除的时间段。")
    return total_loss / total_batches


def train(args) -> None:
    assets = verify_auto_lrc_assets()
    configure_model_paths()
    import torch
    import torchaudio

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("选择了 GPU，但当前环境没有可用的 CUDA GPU。")
    device = torch.device("cuda" if args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available()) else "cpu")

    tracks_by_fingerprint: dict[str, Track] = {}
    data_directories = [Path(directory).resolve() for directory in args.data_dir]
    for root in data_directories:
        if not root.is_dir():
            raise RuntimeError(f"训练资料文件夹不存在：{root}")
        for track in discover_tracks(root):
            # Later sources, such as newly reviewed feedback, replace an older
            # annotation for the same exact audio instead of leaking the song
            # into both the training and validation splits.
            tracks_by_fingerprint[_audio_fingerprint(track.audio_path)] = track
    tracks = sorted(tracks_by_fingerprint.values(), key=lambda track: str(track.audio_path).casefold())
    if len(tracks) < 2:
        raise RuntimeError(
            f"只找到 {len(tracks)} 组可用音频/LRC。至少需要 2 首歌，才能按歌曲留出验证集；建议准备 10 首以上。"
        )
    print(f"找到 {len(tracks)} 组有效配对；边界模型将在不同歌曲间划分训练集和验证集。", flush=True)
    for track in tracks:
        print(f"  {track.audio_path.name}  ← {track.annotation_name}（{len(track.line_starts)} 行）", flush=True)

    order = list(range(len(tracks)))
    random.shuffle(order)
    validation_count = max(1, min(len(tracks) - 1, round(len(tracks) * 0.2)))
    validation_indices = order[:validation_count]
    training_indices = order[validation_count:]

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    from t2l.mtl.model import train_audio_transforms
    from t2l.t2l import load_demucs_model, preprocess_audio, separate_vocals
    from t2l.mtl.utils import load_model

    model = _boundary_model(torch, device)
    pretrained_path = assets.checkpoint_dir / "checkpoint_BDR"
    starting_checkpoint = Path(args.initial_checkpoint).resolve() if args.initial_checkpoint else pretrained_path
    if not starting_checkpoint.is_file():
        raise RuntimeError(f"找不到起始边界模型：{starting_checkpoint}")
    load_model(model, str(starting_checkpoint), cuda=(device.type == "cuda"))
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    batch_size = args.batch_size or (8 if device.type == "cuda" else 2)

    demucs_model = None
    if any(track.vocalize if track.vocalize is not None else args.vocalize for track in tracks):
        print("加载人声分离模型…", flush=True)
        demucs_model = load_demucs_model("mdx_extra", -1, device=str(device), verbose=False)

    cache_paths: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="auto-lrc-boundary-") as cache_directory:
        cache_root = Path(cache_directory)
        transform = train_audio_transforms.cpu().eval()
        for index, track in enumerate(tracks):
            print(f"准备音频 {index + 1}/{len(tracks)}：{track.audio_path.name}", flush=True)
            track_uses_vocals = track.vocalize if track.vocalize is not None else args.vocalize
            if track_uses_vocals:
                waveform, sample_rate = separate_vocals(
                    str(track.audio_path), sample_rate=SAMPLE_RATE, demucs_model=demucs_model, verbose=False
                )
            else:
                waveform, sample_rate = preprocess_audio(str(track.audio_path), sr=SAMPLE_RATE)
            if sample_rate != SAMPLE_RATE:
                waveform = torchaudio.functional.resample(waveform, sample_rate, SAMPLE_RATE)
            waveform = waveform.mean(dim=0, keepdim=True).to(dtype=torch.float32, device="cpu")
            audio_duration = waveform.shape[-1] / SAMPLE_RATE
            valid_starts = tuple(time for time in track.line_starts if 0 <= time < audio_duration)
            if len(valid_starts) < 5:
                raise RuntimeError(
                    f"{track.annotation_name} 与音频时间不匹配：音频范围内少于 5 个有效歌词时间。"
                )
            if len(valid_starts) != len(track.line_starts):
                print(
                    f"提示：{track.annotation_name} 中有 {len(track.line_starts) - len(valid_starts)} 个时间超出音频，已忽略。",
                    flush=True,
                )
                tracks[index] = Track(
                    track.audio_path,
                    track.lrc_path,
                    valid_starts,
                    track.vocalize,
                    track.ignored_regions,
                    track.negative_regions,
                )
            with torch.inference_mode():
                spec = transform(waveform).squeeze(0).cpu().numpy().astype(np.float32, copy=False)
            if spec.shape[1] < 3:
                raise RuntimeError(f"音频太短，无法提取特征：{track.audio_path.name}")
            cache_path = cache_root / f"track-{index:05d}.npy"
            np.save(cache_path, spec)
            cache_paths.append(cache_path)
            del waveform, spec

        baseline_loss = _run_epoch(
            torch, np, model, optimizer, cache_paths, tracks, validation_indices, device, batch_size, False
        )
        print(f"起始模型验证损失：{baseline_loss:.5f}", flush=True)
        best_loss = baseline_loss
        best_checkpoint = cache_root / "best-boundary.pt"
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "state": {"epochs": 0, "best_loss": best_loss},
            },
            best_checkpoint,
        )
        patience = 0
        for epoch in range(1, args.epochs + 1):
            train_loss = _run_epoch(
                torch, np, model, optimizer, cache_paths, tracks, training_indices, device, batch_size, True
            )
            validation_loss = _run_epoch(
                torch, np, model, optimizer, cache_paths, tracks, validation_indices, device, batch_size, False
            )
            print(
                f"第 {epoch}/{args.epochs} 轮：训练损失 {train_loss:.5f}；验证损失 {validation_loss:.5f}",
                flush=True,
            )
            if validation_loss < best_loss:
                best_loss = validation_loss
                patience = 0
                torch.save(
                    {
                        "model_state_dict": model.state_dict(),
                        "optimizer_state_dict": optimizer.state_dict(),
                        "state": {"epochs": epoch, "best_loss": best_loss},
                    },
                    best_checkpoint,
                )
            else:
                patience += 1
                if patience >= 3:
                    print("验证集连续三轮未改善，提前停止。", flush=True)
                    break
        temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
        temporary_output.write_bytes(best_checkpoint.read_bytes())
        os.replace(temporary_output, output_path)
        improvement = baseline_loss - best_loss
        print(
            f"训练完成；最佳验证损失 {best_loss:.5f}（较起始模型改善 {improvement:+.5f}）。"
            f"已保存本地边界模型：{output_path}",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        action="append",
        required=True,
        help="Folder containing audio and timed LRC; may be supplied more than once",
    )
    parser.add_argument("--initial-checkpoint", help="Optional local boundary checkpoint to continue fine-tuning")
    parser.add_argument("--output", required=True, help="Output checkpoint path")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--vocalize", action="store_true", help="Separate vocals before training")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.epochs < 1 or args.epochs > 50:
        parser.error("--epochs 必须在 1 到 50 之间")
    try:
        previous_directory = Path.cwd()
        os.chdir(UPSTREAM_ROOT)
        try:
            train(args)
        finally:
            os.chdir(previous_directory)
    except Exception as exc:
        print(f"训练失败：{exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

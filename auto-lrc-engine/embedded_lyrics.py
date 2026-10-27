"""Read and write lyrics stored in common embedded audio metadata tags."""

from pathlib import Path


MAX_ID3_TAG_BYTES = 32 * 1024 * 1024


def _syncsafe_integer(value: bytes) -> int:
    if len(value) != 4 or any(byte & 0x80 for byte in value):
        raise ValueError("无效的 ID3 同步安全整数。")
    return (value[0] << 21) | (value[1] << 14) | (value[2] << 7) | value[3]


def _decode_id3_text(encoding: int, value: bytes) -> str:
    codecs = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}
    return value.decode(codecs.get(encoding, "utf-8"), errors="replace").lstrip("\ufeff")


def _split_id3_description(encoding: int, value: bytes) -> bytes | None:
    if encoding in (1, 2):
        for offset in range(0, len(value) - 1, 2):
            if value[offset : offset + 2] == b"\0\0":
                return value[offset + 2 :]
        return None
    terminator = value.find(b"\0")
    return value[terminator + 1 :] if terminator >= 0 else None


def _read_id3_lyrics_raw(path: Path) -> list[str]:
    """Read ID3 USLT frames without depending on the optional tag library."""
    try:
        with path.open("rb") as audio_file:
            header = audio_file.read(10)
            if header[:3] != b"ID3":
                return []
            version = header[3]
            flags = header[5]
            tag_size = _syncsafe_integer(header[6:10])
            if tag_size > MAX_ID3_TAG_BYTES:
                return []
            payload = audio_file.read(tag_size)
    except (OSError, ValueError):
        return []

    if len(payload) != tag_size or version not in (2, 3, 4):
        return []
    if flags & 0x80:
        payload = payload.replace(b"\xff\0", b"\xff")

    position = 0
    if flags & 0x40:
        if version == 2 or len(payload) < 4:
            return []
        extended_size = (
            _syncsafe_integer(payload[:4])
            if version == 4
            else int.from_bytes(payload[:4], "big") + 4
        )
        if extended_size > len(payload):
            return []
        position = extended_size

    contents: list[str] = []
    while position < len(payload):
        if version == 2:
            if position + 6 > len(payload):
                break
            frame_id = bytes(payload[position : position + 3]).decode("ascii", errors="ignore")
            frame_size = int.from_bytes(payload[position + 3 : position + 6], "big")
            header_size = 6
        else:
            if position + 10 > len(payload):
                break
            frame_id = bytes(payload[position : position + 4]).decode("ascii", errors="ignore")
            size_bytes = bytes(payload[position + 4 : position + 8])
            try:
                frame_size = (
                    _syncsafe_integer(size_bytes)
                    if version == 4
                    else int.from_bytes(size_bytes, "big")
                )
            except ValueError:
                break
            frame_flags = payload[position + 9]
            if (version == 3 and frame_flags & 0xC0) or (version == 4 and frame_flags & 0x0C):
                position += 10 + frame_size
                continue
            header_size = 10

        frame_start = position + header_size
        frame_end = frame_start + frame_size
        if frame_size <= 0 or frame_end > len(payload):
            break
        if frame_id in ("USLT", "ULT"):
            frame = bytes(payload[frame_start:frame_end])
            if len(frame) >= 5:
                encoding = frame[0]
                lyric_bytes = _split_id3_description(encoding, frame[4:])
                if lyric_bytes is not None:
                    contents.append(_decode_id3_text(encoding, lyric_bytes))
        position = frame_end
    return contents


def _text_values(values) -> list[str]:
    if isinstance(values, str):
        return [values] if values.strip() else []
    if not isinstance(values, (list, tuple)):
        return []
    return [value for value in values if isinstance(value, str) and value.strip()]


def _synchronized_id3_lyrics(tags) -> list[str]:
    """Convert ID3 synchronized lyric frames (millisecond timestamps) to LRC."""
    contents: list[str] = []
    for frame in tags.getall("SYLT"):
        # ID3 timestamp format 2 is milliseconds. Format 1 counts MPEG frames,
        # which cannot be converted reliably without decoding the audio stream.
        if getattr(frame, "format", None) != 2 or getattr(frame, "type", None) not in (1, 2):
            continue
        lines: list[str] = []
        for item in getattr(frame, "text", []):
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            lyric, timestamp = item
            if not isinstance(lyric, str) or not lyric.strip():
                continue
            try:
                milliseconds = int(timestamp)
            except (TypeError, ValueError, OverflowError):
                continue
            if milliseconds < 0:
                continue
            minutes, remainder = divmod(milliseconds, 60_000)
            seconds, fraction = divmod(remainder, 1_000)
            lines.append(f"[{minutes:02d}:{seconds:02d}.{fraction:03d}]{lyric.strip()}")
        if lines:
            contents.append("\n".join(lines))
    return contents


def read_embedded_lyrics(path: Path) -> list[str]:
    """Read embedded lyric text from ID3, MP4, FLAC, and Ogg tags."""
    try:
        from mutagen import File
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except ImportError:
        return _read_id3_lyrics_raw(path)

    try:
        audio = File(str(path), easy=False)
    except Exception:
        audio = None
    if audio is None or audio.tags is None:
        return _read_id3_lyrics_raw(path)

    contents: list[str] = []
    if isinstance(audio.tags, ID3):
        contents.extend(
            text
            for frame in audio.tags.getall("USLT")
            for text in _text_values(frame.text)
        )
        contents.extend(_synchronized_id3_lyrics(audio.tags))
        if not contents:
            contents.extend(
                text
                for frame in audio.tags.getall("TXXX")
                if frame.desc.casefold() in {"lyrics", "unsyncedlyrics", "unsynced_lyrics"}
                for text in _text_values(frame.text)
            )
    elif isinstance(audio, MP4):
        contents.extend(_text_values(audio.tags.get("\xa9lyr", [])))
    else:
        for key in ("LYRICS", "UNSYNCEDLYRICS", "UNSYNCED_LYRICS"):
            contents.extend(_text_values(audio.tags.get(key, [])))
    return contents or _read_id3_lyrics_raw(path)


def write_embedded_lyrics(path: Path, content: str | list[str]) -> None:
    """Replace embedded lyric tags while preserving other audio metadata."""
    try:
        from mutagen import File
        from mutagen.id3 import Encoding, ID3, USLT
        from mutagen.mp4 import MP4
    except ImportError as exc:
        raise RuntimeError(
            "缺少内嵌歌词写入组件。请运行 InstallAutoLrc.cmd 更新本机依赖后重试。"
        ) from exc

    audio = File(str(path), easy=False)
    if audio is None:
        raise ValueError(f"此音频格式暂不支持写入内嵌歌词：{path.suffix or path.name}")
    contents = [content] if isinstance(content, str) else list(content)
    if any(not isinstance(item, str) for item in contents):
        raise ValueError("内嵌歌词内容必须是文本或文本列表。")

    suffix = path.suffix.casefold()
    if isinstance(audio, MP4):
        if audio.tags is None:
            if not contents:
                return
            audio.add_tags()
        if contents:
            audio.tags["\xa9lyr"] = contents
        else:
            audio.tags.pop("\xa9lyr", None)
    elif isinstance(audio.tags, ID3) or suffix in {".mp3", ".wav"}:
        if audio.tags is None:
            if not contents:
                return
            audio.add_tags()
        if not isinstance(audio.tags, ID3):
            raise ValueError(f"此音频文件无法保存 ID3 歌词标签：{path.suffix}")
        audio.tags.delall("USLT")
        audio.tags.delall("SYLT")
        for frame in list(audio.tags.getall("TXXX")):
            if frame.desc.casefold() in {"lyrics", "unsyncedlyrics", "unsynced_lyrics"}:
                audio.tags.delall(frame.HashKey)
        for index, lyric_content in enumerate(contents):
            audio.tags.add(
                USLT(
                    encoding=Encoding.UTF8,
                    lang="zho",
                    desc=(
                        "Auto-LRC draft"
                        if len(contents) == 1
                        else f"Auto-LRC formatted {index + 1}"
                    ),
                    text=lyric_content,
                )
            )
    elif suffix in {".flac", ".ogg", ".opus"}:
        if audio.tags is None:
            if not contents:
                return
            audio.add_tags()
        for key in list(audio.tags.keys()):
            if str(key).casefold() in {"lyrics", "unsyncedlyrics", "unsynced_lyrics"}:
                del audio.tags[key]
        if contents:
            audio.tags["LYRICS"] = contents
    else:
        raise ValueError(f"此音频格式暂不支持写入内嵌歌词：{path.suffix or path.name}")

    audio.save()

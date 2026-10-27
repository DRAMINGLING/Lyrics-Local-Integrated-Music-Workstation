"""Read and write common audio tags while preserving unrelated metadata."""

import base64
import io
from pathlib import Path


FIELD_KEYS = {
    "title": ("TIT2", "\xa9nam", "TITLE"),
    "artist": ("TPE1", "\xa9ART", "ARTIST"),
    "album": ("TALB", "\xa9alb", "ALBUM"),
    "album_artist": ("TPE2", "aART", "ALBUMARTIST"),
    "year": ("TDRC", "\xa9day", "DATE"),
    "genre": ("TCON", "\xa9gen", "GENRE"),
    "track_number": ("TRCK", "trkn", "TRACKNUMBER"),
    "composer": ("TCOM", "\xa9wrt", "COMPOSER"),
    "comment": ("COMM", "\xa9cmt", "COMMENT"),
}
MAX_COVER_BYTES = 20 * 1024 * 1024
MAX_COVER_PIXELS = 40_000_000


def _first_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, tuple):
            if not first:
                return ""
            track = first[0]
            total = first[1] if len(first) > 1 else 0
            return f"{track}/{total}" if total else str(track)
        return str(first)
    return ""


def _id3_text(tags, frame_id: str) -> str:
    frames = tags.getall(frame_id)
    if not frames:
        return ""
    frame = frames[0]
    if frame_id == "COMM":
        frame = next((item for item in frames if not item.desc), frame)
    return _first_text(frame.text)


def _delete_tag(tags, key: str) -> None:
    if key in tags:
        del tags[key]


def _picture_from_vorbis(tags) -> bytes | None:
    from mutagen.flac import Picture

    for encoded in tags.get("METADATA_BLOCK_PICTURE", []):
        try:
            picture = Picture(base64.b64decode(encoded))
        except (TypeError, ValueError):
            continue
        if picture.data:
            return picture.data
    for encoded in tags.get("COVERART", []):
        try:
            data = base64.b64decode(encoded)
        except (TypeError, ValueError):
            continue
        if data:
            return data
    return None


def read_audio_metadata(path: Path) -> dict:
    try:
        from mutagen import File
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except ImportError as exc:
        raise RuntimeError(
            "缺少音频标签编辑组件。请运行 InstallAutoLrc.cmd 更新本机依赖后重试。"
        ) from exc

    audio = File(str(path), easy=False)
    if audio is None:
        raise ValueError(f"无法识别音频格式：{path.suffix or path.name}")
    tags = audio.tags
    fields = {name: "" for name in FIELD_KEYS}
    cover_data = None
    if tags is None:
        pictures = getattr(audio, "pictures", [])
        if pictures:
            cover = next((picture for picture in pictures if picture.type == 3), pictures[0])
            cover_data = cover.data
        return {"fields": fields, "cover_data": cover_data}

    if isinstance(tags, ID3):
        fields.update(
            title=_id3_text(tags, "TIT2"),
            artist=_id3_text(tags, "TPE1"),
            album=_id3_text(tags, "TALB"),
            album_artist=_id3_text(tags, "TPE2"),
            year=_id3_text(tags, "TDRC") or _id3_text(tags, "TYER"),
            genre=_id3_text(tags, "TCON"),
            track_number=_id3_text(tags, "TRCK"),
            composer=_id3_text(tags, "TCOM"),
            comment=_id3_text(tags, "COMM"),
        )
        pictures = tags.getall("APIC")
        if pictures:
            cover = next((picture for picture in pictures if picture.type == 3), pictures[0])
            cover_data = cover.data
    elif isinstance(audio, MP4):
        fields.update(
            title=_first_text(tags.get("\xa9nam", [])),
            artist=_first_text(tags.get("\xa9ART", [])),
            album=_first_text(tags.get("\xa9alb", [])),
            album_artist=_first_text(tags.get("aART", [])),
            year=_first_text(tags.get("\xa9day", [])),
            genre=_first_text(tags.get("\xa9gen", [])),
            track_number=_first_text(tags.get("trkn", [])),
            composer=_first_text(tags.get("\xa9wrt", [])),
            comment=_first_text(tags.get("\xa9cmt", [])),
        )
        pictures = tags.get("covr", [])
        if pictures:
            cover_data = bytes(pictures[0])
    else:
        for name, (_id3_key, _mp4_key, vorbis_key) in FIELD_KEYS.items():
            values = tags.get(vorbis_key, [])
            if not values and name == "year":
                values = tags.get("YEAR", [])
            fields[name] = _first_text(values)
        pictures = getattr(audio, "pictures", [])
        if pictures:
            cover = next((picture for picture in pictures if picture.type == 3), pictures[0])
            cover_data = cover.data
        if cover_data is None:
            cover_data = _picture_from_vorbis(tags)

    return {"fields": fields, "cover_data": cover_data}


def validate_cover_data(data: bytes) -> tuple[str, int, int, int]:
    if len(data) > MAX_COVER_BYTES:
        raise ValueError("封面图片不能超过 20 MB。")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "缺少图片处理组件。请运行 InstallAutoLrc.cmd 更新本机依赖后重试。"
        ) from exc

    with Image.open(io.BytesIO(data)) as image:
        image_format = image.format
        width, height = image.size
        if width * height > MAX_COVER_PIXELS:
            raise ValueError("封面图片尺寸不能超过 4000 万像素。")
        depth = 32 if "A" in image.getbands() else 24
        image.verify()
    mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image_format)
    if mime is None:
        raise ValueError("封面图片仅支持 JPEG 或 PNG 格式。")
    return mime, width, height, depth


def _write_id3(audio, fields: dict[str, str], cover_data: bytes | None, remove_cover: bool) -> None:
    from mutagen.id3 import (
        APIC,
        COMM,
        Encoding,
        ID3,
        TALB,
        TCOM,
        TCON,
        TIT2,
        TDRC,
        TRCK,
        TPE1,
        TPE2,
    )

    if audio.tags is None:
        audio.add_tags()
    if not isinstance(audio.tags, ID3):
        raise ValueError("此文件不支持 ID3 标签。")
    tags = audio.tags
    for field, frame_id, frame_type in (
        ("title", "TIT2", TIT2),
        ("artist", "TPE1", TPE1),
        ("album", "TALB", TALB),
        ("album_artist", "TPE2", TPE2),
        ("year", "TDRC", TDRC),
        ("genre", "TCON", TCON),
        ("track_number", "TRCK", TRCK),
        ("composer", "TCOM", TCOM),
    ):
        tags.delall(frame_id)
        value = fields.get(field, "").strip()
        if value:
            tags.add(frame_type(encoding=Encoding.UTF8, text=[value]))
    tags.delall("TYER")
    tags.delall("COMM")
    comment = fields.get("comment", "").strip()
    if comment:
        tags.add(COMM(encoding=Encoding.UTF8, lang="zho", desc="", text=[comment]))

    if cover_data is not None or remove_cover:
        preserved_pictures = [picture for picture in tags.getall("APIC") if picture.type != 3]
        tags.delall("APIC")
        for picture in preserved_pictures:
            tags.add(picture)
        if cover_data is not None:
            mime, _width, _height, _depth = validate_cover_data(cover_data)
            tags.add(
                APIC(
                    encoding=Encoding.UTF8,
                    mime=mime,
                    type=3,
                    desc="Cover",
                    data=cover_data,
                )
            )


def _write_mp4(audio, fields: dict[str, str], cover_data: bytes | None, remove_cover: bool) -> None:
    from mutagen.mp4 import MP4Cover

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for field, key in (
        ("title", "\xa9nam"),
        ("artist", "\xa9ART"),
        ("album", "\xa9alb"),
        ("album_artist", "aART"),
        ("year", "\xa9day"),
        ("genre", "\xa9gen"),
        ("composer", "\xa9wrt"),
        ("comment", "\xa9cmt"),
    ):
        value = fields.get(field, "").strip()
        if value:
            tags[key] = [value]
        else:
            _delete_tag(tags, key)

    track_number = fields.get("track_number", "").strip()
    if track_number:
        try:
            pieces = track_number.split("/", 1)
            tags["trkn"] = [(int(pieces[0]), int(pieces[1]) if len(pieces) > 1 else 0)]
        except ValueError as exc:
            raise ValueError("音轨编号请使用数字或“当前/总数”格式，例如 2/12。") from exc
    else:
        _delete_tag(tags, "trkn")

    if cover_data is not None or remove_cover:
        if cover_data is None:
            _delete_tag(tags, "covr")
        else:
            mime, _width, _height, _depth = validate_cover_data(cover_data)
            image_format = (
                MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
            )
            tags["covr"] = [MP4Cover(cover_data, imageformat=image_format)]


def _write_vorbis(audio, fields: dict[str, str], cover_data: bytes | None, remove_cover: bool) -> None:
    from mutagen.flac import FLAC, Picture

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for field, (_id3_key, _mp4_key, key) in FIELD_KEYS.items():
        value = fields.get(field, "").strip()
        if value:
            tags[key] = [value]
        else:
            _delete_tag(tags, key)
    if not fields.get("year", "").strip():
        _delete_tag(tags, "YEAR")

    if cover_data is not None or remove_cover:
        for key in list(tags.keys()):
            if str(key).casefold() in {
                "metadata_block_picture",
                "coverart",
                "coverartmime",
            }:
                del tags[key]
        if isinstance(audio, FLAC):
            old_pictures = list(audio.pictures)
            audio.clear_pictures()
            for picture in old_pictures:
                if picture.type != 3:
                    audio.add_picture(picture)
        if cover_data is not None:
            mime, width, height, depth = validate_cover_data(cover_data)
            picture = Picture()
            picture.type = 3
            picture.mime = mime
            picture.desc = "Cover"
            picture.width = width
            picture.height = height
            picture.depth = depth
            picture.data = cover_data
            if isinstance(audio, FLAC):
                audio.add_picture(picture)
            else:
                tags["METADATA_BLOCK_PICTURE"] = [
                    base64.b64encode(picture.write()).decode("ascii")
                ]


def write_audio_metadata(
    path: Path,
    fields: dict[str, str],
    cover_data: bytes | None = None,
    remove_cover: bool = False,
) -> None:
    try:
        from mutagen import File
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except ImportError as exc:
        raise RuntimeError(
            "缺少音频标签编辑组件。请运行 InstallAutoLrc.cmd 更新本机依赖后重试。"
        ) from exc

    audio = File(str(path), easy=False)
    if audio is None:
        raise ValueError(f"无法识别音频格式：{path.suffix or path.name}")
    suffix = path.suffix.casefold()
    if isinstance(audio, MP4):
        _write_mp4(audio, fields, cover_data, remove_cover)
    elif isinstance(audio.tags, ID3) or suffix in {".mp3", ".wav"}:
        _write_id3(audio, fields, cover_data, remove_cover)
    elif suffix in {".flac", ".ogg", ".opus"}:
        _write_vorbis(audio, fields, cover_data, remove_cover)
    else:
        raise ValueError(f"此音频格式暂不支持编辑标签：{path.suffix or path.name}")
    audio.save()

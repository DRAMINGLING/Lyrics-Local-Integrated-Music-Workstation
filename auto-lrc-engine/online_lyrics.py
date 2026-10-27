"""Search and fetch lyric candidates from public music metadata APIs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote

import requests
from mutagen import File
from Crypto.Cipher import AES


LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
LRCLIB_GET_URL = "https://lrclib.net/api/get"
NETEASE_FORWARD_URL = "https://music.163.com/api/linux/forward"
NETEASE_SEARCH_TARGET = "https://music.163.com/api/cloudsearch/pc"
NETEASE_LYRIC_TARGET = "https://music.163.com/api/song/lyric"
QQ_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
QQ_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
KUGOU_SEARCH_URL = "https://complexsearch.kugou.com/v2/search/song"
KUGOU_LYRIC_SEARCH_URL = "https://lyrics.kugou.com/search"
KUGOU_LYRIC_DOWNLOAD_URL = "https://lyrics.kugou.com/download"
REQUEST_TIMEOUT = (4, 8)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RESULTS_PER_SOURCE = 5
USER_AGENT = "Auto-LRC/1.0 (https://github.com/chenmijiang/cplay)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
KUGOU_SIGNATURE_SECRET = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
NETEASE_LINUX_KEY = b"rFgB&h#%2?^eDg:Q"
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def _first_tag(tags, key: str) -> str:
    value = tags.get(key, "") if tags else ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def read_track_metadata(path: Path) -> dict:
    """Read common search fields and duration without loading audio samples."""
    result = {
        "title": path.stem,
        "artist": "",
        "album": "",
        "composer": "",
        "duration": 0.0,
    }
    try:
        audio = File(str(path), easy=True)
    except Exception:
        return result
    if audio is None:
        return result

    tags = audio.tags
    result["title"] = _first_tag(tags, "title") or result["title"]
    result["artist"] = _first_tag(tags, "artist")
    result["album"] = _first_tag(tags, "album")
    result["composer"] = _first_tag(tags, "composer")
    try:
        result["duration"] = max(0.0, float(audio.info.length))
    except (AttributeError, TypeError, ValueError):
        pass
    return result


def _request_json(
    url: str,
    *,
    source: str,
    params: dict | list[tuple[str, str]] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    try:
        request_headers = headers or {"Accept": "application/json", "User-Agent": USER_AGENT}
        if data is None:
            response = requests.get(
                url,
                params=params,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            )
        else:
            response = requests.post(
                url,
                data=data,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            )
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"连接 {source} 超时，请检查网络后重试。") from exc
    except requests.exceptions.SSLError as exc:
        raise RuntimeError(f"与 {source} 建立 HTTPS 连接失败，请检查系统证书或网络代理。") from exc
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"无法连接 {source}，请检查网络连接或代理设置。") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"{source} 请求失败：{exc}") from exc

    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = None
        message = error_payload.get("message") if isinstance(error_payload, dict) else None
        suffix = f"：{message}" if message else ""
        raise RuntimeError(f"{source}请求失败（HTTP {response.status_code}）{suffix}")
    response.raise_for_status()
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"{source}返回的数据过大，已停止读取。")
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{source}返回了无法识别的数据。") from exc


def _request_search(params: dict[str, str]) -> list[dict]:
    """Query LRCLIB while keeping a small delay between this app's requests."""
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        delay = 0.3 - (time.monotonic() - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        try:
            payload = _request_json(
                LRCLIB_SEARCH_URL,
                params=params,
                source="LRCLIB",
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        finally:
            _LAST_REQUEST_AT = time.monotonic()

    if not isinstance(payload, list):
        raise RuntimeError("LRCLIB 返回了意外的数据格式。")
    return [
        item
        for item in payload
        if isinstance(item, dict)
        and (item.get("syncedLyrics") or item.get("plainLyrics"))
    ]


def _search_lrclib(title: str, artist: str, album: str) -> list[dict]:
    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if album:
        params["album_name"] = album
    results = _request_search(params)
    if not results and artist:
        results = _request_search({"q": f"{title} {artist}"})
    return [
        {
            **item,
            "_source": "lrclib",
            "_source_name": "LRCLIB",
            "_candidate_id": str(item.get("id", "")),
            "_lyrics_loaded": True,
        }
        for item in results
    ]


def _search_lrclib_keyword(keyword: str) -> list[dict]:
    """Search LRCLIB using only the user's temporary free-text keyword."""
    results = _request_search({"q": keyword})
    return [
        {
            **item,
            "_source": "lrclib",
            "_source_name": "LRCLIB",
            "_candidate_id": str(item.get("id", "")),
            "_lyrics_loaded": True,
        }
        for item in results
    ]


def _linuxapi_encrypt(payload: dict) -> str:
    plaintext = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    padding_size = AES.block_size - len(plaintext) % AES.block_size
    plaintext += bytes([padding_size]) * padding_size
    return AES.new(NETEASE_LINUX_KEY, AES.MODE_ECB).encrypt(plaintext).hex().upper()


def _netease_payload(target: str, params: dict) -> str:
    return _linuxapi_encrypt({"method": "POST", "url": target, "params": params})


def _search_netease(title: str, artist: str, album: str) -> list[dict]:
    query = " ".join(part for part in (title, artist, album) if part)
    eparams = _netease_payload(
        NETEASE_SEARCH_TARGET,
        {"s": query, "type": 1, "limit": 10, "offset": 0},
    )
    payload = _request_json(
        NETEASE_FORWARD_URL,
        source="网易云",
        data={"eparams": eparams},
        headers={
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://music.163.com",
            "Cookie": "os=pc; appver=2.9.7",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("网易云返回了意外的数据格式。")
    try:
        error_code = int(payload.get("code", 200))
    except (TypeError, ValueError):
        error_code = 200
    if error_code != 200:
        raise RuntimeError(f"网易云搜索被拒（code={error_code}）。")

    songs = payload.get("result", {}).get("songs", [])
    if not isinstance(songs, list):
        raise RuntimeError("网易云返回了意外的候选列表。")
    results = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        candidate_id = _as_text(song.get("id"))
        track_name = _as_text(song.get("name"))
        if not candidate_id or not track_name:
            continue
        artists = song.get("ar")
        artist_name = ", ".join(
            _as_text(item.get("name"))
            for item in artists
            if isinstance(item, dict) and _as_text(item.get("name"))
        ) if isinstance(artists, list) else ""
        album_info = song.get("al") if isinstance(song.get("al"), dict) else {}
        results.append(
            {
                "trackName": track_name,
                "artistName": artist_name,
                "albumName": _as_text(album_info.get("name")),
                "duration": _as_duration(song.get("dt")) / 1000,
                "syncedLyrics": "",
                "plainLyrics": "",
                "_source": "netease",
                "_source_name": "网易云",
                "_candidate_id": candidate_id,
                "_lyrics_loaded": False,
            }
        )
    return results


def _as_text(value) -> str:
    return str(value).strip() if value is not None else ""


def _as_duration(*values) -> float:
    for value in values:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return 0.0


def _search_qqmusic(title: str, artist: str, album: str) -> list[dict]:
    query = " ".join(part for part in (title, artist, album) if part)
    payload = _request_json(
        QQ_SEARCH_URL,
        source="QQ 音乐",
        params={"p": "1", "n": "10", "w": query, "format": "json"},
        headers={
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://y.qq.com/portal/profile.html",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QQ 音乐返回了意外的数据格式。")
    try:
        error_code = int(payload.get("code", 0))
    except (TypeError, ValueError):
        error_code = 0
    if error_code != 0:
        raise RuntimeError(f"QQ 音乐搜索被拒（code={error_code}）。")

    songs = payload.get("data", {}).get("song", {}).get("list", [])
    if not isinstance(songs, list):
        raise RuntimeError("QQ 音乐返回了意外的候选列表。")
    results = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        candidate_id = _as_text(song.get("songmid") or song.get("songid"))
        track_name = _as_text(song.get("songname"))
        if not candidate_id or not track_name:
            continue
        singers = song.get("singer")
        artist_name = ", ".join(
            _as_text(item.get("name"))
            for item in singers
            if isinstance(item, dict) and _as_text(item.get("name"))
        ) if isinstance(singers, list) else ""
        results.append(
            {
                "trackName": track_name,
                "artistName": artist_name,
                "albumName": _as_text(song.get("albumname")),
                "duration": _as_duration(song.get("interval"), song.get("songtime")),
                "syncedLyrics": "",
                "plainLyrics": "",
                "_source": "qqmusic",
                "_source_name": "QQ 音乐",
                "_candidate_id": candidate_id,
                "_lyrics_loaded": False,
            }
        )
    return results


def _kugou_search_params(keyword: str) -> list[tuple[str, str]]:
    client_time = str(int(time.time() * 1000))
    device_id = hashlib.md5(os.urandom(16)).hexdigest()
    # Keep the parameter order stable: the endpoint signs this exact key/value sequence.
    params = [
        ("appid", "1014"),
        ("bitrate", "0"),
        ("clienttime", client_time),
        ("clientver", "1000"),
        ("dfid", "-"),
        ("filter", "10"),
        ("inputtype", "0"),
        ("iscorrection", "1"),
        ("isfuzzy", "0"),
        ("keyword", keyword),
        ("mid", device_id),
        ("page", "1"),
        ("pagesize", "10"),
        ("platform", "WebFilter"),
        ("privilege_filter", "0"),
        ("srcappid", "2919"),
        ("token", ""),
        ("userid", "0"),
        ("uuid", device_id),
    ]


def _fetch_netease(candidate_id: str) -> str:
    payload = _request_lyric_json(
        NETEASE_FORWARD_URL,
        source="网易云",
        data={
            "eparams": _netease_payload(
                NETEASE_LYRIC_TARGET,
                {"id": candidate_id, "lv": -1, "kv": -1, "tv": -1},
            )
        },
        headers={
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://music.163.com",
            "Cookie": "os=pc; appver=2.9.7",
        },
    )
    if not isinstance(payload, dict):
        return ""
    lyric = payload.get("lrc", {}).get("lyric", "")
    return _as_text(lyric)
    signed = KUGOU_SIGNATURE_SECRET + "".join(f"{key}={value}" for key, value in params)
    signature = hashlib.md5(signed.encode("utf-8")).hexdigest()
    return [*params, ("signature", signature)]


def _search_kugou(title: str, artist: str, album: str) -> list[dict]:
    query = " ".join(part for part in (title, artist, album) if part)
    payload = _request_json(
        KUGOU_SEARCH_URL,
        source="酷狗",
        params=_kugou_search_params(query),
        headers={
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://www.kugou.com/",
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("酷狗返回了意外的数据格式。")
    try:
        error_code = int(payload.get("error_code", 0))
    except (TypeError, ValueError):
        error_code = 0
    if error_code != 0:
        raise RuntimeError(f"酷狗搜索被拒（code={error_code}）。")

    songs = payload.get("data", {}).get("lists", [])
    if not isinstance(songs, list):
        raise RuntimeError("酷狗返回了意外的候选列表。")
    results = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        candidate_id = _as_text(song.get("FileHash"))
        track_name = _as_text(song.get("SongName"))
        if not candidate_id or not track_name:
            continue
        results.append(
            {
                "trackName": track_name,
                "artistName": _as_text(song.get("SingerName")),
                "albumName": _as_text(song.get("AlbumName")),
                "duration": _as_duration(song.get("Duration")),
                "syncedLyrics": "",
                "plainLyrics": "",
                "_source": "kugou",
                "_source_name": "酷狗",
                "_candidate_id": candidate_id,
                "_lyrics_loaded": False,
            }
        )
    return results


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(character for character in normalized if character.isalnum())


def _text_similarity(left: str, right: str) -> float | None:
    left_normalized = _normalized_text(left)
    right_normalized = _normalized_text(right)
    if not left_normalized or not right_normalized:
        return None
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def rank_lyrics_results(
    results: list[dict],
    *,
    title: str,
    artist: str = "",
    album: str = "",
    duration: float = 0.0,
    keyword: str = "",
) -> list[dict]:
    """Sort candidates by metadata or an explicit keyword; keep selection explicit."""
    ranked = []
    for result in results:
        if keyword:
            candidate_fields = [
                str(result.get(field, "") or "")
                for field in ("trackName", "artistName", "albumName")
            ]
            candidate_fields.append(" ".join(candidate_fields))
            score = max(
                (_text_similarity(keyword, value) or 0.0 for value in candidate_fields),
                default=0.0,
            )
        else:
            dimensions: list[tuple[float, float]] = []
            for weight, expected, actual in (
                (0.52, title, result.get("trackName", "")),
                (0.28, artist, result.get("artistName", "")),
                (0.12, album, result.get("albumName", "")),
            ):
                similarity = _text_similarity(expected, actual)
                if similarity is not None:
                    dimensions.append((weight, similarity))

            candidate_duration = _as_duration(result.get("duration"))
            if duration > 0 and candidate_duration > 0:
                difference = abs(duration - candidate_duration)
                duration_score = max(0.0, 1.0 - difference / 20.0)
                dimensions.append((0.08, duration_score))

            total_weight = sum(weight for weight, _score in dimensions)
            score = (
                sum(weight * value for weight, value in dimensions) / total_weight
                if total_weight
                else 0.0
            )
        candidate = dict(result)
        candidate["_match_score"] = score
        ranked.append(candidate)

    ranked.sort(
        key=lambda item: (
            item["_match_score"],
            bool(item.get("syncedLyrics")),
        ),
        reverse=True,
    )
    return ranked


def search_lyrics(
    *,
    title: str,
    artist: str = "",
    album: str = "",
    duration: float = 0.0,
    keyword: str = "",
) -> list[dict]:
    """Search NetEase, QQ Music, KuGou and LRCLIB concurrently; skip failed sources."""
    title = title.strip()
    artist = artist.strip()
    album = album.strip()
    keyword = keyword.strip()
    if keyword:
        query_title, query_artist, query_album = keyword, "", ""
        lrclib_searcher = _search_lrclib_keyword
        lrclib_args = (keyword,)
    else:
        if not title:
            raise ValueError("请填写歌曲名称或临时搜索关键字。")
        query_title, query_artist, query_album = title, artist, album
        lrclib_searcher = _search_lrclib
        lrclib_args = (query_title, query_artist, query_album)

    providers = (
        ("网易云", _search_netease, (query_title, query_artist, query_album)),
        ("QQ 音乐", _search_qqmusic, (query_title, query_artist, query_album)),
        ("酷狗", _search_kugou, (query_title, query_artist, query_album)),
        ("LRCLIB", lrclib_searcher, lrclib_args),
    )
    results_by_source: dict[str, list[dict]] = {}
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="lyrics-search") as pool:
        futures = {
            source: pool.submit(searcher, *args)
            for source, searcher, args in providers
        }
        # Read in provider order for deterministic tie-breaking after ranking.
        for source, _searcher, _args in providers:
            try:
                results_by_source[source] = futures[source].result()
            except Exception as exc:
                failures.append((source, str(exc)))

    if not results_by_source:
        details = "；".join(f"{source}：{error}" for source, error in failures)
        raise RuntimeError(f"所有歌词来源均不可用。{details}")

    candidates = [
        result
        for source, _searcher, _args in providers
        for result in results_by_source.get(source, [])
    ]
    ranked = rank_lyrics_results(
        candidates,
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        keyword=keyword,
    )

    # Keep a small, useful set from each provider. Repeated songs from different
    # providers stay separate because their lyric text can differ.
    kept: list[dict] = []
    per_source_count: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for candidate in ranked:
        source = candidate.get("_source", "lrclib")
        candidate_id = candidate.get("_candidate_id", "")
        identity = (source, candidate_id or candidate.get("trackName", ""))
        if identity in seen:
            continue
        seen.add(identity)
        count = per_source_count.get(source, 0)
        if count >= MAX_RESULTS_PER_SOURCE:
            continue
        per_source_count[source] = count + 1
        kept.append(candidate)
    return kept


def _request_lyric_json(
    url: str,
    *,
    source: str,
    params=None,
    data=None,
    headers=None,
) -> object:
    return _request_json(url, source=source, params=params, data=data, headers=headers)


def _fetch_lrclib(candidate_id: str) -> str:
    payload = _request_lyric_json(
        f"{LRCLIB_GET_URL}/{quote(candidate_id, safe='')}",
        source="LRCLIB",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    if not isinstance(payload, dict):
        return ""
    return _as_text(payload.get("syncedLyrics") or payload.get("plainLyrics"))


def _fetch_qqmusic(candidate_id: str) -> str:
    payload = _request_lyric_json(
        QQ_LYRIC_URL,
        source="QQ 音乐",
        params={
            "g_tk": "5381",
            "format": "json",
            "platform": "yqq",
            "needNewCode": "0",
            "inCharset": "utf8",
            "outCharset": "utf-8",
            "notice": "0",
            "loginUin": "0",
            "hostUin": "0",
            "songmid": candidate_id,
        },
        headers={
            "Accept": "application/json",
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://y.qq.com/portal/profile.html",
        },
    )
    if not isinstance(payload, dict):
        return ""
    encoded = _as_text(payload.get("lyric"))
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("QQ 音乐返回的歌词编码无法识别。")


def _fetch_kugou(candidate_id: str) -> str:
    headers = {
        "Accept": "application/json",
        "User-Agent": BROWSER_USER_AGENT,
        "Referer": "https://www.kugou.com/",
    }
    search_payload = _request_lyric_json(
        KUGOU_LYRIC_SEARCH_URL,
        source="酷狗",
        params={
            "ver": "1",
            "man": "yes",
            "client": "pc",
            "keyword": "",
            "hash": candidate_id,
        },
        headers=headers,
    )
    candidates = search_payload.get("candidates", []) if isinstance(search_payload, dict) else []
    if not candidates or not isinstance(candidates[0], dict):
        return ""
    lyric_id = _as_text(candidates[0].get("id"))
    access_key = _as_text(candidates[0].get("accesskey"))
    if not lyric_id or not access_key:
        return ""

    download_payload = _request_lyric_json(
        KUGOU_LYRIC_DOWNLOAD_URL,
        source="酷狗",
        params={
            "ver": "1",
            "client": "pc",
            "fmt": "lrc",
            "charset": "utf8",
            "id": lyric_id,
            "accesskey": access_key,
        },
        headers=headers,
    )
    encoded = _as_text(download_payload.get("content")) if isinstance(download_payload, dict) else ""
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("酷狗返回的歌词编码无法识别。")


def fetch_lyrics(source: str, candidate_id: str) -> str:
    """Fetch lyric text for a selected candidate from its originating provider."""
    source = (source or "lrclib").casefold()
    candidate_id = _as_text(candidate_id)
    if not candidate_id:
        return ""
    if source == "lrclib":
        return _fetch_lrclib(candidate_id)
    if source == "netease":
        return _fetch_netease(candidate_id)
    if source == "qqmusic":
        return _fetch_qqmusic(candidate_id)
    if source == "kugou":
        return _fetch_kugou(candidate_id)
    raise ValueError("不支持的歌词来源。")

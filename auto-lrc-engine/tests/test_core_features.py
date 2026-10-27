from __future__ import annotations

import io
import runpy
import sys
import tempfile
from types import SimpleNamespace
import wave
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
sys.path.insert(0, str(ENGINE_ROOT))

import adapter  # noqa: E402
import audio_metadata  # noqa: E402
import embedded_lyrics  # noqa: E402
import online_lyrics  # noqa: E402


def _load_app_helpers():
    original_executable = sys.executable
    sys.executable = str(ENGINE_ROOT / ".venv" / "Scripts" / "pythonw.exe")
    try:
        return runpy.run_path(str(REPO_ROOT / "AutoLrcAligner.pyw"), run_name="_auto_lrc_test")
    finally:
        sys.executable = original_executable


APP = _load_app_helpers()


def _syncsafe(value: int) -> bytes:
    return bytes(
        ((value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F)
    )


def _write_id3v23_uslt(path: Path, text: str) -> None:
    frame_data = b"\x03zho\x00" + text.encode("utf-8")
    frame = b"USLT" + len(frame_data).to_bytes(4, "big") + b"\x00\x00" + frame_data
    path.write_bytes(b"ID3\x03\x00\x00" + _syncsafe(len(frame)) + frame)


class LyricsParsingTests(TestCase):
    def test_app_parser_splits_headers_and_expands_multiple_timestamps(self):
        content = "[ti:Title]\n[ar:Artist]\n\n[00:01.2]Hello\n[00:02.03][00:03]World\n"
        lyrics, times, metadata, has_timeline = APP["parse_lyrics_content"](content)

        self.assertEqual(lyrics, ["Hello", "World", "World"])
        self.assertEqual(times, ["00:01.200", "00:02.030", "00:03.000"])
        self.assertEqual(metadata, ["[ti:Title]", "[ar:Artist]", ""])
        self.assertTrue(has_timeline)

    def test_invalid_seconds_are_not_treated_as_lrc_timestamps(self):
        lyrics, times, _metadata, has_timeline = APP["parse_lyrics_content"](
            "[00:61.00]Invalid timestamp"
        )

        self.assertEqual(lyrics, ["[00:61.00]Invalid timestamp"])
        self.assertEqual(times, ["00:00.000"])
        self.assertFalse(has_timeline)

    def test_reference_parser_skips_metadata_and_keeps_duplicate_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.lrc"
            path.write_text("[ar:Artist]\n[00:01.00]作词：Someone\n[00:02.50]Hello\n", encoding="utf-8")

            self.assertEqual(APP["parse_timed_lrc_reference"](path), [("Hello", "00:02.500")])

    def test_lyrics_files_are_read_with_a_fixed_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.lrc"
            path.write_bytes(b"x" * (APP["MAX_LYRICS_FILE_BYTES"] + 1))

            with self.assertRaisesRegex(ValueError, "10 MB"):
                APP["parse_lyrics_file"](path)
            with self.assertRaisesRegex(ValueError, "10 MB"):
                APP["parse_timed_lrc_reference"](path)


class EditedCopyTests(TestCase):
    def test_audio_copy_replaces_output_after_edit_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            output = Path(directory) / "output.wav"
            source.write_bytes(b"original")
            output.write_bytes(b"previous")

            def append_edit(path):
                path.write_bytes(path.read_bytes() + b"-edited")

            APP["_create_edited_audio_copy"](source, output, append_edit)

            self.assertEqual(source.read_bytes(), b"original")
            self.assertEqual(output.read_bytes(), b"original-edited")
            self.assertEqual(list(Path(directory).glob(".source.*.wav")), [])

    def test_failed_audio_edit_keeps_existing_output_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            output = Path(directory) / "output.wav"
            source.write_bytes(b"original")
            output.write_bytes(b"previous")

            def fail_edit(_path):
                raise RuntimeError("edit failed")

            with self.assertRaisesRegex(RuntimeError, "edit failed"):
                APP["_create_edited_audio_copy"](source, output, fail_edit)

            self.assertEqual(output.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).glob(".source.*.wav")), [])


class MetadataTabsStateTests(TestCase):
    def test_linked_alignment_document_moves_to_first_tab_without_closing_others(self):
        aligner = object.__new__(APP["DesktopAligner"])
        aligner.metadata_values = {"title": None, "artist": None}
        aligner._metadata_documents = [
            {"id": 1, "path": Path("first.mp3").absolute(), "autolrc_linked": False},
            {"id": 2, "path": Path("second.mp3").absolute(), "autolrc_linked": False},
        ]
        aligner._active_metadata_document_id = 2
        aligner._next_metadata_document_id = 3
        aligner._store_active_metadata_document = lambda: None
        aligner._render_active_metadata_document = lambda: None
        aligner._refresh_metadata_tabs = lambda **_kwargs: None
        aligner._load_metadata_audio = lambda *_args: None
        linked_path = Path("alignment.mp3").absolute()

        APP["DesktopAligner"]._open_metadata_document(
            aligner, linked_path, autolrc_linked=True
        )

        self.assertEqual([document["path"] for document in aligner._metadata_documents], [
            linked_path,
            Path("first.mp3").absolute(),
            Path("second.mp3").absolute(),
        ])
        self.assertEqual(aligner._active_metadata_document_id, 3)
        self.assertEqual(
            [document["autolrc_linked"] for document in aligner._metadata_documents],
            [True, False, False],
        )

    def test_sidebar_switch_syncs_alignment_audio_when_opening_editor(self):
        aligner = object.__new__(APP["DesktopAligner"])
        aligner.audio_editor_page = SimpleNamespace(tkraise=lambda: None)
        aligner.autolrc_page = SimpleNamespace(tkraise=lambda: None)
        aligner.player_footer = SimpleNamespace(grid=lambda: None, grid_remove=lambda: None)
        aligner.audio_path = SimpleNamespace(get=lambda: "song.mp3")
        pending_idle_callbacks = []
        aligner.root = SimpleNamespace(after_idle=pending_idle_callbacks.append)
        aligner._sync_playback_controls = lambda: None
        aligner.nav_buttons = {
            name: (SimpleNamespace(configure=lambda **_kwargs: None),
                   SimpleNamespace(configure=lambda **_kwargs: None),
                   SimpleNamespace(configure=lambda **_kwargs: None))
            for name in ("audio", "autolrc")
        }
        synced = []
        aligner._sync_autolrc_metadata_document = synced.append

        APP["DesktopAligner"]._show_page(aligner, "audio")
        pending_idle_callbacks.pop(0)()
        APP["DesktopAligner"]._show_page(aligner, "autolrc")

        self.assertEqual(aligner._active_page, "autolrc")
        self.assertEqual(synced, [Path("song.mp3").absolute()])


class OnlineLyricsTests(TestCase):
    def test_search_falls_back_to_keyword_query_when_structured_search_is_empty(self):
        candidate = {
            "trackName": "I Want to Live",
            "artistName": "Borislav Slavov",
            "albumName": "Baldur's Gate 3",
            "duration": 233,
            "syncedLyrics": "[00:01.00]Example",
        }
        with patch.object(online_lyrics, "_request_search", side_effect=[[], [candidate]]) as request:
            results = online_lyrics.search_lyrics(
                title="I Want to Live",
                artist="Borislav Slavov",
                album="Baldur's Gate 3",
                duration=233,
            )

        self.assertEqual(request.call_args_list[0].args[0], {
            "track_name": "I Want to Live",
            "artist_name": "Borislav Slavov",
            "album_name": "Baldur's Gate 3",
        })
        self.assertEqual(request.call_args_list[1].args[0], {"q": "I Want to Live Borislav Slavov"})
        self.assertEqual(results[0]["trackName"], "I Want to Live")
        self.assertEqual(results[0]["_match_score"], 1.0)

    def test_search_reports_the_api_validation_message(self):
        response = SimpleNamespace(
            status_code=400,
            headers={},
            content=b'{"message":"invalid query"}',
            json=lambda: {"message": "invalid query"},
            raise_for_status=lambda: None,
        )
        with patch.object(online_lyrics.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "HTTP 400.*invalid query"):
                online_lyrics._request_search({"track_name": "example"})

    def test_search_turns_network_timeout_into_an_actionable_message(self):
        with patch.object(
            online_lyrics.requests,
            "get",
            side_effect=online_lyrics.requests.exceptions.Timeout("timed out"),
        ):
            with self.assertRaisesRegex(RuntimeError, "连接 LRCLIB 超时"):
                online_lyrics._request_search({"track_name": "example"})


class AdapterTests(TestCase):
    def test_output_time_parsing_and_offset(self):
        self.assertEqual(
            adapter._parse_output_times(
                "[00:01.2]one\nnot timed\n[01:02:003]two\n[01:99:003]invalid"
            ),
            ["00:01.200", "01:02.003"],
        )
        self.assertEqual(adapter._add_time_offset("00:59.900", 250), "01:00.150")

    def test_alignment_rejects_invalid_inputs_before_loading_models(self):
        with self.assertRaisesRegex(ValueError, "为空"):
            adapter.run_alignment(b"", ".mp3", ["hello"], [])
        with self.assertRaisesRegex(ValueError, "逐行歌词"):
            adapter.run_alignment(b"audio", ".mp3", [], [])
        with patch.object(adapter, "MAX_AUDIO_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "200 MB"):
                adapter.run_alignment(b"audio", ".mp3", ["hello"], [])

    def test_boundary_checkpoint_load_is_restricted_to_weights(self):
        class FakeTorch:
            def __init__(self):
                self.arguments = None

            def load(self, path, *, map_location, weights_only):
                self.arguments = (path, map_location, weights_only)
                return {"model_state_dict": {"weight": [1, 2]}}

        class FakeModel:
            state = None

            def load_state_dict(self, state):
                self.state = state

        fake_torch = FakeTorch()
        fake_model = FakeModel()
        adapter._load_boundary_weights(fake_torch, fake_model, "checkpoint.pt", "cpu")

        self.assertEqual(fake_torch.arguments, ("checkpoint.pt", "cpu", True))
        self.assertEqual(fake_model.state, {"weight": [1, 2]})

    def test_boundary_checkpoint_rejects_unexpected_payload(self):
        class FakeTorch:
            @staticmethod
            def load(_path, *, map_location, weights_only):
                return {"unexpected": "payload"}

        with self.assertRaisesRegex(ValueError, "权重数据"):
            adapter._load_boundary_weights(FakeTorch(), object(), "checkpoint.pt", "cpu")


class EmbeddedLyricsTests(TestCase):
    def test_raw_id3_reader_extracts_unsynchronized_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp3"
            _write_id3v23_uslt(path, "[00:01.000]Hello")

            self.assertEqual(embedded_lyrics._read_id3_lyrics_raw(path), ["[00:01.000]Hello"])

    def test_in_place_lyrics_overwrite_is_returned_by_a_fresh_read(self):
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("Mutagen is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8_000)
                audio.writeframes(b"\x00\x00" * 800)

            old_content = "[00:01.000]Old lyric"
            new_content = "[00:02.000]Updated lyric"
            embedded_lyrics.write_embedded_lyrics(path, old_content)
            self.assertEqual(embedded_lyrics.read_embedded_lyrics(path), [old_content])

            saved_contents = APP["_write_embedded_lyrics_in_place"](path, new_content)

            self.assertEqual(saved_contents, [new_content])
            self.assertEqual(embedded_lyrics.read_embedded_lyrics(path), [new_content])
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.readframes(800), b"\x00\x00" * 800)

    def test_oversized_id3_header_is_rejected_before_reading_its_payload(self):
        class TrackingStream(io.BytesIO):
            read_sizes = []

            def read(self, size=-1):
                self.read_sizes.append(size)
                return super().read(size)

        stream = TrackingStream(
            b"ID3\x03\x00\x00" + _syncsafe(embedded_lyrics.MAX_ID3_TAG_BYTES + 1)
        )
        with patch.object(Path, "open", return_value=stream):
            self.assertEqual(embedded_lyrics._read_id3_lyrics_raw(Path("unused.mp3")), [])

        self.assertEqual(stream.read_sizes, [10])


class AudioMetadataTests(TestCase):
    def test_track_number_reader_handles_malformed_tuples(self):
        self.assertEqual(audio_metadata._first_text([(2, 12)]), "2/12")
        self.assertEqual(audio_metadata._first_text([(2,)]), "2")
        self.assertEqual(audio_metadata._first_text([()]), "")

    def test_cover_size_limit_is_checked_before_image_decode(self):
        with self.assertRaisesRegex(ValueError, "20 MB"):
            audio_metadata.validate_cover_data(bytes(audio_metadata.MAX_COVER_BYTES + 1))

    def test_wav_tag_and_embedded_lyrics_round_trip_in_a_temporary_directory(self):
        try:
            from PIL import Image
            import mutagen  # noqa: F401
        except ImportError:
            self.skipTest("Pillow or Mutagen is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(8_000)
                audio.writeframes(b"\x00\x00" * 800)

            cover_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), color=(40, 80, 120)).save(cover_buffer, format="PNG")
            cover_data = cover_buffer.getvalue()
            fields = {
                "title": "Round trip",
                "artist": "Test artist",
                "album": "Test album",
                "album_artist": "Test artist",
                "year": "2026",
                "genre": "Pop",
                "track_number": "2/12",
                "composer": "Composer",
                "comment": "Temporary test",
            }
            audio_metadata.write_audio_metadata(path, fields, cover_data)
            embedded_lyrics.write_embedded_lyrics(path, "[00:01.000]Temporary lyric")

            result = audio_metadata.read_audio_metadata(path)
            self.assertEqual(result["fields"], fields)
            self.assertEqual(result["cover_data"], cover_data)
            self.assertEqual(
                embedded_lyrics.read_embedded_lyrics(path), ["[00:01.000]Temporary lyric"]
            )


class AudioPlayerTests(TestCase):
    def test_player_seek_pause_resume_and_stop_state(self):
        class FakeMusic:
            def __init__(self):
                self.busy = False
                self.elapsed = 0
                self.loaded_path = None

            def load(self, path):
                self.loaded_path = path

            def play(self, *, start):
                self.elapsed = 0
                self.busy = True

            def get_pos(self):
                return self.elapsed

            def get_busy(self):
                return self.busy

            def pause(self):
                self.busy = False

            def unpause(self):
                self.busy = True

            def stop(self):
                self.busy = False

            def unload(self):
                self.loaded_path = None

        music = FakeMusic()
        pygame = SimpleNamespace(
            mixer=SimpleNamespace(
                get_init=lambda: True,
                music=music,
            ),
            error=RuntimeError,
        )
        player_type = APP["PygameAudioPlayer"]
        with patch.dict(sys.modules, {"pygame": pygame}):
            with patch.object(player_type, "_read_duration_ms", return_value=120_000):
                player = player_type()
                self.assertEqual(player.open(Path("song.mp3")), 120_000)
                player.play_from(15_000)
                music.elapsed = 600
                self.assertEqual(player.position_ms(), 15_600)
                player.pause()
                player.seek_to(30_000)
                self.assertEqual(player.mode(), "paused")
                self.assertEqual(player.position_ms(), 30_000)
                player.resume()
                self.assertEqual(player.mode(), "playing")
                player.stop()
                self.assertEqual(player.mode(), "stopped")
                self.assertEqual(player.position_ms(), 0)
                player.close()


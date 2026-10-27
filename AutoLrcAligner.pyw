from __future__ import annotations

import json
import hashlib
import io
import ctypes
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

ROOT_DIR = Path(__file__).resolve().parent
ENGINE_DIR = ROOT_DIR / "auto-lrc-engine"
VENV_PYTHON = ENGINE_DIR / ".venv" / "Scripts" / "python.exe"
VENV_PYTHONW = ENGINE_DIR / ".venv" / "Scripts" / "pythonw.exe"
APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Lyrics"
LEGACY_SETTINGS_FILE = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    / "AutoLrcAligner"
    / "settings.json"
)
WINDOWS_APP_USER_MODEL_ID = "Lyrics.LocalDesktop"

# A double-click uses the Windows .pyw association. Reopen in the project's
# virtual environment so the user does not need to activate it in PowerShell.
if VENV_PYTHONW.is_file() and Path(sys.executable).resolve() != VENV_PYTHONW.resolve():
    subprocess.Popen([str(VENV_PYTHONW), str(Path(__file__).resolve())], cwd=ROOT_DIR)
    raise SystemExit(0)

sys.path.insert(0, str(ENGINE_DIR))

try:
    from adapter import MAX_AUDIO_BYTES, VocalModelUnavailableError, run_alignment
    from embedded_lyrics import read_embedded_lyrics, write_embedded_lyrics
    from audio_metadata import read_audio_metadata, validate_cover_data, write_audio_metadata
    from online_lyrics import fetch_lyrics, read_track_metadata, search_lyrics
except ImportError as exc:
    _bootstrap_root = tk.Tk()
    _bootstrap_root.withdraw()
    messagebox.showerror(
        "请先安装运行环境",
        "请先阅读 README.md，并在开始菜单中手动安装本机运行环境。\n\n"
        f"详细信息：{exc}",
        parent=_bootstrap_root,
    )
    _bootstrap_root.destroy()
    raise SystemExit(1)


TIME_TAG = re.compile(r"\[(\d{1,3}):([0-5]\d)(?:[.:](\d{1,3}))?\]")
DEFAULT_LYRIC_DRAFT_PANEL_HEIGHT = 420
MIN_LYRIC_DRAFT_PANEL_HEIGHT = 320
MAX_LYRIC_DRAFT_PANEL_HEIGHT = 1000
DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS = 0.5
MIN_WHEEL_HANDOFF_COOLDOWN_SECONDS = 0.1
MAX_WHEEL_HANDOFF_COOLDOWN_SECONDS = 1.0
META_TAG = re.compile(
    r"^\s*\[(?:ar|al|ti|by|re|ve|la|length|offset|kana):.*\]\s*$", re.IGNORECASE
)
HEADER_INFO_LINE = re.compile(
    r"^\s*(?:标题|標題|曲名|歌曲名|歌名|title|作词|作詞|歌词|歌詞|作曲|编曲|編曲|原曲作者|原唱|人声翻唱|人聲翻唱|"
    r"歌手|演唱|翻唱|和声|和聲|混音|母带|母帶|制作|製作|制作人|製作人|监制|監製|配唱|录音|錄音|"
    r"词曲|詞曲|翻译|翻譯|译词|譯詞|译者|譯者|歌词翻译|歌詞翻譯|字幕|整理|"
    r"策划|策劃|企划|企劃|出品|发行|發行|鸣谢|鳴謝|lyrics?|lyricist|composer|music|"
    r"arranger|artist|singer|vocal(?:ist)?|producer|translator|transcriber|subtitler)\s*[:：]",
    re.IGNORECASE,
)
BRACKETED_TITLE_LINE = re.compile(r"^\s*(?:【[^】]+】.+|《[^》]+》.+)\s*$")
LABELED_INFO_LINE = re.compile(r"^\s*[^:：]{1,24}[:：]\s*\S.*$")
LYRIC_INFO_TAG_ORDER = ("ti", "ar", "al", "by", "re", "ve", "la", "length", "offset", "kana")
LYRIC_TEMPLATE_LABELS = {
    "zh": {
        "ti": "标题",
        "ar": "歌手",
        "al": "专辑",
        "by": "制作者",
        "re": "生成程序",
        "ve": "版本",
        "la": "语言",
        "length": "时长",
        "offset": "时间偏移",
        "kana": "假名",
    },
    "en": {
        "ti": "Title",
        "ar": "Artist",
        "al": "Album",
        "by": "Creator",
        "re": "Program",
        "ve": "Version",
        "la": "Language",
        "length": "Length",
        "offset": "Offset",
        "kana": "Kana",
    },
}


def _normalize_wheel_handoff_cooldown(value) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS
    if not math.isfinite(seconds):
        seconds = DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS
    return round(
        max(
            MIN_WHEEL_HANDOFF_COOLDOWN_SECONDS,
            min(MAX_WHEEL_HANDOFF_COOLDOWN_SECONDS, seconds),
        ),
        1,
    )


def _wheel_handoff_cooldown_for(root) -> float:
    return _normalize_wheel_handoff_cooldown(
        getattr(root, "_wheel_handoff_cooldown_seconds", DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS)
    )


LYRIC_CREDIT_TEMPLATE_LABELS = {
    "zh": {key: key for key in (
        "原唱", "作词", "作曲", "编曲", "原曲作者", "演唱", "翻唱", "人声翻唱", "和声", "词曲",
        "歌词翻译", "翻译", "配唱", "制作", "制作人", "监制", "录音", "混音", "母带", "整理",
        "字幕", "策划", "出品", "发行", "鸣谢",
    )},
    "en": {
        "原唱": "Original singer",
        "作词": "Lyrics",
        "作曲": "Music",
        "编曲": "Arrangement",
        "原曲作者": "Original composer",
        "演唱": "Vocals",
        "翻唱": "Cover artist",
        "人声翻唱": "Cover vocals",
        "和声": "Backing vocals",
        "词曲": "Songwriter",
        "歌词翻译": "Lyric translation",
        "翻译": "Translation",
        "配唱": "Vocal direction",
        "制作": "Production",
        "制作人": "Producer",
        "监制": "Executive producer",
        "录音": "Recording",
        "混音": "Mixing",
        "母带": "Mastering",
        "整理": "Transcription",
        "字幕": "Subtitles",
        "策划": "Planning",
        "出品": "Presented by",
        "发行": "Distribution",
        "鸣谢": "Special thanks",
    },
}
LYRIC_TRACK_LABELS = {
    label: key
    for key, aliases in {
        "ti": ("标题", "標題", "曲名", "歌曲名", "歌曲名称", "歌名", "title", "songtitle", "track", "tracktitle", "trackname"),
        "ar": ("歌手", "演唱者", "artist", "singer"),
        "al": ("专辑", "專輯", "album"),
    }.items()
    for label in aliases
}
LYRIC_INFO_LABELS = {
    re.sub(r"[\s_-]+", "", label.casefold()): key
    for language_labels in LYRIC_TEMPLATE_LABELS.values()
    for key, label in language_labels.items()
}
LYRIC_CREDIT_ALIASES = {
    alias: canonical
    for canonical, aliases in {
        "原唱": ("原唱", "原唱者", "原歌手", "originalsinger", "originalvocalist"),
        "作词": ("作词", "作詞", "作词者", "作詞者", "词作者", "詞作者", "lyricist", "lyrics"),
        "作曲": ("作曲", "作曲者", "曲作者", "composer", "music"),
        "编曲": ("编曲", "編曲", "编曲者", "編曲者", "arranger", "arrangement"),
        "原曲作者": ("原曲作者", "originalcomposer"),
        "演唱": ("演唱", "vocal", "vocalist"),
        "翻唱": ("翻唱", "翻唱者", "cover", "coverartist"),
        "人声翻唱": ("人声翻唱", "人聲翻唱", "covervocalist"),
        "和声": ("和声", "和聲", "harmony", "backingvocal"),
        "词曲": ("词曲", "詞曲", "songwriter"),
        "歌词翻译": ("歌词翻译", "歌詞翻譯", "lyricstranslation"),
        "翻译": ("翻译", "翻譯", "译词", "譯詞", "译者", "譯者", "translator"),
        "配唱": ("配唱",),
        "制作": ("制作", "製作", "production"),
        "制作人": ("制作人", "製作人", "producer"),
        "监制": ("监制", "監製", "executiveproducer"),
        "录音": ("录音", "錄音", "recording", "recordist"),
        "混音": ("混音", "mixer", "mixing"),
        "母带": ("母带", "母帶", "mastering"),
        "整理": ("整理", "transcriber"),
        "字幕": ("字幕", "subtitler"),
        "策划": ("策划", "策劃", "企划", "企劃"),
        "出品": ("出品", "publisher"),
        "发行": ("发行", "發行", "distributor"),
        "鸣谢": ("鸣谢", "鳴謝", "thanks"),
    }.items()
    for alias in aliases
}
LYRIC_CREDIT_ALIASES.update(
    {
        re.sub(r"[\s_-]+", "", label.casefold()): key
        for language_labels in LYRIC_CREDIT_TEMPLATE_LABELS.values()
        for key, label in language_labels.items()
    }
)
LYRIC_CREDIT_ORDER = tuple(dict.fromkeys(LYRIC_CREDIT_ALIASES.values()))
MIN_METADATA_INTERVAL_MS = 500
TIME_FORMAT = re.compile(r"^\d{2,}:[0-5]\d\.\d{3}$")
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".opus"}
THEMES = {
    "light": {
        "background": "#f0f3f8",
        "card": "#fafbfd",
        "input": "#f5f7fb",
        "sidebar": "#e8edf5",
        "border": "#dbe3ef",
        "text": "#435168",
        "secondary": "#738199",
        "accent": "#6c82a0",
        "accent_hover": "#5d7392",
        "accent_foreground": "#f8fafd",
        "section_text": "#53647d",
        "card_text": "#495a72",
        "status_background": "#e8edf4",
        "status_foreground": "#65758e",
        "selected_background": "#e0e8f3",
        "tree_heading_background": "#e9eef5",
        "tree_heading_foreground": "#61718a",
        "nav_selected_background": "#e0e8f3",
        "nav_hover_background": "#e8edf4",
        "nav_foreground": "#596a82",
        "nav_selected_foreground": "#587395",
        "lyrics_metadata_background": "#e7eef8",
        "table_review_background": "#f4eee5",
        "table_review_foreground": "#8d6841",
        "table_pending_background": "#f2eedc",
        "table_pending_foreground": "#8c7840",
        "table_checked_background": "#e9f0e9",
        "table_checked_foreground": "#557a61",
        "table_skipped_foreground": "#88918a",
        "table_problem_background": "#f3e9e8",
        "table_problem_foreground": "#986663",
        "table_active_background": "#e5ebf4",
        "button_background": "#edf1f7",
        "button_hover_background": "#e3eaf3",
        "button_foreground": "#50617a",
        "button_disabled_background": "#e6ebf2",
        "button_disabled_foreground": "#929baa",
        "metadata_close_hover_background": "#f1e9e9",
        "metadata_close_hover_foreground": "#986663",
        "scrollbar_background": "#cbd6e5",
        "scrollbar_hover_background": "#b9c8dc",
        "scrollbar_trough_background": "#e8edf4",
        "scrollbar_arrow_foreground": "#72839a",
        "window_frame_background": "#e7edf5",
        "window_frame_foreground": "#4d5d73",
        "seek_track_background": "#d9e2ef",
        "seek_thumb_background": "#fafbfd",
        "seek_thumb_halo_background": "#e2eaf5",
    },
    "dark": {
        "background": "#25292e",
        "card": "#2d3238",
        "input": "#272c31",
        "sidebar": "#22262b",
        "border": "#3c4249",
        "text": "#c8cdd2",
        "secondary": "#a1a8af",
        "accent": "#8799ad",
        "accent_hover": "#99a9b9",
        "accent_foreground": "#252a30",
        "section_text": "#c0c6cc",
        "card_text": "#c3c9cf",
        "status_background": "#30353b",
        "status_foreground": "#a6adb4",
        "selected_background": "#3b424a",
        "tree_heading_background": "#32373d",
        "tree_heading_foreground": "#bdc3c9",
        "nav_selected_background": "#394149",
        "nav_hover_background": "#30363d",
        "nav_foreground": "#b3b9c0",
        "nav_selected_foreground": "#bac7d5",
        "lyrics_metadata_background": "#353d46",
        "table_review_background": "#403b35",
        "table_review_foreground": "#c5ad91",
        "table_pending_background": "#3e3b33",
        "table_pending_foreground": "#c5bb96",
        "table_checked_background": "#333b36",
        "table_checked_foreground": "#a8b9ad",
        "table_skipped_foreground": "#92999f",
        "table_problem_background": "#403638",
        "table_problem_foreground": "#c6a3a5",
        "table_active_background": "#383e45",
        "button_background": "#353a40",
        "button_hover_background": "#3e444b",
        "button_foreground": "#c5cbd1",
        "button_disabled_background": "#30353a",
        "button_disabled_foreground": "#858d95",
        "metadata_close_hover_background": "#43383a",
        "metadata_close_hover_foreground": "#c6a3a5",
        "scrollbar_background": "#50565e",
        "scrollbar_hover_background": "#616971",
        "scrollbar_trough_background": "#292d32",
        "scrollbar_arrow_foreground": "#adb4bb",
        "window_frame_background": "#22262a",
        "window_frame_foreground": "#cbd0d5",
        "seek_track_background": "#454b52",
        "seek_thumb_background": "#2d3238",
        "seek_thumb_halo_background": "#414951",
    },
}
ACTIVE_THEME = THEMES["light"]


def _activate_theme(theme_name: str) -> None:
    global ACTIVE_THEME, APP_BACKGROUND, CARD_BACKGROUND, INPUT_BACKGROUND
    global SIDEBAR_BACKGROUND, BORDER_COLOR, TEXT_PRIMARY, TEXT_SECONDARY
    global ACCENT_COLOR, ACCENT_HOVER
    ACTIVE_THEME = THEMES[theme_name]
    APP_BACKGROUND = ACTIVE_THEME["background"]
    CARD_BACKGROUND = ACTIVE_THEME["card"]
    INPUT_BACKGROUND = ACTIVE_THEME["input"]
    SIDEBAR_BACKGROUND = ACTIVE_THEME["sidebar"]
    BORDER_COLOR = ACTIVE_THEME["border"]
    TEXT_PRIMARY = ACTIVE_THEME["text"]
    TEXT_SECONDARY = ACTIVE_THEME["secondary"]
    ACCENT_COLOR = ACTIVE_THEME["accent"]
    ACCENT_HOVER = ACTIVE_THEME["accent_hover"]


def ui_color(name: str) -> str:
    return ACTIVE_THEME[name]


_activate_theme("light")
COVER_PREVIEW_SIZE = 200
MAX_LYRICS_FILE_BYTES = 10 * 1024 * 1024


def is_header_line(line: str) -> bool:
    text = TIME_TAG.sub("", line).strip()
    return bool(
        META_TAG.match(text)
        or HEADER_INFO_LINE.match(text)
        or BRACKETED_TITLE_LINE.match(text)
        or LABELED_INFO_LINE.match(text)
    )


def rounded_rectangle_points(width: int, height: int, radius: int) -> list[float]:
    inset = 1.0
    x1, y1 = inset, inset
    x2, y2 = max(inset + 1, width - inset), max(inset + 1, height - inset)
    radius = max(1, min(radius, int((x2 - x1) / 2), int((y2 - y1) / 2)))
    r = float(radius)
    return [
        x1 + r, y1, x2 - r, y1,
        x2 - r * 0.45, y1, x2, y1 + r * 0.45, x2, y1 + r,
        x2, y2 - r,
        x2, y2 - r * 0.45, x2 - r * 0.45, y2, x2 - r, y2,
        x1 + r, y2,
        x1 + r * 0.45, y2, x1, y2 - r * 0.45, x1, y2 - r,
        x1, y1 + r,
        x1, y1 + r * 0.45, x1 + r * 0.45, y1, x1 + r, y1,
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _create_edited_audio_copy(source_path: Path, output_path: Path, edit_copy) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source_path.stem}.",
        suffix=source_path.suffix,
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source_path, temporary_path)
        edit_copy(temporary_path)
        os.replace(temporary_path, output_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


class RoundedPanel(tk.Frame):
    """A softly rounded panel with a regular Tk/ttk content area."""

    def __init__(
        self,
        master,
        *,
        padding=12,
        radius=16,
        surface=None,
        outer_background=None,
        content_style="Card.TFrame",
    ):
        surface = CARD_BACKGROUND if surface is None else surface
        outer_background = APP_BACKGROUND if outer_background is None else outer_background
        super().__init__(master, background=outer_background, bd=0, highlightthickness=0)
        self._radius = radius
        self._surface = surface
        self._canvas = tk.Canvas(
            self,
            background=outer_background,
            bd=0,
            highlightthickness=0,
            takefocus=0,
        )
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._shape = self._canvas.create_polygon(
            0,
            0,
            1,
            1,
            smooth=True,
            splinesteps=24,
            fill=surface,
            outline=BORDER_COLOR,
            width=1,
        )
        self.content = ttk.Frame(self, style=content_style, padding=padding)
        self.content.pack(fill="both", expand=True, padx=radius, pady=radius)
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, event) -> None:
        self._canvas.coords(
            self._shape,
            *rounded_rectangle_points(event.width, event.height, self._radius),
        )


class ScrollablePage(ttk.Frame):
    """Reusable page body with vertical scrollbar and pointer-aware wheel routing."""

    _wheel_passthrough_classes = {
        "Entry",
        "Scrollbar",
        "Spinbox",
        "TCombobox",
        "TEntry",
        "TScrollbar",
        "TSpinbox",
        "Text",
        "Treeview",
    }

    def __init__(
        self,
        master,
        *,
        style: str = "App.TFrame",
        background: str | None = None,
        content_style: str = "App.TFrame",
    ):
        background = APP_BACKGROUND if background is None else background
        super().__init__(master, style=style)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._scrollregion_after: str | None = None
        self._content_window_width: int | None = None
        self._wheel_remainder = 0.0
        self._scroll_router_root = None
        self._parent_scroll_lock: dict | None = None

        self.canvas = tk.Canvas(
            self,
            background=background,
            bd=0,
            highlightthickness=0,
            yscrollincrement=24,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.grid_remove()
        self.content = ttk.Frame(self.canvas, style=content_style)
        self._content_window = self.canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )
        self.content.bind("<Configure>", self._schedule_scrollregion, add="+")
        self.canvas.bind("<Configure>", self._resize_content, add="+")

        root = self.winfo_toplevel()
        self._scroll_router_root = root
        scrollable_pages = getattr(root, "_auto_lrc_scrollable_pages", None)
        if scrollable_pages is None:
            scrollable_pages = []
            root._auto_lrc_scrollable_pages = scrollable_pages
            root.bind_all(
                "<MouseWheel>",
                lambda event, owner=root: ScrollablePage._route_mousewheel(owner, event),
                add="+",
            )
            root.bind_all(
                "<Button-4>",
                lambda event, owner=root: ScrollablePage._route_mousewheel(owner, event),
                add="+",
            )
            root.bind_all(
                "<Button-5>",
                lambda event, owner=root: ScrollablePage._route_mousewheel(owner, event),
                add="+",
            )
            root.bind_all(
                "<Motion>",
                lambda event, owner=root: ScrollablePage._route_mousemotion(owner, event),
                add="+",
            )
        scrollable_pages.append(self)
        self.bind("<Destroy>", self._remove_from_scroll_router, add="+")

    @staticmethod
    def _route_mousewheel(root, event):
        widget = event.widget
        for page in reversed(getattr(root, "_auto_lrc_scrollable_pages", [])):
            if not page._contains_widget(widget):
                continue
            try:
                widget_class = widget.winfo_class()
            except tk.TclError:
                return None
            if page._parent_scroll_lock_active(widget, event):
                page._remember_parent_scroll(event, source_widget=widget)
                return page._scroll_from_wheel(event)
            if widget_class in page._wheel_passthrough_classes:
                if widget_class in {"Text", "Treeview"}:
                    at_boundary, allow_parent = ScrollablePage._wheel_boundary_handoff(
                        widget, event
                    )
                    if at_boundary:
                        if not allow_parent:
                            return "break"
                        page._remember_parent_scroll(event, source_widget=widget)
                        return page._scroll_from_wheel(event)
                return None
            page._remember_parent_scroll(event)
            return page._scroll_from_wheel(event)
        return None

    @staticmethod
    def _route_mousemotion(root, event):
        for page in reversed(getattr(root, "_auto_lrc_scrollable_pages", [])):
            page._release_parent_scroll_lock_on_motion(event)
        return None

    def _remember_parent_scroll(self, event, *, source_widget=None) -> None:
        previous = self._parent_scroll_lock
        source_widgets = (
            list(previous.get("source_widgets", []))
            if previous is not None
            else []
        )
        if source_widget is not None and source_widget not in source_widgets:
            source_widgets.append(source_widget)
        self._parent_scroll_lock = {
            "x": getattr(event, "x_root", None),
            "y": getattr(event, "y_root", None),
            "deadline": time.monotonic() + _wheel_handoff_cooldown_for(self._scroll_router_root),
            "source_widgets": source_widgets,
        }

    def _parent_scroll_lock_active(self, widget, event) -> bool:
        lock = self._parent_scroll_lock
        if lock is None:
            return False
        same_pointer = (
            getattr(event, "x_root", None) == lock.get("x")
            and getattr(event, "y_root", None) == lock.get("y")
        )
        if same_pointer and time.monotonic() < lock.get("deadline", 0):
            return True
        self._clear_parent_scroll_lock(widget)
        return False

    def _release_parent_scroll_lock_on_motion(self, event) -> None:
        lock = self._parent_scroll_lock
        if lock is None:
            return
        if (
            getattr(event, "x_root", None) != lock.get("x")
            or getattr(event, "y_root", None) != lock.get("y")
        ):
            self._clear_parent_scroll_lock()

    def _clear_parent_scroll_lock(self, current_widget=None) -> None:
        lock = self._parent_scroll_lock
        source_widgets = lock.get("source_widgets", []) if lock is not None else []
        widgets_to_reset = list(source_widgets)
        if current_widget is not None:
            widgets_to_reset.append(current_widget)
        try:
            pending = list(self.content.winfo_children())
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                try:
                    if widget.winfo_class() in {"Text", "Treeview"}:
                        widgets_to_reset.append(widget)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        for widget in widgets_to_reset:
            if widget is not None:
                try:
                    widget._parent_wheel_handoff_until = {}
                except (AttributeError, tk.TclError):
                    pass
        self._parent_scroll_lock = None

    @staticmethod
    def _wheel_boundary_handoff(widget, event) -> tuple[bool, bool]:
        button_number = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        if button_number == 4 or delta > 0:
            direction = -1
        elif button_number == 5 or delta < 0:
            direction = 1
        else:
            return False, False
        try:
            first, last = widget.yview()
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return False, False

        at_boundary = first <= 0.001 if direction < 0 else last >= 0.999
        deadlines = getattr(widget, "_parent_wheel_handoff_until", {})
        if not isinstance(deadlines, dict):
            deadlines = {}
        if not at_boundary:
            deadlines.clear()
            widget._parent_wheel_handoff_until = deadlines
            return False, False

        now = time.monotonic()
        deadline = deadlines.get(direction)
        if deadline is None:
            deadlines[direction] = now + _wheel_handoff_cooldown_for(widget.winfo_toplevel())
            allow_parent = False
        else:
            allow_parent = now >= deadline
        widget._parent_wheel_handoff_until = deadlines
        return True, allow_parent

    def _contains_widget(self, widget) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _scroll_from_wheel(self, event):
        button_number = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        if button_number == 4:
            units = -3
        elif button_number == 5:
            units = 3
        elif delta:
            self._wheel_remainder -= delta * 3 / 120
            units = int(self._wheel_remainder)
            self._wheel_remainder -= units
        else:
            return "break"
        if units:
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _schedule_scrollregion(self, _event=None) -> None:
        if self._scrollregion_after is None:
            self._scrollregion_after = self.after_idle(self._update_scrollregion)

    def _update_scrollregion(self) -> None:
        self._scrollregion_after = None
        try:
            bounds = self.canvas.bbox("all")
            self.canvas.configure(scrollregion=bounds or (0, 0, 0, 0))
            self._bind_parent_scroll_lock_to_descendants()
            content_height = bounds[3] - bounds[1] if bounds else 0
            viewport_height = self.canvas.winfo_height()
            needs_scrollbar = content_height > viewport_height + 1
            is_visible = self.scrollbar.winfo_manager() == "grid"
            if needs_scrollbar != is_visible:
                if needs_scrollbar:
                    self.scrollbar.grid(row=0, column=1, sticky="ns")
                else:
                    self.scrollbar.grid_remove()
        except tk.TclError:
            pass

    def _bind_parent_scroll_lock_to_descendants(self) -> None:
        pending = list(self.content.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if getattr(widget, "_wheel_parent_lock_owner", None) == id(self):
                continue
            try:
                for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    widget.bind(sequence, self._route_locked_child_wheel, add="+")
                widget._wheel_parent_lock_owner = id(self)
            except (AttributeError, tk.TclError):
                pass

    def _route_locked_child_wheel(self, event):
        if self._parent_scroll_lock_active(event.widget, event):
            self._remember_parent_scroll(event, source_widget=event.widget)
            return self._scroll_from_wheel(event)
        return None

    def _resize_content(self, event) -> None:
        self._schedule_scrollregion()
        if event.width == self._content_window_width:
            return
        self._content_window_width = event.width
        self.canvas.itemconfigure(self._content_window, width=event.width)

    def _remove_from_scroll_router(self, event) -> None:
        if event.widget is not self:
            return
        root = self._scroll_router_root
        pages = getattr(root, "_auto_lrc_scrollable_pages", None)
        if pages is not None and self in pages:
            pages.remove(self)


class RoundedButton(tk.Canvas):
    """Canvas button used where the native ttk button has square corners."""

    def __init__(
        self,
        master,
        text: str,
        command,
        *,
        accent: bool = False,
        background: str | None = None,
    ):
        background = APP_BACKGROUND if background is None else background
        self._text = text
        self._command = command
        self._accent = accent
        self._button_background = background
        self._disabled = False
        self._hovered = False
        self._focused = False
        self._font = tkfont.Font(
            root=master,
            family="Microsoft YaHei UI",
            size=10,
            weight="bold" if accent else "normal",
        )
        width = max(82, self._font.measure(text) + 28)
        super().__init__(
            master,
            width=width,
            height=38,
            background=background,
            bd=0,
            highlightthickness=0,
            takefocus=1,
            cursor="hand2",
        )
        self._shape = self.create_polygon(
            0,
            0,
            1,
            1,
            smooth=True,
            splinesteps=18,
            width=0,
        )
        self._label = self.create_text(
            0,
            0,
            text=text,
            font=self._font,
            anchor="center",
        )
        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Button-1>", self._invoke)
        self.bind("<space>", self._invoke)
        self.bind("<Return>", self._invoke)
        self._refresh()

    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        if state is not None:
            self._disabled = state == "disabled"
            self._refresh()
        return super().configure(cnf, **kwargs)

    config = configure

    def _redraw(self, event) -> None:
        self.coords(self._shape, *rounded_rectangle_points(event.width, event.height, 10))
        self.coords(self._label, event.width / 2, event.height / 2)

    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._refresh()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._refresh()

    def _on_focus_in(self, _event=None) -> None:
        self._focused = True
        self._refresh()

    def _on_focus_out(self, _event=None) -> None:
        self._focused = False
        self._refresh()

    def _invoke(self, _event=None):
        if not self._disabled:
            self._command()
        return "break"

    def _refresh(self) -> None:
        if self._disabled:
            fill = ui_color("button_disabled_background")
            foreground = ui_color("button_disabled_foreground")
        elif self._accent:
            fill = ACCENT_HOVER if self._hovered else ACCENT_COLOR
            foreground = ui_color("accent_foreground")
        else:
            fill = (
                ui_color("button_hover_background")
                if self._hovered
                else ui_color("button_background")
            )
            foreground = ui_color("button_foreground")
        self.itemconfigure(
            self._shape,
            fill=fill,
            outline=ACCENT_COLOR if self._focused and not self._disabled else fill,
            width=2 if self._focused and not self._disabled else 0,
        )
        self.itemconfigure(self._label, fill=foreground)
        self.configure(cursor="arrow" if self._disabled else "hand2")


class RoundedTabButton(tk.Canvas):
    """Soft, borderless settings tab with a restrained selected state."""

    def __init__(self, master, text: str, command, *, variant: str):
        self._text = text
        self._command = command
        self._variant = variant
        self._selected = False
        self._hovered = False
        self._font = tkfont.Font(
            root=master,
            family="Microsoft YaHei UI",
            size=9,
        )
        width = max(62, self._font.measure(text) + 28)
        super().__init__(
            master,
            width=width,
            height=36,
            background=APP_BACKGROUND,
            bd=0,
            highlightthickness=0,
            takefocus=1,
            cursor="hand2",
        )
        self._shape = self.create_polygon(
            0,
            0,
            1,
            1,
            smooth=True,
            splinesteps=18,
            width=0,
        )
        self._label = self.create_text(
            0,
            0,
            text=text,
            font=self._font,
            anchor="center",
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Button-1>", self._invoke, add="+")
        self.bind("<space>", self._invoke, add="+")
        self.bind("<Return>", self._invoke, add="+")
        self._refresh_theme()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_theme()

    def _redraw(self, event) -> None:
        self.coords(self._shape, *rounded_rectangle_points(event.width, event.height, 9))
        self.coords(self._label, event.width / 2, event.height / 2)

    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self._refresh_theme()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._refresh_theme()

    def _invoke(self, _event=None):
        self._command()
        return "break"

    def _refresh_theme(self) -> None:
        selected_background = (
            ui_color("status_background")
            if self._variant == "category"
            else CARD_BACKGROUND
        )
        hover_background = ui_color("nav_hover_background")
        if self._selected:
            fill = selected_background
            foreground = TEXT_PRIMARY
        elif self._hovered:
            fill = hover_background
            foreground = TEXT_SECONDARY
        else:
            fill = APP_BACKGROUND
            foreground = TEXT_SECONDARY
        self.configure(background=APP_BACKGROUND)
        self.itemconfigure(self._shape, fill=fill, outline=fill, width=0)
        self.itemconfigure(self._label, fill=foreground)


class RoundedTabs(ttk.Frame):
    """A compact tab strip with rounded, theme-aware tabs and stacked pages."""

    def __init__(self, master, *, variant: str = "category"):
        super().__init__(master, style="App.TFrame")
        self._variant = variant
        self._pages: list[ttk.Frame] = []
        self._tab_buttons: list[RoundedTabButton] = []
        self._selected_index = -1

        self.tab_bar = ttk.Frame(self, style="App.TFrame")
        self.tab_bar.pack(fill="x", anchor="w", pady=(0, 6))
        self.content = ttk.Frame(self, style="App.TFrame")
        self.content.pack(fill="both", expand=True)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

    def add(self, page: ttk.Frame, *, text: str) -> None:
        index = len(self._pages)
        self._pages.append(page)
        page.grid(row=0, column=0, sticky="nsew")
        button = RoundedTabButton(
            self.tab_bar,
            text,
            lambda target=index: self.select(target),
            variant=self._variant,
        )
        button.pack(side="left", padx=(0, 6), pady=(1, 2))
        self._tab_buttons.append(button)
        if self._selected_index < 0:
            self.select(index)
        else:
            page.grid_remove()

    def select(self, index: int) -> None:
        if not 0 <= index < len(self._pages):
            return
        self._selected_index = index
        for page_index, (page, button) in enumerate(zip(self._pages, self._tab_buttons)):
            if page_index == index:
                page.grid()
            else:
                page.grid_remove()
            button.set_selected(page_index == index)


class PlaybackSeekbar(tk.Canvas):
    """Compact themed audio seekbar with a rounded track and tactile thumb."""

    def __init__(
        self,
        master,
        variable: tk.DoubleVar,
        *,
        maximum: int = 1,
        on_seek_start=None,
        on_seek_end=None,
    ):
        self.variable = variable
        self._maximum = max(1, int(maximum))
        self._enabled = True
        self._dragging = False
        self._hovered = False
        self._focused = False
        self._on_seek_start = on_seek_start
        self._on_seek_end = on_seek_end
        super().__init__(
            master,
            height=32,
            background=CARD_BACKGROUND,
            bd=0,
            highlightthickness=0,
            takefocus=1,
            cursor="hand2",
        )
        self._track = self.create_line(0, 0, 1, 0, width=5, capstyle=tk.ROUND)
        self._progress = self.create_line(0, 0, 0, 0, width=5, capstyle=tk.ROUND)
        self._halo = self.create_oval(0, 0, 0, 0, outline="", state="hidden")
        self._thumb = self.create_oval(0, 0, 0, 0, width=2)
        self._thumb_center = self.create_oval(0, 0, 0, 0, outline="")
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_motion, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<FocusIn>", self._on_focus_in, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Left>", lambda event: self._step(event, -1), add="+")
        self.bind("<Right>", lambda event: self._step(event, 1), add="+")
        self.bind("<Home>", self._on_home, add="+")
        self.bind("<End>", self._on_end, add="+")
        self.variable.trace_add("write", self._on_value_changed)
        self.redraw_theme()

    def set_range(self, maximum: int) -> None:
        try:
            current = int(float(self.variable.get()))
        except (tk.TclError, TypeError, ValueError):
            current = 0
        self._maximum = max(1, int(maximum))
        self.variable.set(min(self._maximum, max(0, current)))
        self.redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        thumb_color = ACCENT_COLOR if self._enabled else TEXT_SECONDARY
        self.itemconfigure(self._progress, fill=thumb_color)
        self.itemconfigure(self._thumb, outline=thumb_color)
        self.itemconfigure(self._thumb_center, fill=thumb_color)
        self.redraw()

    def redraw_theme(self) -> None:
        self.configure(background=CARD_BACKGROUND)
        self.itemconfigure(self._track, fill=ui_color("seek_track_background"))
        self.itemconfigure(self._progress, fill=ACCENT_COLOR)
        self.itemconfigure(self._halo, fill=ui_color("seek_thumb_halo_background"))
        self.itemconfigure(
            self._thumb,
            fill=ui_color("seek_thumb_background"),
            outline=ACCENT_COLOR if self._enabled else TEXT_SECONDARY,
        )
        self.itemconfigure(self._thumb_center, fill=ACCENT_COLOR if self._enabled else TEXT_SECONDARY)
        self.redraw()

    def redraw(self) -> None:
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        left = min(14, max(6, width / 2 - 1))
        right = max(left + 1, width - left)
        center_y = height / 2
        fraction = min(1.0, max(0.0, self._read_value() / self._maximum))
        thumb_x = left + (right - left) * fraction
        self.coords(self._track, left, center_y, right, center_y)
        self.coords(self._progress, left, center_y, max(left, thumb_x), center_y)
        self.itemconfigure(self._progress, state="normal" if fraction > 0 else "hidden")

        halo_radius = 10 if self._hovered or self._focused or self._dragging else 7
        thumb_radius = 7 if self._hovered or self._dragging else 6
        self.coords(
            self._halo,
            thumb_x - halo_radius,
            center_y - halo_radius,
            thumb_x + halo_radius,
            center_y + halo_radius,
        )
        self.itemconfigure(
            self._halo,
            state="normal" if self._hovered or self._focused or self._dragging else "hidden",
        )
        self.coords(
            self._thumb,
            thumb_x - thumb_radius,
            center_y - thumb_radius,
            thumb_x + thumb_radius,
            center_y + thumb_radius,
        )
        self.coords(
            self._thumb_center,
            thumb_x - 2,
            center_y - 2,
            thumb_x + 2,
            center_y + 2,
        )
        self.tag_raise(self._halo)
        self.tag_raise(self._thumb)
        self.tag_raise(self._thumb_center)

    def _read_value(self) -> int:
        try:
            return min(self._maximum, max(0, int(float(self.variable.get()))))
        except (tk.TclError, TypeError, ValueError):
            return 0

    def _set_value(self, value: int) -> None:
        bounded = min(self._maximum, max(0, int(value)))
        if self._read_value() != bounded:
            self.variable.set(bounded)

    def _set_from_x(self, x: float) -> None:
        width = max(1, self.winfo_width())
        left = min(14, max(6, width / 2 - 1))
        right = max(left + 1, width - left)
        fraction = min(1.0, max(0.0, (x - left) / (right - left)))
        self._set_value(round(fraction * self._maximum))

    def _on_configure(self, _event=None) -> None:
        self.redraw()

    def _on_value_changed(self, *_args) -> None:
        self.redraw()

    def _on_press(self, event):
        if not self._enabled:
            return "break"
        self._dragging = True
        self.focus_set()
        if self._on_seek_start is not None:
            self._on_seek_start(event)
        self._set_from_x(event.x)
        self.redraw()
        return "break"

    def _on_motion(self, event):
        if self._enabled and self._dragging:
            self._set_from_x(event.x)
        return "break"

    def _on_release(self, event):
        if self._dragging:
            self._set_from_x(event.x)
            self._dragging = False
            self.redraw()
            if self._on_seek_end is not None:
                self._on_seek_end(event)
        return "break"

    def _on_enter(self, _event=None) -> None:
        self._hovered = True
        self.redraw()

    def _on_leave(self, _event=None) -> None:
        if not self._dragging:
            self._hovered = False
            self.redraw()

    def _on_focus_in(self, _event=None) -> None:
        self._focused = True
        self.redraw()

    def _on_focus_out(self, _event=None) -> None:
        self._focused = False
        self.redraw()

    def _step(self, event, direction: int):
        if not self._enabled:
            return "break"
        step_ms = 100 if event.state & 0x0001 else 1_000
        if self._on_seek_start is not None:
            self._on_seek_start(event)
        self._set_value(self._read_value() + direction * step_ms)
        if self._on_seek_end is not None:
            self._on_seek_end(event)
        return "break"

    def _on_home(self, event):
        return self._seek_to_endpoint(event, 0)

    def _on_end(self, event):
        return self._seek_to_endpoint(event, self._maximum)

    def _seek_to_endpoint(self, event, value: int):
        if not self._enabled:
            return "break"
        if self._on_seek_start is not None:
            self._on_seek_start(event)
        self._set_value(value)
        if self._on_seek_end is not None:
            self._on_seek_end(event)
        return "break"


def _read_lyrics_file(path: Path) -> str:
    with path.open("rb") as lyrics_file:
        content = lyrics_file.read(MAX_LYRICS_FILE_BYTES + 1)
    if len(content) > MAX_LYRICS_FILE_BYTES:
        raise ValueError("歌词文件不能超过 10 MB。")
    return content.decode("utf-8-sig", errors="replace")


def parse_lyrics_file(path: Path) -> tuple[list[str], list[str], list[str], bool]:
    content = _read_lyrics_file(path)
    return parse_lyrics_content(content)


def parse_lyrics_content(content: str) -> tuple[list[str], list[str], list[str], bool]:
    lyrics: list[str] = []
    times: list[str] = []
    metadata: list[str] = []
    has_timeline = False

    raw_lines = [line.strip("\ufeff\r") for line in content.splitlines()]
    # Prefer an explicit blank separator, which also supports custom header text.
    separator_index = next((index for index, line in enumerate(raw_lines) if not line.strip()), None)
    explicit_separator = separator_index is not None
    preamble_end = separator_index if explicit_separator else 0
    if not explicit_separator:
        # Also recognize a leading run of known title/credit lines. This lets common
        # files work without requiring users to edit them first.
        while preamble_end < len(raw_lines) and raw_lines[preamble_end].strip() and is_header_line(raw_lines[preamble_end]):
            preamble_end += 1

    if preamble_end:
        preamble = raw_lines[:preamble_end]
        header_texts = [TIME_TAG.sub("", line).strip() for line in preamble]
        has_header_marker = any(
            META_TAG.match(line)
            or HEADER_INFO_LINE.match(line)
            or BRACKETED_TITLE_LINE.match(line)
            or LABELED_INFO_LINE.match(line)
            for line in header_texts
        )
        has_credit_or_lrc_metadata = any(
            META_TAG.match(line) or HEADER_INFO_LINE.match(line) for line in header_texts
        )
        is_explicit_header = explicit_separator and has_header_marker
        is_inferred_header = not explicit_separator and has_credit_or_lrc_metadata and all(
            is_header_line(line) for line in preamble
        )
        if (
            any(line.strip() for line in preamble)
            and (is_explicit_header or is_inferred_header)
        ):
            metadata.extend(header_texts)
            metadata.append("")
            raw_lines = raw_lines[preamble_end:]
            while raw_lines and not raw_lines[0].strip():
                raw_lines.pop(0)

    for line in raw_lines:
        matches = list(TIME_TAG.finditer(line))
        if matches:
            has_timeline = True
            lyric = TIME_TAG.sub("", line).strip()
            for match in matches:
                minutes, seconds, fraction = match.groups()
                milliseconds = int((fraction or "0").ljust(3, "0"))
                lyrics.append(lyric)
                times.append(f"{int(minutes):02d}:{int(seconds):02d}.{milliseconds:03d}")
        elif META_TAG.match(line):
            metadata.append(line)
        else:
            lyrics.append(line)
            times.append("00:00.000")

    return lyrics, times, metadata, has_timeline


def _metadata_header_lines(metadata: list[str]) -> list[str]:
    return [line for line in metadata if line.strip()]


def _normalize_lyric_label(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value.casefold())


def _parse_lyric_info_label(line: str) -> tuple[str, str, str] | None:
    text = line.strip()
    tag_match = re.fullmatch(r"\[([A-Za-z]+):(.*?)\]", text)
    if tag_match:
        key = tag_match.group(1).casefold()
        if key in LYRIC_INFO_TAG_ORDER:
            return "tag", key, tag_match.group(2).strip()

    if len(text) >= 2 and (text[0], text[-1]) in (("[", "]"), ("【", "】")):
        text = text[1:-1].strip()
    label_match = re.match(r"^([^:：]{1,40})\s*[:：]\s*(.*?)\s*$", text)
    if not label_match:
        return None
    label = _normalize_lyric_label(label_match.group(1))
    value = label_match.group(2).strip()
    if label in LYRIC_TRACK_LABELS:
        return "track", LYRIC_TRACK_LABELS[label], value
    if label in LYRIC_INFO_LABELS:
        key = LYRIC_INFO_LABELS[label]
        return ("track" if key in {"ti", "ar", "al"} else "tag"), key, value
    if label in LYRIC_CREDIT_ALIASES:
        return "credit", LYRIC_CREDIT_ALIASES[label], value
    if LABELED_INFO_LINE.match(text):
        return "custom", label_match.group(1).strip(), value
    return None


def _is_timed_lyric_info_line(line: str) -> bool:
    parsed = _parse_lyric_info_label(line)
    return bool(parsed and parsed[0] in {"tag", "track", "credit"}) or bool(
        HEADER_INFO_LINE.match(line) or BRACKETED_TITLE_LINE.match(line)
    )


def _merge_online_lyrics_metadata(
    metadata: list[str],
    candidate: dict,
    audio_metadata: dict,
) -> list[str]:
    """Use this audio's tags for track fields while retaining lyric credits."""
    track_tags: dict[str, list[str]] = {key: [] for key in ("ti", "ar", "al")}
    retained_lines: list[str] = []
    for line in metadata:
        match = re.match(r"^\s*\[(ti|ar|al):(.*?)\]\s*$", line, re.IGNORECASE)
        if match:
            key = match.group(1).casefold()
            value = match.group(2).strip()
            if value:
                track_tags[key].append(value)
        else:
            parsed = _parse_lyric_info_label(line)
            if parsed and parsed[0] == "track":
                _kind, key, value = parsed
                if value:
                    track_tags[key].append(value)
            else:
                retained_lines.append(line)

    candidate_fields = {
        "ti": str(candidate.get("trackName") or "").strip(),
        "ar": str(candidate.get("artistName") or "").strip(),
        "al": str(candidate.get("albumName") or "").strip(),
    }
    audio_fields = {
        "ti": str(audio_metadata.get("title") or "").strip(),
        "ar": str(audio_metadata.get("artist") or "").strip(),
        "al": str(audio_metadata.get("album") or "").strip(),
    }

    source_artists = track_tags["ar"] or (
        [candidate_fields["ar"]] if candidate_fields["ar"] else []
    )
    original_singer = ""
    if audio_fields["ar"] and source_artists:
        def normalize_artist(value: str) -> str:
            return "".join(
                character for character in value.casefold() if character.isalnum()
            )

        differing_artists = [
            value
            for value in source_artists
            if normalize_artist(value) != normalize_artist(audio_fields["ar"])
        ]
        existing_originals = [
            parsed[2]
            for line in retained_lines
            if (parsed := _parse_lyric_info_label(line))
            and parsed[0] == "credit"
            and parsed[1] == "原唱"
            and parsed[2]
        ]
        existing_originals_normalized = normalize_artist("".join(existing_originals))
        missing_originals = [
            value
            for value in differing_artists
            if normalize_artist(value) not in existing_originals_normalized
        ]
        if missing_originals:
            original_singer = "、".join(dict.fromkeys(missing_originals))

    merged = []
    for key in ("ti", "ar", "al"):
        if audio_fields[key]:
            value = audio_fields[key]
        elif key == "ar" and track_tags[key]:
            value = "、".join(dict.fromkeys(track_tags[key]))
        else:
            value = track_tags[key][0] if track_tags[key] else candidate_fields[key]
        if value:
            merged.append(f"[{key}:{value}]")
    if original_singer:
        merged.append(f"原唱：{original_singer}")
    merged.extend(retained_lines)
    return merged


def _canonicalize_lyrics_metadata(metadata: list[str], language: str = "zh") -> list[str]:
    language = language if language in LYRIC_TEMPLATE_LABELS else "zh"
    tag_labels = LYRIC_TEMPLATE_LABELS[language]
    credit_labels = LYRIC_CREDIT_TEMPLATE_LABELS[language]
    separator = "：" if language == "zh" else ": "
    tag_values: dict[str, list[str]] = {key: [] for key in LYRIC_INFO_TAG_ORDER}
    credit_values: dict[str, list[str]] = {key: [] for key in LYRIC_CREDIT_ORDER}
    custom_lines: list[str] = []
    for line in metadata:
        if not line.strip():
            continue
        parsed = _parse_lyric_info_label(line)
        if not parsed:
            custom_lines.append(line.strip())
            continue
        kind, key, value = parsed
        if not value:
            continue
        if kind == "tag" or kind == "track":
            values = tag_values.get(key)
            if values is not None:
                values.append(value)
        elif kind == "credit":
            values = credit_values.get(key)
            if values is not None:
                values.append(value)
        else:
            if line.strip().startswith("[") and line.strip().endswith("]"):
                custom_lines.append(line.strip())
            else:
                custom_lines.append(f"{key}{separator}{value}")

    output: list[str] = []
    for key in LYRIC_INFO_TAG_ORDER:
        unique_values = list(dict.fromkeys(tag_values[key]))
        output.extend(f"{tag_labels[key]}{separator}{value}" for value in unique_values)
    for key in LYRIC_CREDIT_ORDER:
        unique_values = list(dict.fromkeys(credit_values[key]))
        if unique_values:
            joined_values = "、".join(unique_values) if language == "zh" else ", ".join(unique_values)
            output.append(f"{credit_labels[key]}{separator}{joined_values}")
    output.extend(dict.fromkeys(custom_lines))
    return output


def _normalize_lyrics_content(
    content: str,
    candidate: dict,
    audio_metadata: dict,
    metadata_language: str = "zh",
) -> tuple[list[str], list[str], list[str], bool]:
    lyrics, times, metadata, has_timeline = parse_lyrics_content(content)
    normalized_lyrics: list[str] = []
    normalized_times: list[str] = []
    has_timed_lyrics = False
    header_prefix_open = True
    for index, lyric in enumerate(lyrics):
        is_info = _is_timed_lyric_info_line(lyric)
        if (
            lyric.strip()
            and not is_info
            and header_prefix_open
            and LABELED_INFO_LINE.match(lyric)
        ):
            is_info = True
        if lyric.strip() and is_info:
            metadata.append(lyric.strip())
            continue
        if lyric.strip():
            header_prefix_open = False
        normalized_lyrics.append(lyric)
        normalized_times.append(times[index] if index < len(times) else "00:00.000")
        has_timed_lyrics = has_timed_lyrics or bool(lyric.strip())

    merged_metadata = _merge_online_lyrics_metadata(metadata, candidate, audio_metadata)
    formatted_metadata = _canonicalize_lyrics_metadata(merged_metadata, metadata_language)
    audio_composer = str(audio_metadata.get("composer") or "").strip()
    has_composer = any(
        (parsed := _parse_lyric_info_label(line))
        and parsed[0] == "credit"
        and parsed[1] == "作曲"
        and parsed[2]
        for line in formatted_metadata
    )
    if audio_composer and not has_composer:
        formatted_metadata = _canonicalize_lyrics_metadata(
            [*formatted_metadata, f"作曲：{audio_composer}"],
            metadata_language,
        )

    return (
        normalized_lyrics,
        normalized_times,
        formatted_metadata,
        has_timeline and has_timed_lyrics,
    )


def _render_lyrics_content(
    lyrics: list[str],
    times: list[str],
    metadata: list[str],
    has_timeline: bool,
) -> str:
    output = _metadata_header_lines(metadata)
    if output:
        output.append("")
    if has_timeline:
        output.extend(
            f"[{times[index] if index < len(times) else '00:00.000'}]{lyric}"
            if lyric
            else ""
            for index, lyric in enumerate(lyrics)
        )
    else:
        output.extend(lyrics)
    return "\n".join(output) + "\n"


def parse_timed_lrc_reference(path: Path) -> list[tuple[str, str]]:
    """Read timed lyric rows for comparison, omitting title and credit rows."""
    content = _read_lyrics_file(path)
    rows: list[tuple[str, str]] = []
    for line in content.splitlines():
        matches = list(TIME_TAG.finditer(line))
        if not matches:
            continue
        lyric = TIME_TAG.sub("", line).strip()
        if not lyric or is_header_line(lyric):
            continue
        for match in matches:
            minutes, seconds, fraction = match.groups()
            milliseconds = int((fraction or "0").ljust(3, "0"))
            timestamp = f"{int(minutes):02d}:{int(seconds):02d}.{milliseconds:03d}"
            rows.append((lyric, timestamp))
    return rows


def _comparison_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold(), flags=re.UNICODE)


class PygameAudioPlayer:
    """Small in-process player with seeking support for common audio formats."""

    def __init__(self) -> None:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError("音频播放组件尚未安装。请重新运行 InstallAutoLrc.cmd。") from exc
        self._pygame = pygame
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        except pygame.error as exc:
            raise RuntimeError(f"无法初始化音频设备：{exc}") from exc
        self.duration_ms = 0
        self._loaded = False
        self._playing = False
        self._paused = False
        self._start_offset_ms = 0
        self._paused_position_ms = 0
        self._stopped_position_ms = 0

    @staticmethod
    def _read_duration_ms(path: Path) -> int:
        try:
            duration = float(read_track_metadata(path).get("duration") or 0)
            if duration > 0:
                return round(duration * 1_000)
        except Exception:
            pass
        try:
            import torchaudio

            info = torchaudio.info(str(path))
            if info.sample_rate and info.num_frames:
                return round(info.num_frames * 1_000 / info.sample_rate)
        except Exception:
            pass
        try:
            import librosa

            return round(librosa.get_duration(path=str(path)) * 1_000)
        except Exception:
            return 0

    def open(self, path: Path) -> int:
        self.close()
        try:
            self._pygame.mixer.music.load(str(path))
        except self._pygame.error as exc:
            raise RuntimeError(f"播放器无法读取此音频文件：{exc}") from exc
        self._loaded = True
        self.duration_ms = self._read_duration_ms(path)
        return self.duration_ms

    def play_from(self, position_ms: int) -> None:
        if not self._loaded:
            raise RuntimeError("请先选择音频文件。")
        bounded = max(0, int(position_ms))
        if self.duration_ms:
            bounded = min(bounded, self.duration_ms)
        try:
            self._pygame.mixer.music.play(start=bounded / 1_000)
        except self._pygame.error as exc:
            raise RuntimeError(f"无法从该时间点播放音频：{exc}") from exc
        self._start_offset_ms = bounded
        self._paused_position_ms = bounded
        self._stopped_position_ms = bounded
        self._playing = True
        self._paused = False

    def pause(self) -> None:
        if self._playing and not self._paused:
            self._paused_position_ms = self.position_ms()
            self._pygame.mixer.music.pause()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._pygame.mixer.music.unpause()
            self._paused = False
            self._playing = True

    def stop(self) -> None:
        if self._loaded:
            self._pygame.mixer.music.stop()
        self._playing = False
        self._paused = False
        self._start_offset_ms = 0
        self._paused_position_ms = 0
        self._stopped_position_ms = 0

    def seek_to(self, position_ms: int) -> None:
        bounded = max(0, int(position_ms))
        if self.duration_ms:
            bounded = min(bounded, self.duration_ms)
        mode = self.mode()
        if mode == "playing":
            self.play_from(bounded)
        elif mode == "paused":
            self.play_from(bounded)
            self.pause()
        else:
            self._stopped_position_ms = bounded

    def position_ms(self) -> int:
        if self._paused:
            return self._paused_position_ms
        if self._playing:
            elapsed = self._pygame.mixer.music.get_pos()
            if elapsed >= 0:
                position = self._start_offset_ms + elapsed
                return min(position, self.duration_ms) if self.duration_ms else position
            if not self._pygame.mixer.music.get_busy():
                self._playing = False
                self._stopped_position_ms = self.duration_ms or self._start_offset_ms
        return self._stopped_position_ms

    def mode(self) -> str:
        if self._paused:
            return "paused"
        if self._playing and self._pygame.mixer.music.get_busy():
            return "playing"
        if self._playing:
            self._playing = False
            self._stopped_position_ms = self.duration_ms or self._start_offset_ms
        return "stopped"

    def close(self) -> None:
        if self._loaded:
            self._pygame.mixer.music.stop()
            self._pygame.mixer.music.unload()
        self._loaded = False
        self.duration_ms = 0
        self.stop()


class DesktopAligner:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Lyrics 本地音乐歌词工作站")
        self.root.geometry("1180x760")
        self.root.minsize(900, 620)
        self.root.configure(bg=APP_BACKGROUND)
        self._native_window_icons: dict[str, tuple[int, int]] = {}

        self.audio_path = tk.StringVar()
        self.metadata_audio_path = tk.StringVar()
        self.metadata_values = {
            key: tk.StringVar()
            for key in (
                "title",
                "artist",
                "album",
                "album_artist",
                "year",
                "genre",
                "track_number",
                "composer",
                "comment",
            )
        }
        self.metadata_lyrics_hint = tk.StringVar(
            value="打开音频后可查看或编辑内嵌歌词；LRC 时间戳会保留。"
        )
        self._metadata_cover_data: bytes | None = None
        self._metadata_selected_cover: bytes | None = None
        self._metadata_selected_cover_name = ""
        self._metadata_remove_cover = False
        self._metadata_cover_photo = None
        self._metadata_controls: list[tk.Widget] = []
        self._metadata_action_buttons: list[RoundedButton] = []
        self._metadata_form_wheel_remainder = 0.0
        self._metadata_comment_wheel_remainder = 0.0
        self._metadata_lyrics_wheel_remainder = 0.0
        self._metadata_form_parent_scroll_lock: dict | None = None
        self._metadata_tabs_wheel_remainder = 0.0
        self._metadata_documents: list[dict] = []
        self._active_metadata_document_id: int | None = None
        self._next_metadata_document_id = 1
        self._metadata_tab_controls: list[tk.Button] = []
        self._metadata_tab_widgets: dict[int, dict] = {}
        self._metadata_tabs_order: tuple[int, ...] | None = None
        self._metadata_tabs_scrollregion_after: str | None = None
        self._metadata_form_scrollregion_after: str | None = None
        self._metadata_tabs_window_width: int | None = None
        self._metadata_tabs_window_height: int | None = None
        self._metadata_form_window_width: int | None = None
        self._busy = False
        self._operation_busy = False
        self._training_busy = False
        self.status_text = tk.StringVar(value="选择音频并输入或导入歌词，即可生成时间轴草稿。")
        self.current_times: list[str] = []
        self.imported_lrc_has_timeline = False
        self.draft_times: list[str] | None = None
        self._embedded_audio_exported_source: Path | None = None
        self._embedded_audio_exported_output: Path | None = None
        self._editor_lyrics_snapshot: list[str] = []
        self.row_results: list[dict] = []
        self.metadata: list[str] = []
        self._active_lyric_index: int | None = None
        self._preview_row_index: int | None = None
        self.manual_problem_rows: set[int] = set()
        self.pending_review_rows: set[int] = set()
        self.checked_rows: set[int] = set()
        self.reference_times: dict[int, str] = {}
        self.reference_path: Path | None = None
        self.reference_audio_path: Path | None = None
        self.boundary_checkpoint: Path | None = None
        default_training_data = ROOT_DIR / "training data"
        self.training_data_directory: Path | None = default_training_data if default_training_data.is_dir() else None
        default_input_directory = ROOT_DIR / "input"
        self.input_directory = default_input_directory if default_input_directory.is_dir() else ROOT_DIR
        self.output_directory = ROOT_DIR / "output"
        default_cover_directory = ROOT_DIR / "covers"
        try:
            default_cover_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            default_cover_directory = ROOT_DIR
        self.cover_image_directory = default_cover_directory
        self.feedback_session: dict | None = None
        self._training_process: subprocess.Popen | None = None
        self.vocalize_enabled = tk.BooleanVar(value=False)
        self.demucs_model_terms_acknowledged = False
        self.show_playback_milliseconds = tk.BooleanVar(value=False)
        self.lyric_draft_panel_height = tk.StringVar(
            value=str(DEFAULT_LYRIC_DRAFT_PANEL_HEIGHT)
        )
        self.wheel_handoff_cooldown_seconds = tk.StringVar(
            value=f"{DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS:.1f}"
        )
        self.root._wheel_handoff_cooldown_seconds = DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS
        self.device_mode = tk.StringVar(value="自动")
        self.lyric_template_language = tk.StringVar(value="zh")
        self._text_refresh_pending = False
        self._suppress_lyrics_modified = False
        self._time_editor: ttk.Entry | None = None
        self._player: PygameAudioPlayer | None = None
        self._player_path: Path | None = None
        self._player_state = "stopped"
        self._player_loading = False
        self._player_loading_path: Path | None = None
        self._player_load_token = 0
        self._pending_player_action = None
        self._player_poll_id: str | None = None
        self._seeking = False
        self.settings_file = APP_DATA_DIR / "settings.json"
        self.model_preset_directory = ROOT_DIR / "model-presets"
        self._model_preset_paths: dict[str, Path] = {}
        self.model_preset_selection = tk.StringVar(value="")
        self.theme = "light"
        self._settings_save_after: str | None = None
        self._settings_were_loaded = False
        self.player_position = tk.DoubleVar(value=0.0)
        self.player_time_text = tk.StringVar(value="00:00 / 00:00")
        self.player_note_text = tk.StringVar(value="点击“播放 / 继续”或“暂停”控制音乐；双击歌词行试听。")

        self._load_settings()
        self._apply_wheel_handoff_cooldown()
        _activate_theme(self.theme)
        self._apply_window_icon()
        self._configure_style()
        self._build_window()
        self.root.after_idle(self._apply_window_frame_theme)
        self.root.after(120, self._apply_window_frame_theme)
        self.root.after_idle(self._apply_native_window_icon)
        self.root.after(160, self._apply_native_window_icon)
        for setting_variable in (
            self.audio_path,
            self.device_mode,
            self.vocalize_enabled,
            self.show_playback_milliseconds,
            self.lyric_draft_panel_height,
            self.wheel_handoff_cooldown_seconds,
            self.lyric_template_language,
        ):
            setting_variable.trace_add("write", self._queue_settings_save)
        self.lyric_draft_panel_height.trace_add(
            "write", self._apply_lyric_draft_panel_height
        )
        self._apply_lyric_draft_panel_height()
        self.wheel_handoff_cooldown_seconds.trace_add(
            "write", self._apply_wheel_handoff_cooldown
        )
        self.show_playback_milliseconds.trace_add("write", self._refresh_player_time_display)
        self.player_position.trace_add("write", self._refresh_player_time_display)
        self._refresh_player_time_display()
        self.audio_path.trace_add("write", self._invalidate_feedback_for_audio_change)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind_all("<Control-s>", self._handle_audio_save_shortcut, add="+")
        self.root.bind_all("<Control-S>", self._handle_audio_save_shortcut, add="+")
        if self._settings_were_loaded:
            self.status_text.set(
                "已恢复上次的音频、运行选项和窗口布局。"
                + ("当前使用本地训练的边界模型。" if self.boundary_checkpoint else "")
            )

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        background = APP_BACKGROUND
        card = CARD_BACKGROUND
        input_background = INPUT_BACKGROUND
        text = TEXT_PRIMARY
        secondary = TEXT_SECONDARY
        style.configure(".", background=background, foreground=text, font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=background)
        style.configure("TLabel", background=background, foreground=text)
        style.configure("TLabelframe", background=background, foreground=text)
        style.configure("TLabelframe.Label", background=background, foreground=text)
        style.configure("TEntry", fieldbackground=input_background, foreground=text)
        style.configure("TCombobox", fieldbackground=input_background, foreground=text)
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", input_background), ("focus", card)],
            foreground=[("readonly", text)],
        )
        scrollbar_options = {
            "background": ui_color("scrollbar_background"),
            "troughcolor": ui_color("scrollbar_trough_background"),
            "bordercolor": ui_color("border"),
            "lightcolor": ui_color("scrollbar_background"),
            "darkcolor": ui_color("border"),
            "arrowcolor": ui_color("scrollbar_arrow_foreground"),
        }
        for scrollbar_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(scrollbar_style, **scrollbar_options)
            style.map(
                scrollbar_style,
                background=[
                    ("pressed", ui_color("scrollbar_hover_background")),
                    ("active", ui_color("scrollbar_hover_background")),
                ],
                arrowcolor=[("disabled", secondary)],
            )
        style.configure("TScale", background=card, troughcolor=ui_color("status_background"))
        style.configure("TProgressbar", background=ACCENT_COLOR, troughcolor=ui_color("status_background"), bordercolor=ui_color("status_background"))
        style.configure("App.TFrame", background=APP_BACKGROUND)
        style.configure("Card.TFrame", background=CARD_BACKGROUND)
        style.configure("InputSurface.TFrame", background=INPUT_BACKGROUND)
        style.configure("Title.TLabel", background=APP_BACKGROUND, foreground=TEXT_PRIMARY, font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background=APP_BACKGROUND, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 10))
        style.configure("Eyebrow.TLabel", background=APP_BACKGROUND, foreground=ACCENT_COLOR, font=("Microsoft YaHei UI", 8, "bold"))
        style.configure("Section.TLabel", background=APP_BACKGROUND, foreground=ui_color("section_text"), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("ToolbarLabel.TLabel", background=APP_BACKGROUND, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 9))
        style.configure("CardTitle.TLabel", background=CARD_BACKGROUND, foreground=ui_color("card_text"), font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Hint.TLabel", background=CARD_BACKGROUND, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 9))
        style.configure("Setting.TLabel", background=CARD_BACKGROUND, foreground=TEXT_SECONDARY, font=("Microsoft YaHei UI", 9))
        style.configure("PlayerTime.TLabel", background=CARD_BACKGROUND, foreground=TEXT_SECONDARY, font=("Consolas", 9))
        style.configure(
            "Card.TCheckbutton",
            background=CARD_BACKGROUND,
            foreground=ui_color("card_text"),
            font=("Microsoft YaHei UI", 9),
            padding=(2, 4),
            indicatorsize=15,
            indicatorcolor=INPUT_BACKGROUND,
            bordercolor=BORDER_COLOR,
            lightcolor=BORDER_COLOR,
            darkcolor=BORDER_COLOR,
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", CARD_BACKGROUND)],
            foreground=[("disabled", secondary)],
            indicatorcolor=[
                ("disabled", ui_color("button_disabled_background")),
                ("selected", ACCENT_COLOR),
                ("active", ui_color("button_hover_background")),
                ("!selected", INPUT_BACKGROUND),
            ],
            bordercolor=[("focus", ACCENT_COLOR), ("!focus", BORDER_COLOR)],
        )
        style.configure("Card.TRadiobutton", background=CARD_BACKGROUND, foreground=ui_color("card_text"), font=("Microsoft YaHei UI", 9))
        style.map("Card.TRadiobutton", background=[("active", CARD_BACKGROUND)], foreground=[("disabled", secondary)])
        style.configure(
            "TemplatePreview.TLabel",
            background=INPUT_BACKGROUND,
            foreground=ui_color("card_text"),
            font=("Consolas", 10),
            padding=(10, 8),
        )
        style.configure("Soft.TEntry", padding=(9, 7), fieldbackground=INPUT_BACKGROUND, foreground=TEXT_PRIMARY, borderwidth=0, relief="flat", bordercolor=INPUT_BACKGROUND, lightcolor=INPUT_BACKGROUND, darkcolor=INPUT_BACKGROUND)
        style.map("Soft.TEntry", fieldbackground=[("readonly", INPUT_BACKGROUND), ("focus", CARD_BACKGROUND)], foreground=[("disabled", secondary)])
        style.configure(
            "Soft.TCombobox",
            padding=(8, 5),
            background=CARD_BACKGROUND,
            fieldbackground=INPUT_BACKGROUND,
            foreground=TEXT_PRIMARY,
            bordercolor=BORDER_COLOR,
            lightcolor=CARD_BACKGROUND,
            darkcolor=BORDER_COLOR,
            arrowcolor=TEXT_SECONDARY,
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "Soft.TCombobox",
            fieldbackground=[("readonly", INPUT_BACKGROUND), ("focus", INPUT_BACKGROUND)],
            foreground=[("readonly", TEXT_PRIMARY)],
            bordercolor=[("focus", ACCENT_COLOR), ("!focus", BORDER_COLOR)],
            arrowcolor=[("disabled", secondary), ("active", TEXT_PRIMARY)],
        )
        style.configure(
            "Soft.TSpinbox",
            padding=(8, 5),
            background=CARD_BACKGROUND,
            fieldbackground=INPUT_BACKGROUND,
            foreground=TEXT_PRIMARY,
            arrowsize=12,
            arrowcolor=TEXT_SECONDARY,
            bordercolor=BORDER_COLOR,
            lightcolor=CARD_BACKGROUND,
            darkcolor=BORDER_COLOR,
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "Soft.TSpinbox",
            fieldbackground=[("readonly", INPUT_BACKGROUND), ("focus", INPUT_BACKGROUND)],
            foreground=[("disabled", secondary)],
            bordercolor=[("focus", ACCENT_COLOR), ("!focus", BORDER_COLOR)],
            arrowcolor=[("disabled", secondary), ("active", TEXT_PRIMARY)],
        )
        style.configure("Status.TFrame", background=APP_BACKGROUND)
        style.configure("Status.TLabel", background=APP_BACKGROUND, foreground=ui_color("status_foreground"), font=("Microsoft YaHei UI", 9))
        style.configure(
            "Treeview",
            rowheight=34,
            font=("Microsoft YaHei UI", 9),
            borderwidth=0,
            relief="flat",
            bordercolor=INPUT_BACKGROUND,
            lightcolor=INPUT_BACKGROUND,
            darkcolor=INPUT_BACKGROUND,
            background=INPUT_BACKGROUND,
            fieldbackground=INPUT_BACKGROUND,
            foreground=ui_color("card_text"),
        )
        style.map(
            "Treeview",
            background=[("selected", ui_color("selected_background"))],
            foreground=[("selected", TEXT_PRIMARY)],
            bordercolor=[("focus", INPUT_BACKGROUND)],
        )
        # The Clam theme adds a bright outer field around Treeview; draw only its content area.
        try:
            style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            pass
        style.configure(
            "Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            background=ui_color("tree_heading_background"),
            foreground=ui_color("tree_heading_foreground"),
            padding=(10, 8),
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", ui_color("nav_hover_background"))])

    def _apply_window_frame_theme(self) -> None:
        if sys.platform != "win32":
            return
        try:
            dwm = ctypes.WinDLL("dwmapi")
            user32 = ctypes.WinDLL("user32")
            set_attribute = dwm.DwmSetWindowAttribute
            set_attribute.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            set_attribute.restype = ctypes.c_long
            set_window_pos = user32.SetWindowPos
            set_window_pos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            set_window_pos.restype = ctypes.c_int

            build = sys.getwindowsversion().build
            dark_mode = ctypes.c_int(self.theme == "dark")
            windows = [self.root]
            windows.extend(
                widget
                for widget in self.root.winfo_children()
                if isinstance(widget, tk.Toplevel)
            )

            def colorref(hex_color: str) -> ctypes.c_uint32:
                red, green, blue = (
                    int(hex_color[index:index + 2], 16)
                    for index in (1, 3, 5)
                )
                return ctypes.c_uint32(red | (green << 8) | (blue << 16))

            for window in windows:
                try:
                    frame_handle = str(window.tk.call("wm", "frame", window._w))
                    handle_value = int(frame_handle, 0)
                except (AttributeError, tk.TclError, ValueError):
                    handle_value = window.winfo_id()
                hwnd = ctypes.c_void_p(handle_value)

                # Windows builds have used both attribute IDs for immersive dark mode.
                for attribute in (20, 19):
                    set_attribute(
                        hwnd,
                        attribute,
                        ctypes.byref(dark_mode),
                        ctypes.sizeof(dark_mode),
                    )
                if build >= 22000:
                    for attribute, color_name in (
                        (34, "window_frame_background"),
                        (35, "window_frame_background"),
                        (36, "window_frame_foreground"),
                    ):
                        color = colorref(ui_color(color_name))
                        set_attribute(hwnd, attribute, ctypes.byref(color), ctypes.sizeof(color))

                # Ask Windows to repaint the non-client area after changing DWM attributes.
                set_window_pos(hwnd, None, 0, 0, 0, 0, 0x0037)
        except Exception:
            pass

    def _configure_table_tags(self) -> None:
        if not hasattr(self, "table"):
            return
        self.table.tag_configure(
            "review",
            foreground=ui_color("table_review_foreground"),
            background=ui_color("table_review_background"),
        )
        self.table.tag_configure(
            "pending",
            foreground=ui_color("table_pending_foreground"),
            background=ui_color("table_pending_background"),
        )
        self.table.tag_configure(
            "checked",
            foreground=ui_color("table_checked_foreground"),
            background=ui_color("table_checked_background"),
        )
        self.table.tag_configure("skipped", foreground=ui_color("table_skipped_foreground"))
        self.table.tag_configure(
            "problem",
            foreground=ui_color("table_problem_foreground"),
            background=ui_color("table_problem_background"),
        )
        self.table.tag_configure(
            "active",
            background=ui_color("table_active_background"),
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _show_legal_notice(self) -> None:
        messagebox.showinfo(
            "关于 Lyrics / 软件许可",
            "Lyrics 本地音乐歌词工作站\n"
            "Copyright (C) 2026 DRAMINGLING\n\n"
            "Lyrics 原创代码采用 GNU GPL-3.0-or-later。你可以按该许可条件复制、修改和再发布。\n"
            "本程序不提供任何担保。\n\n"
            "完整许可证文本见程序目录中的 LICENSE；第三方代码与模型条款见 THIRD-PARTY-NOTICES.md。\n"
            "对应源代码和发布记录见 GitHub 项目：\n"
            "https://github.com/DRAMINGLING/Lyrics-Local-Integrated-Music-Workstation",
            parent=self.root,
        )

    def _build_window(self) -> None:
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.shell = ttk.Frame(self.root, style="App.TFrame")
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.rowconfigure(0, weight=1)
        self.shell.columnconfigure(1, weight=1)

        self.sidebar = tk.Frame(self.shell, width=152, background=SIDEBAR_BACKGROUND)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        brand = tk.Frame(self.sidebar, background=SIDEBAR_BACKGROUND)
        brand.pack(fill="x", padx=14, pady=(20, 18))
        tk.Label(
            brand,
            text="A",
            background=ACCENT_COLOR,
            foreground=ui_color("accent_foreground"),
            font=("Microsoft YaHei UI", 12, "bold"),
            width=3,
            height=1,
        ).pack(side="left", padx=(0, 9))
        brand_text = tk.Frame(brand, background=SIDEBAR_BACKGROUND)
        brand_text.pack(side="left", fill="x", expand=True)
        tk.Label(
            brand_text,
            text="AUTO-LRC",
            background=SIDEBAR_BACKGROUND,
            foreground=TEXT_PRIMARY,
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            brand_text,
            text="本地音频工作台",
            background=SIDEBAR_BACKGROUND,
            foreground=TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))
        tk.Frame(self.sidebar, height=1, background=BORDER_COLOR).pack(fill="x", padx=12, pady=(0, 13))
        self.nav_buttons: dict[str, tuple[tk.Frame, tk.Button, tk.Frame]] = {}
        for page_name, label in (
            ("autolrc", "Auto-LRC"),
            ("audio", "音频编辑"),
            ("settings", "设置"),
        ):
            nav_item = tk.Frame(self.sidebar, background=SIDEBAR_BACKGROUND, height=42)
            nav_item.pack(fill="x", padx=8, pady=3)
            nav_item.pack_propagate(False)
            indicator = tk.Frame(nav_item, width=3, background=SIDEBAR_BACKGROUND)
            indicator.pack(side="left", fill="y", padx=(0, 5))
            button = tk.Button(
                nav_item,
                text=label,
                command=lambda target=page_name: self._show_page(target),
                background=SIDEBAR_BACKGROUND,
                foreground=ui_color("nav_foreground"),
                activebackground=ui_color("nav_selected_background"),
                activeforeground=ui_color("nav_selected_foreground"),
                anchor="w",
                relief="flat",
                bd=0,
                padx=10,
                pady=8,
                font=("Microsoft YaHei UI", 10),
                cursor="hand2",
            )
            button.pack(side="left", fill="both", expand=True)
            button.bind("<Enter>", lambda _event, item=nav_item, nav_button=button, key=page_name: (
                item.configure(background=ui_color("nav_selected_background") if self._active_page == key else ui_color("nav_hover_background")),
                nav_button.configure(background=ui_color("nav_selected_background") if self._active_page == key else ui_color("nav_hover_background")),
            ))
            button.bind("<Leave>", lambda _event, item=nav_item, nav_button=button, key=page_name: (
                item.configure(background=ui_color("nav_selected_background") if self._active_page == key else SIDEBAR_BACKGROUND),
                nav_button.configure(background=ui_color("nav_selected_background") if self._active_page == key else SIDEBAR_BACKGROUND),
            ))
            self.nav_buttons[page_name] = (nav_item, button, indicator)

        self.theme_button = RoundedButton(
            self.sidebar,
            "切换浅色模式" if self.theme == "dark" else "切换深色模式",
            self._toggle_theme,
            background=SIDEBAR_BACKGROUND,
        )
        self.theme_button.pack(side="bottom", fill="x", padx=10, pady=12)
        self.about_button = RoundedButton(
            self.sidebar,
            "关于 / 软件许可",
            self._show_legal_notice,
            background=SIDEBAR_BACKGROUND,
        )
        self.about_button.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        self.page_container = ttk.Frame(self.shell, style="App.TFrame")
        self.page_container.grid(row=0, column=1, sticky="nsew")
        self.page_container.rowconfigure(0, weight=1)
        self.page_container.columnconfigure(0, weight=1)
        self.audio_editor_page = ttk.Frame(self.page_container, style="App.TFrame")
        self.audio_editor_page.grid(row=0, column=0, sticky="nsew")
        self.autolrc_page = ttk.Frame(self.page_container, style="App.TFrame")
        self.autolrc_page.grid(row=0, column=0, sticky="nsew")
        self.settings_page = ttk.Frame(self.page_container, style="App.TFrame")
        self.settings_page.grid(row=0, column=0, sticky="nsew")
        self._build_audio_editor_page()
        self._build_settings_page()
        self._build_player_footer()

        self.autolrc_scrollable_page = ScrollablePage(self.autolrc_page)
        self.autolrc_scrollable_page.pack(fill="both", expand=True)
        page = ttk.Frame(self.autolrc_scrollable_page.content, style="App.TFrame", padding=18)
        page.pack(fill="x")

        header = ttk.Frame(page, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text="AUTO-LRC  /  本机工作流", style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 3))
        ttk.Label(header, text="本机歌词自动打轴", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="选择音频与歌词，生成并检查时间轴草稿。人工检查后可把修正用于本机模型训练；音频不会上传。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        audio_panel = RoundedPanel(page, padding=(12, 10), radius=15)
        audio_panel.pack(fill="x", pady=(0, 12))
        audio_card = audio_panel.content
        audio_row = ttk.Frame(audio_card, style="Card.TFrame")
        audio_row.pack(fill="x")
        ttk.Label(audio_row, text="音频文件", style="CardTitle.TLabel").pack(side="left", padx=(0, 12))
        self.audio_entry = ttk.Entry(audio_row, textvariable=self.audio_path, style="Soft.TEntry")
        self.audio_entry.pack(side="left", fill="x", expand=True)
        self.audio_button = RoundedButton(
            audio_row,
            "选择音频…",
            self._choose_audio,
            background=CARD_BACKGROUND,
        )
        self.audio_button.pack(side="left", padx=(8, 0))
        self.embedded_lyrics_button = RoundedButton(
            audio_row,
            "读取内嵌歌词",
            self._import_embedded_lyrics,
            background=CARD_BACKGROUND,
        )
        self.embedded_lyrics_button.pack(side="left", padx=(8, 0))
        self.online_lyrics_button = RoundedButton(
            audio_row,
            "在线搜索歌词…",
            self._open_online_lyrics_search,
            background=CARD_BACKGROUND,
        )
        self.online_lyrics_button.pack(side="left", padx=(8, 0))

        toolbar = ttk.Frame(page, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        self.import_button = RoundedButton(toolbar, "导入歌词 / LRC…", self._import_lyrics)
        self.import_button.pack(side="left")
        self.align_button = RoundedButton(toolbar, "生成时间轴草稿", self._align, accent=True)
        self.align_button.pack(side="left", padx=8)
        self.realign_problem_button = RoundedButton(
            toolbar,
            "重打标记段",
            self._realign_problem_segments,
        )
        self.realign_problem_button.pack(side="left", padx=(0, 8))
        self.realign_problem_button.configure(state="disabled")
        self.export_button = RoundedButton(toolbar, "导出 LRC…", self._export_lrc)
        self.export_button.pack(side="right")
        self.embedded_export_button = RoundedButton(
            toolbar,
            "导出内嵌歌词音频",
            self._export_embedded_audio,
        )
        self.embedded_export_button.pack(side="right", padx=(0, 8))
        self.embedded_export_button.configure(state="disabled")
        self.delete_audio_button = RoundedButton(
            toolbar,
            "删除当前音频",
            self._delete_current_audio,
        )
        self.delete_audio_button.pack(side="right", padx=(0, 8))
        self.delete_audio_button.configure(state="disabled")

        training_toolbar = ttk.Frame(page, style="App.TFrame")
        training_toolbar.pack(fill="x", pady=(0, 10))
        review_actions = ttk.Frame(training_toolbar, style="App.TFrame")
        review_actions.pack(side="left", fill="x", expand=True)
        ttk.Label(review_actions, text="人工校准", style="ToolbarLabel.TLabel").pack(
            side="left", padx=(0, 10), pady=(0, 1)
        )
        self.reference_button = RoundedButton(review_actions, "对照人工 LRC…", self._import_reference_lrc)
        self.reference_button.pack(side="left", padx=(0, 8))
        self.feedback_button = RoundedButton(
            review_actions,
            "确认检查并学习",
            self._save_review_feedback,
        )
        self.feedback_button.pack(side="left", padx=(0, 8))
        self.feedback_button.configure(state="disabled")

        split = ttk.Frame(page, style="App.TFrame")
        self.lyric_draft_split = split
        split.configure(height=self._lyric_draft_panel_height_value())
        split.grid_propagate(False)
        split.pack(fill="x", expand=False)
        left_weight = 42
        split.columnconfigure(0, weight=left_weight)
        split.columnconfigure(1, weight=100 - left_weight)
        split.rowconfigure(0, weight=1)

        lyrics_panel = RoundedPanel(split, padding=12, radius=16)
        results_panel = RoundedPanel(split, padding=12, radius=16)
        lyrics_card = lyrics_panel.content
        results_card = results_panel.content
        lyrics_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        results_panel.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        lyrics_heading = ttk.Frame(lyrics_card, style="Card.TFrame")
        lyrics_heading.pack(fill="x")
        ttk.Label(lyrics_heading, text="歌词（每行一句）", style="CardTitle.TLabel").pack(side="left", anchor="w")
        self.lyrics_hint_expanded = False
        self.lyrics_hint_button = tk.Button(
            lyrics_heading,
            text="展开说明  +",
            command=lambda: self._toggle_editor_hint("lyrics"),
            background=CARD_BACKGROUND,
            foreground=TEXT_SECONDARY,
            activebackground=ui_color("nav_hover_background"),
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=7,
            pady=2,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        )
        self.lyrics_hint_button.pack(side="right")
        self.lyrics_hint = ttk.Label(
            lyrics_card,
            text="支持中文、日文、韩文、英文和俄文。导入的曲目信息会显示在编辑区顶部，但不会进入右侧草稿；编辑区开头的标题格式行及作词/作曲/歌手等信息会自动识别。自定义信息请用“标签：内容”并与第一句歌词间留一空行。导出时曲目信息会匀速排在首句歌词前，不改变歌词时间。",
            style="Hint.TLabel",
            wraplength=320,
            justify="left",
        )
        self.lyrics_hint.bind("<Configure>", self._fit_hint_label, add="+")
        lyrics_input = RoundedPanel(
            lyrics_card,
            padding=5,
            radius=11,
            surface=INPUT_BACKGROUND,
            outer_background=CARD_BACKGROUND,
            content_style="InputSurface.TFrame",
        )
        self.lyrics_input_panel = lyrics_input
        lyrics_input.pack(fill="both", expand=True)
        lyrics_body = lyrics_input.content
        self.lyrics_text = tk.Text(
            lyrics_body,
            wrap="word",
            undo=True,
            font=("Microsoft YaHei UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=8,
            background=INPUT_BACKGROUND,
            foreground=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            selectbackground=ui_color("selected_background"),
            selectforeground=TEXT_PRIMARY,
        )
        self.lyrics_text.tag_configure(
            "metadata",
            foreground=ACCENT_COLOR,
            background=ui_color("lyrics_metadata_background"),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        lyrics_scroll = ttk.Scrollbar(lyrics_body, orient="vertical", command=self.lyrics_text.yview)
        self.lyrics_text.configure(yscrollcommand=lyrics_scroll.set)
        self.lyrics_text.pack(side="left", fill="both", expand=True)
        lyrics_scroll.pack(side="right", fill="y")
        self.lyrics_text.bind("<<Modified>>", self._on_lyrics_modified)

        draft_heading = ttk.Frame(results_card, style="Card.TFrame")
        draft_heading.pack(fill="x")
        ttk.Label(draft_heading, text="时间轴草稿", style="CardTitle.TLabel").pack(side="left", anchor="w")
        self.draft_hint_expanded = False
        self.draft_hint_button = tk.Button(
            draft_heading,
            text="展开说明  +",
            command=lambda: self._toggle_editor_hint("draft"),
            background=CARD_BACKGROUND,
            foreground=TEXT_SECONDARY,
            activebackground=ui_color("nav_hover_background"),
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=7,
            pady=2,
            font=("Microsoft YaHei UI", 8),
            cursor="hand2",
        )
        self.draft_hint_button.pack(side="right")
        self.draft_hint = ttk.Label(
            results_card,
            text="双击歌词试听、双击时间修正；双击人工对照时间可应用到草稿。中键标记问题行后重打；黄色待检查行可中键确认为已检查。",
            style="Hint.TLabel",
            wraplength=480,
        )
        self.draft_hint.bind("<Configure>", self._fit_draft_hint, add="+")

        self.reason_label = ttk.Label(
            results_card,
            text="",
            style="Hint.TLabel",
            wraplength=400,
            justify="left",
        )
        self.reason_label.bind("<Configure>", self._fit_hint_label, add="+")

        table_panel = RoundedPanel(
            results_card,
            padding=3,
            radius=11,
            surface=INPUT_BACKGROUND,
            outer_background=CARD_BACKGROUND,
            content_style="InputSurface.TFrame",
        )
        self.table_panel = table_panel
        table_panel.pack(fill="both", expand=True)
        table_frame = ttk.Frame(table_panel.content, style="InputSurface.TFrame")
        table_frame.pack(fill="both", expand=True)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ("line", "lyric", "time", "status", "reference")
        self.table = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Treeview",
        )
        self.table.heading("line", text="#")
        self.table.heading("lyric", text="歌词")
        self.table.heading("time", text="时间（双击编辑）")
        self.table.heading("status", text="状态")
        self.table.heading("reference", text="人工时间 / 偏差")
        self.table.column("line", width=48, minwidth=42, anchor="center", stretch=False)
        self.table.column("lyric", width=220, minwidth=130)
        self.table.column("time", width=120, minwidth=110, anchor="center", stretch=False)
        self.table.column("status", width=90, minwidth=82, anchor="center", stretch=False)
        self.table.column("reference", width=155, minwidth=145, anchor="center", stretch=False)
        table_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        table_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=table_y.set, xscrollcommand=table_x.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        table_y.grid(row=0, column=1, sticky="ns")
        table_x.grid(row=1, column=0, sticky="ew")
        self._configure_table_tags()
        self.table.bind("<Double-1>", self._handle_table_double_click)
        self.table.bind("<Button-2>", self._handle_table_middle_click)
        self.table.bind("<Shift-MouseWheel>", self._scroll_table_horizontally, add="+")
        self.table.bind("<<TreeviewSelect>>", self._show_row_reason)

        footer = ttk.Frame(self.shell, style="Status.TFrame", padding=(10, 7))
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 10))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=150)
        self.progress.pack(side="right", padx=(10, 0))
        self.progress.pack_forget()
        ttk.Label(footer, textvariable=self.status_text, style="Status.TLabel").pack(side="left", fill="x", expand=True)
        self._show_page("autolrc")
        self.autolrc_scrollable_page._bind_parent_scroll_lock_to_descendants()

    def _build_player_footer(self) -> None:
        self.page_container.rowconfigure(1, weight=0)
        self.player_footer = RoundedPanel(
            self.page_container,
            padding=(10, 5),
            radius=12,
        )
        self.player_footer.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        content = self.player_footer.content

        controls = ttk.Frame(content, style="Card.TFrame")
        controls.pack(fill="x")
        self.play_button = RoundedButton(
            controls,
            "播放 / 继续",
            self._play_or_resume_playback,
            background=CARD_BACKGROUND,
        )
        self.play_button.pack(side="left")
        self.pause_button = RoundedButton(
            controls,
            "暂停",
            self._pause_playback,
            background=CARD_BACKGROUND,
        )
        self.pause_button.pack(side="left", padx=(6, 0))
        self.pause_button.configure(state="disabled")
        self.stop_button = RoundedButton(
            controls,
            "停止",
            self._stop_playback,
            background=CARD_BACKGROUND,
        )
        self.stop_button.pack(side="left", padx=(6, 12))

        self.player_scale = PlaybackSeekbar(
            controls,
            self.player_position,
            maximum=1,
            on_seek_start=self._begin_seek,
            on_seek_end=self._finish_seek,
        )
        self.player_scale.pack(side="left", fill="x", expand=True)
        ttk.Label(
            controls,
            textvariable=self.player_time_text,
            style="PlayerTime.TLabel",
            width=23,
            anchor="e",
        ).pack(side="right", padx=(10, 0))

        self.player_note_label = ttk.Label(
            content,
            textvariable=self.player_note_text,
            style="Hint.TLabel",
            wraplength=800,
            justify="left",
        )
        self.player_note_label.pack(fill="x", anchor="w", pady=(3, 0))
        self.player_note_label.bind("<Configure>", self._fit_hint_label, add="+")

    def _build_settings_page(self) -> None:
        scrollable_page = ScrollablePage(self.settings_page)
        scrollable_page.pack(fill="both", expand=True)
        page = ttk.Frame(scrollable_page.content, style="App.TFrame", padding=22)
        page.pack(fill="x")
        ttk.Label(page, text="SETTINGS  /  全局配置", style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 3))
        ttk.Label(page, text="设置", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            page,
            text="默认文件夹置顶，其他选项按用途分类。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 14))

        directory_panel = RoundedPanel(page, padding=16, radius=16)
        directory_panel.pack(fill="x", pady=(0, 14))
        directories = directory_panel.content
        ttk.Label(directories, text="默认文件夹", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.settings_input_directory = tk.StringVar(value=str(self.input_directory))
        self.settings_output_directory = tk.StringVar(value=str(self.output_directory))
        self.settings_cover_directory = tk.StringVar(value=str(self.cover_image_directory))

        def add_directory_row(title: str, variable: tk.StringVar, command, help_text: str):
            row = ttk.Frame(directories, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=title, style="Setting.TLabel", width=16).pack(side="left")
            ttk.Label(
                row,
                textvariable=variable,
                style="Hint.TLabel",
                wraplength=620,
            ).pack(side="left", fill="x", expand=True, padx=(0, 10))
            button = RoundedButton(
                row,
                "选择文件夹…",
                command,
                background=CARD_BACKGROUND,
            )
            button.pack(side="right")
            ttk.Label(
                directories,
                text=help_text,
                style="Hint.TLabel",
            ).pack(anchor="w", padx=(16, 0), pady=(0, 4))
            return button

        add_directory_row(
            "默认输入文件夹",
            self.settings_input_directory,
            self._choose_input_directory,
            "选择音频、导入歌词或载入人工 LRC 对照时，从此目录打开。",
        )
        add_directory_row(
            "默认输出文件夹",
            self.settings_output_directory,
            self._choose_output_directory,
            "导出 LRC 和内嵌草稿音频时，默认保存到此目录。",
        )
        self.choose_cover_directory_button = add_directory_row(
            "封面图片文件夹",
            self.settings_cover_directory,
            self._choose_cover_image_directory,
            "更换专辑封面时从此目录打开；默认使用项目内的 covers 文件夹。",
        )

        categories = RoundedTabs(page, variant="category")
        categories.pack(fill="x", pady=(0, 12))

        common_page = ttk.Frame(categories.content, style="App.TFrame", padding=(4, 10, 4, 4))
        categories.add(common_page, text="常用设置")
        common_tabs = RoundedTabs(common_page, variant="detail")
        common_tabs.pack(fill="x")
        runtime_page = ttk.Frame(common_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        common_tabs.add(runtime_page, text="运行")
        runtime_panel = RoundedPanel(runtime_page, padding=16, radius=16)
        runtime_panel.pack(fill="x")
        runtime = runtime_panel.content
        ttk.Label(runtime, text="运行设备与处理", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        device_row = ttk.Frame(runtime, style="Card.TFrame")
        device_row.pack(fill="x", pady=4)
        ttk.Label(device_row, text="运行设备", style="Setting.TLabel", width=14).pack(side="left")
        self.device_combo = ttk.Combobox(
            device_row,
            textvariable=self.device_mode,
            values=("自动", "GPU（CUDA）", "CPU"),
            state="readonly",
            width=16,
            style="Soft.TCombobox",
        )
        self.device_combo.pack(side="left")
        ttk.Label(
            device_row,
            text="自动优先使用可用 GPU；GPU 需要 CUDA 版 PyTorch。",
            style="Hint.TLabel",
        ).pack(side="left", padx=(12, 0))
        self.vocalize_checkbox = ttk.Checkbutton(
            runtime,
            text="运行打轴时启用人声分离（可选；模型体积较大且有单独使用限制）",
            variable=self.vocalize_enabled,
            command=self._on_vocalization_toggle,
            style="Card.TCheckbutton",
        )
        self.vocalize_checkbox.pack(anchor="w", pady=(10, 4))

        playback_page = ttk.Frame(common_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        common_tabs.add(playback_page, text="播放与显示")
        playback_panel = RoundedPanel(playback_page, padding=16, radius=16)
        playback_panel.pack(fill="x")
        ttk.Label(playback_panel.content, text="播放时间显示", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.playback_precision_checkbox = ttk.Checkbutton(
            playback_panel.content,
            text="显示到毫秒",
            variable=self.show_playback_milliseconds,
            style="Card.TCheckbutton",
        )
        self.playback_precision_checkbox.pack(anchor="w")
        ttk.Label(
            playback_panel.content,
            text="关闭时仅显示到秒（mm:ss）。",
            style="Hint.TLabel",
        ).pack(anchor="w", padx=(24, 0), pady=(3, 0))

        layout_page = ttk.Frame(common_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        common_tabs.add(layout_page, text="界面布局")
        editor_size_panel = RoundedPanel(layout_page, padding=16, radius=16)
        editor_size_panel.pack(fill="x")
        ttk.Label(
            editor_size_panel.content,
            text="歌词与草稿区域大小",
            style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        editor_size_row = ttk.Frame(editor_size_panel.content, style="Card.TFrame")
        editor_size_row.pack(fill="x")
        ttk.Label(
            editor_size_row,
            text="两栏共同高度",
            style="Setting.TLabel",
            width=14,
        ).pack(side="left")
        self.lyric_draft_height_spinbox = ttk.Spinbox(
            editor_size_row,
            from_=MIN_LYRIC_DRAFT_PANEL_HEIGHT,
            to=MAX_LYRIC_DRAFT_PANEL_HEIGHT,
            increment=20,
            width=8,
            textvariable=self.lyric_draft_panel_height,
            command=self._commit_lyric_draft_panel_height,
            style="Soft.TSpinbox",
        )
        self.lyric_draft_height_spinbox.pack(side="left")
        self.lyric_draft_height_spinbox.bind(
            "<Return>", self._commit_lyric_draft_panel_height, add="+"
        )
        self.lyric_draft_height_spinbox.bind(
            "<FocusOut>", self._commit_lyric_draft_panel_height, add="+"
        )
        ttk.Label(
            editor_size_row,
            text="px",
            style="Setting.TLabel",
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            editor_size_panel.content,
            text="此高度同步应用于歌词栏和时间轴草稿栏；窗口空间不足时可在 Auto-LRC 页面滚动查看。",
            style="Hint.TLabel",
            wraplength=820,
        ).pack(anchor="w", padx=(14, 0), pady=(5, 0))

        wheel_cooldown_panel = RoundedPanel(layout_page, padding=16, radius=16)
        wheel_cooldown_panel.pack(fill="x", pady=(10, 0))
        ttk.Label(
            wheel_cooldown_panel.content,
            text="滚轮层级切换冷却",
            style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        wheel_cooldown_row = ttk.Frame(wheel_cooldown_panel.content, style="Card.TFrame")
        wheel_cooldown_row.pack(fill="x")
        ttk.Label(
            wheel_cooldown_row,
            text="冷却时间",
            style="Setting.TLabel",
            width=14,
        ).pack(side="left")
        self.wheel_handoff_cooldown_spinbox = ttk.Spinbox(
            wheel_cooldown_row,
            from_=MIN_WHEEL_HANDOFF_COOLDOWN_SECONDS,
            to=MAX_WHEEL_HANDOFF_COOLDOWN_SECONDS,
            increment=0.1,
            width=8,
            textvariable=self.wheel_handoff_cooldown_seconds,
            command=self._commit_wheel_handoff_cooldown,
            style="Soft.TSpinbox",
        )
        self.wheel_handoff_cooldown_spinbox.pack(side="left")
        self.wheel_handoff_cooldown_spinbox.bind(
            "<Return>", self._commit_wheel_handoff_cooldown, add="+"
        )
        self.wheel_handoff_cooldown_spinbox.bind(
            "<FocusOut>", self._commit_wheel_handoff_cooldown, add="+"
        )
        ttk.Label(
            wheel_cooldown_row,
            text="秒",
            style="Setting.TLabel",
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            wheel_cooldown_panel.content,
            text="控件滚到边界后，等待此时长再把滚轮交给上级；上级接管后，鼠标不动时会保持当前滚动层级。移动鼠标或停滚达到此时长后，下级控件可重新接管。",
            style="Hint.TLabel",
            wraplength=820,
        ).pack(anchor="w", padx=(14, 0), pady=(5, 0))

        lyrics_page = ttk.Frame(categories.content, style="App.TFrame", padding=(4, 10, 4, 4))
        categories.add(lyrics_page, text="歌词")
        lyrics_tabs = RoundedTabs(lyrics_page, variant="detail")
        lyrics_tabs.pack(fill="x")
        template_page = ttk.Frame(lyrics_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        lyrics_tabs.add(template_page, text="信息模板")
        template_panel = RoundedPanel(template_page, padding=16, radius=16)
        template_panel.pack(fill="x")
        ttk.Label(template_panel.content, text="歌词信息语言", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(
            template_panel.content,
            text="选择整理内嵌歌词信息时使用的语言；字段名称和制作人员标签会统一切换。",
            style="Hint.TLabel",
            wraplength=820,
        ).pack(anchor="w", pady=(0, 8))
        self.lyric_template_preview = tk.StringVar(value="")
        choices = ttk.Frame(template_panel.content, style="Card.TFrame")
        choices.pack(anchor="w")
        ttk.Radiobutton(
            choices,
            text="简体中文",
            variable=self.lyric_template_language,
            value="zh",
            command=self._update_lyric_template_preview,
            style="Card.TRadiobutton",
        ).pack(side="left", padx=(0, 18))
        ttk.Radiobutton(
            choices,
            text="English",
            variable=self.lyric_template_language,
            value="en",
            command=self._update_lyric_template_preview,
            style="Card.TRadiobutton",
        ).pack(side="left")
        preview_panel = RoundedPanel(template_panel.content, padding=12, radius=12)
        preview_panel.pack(fill="x", pady=(12, 0))
        ttk.Label(preview_panel.content, text="模板预览", style="Setting.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(
            preview_panel.content,
            textvariable=self.lyric_template_preview,
            style="TemplatePreview.TLabel",
            justify="left",
        ).pack(anchor="w")

        lyric_rules_page = ttk.Frame(lyrics_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        lyrics_tabs.add(lyric_rules_page, text="字段与顺序")
        rules_panel = RoundedPanel(lyric_rules_page, padding=16, radius=16)
        rules_panel.pack(fill="x")
        ttk.Label(rules_panel.content, text="统一的信息排列", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(
            rules_panel.content,
            text=(
                "整理时依次排列曲名、歌手、专辑、文件标签和制作人员信息；重复项目会合并，"
                "歌词正文与时间轴保持原样。无法识别的自定义信息会保留。"
            ),
            style="Hint.TLabel",
            wraplength=850,
            justify="left",
        ).pack(anchor="w")

        model_page = ttk.Frame(categories.content, style="App.TFrame", padding=(4, 10, 4, 4))
        categories.add(model_page, text="本地模型")
        model_tabs = RoundedTabs(model_page, variant="detail")
        model_tabs.pack(fill="x")
        model_management_page = ttk.Frame(model_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        model_tabs.add(model_management_page, text="模型管理")
        model_panel = RoundedPanel(model_management_page, padding=16, radius=16)
        model_panel.pack(fill="x")
        model = model_panel.content
        ttk.Label(model, text="本地歌词模型", style="CardTitle.TLabel").pack(anchor="w")
        self.settings_model_status = tk.StringVar()
        ttk.Label(
            model,
            textvariable=self.settings_model_status,
            style="Hint.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(4, 10))
        model_actions = ttk.Frame(model, style="Card.TFrame")
        model_actions.pack(fill="x")
        self.train_boundary_button = RoundedButton(
            model_actions,
            "用 feedback 训练…",
            self._train_boundary_model,
        )
        self.train_boundary_button.pack(side="left", padx=(0, 8))
        self.enable_boundary_button = RoundedButton(
            model_actions,
            "启用本地模型",
            self._enable_saved_boundary_model,
        )
        self.enable_boundary_button.pack(side="left", padx=(0, 8))
        self.reset_boundary_button = RoundedButton(
            model_actions,
            "恢复预训练模型",
            self._restore_pretrained_boundary_model,
        )
        self.reset_boundary_button.pack(side="left")

        preset_panel = RoundedPanel(model_management_page, padding=16, radius=16)
        preset_panel.pack(fill="x", pady=(10, 0))
        preset_content = preset_panel.content
        ttk.Label(preset_content, text="本地模型预设", style="CardTitle.TLabel").pack(anchor="w")
        self.settings_model_preset_status = tk.StringVar(
            value=f"权重预设保存在程序目录：{self.model_preset_directory}"
        )
        ttk.Label(
            preset_content,
            textvariable=self.settings_model_preset_status,
            style="Hint.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(4, 10))
        ttk.Label(
            preset_content,
            text="预设保存为独立权重副本。训练会生成新的当前模型，不会改写已保存的预设。",
            style="Hint.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(0, 10))
        preset_select_row = ttk.Frame(preset_content, style="Card.TFrame")
        preset_select_row.pack(fill="x", pady=(0, 8))
        ttk.Label(
            preset_select_row,
            text="预设",
            style="Setting.TLabel",
            width=14,
        ).pack(side="left")
        self.model_preset_combo = ttk.Combobox(
            preset_select_row,
            textvariable=self.model_preset_selection,
            state="readonly",
            width=36,
            style="Soft.TCombobox",
        )
        self.model_preset_combo.pack(side="left", padx=(0, 8))
        self.model_preset_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_model_preset_controls(),
        )
        self.activate_model_preset_button = RoundedButton(
            preset_select_row,
            "启用所选预设",
            self._activate_model_preset,
            accent=True,
        )
        self.activate_model_preset_button.pack(side="left")
        preset_actions = ttk.Frame(preset_content, style="Card.TFrame")
        preset_actions.pack(fill="x")
        self.save_model_preset_button = RoundedButton(
            preset_actions,
            "保存为新预设…",
            self._save_model_preset_as_new,
        )
        self.save_model_preset_button.pack(side="left", padx=(0, 8))
        self.overwrite_model_preset_button = RoundedButton(
            preset_actions,
            "覆盖所选预设…",
            self._overwrite_model_preset,
        )
        self.overwrite_model_preset_button.pack(side="left", padx=(0, 8))
        self.rename_model_preset_button = RoundedButton(
            preset_actions,
            "重命名…",
            self._rename_model_preset,
        )
        self.rename_model_preset_button.pack(side="left")

        training_page = ttk.Frame(model_tabs.content, style="App.TFrame", padding=(2, 8, 2, 2))
        model_tabs.add(training_page, text="训练资料")
        initialize_panel = RoundedPanel(training_page, padding=16, radius=16)
        initialize_panel.pack(fill="x")
        initialize_content = initialize_panel.content
        ttk.Label(initialize_content, text="从文件夹初始化模型", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(
            initialize_content,
            text=(
                "选择资料文件夹后，程序会递归读取其中的音频。音频可与同名 LRC 配对，"
                "也可使用文件标签里带时间戳的内嵌歌词；每首歌至少要有 5 行有效时间，"
                "并且至少需要两首不同歌曲用于训练和验证。初始化从随程序提供的预训练模型开始，"
                "不会把当前 feedback 混入本次初始化。"
            ),
            style="Hint.TLabel",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(5, 10))
        self.initialize_model_button = RoundedButton(
            initialize_content,
            "选择训练资料文件夹…",
            self._initialize_model_from_folder,
            accent=True,
        )
        self.initialize_model_button.pack(anchor="w", pady=(0, 8))
        self.settings_training_directory = tk.StringVar(
            value=str(self.training_data_directory) if self.training_data_directory else "尚未选择"
        )
        ttk.Label(
            initialize_content,
            textvariable=self.settings_training_directory,
            style="Hint.TLabel",
            wraplength=900,
        ).pack(anchor="w")

        reset_row = ttk.Frame(page, style="App.TFrame")
        reset_row.pack(fill="x", pady=(2, 0))
        ttk.Label(
            reset_row,
            text="恢复运行选项、歌词模板、默认文件夹和窗口布局的默认值。",
            style="Hint.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.reset_settings_button = RoundedButton(
            reset_row,
            "恢复默认设置",
            self._restore_default_settings,
            background=APP_BACKGROUND,
        )
        self.reset_settings_button.pack(side="right")
        self._update_lyric_template_preview()
        self._initialize_model_presets()
        self._update_boundary_button_states()
        scrollable_page._bind_parent_scroll_lock_to_descendants()

    def _update_lyric_template_preview(self) -> None:
        if not hasattr(self, "lyric_template_preview"):
            return
        if self.lyric_template_language.get() == "en":
            preview = (
                "Title: Blue Sky\n"
                "Artist: Jay Chou\n"
                "Album: Ye Hui Mei\n"
                "Lyrics: Vincent Fang\n"
                "Music: Jay Chou"
            )
        else:
            preview = (
                "标题：晴天\n"
                "歌手：周杰伦\n"
                "专辑：叶惠美\n"
                "作词：方文山\n"
                "作曲：周杰伦"
            )
        self.lyric_template_preview.set(preview)

    def _show_page(self, page_name: str) -> None:
        self._active_page = page_name
        if page_name == "settings":
            self.player_footer.grid_remove()
        else:
            self.player_footer.grid()
        metadata_sync_path = None
        if page_name == "audio":
            self.audio_editor_page.tkraise()
            current_audio_text = self.audio_path.get().strip()
            if current_audio_text:
                metadata_sync_path = Path(current_audio_text).absolute()
        elif page_name == "settings":
            self.settings_page.tkraise()
        else:
            self.autolrc_page.tkraise()
        for name, (item, button, indicator) in self.nav_buttons.items():
            selected = name == page_name
            background = ui_color("nav_selected_background") if selected else SIDEBAR_BACKGROUND
            item.configure(background=background)
            indicator.configure(background=ACCENT_COLOR if selected else SIDEBAR_BACKGROUND)
            button.configure(
                background=background,
                foreground=ui_color("nav_selected_foreground") if selected else ui_color("nav_foreground"),
                font=("Microsoft YaHei UI", 10, "bold" if selected else "normal"),
            )
        if metadata_sync_path is not None:
            self.root.after_idle(
                lambda path=metadata_sync_path: self._sync_audio_metadata_if_current(path)
            )
        self._sync_playback_controls()

    def _toggle_theme(self) -> None:
        self._apply_theme("dark" if self.theme == "light" else "light")

    def _apply_window_icon(self) -> None:
        icon_name = "Lyrics-dark.ico" if self.theme == "dark" else "Lyrics.ico"
        icon_path = ROOT_DIR / "assets" / icon_name
        if not icon_path.is_file():
            return
        try:
            self.root.iconbitmap(str(icon_path))
        except tk.TclError:
            pass
        self._apply_native_window_icon()

    def _apply_native_window_icon(self) -> None:
        """Set Windows' small title-bar and large taskbar icons from the themed ICO."""
        if sys.platform != "win32":
            return
        icon_name = "Lyrics-dark.ico" if self.theme == "dark" else "Lyrics.ico"
        icon_path = ROOT_DIR / "assets" / icon_name
        if not icon_path.is_file():
            return

        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            load_image = user32.LoadImageW
            load_image.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            load_image.restype = ctypes.c_void_p
            send_message = user32.SendMessageW
            send_message.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_void_p,
            ]
            send_message.restype = ctypes.c_void_p

            cache_key = str(icon_path.resolve()).casefold()
            icon_handles = self._native_window_icons.get(cache_key)
            if icon_handles is None:
                get_system_metrics = user32.GetSystemMetrics
                get_system_metrics.argtypes = [ctypes.c_int]
                get_system_metrics.restype = ctypes.c_int
                small_size = (
                    max(16, get_system_metrics(49)),  # SM_CXSMICON
                    max(16, get_system_metrics(50)),  # SM_CYSMICON
                )
                large_size = (
                    max(32, get_system_metrics(11)),  # SM_CXICON
                    max(32, get_system_metrics(12)),  # SM_CYICON
                )
                load_from_file = 0x0010
                small_icon = load_image(
                    None, str(icon_path), 1, *small_size, load_from_file
                )
                large_icon = load_image(
                    None, str(icon_path), 1, *large_size, load_from_file
                )
                if not small_icon or not large_icon:
                    destroy_icon = user32.DestroyIcon
                    destroy_icon.argtypes = [ctypes.c_void_p]
                    destroy_icon.restype = ctypes.c_int
                    if small_icon:
                        destroy_icon(small_icon)
                    if large_icon:
                        destroy_icon(large_icon)
                    return
                icon_handles = (int(small_icon), int(large_icon))
                self._native_window_icons[cache_key] = icon_handles

            try:
                frame_handle = str(self.root.tk.call("wm", "frame", self.root._w))
                hwnd = int(frame_handle, 0)
            except (AttributeError, tk.TclError, ValueError):
                hwnd = self.root.winfo_id()
            if not hwnd:
                return

            wm_set_icon = 0x0080
            icon_small = 0
            icon_big = 1
            send_message(hwnd, wm_set_icon, icon_small, icon_handles[0])
            send_message(hwnd, wm_set_icon, icon_big, icon_handles[1])
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            # Keep Tk's iconbitmap as a portable fallback if a native call is unavailable.
            pass

    def _apply_theme(self, theme_name: str) -> None:
        if theme_name not in THEMES or theme_name == self.theme:
            return

        old_palette = ACTIVE_THEME
        new_palette = THEMES[theme_name]
        background_roles = {
            key
            for key in old_palette
            if key.endswith("_background")
        } | {"background", "card", "input", "sidebar", "border", "accent", "accent_hover"}
        foreground_roles = set(old_palette) - {
            key for key in old_palette if key.endswith("_background")
        } | {"accent", "accent_hover"}
        background_map = {
            old_palette[key].lower(): new_palette[key]
            for key in background_roles
        }
        foreground_map = {
            old_palette[key].lower(): new_palette[key]
            for key in foreground_roles
        }

        self.theme = theme_name
        _activate_theme(theme_name)
        self._apply_window_icon()
        self.root.configure(background=APP_BACKGROUND)
        self._configure_style()
        self._apply_window_frame_theme()
        self.root.after(80, self._apply_window_frame_theme)

        pending = list(self.root.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            try:
                option_names = set(widget.keys())
            except tk.TclError:
                option_names = set()
            updates = {}
            for option in ("background", "activebackground", "fieldbackground", "highlightbackground", "highlightcolor", "selectbackground", "troughcolor", "bordercolor"):
                if option not in option_names:
                    continue
                try:
                    current = str(widget.cget(option)).lower()
                except tk.TclError:
                    continue
                if current in background_map:
                    updates[option] = background_map[current]
            for option in ("foreground", "activeforeground", "disabledforeground", "insertbackground", "selectforeground"):
                if option not in option_names:
                    continue
                try:
                    current = str(widget.cget(option)).lower()
                except tk.TclError:
                    continue
                if current in foreground_map:
                    updates[option] = foreground_map[current]
            if updates:
                try:
                    widget.configure(**updates)
                except tk.TclError:
                    pass

            if isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    item_type = widget.type(item)
                    fill_map = foreground_map if item_type == "text" else background_map
                    try:
                        fill = str(widget.itemcget(item, "fill")).lower()
                        outline = str(widget.itemcget(item, "outline")).lower()
                    except tk.TclError:
                        continue
                    item_updates = {}
                    if fill in fill_map:
                        item_updates["fill"] = fill_map[fill]
                    if outline in background_map:
                        item_updates["outline"] = background_map[outline]
                    if item_updates:
                        try:
                            widget.itemconfigure(item, **item_updates)
                        except tk.TclError:
                            pass

            if isinstance(widget, RoundedPanel):
                if widget._surface.lower() in background_map:
                    widget._surface = background_map[widget._surface.lower()]
            elif isinstance(widget, RoundedButton):
                if widget._button_background.lower() in background_map:
                    widget._button_background = background_map[widget._button_background.lower()]
                widget._refresh()
            elif isinstance(widget, RoundedTabButton):
                widget._refresh_theme()
            elif isinstance(widget, tk.Text):
                widget.configure(
                    selectbackground=ui_color("selected_background"),
                    selectforeground=TEXT_PRIMARY,
                )

        if hasattr(self, "player_scale"):
            self.player_scale.redraw_theme()
        if hasattr(self, "lyrics_text"):
            self.lyrics_text.tag_configure(
                "metadata",
                foreground=ACCENT_COLOR,
                background=ui_color("lyrics_metadata_background"),
            )
        self._configure_table_tags()
        for name, (item, button, indicator) in self.nav_buttons.items():
            selected = name == self._active_page
            background = ui_color("nav_selected_background") if selected else SIDEBAR_BACKGROUND
            item.configure(background=background)
            indicator.configure(background=ACCENT_COLOR if selected else SIDEBAR_BACKGROUND)
            button.configure(
                background=background,
                foreground=ui_color("nav_selected_foreground") if selected else ui_color("nav_foreground"),
                activebackground=ui_color("nav_selected_background"),
                activeforeground=ui_color("nav_selected_foreground"),
            )
        self.theme_button._text = "切换浅色模式" if theme_name == "dark" else "切换深色模式"
        self.theme_button.itemconfigure(self.theme_button._label, text=self.theme_button._text)
        self.theme_button._refresh()
        self._queue_settings_save()

    def _sync_audio_metadata_if_current(self, path: Path) -> None:
        if self._active_page != "audio":
            return
        current_audio_text = self.audio_path.get().strip()
        if not current_audio_text or Path(current_audio_text).absolute() != path:
            return
        self._sync_autolrc_metadata_document(path)

    def _build_audio_editor_page(self) -> None:
        page = ttk.Frame(self.audio_editor_page, style="App.TFrame", padding=18)
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="AUDIO EDITOR  /  音频信息", style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 3))
        ttk.Label(page, text="音频文件编辑", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            page,
            text="可编辑封面、常见标签和内嵌歌词，也可查看或修改 LRC 时间戳。单击选项卡只切换编辑内容，不会中断播放；双击切换并播放对应音频。保存时覆盖所选文件。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 14))

        file_panel = RoundedPanel(page, padding=(12, 10), radius=15)
        file_panel.pack(fill="x")
        file_row = ttk.Frame(file_panel.content, style="Card.TFrame")
        file_row.pack(fill="x")
        ttk.Label(file_row, text="音频文件", style="CardTitle.TLabel").pack(side="left", padx=(0, 12))
        self.metadata_audio_entry = ttk.Entry(
            file_row,
            textvariable=self.metadata_audio_path,
            state="readonly",
            style="Soft.TEntry",
        )
        self.metadata_audio_entry.pack(side="left", fill="x", expand=True)
        self.metadata_choose_audio_button = RoundedButton(
            file_row,
            "添加歌曲…",
            self._choose_metadata_audio,
            background=CARD_BACKGROUND,
        )
        self.metadata_choose_audio_button.pack(side="left", padx=(8, 0))
        self.metadata_use_alignment_audio_button = RoundedButton(
            file_row,
            "使用打轴页音频",
            self._use_alignment_audio_for_metadata,
            background=CARD_BACKGROUND,
        )
        self.metadata_use_alignment_audio_button.pack(side="left", padx=(8, 0))
        self.metadata_rename_audio_button = RoundedButton(
            file_row,
            "重命名文件…",
            self._rename_metadata_audio,
            background=CARD_BACKGROUND,
        )
        self.metadata_rename_audio_button.pack(side="left", padx=(8, 0))
        self._metadata_action_buttons.extend(
            (
                self.metadata_choose_audio_button,
                self.metadata_use_alignment_audio_button,
                self.metadata_rename_audio_button,
            )
        )

        tabs_rail = ttk.Frame(page, style="App.TFrame")
        tabs_rail.pack(fill="x", pady=(12, 0))
        self.metadata_tabs_rail = tabs_rail
        self.metadata_tabs_canvas = tk.Canvas(
            tabs_rail,
            height=40,
            background=APP_BACKGROUND,
            bd=0,
            highlightthickness=0,
            xscrollincrement=28,
        )
        self.metadata_tabs_canvas.pack(side="left", fill="x", expand=True)
        self.metadata_tabs_inner = tk.Frame(self.metadata_tabs_canvas, background=APP_BACKGROUND)
        self._metadata_tabs_window = self.metadata_tabs_canvas.create_window(
            (0, 0), window=self.metadata_tabs_inner, anchor="nw"
        )
        for wheel_target in (tabs_rail, self.metadata_tabs_canvas, self.metadata_tabs_inner):
            self._bind_metadata_tabs_wheel(wheel_target)
        self.metadata_tabs_inner.bind(
            "<Configure>",
            lambda _event: self._schedule_metadata_tabs_scrollregion(),
        )
        self.metadata_tabs_canvas.bind(
            "<Configure>",
            lambda _event: self._schedule_metadata_tabs_scrollregion(),
        )
        self.metadata_tabs_canvas.bind("<Shift-MouseWheel>", self._scroll_metadata_tabs, add="+")
        self.root.bind_all("<MouseWheel>", self._scroll_metadata_tabs_wheel_region, add="+")
        self.root.bind_all("<Button-4>", self._scroll_metadata_tabs_wheel_region, add="+")
        self.root.bind_all("<Button-5>", self._scroll_metadata_tabs_wheel_region, add="+")
        self.root.bind_all(
            "<Motion>", self._release_metadata_form_parent_scroll_on_motion, add="+"
        )
        self.metadata_tabs_left_button = tk.Button(
            tabs_rail,
            text="‹",
            command=lambda: self._scroll_metadata_tabs_by(-3),
            background=APP_BACKGROUND,
            foreground=TEXT_SECONDARY,
            activebackground=ui_color("nav_hover_background"),
            relief="flat",
            bd=0,
            width=2,
            font=("Microsoft YaHei UI", 12),
            cursor="hand2",
        )
        self.metadata_tabs_left_button.pack(side="left", before=self.metadata_tabs_canvas)
        self._bind_metadata_tabs_wheel(self.metadata_tabs_left_button)
        self.metadata_tabs_right_button = tk.Button(
            tabs_rail,
            text="›",
            command=lambda: self._scroll_metadata_tabs_by(3),
            background=APP_BACKGROUND,
            foreground=TEXT_SECONDARY,
            activebackground=ui_color("nav_hover_background"),
            relief="flat",
            bd=0,
            width=2,
            font=("Microsoft YaHei UI", 12),
            cursor="hand2",
        )
        self.metadata_tabs_right_button.pack(side="right")
        self._bind_metadata_tabs_wheel(self.metadata_tabs_right_button)

        editor_body = ttk.Frame(page, style="App.TFrame")
        editor_body.pack(fill="both", expand=True, pady=(12, 0))
        cover_panel = RoundedPanel(editor_body, padding=12, radius=16)
        cover_panel.pack(side="left", fill="y", padx=(0, 12))
        cover_content = cover_panel.content
        ttk.Label(cover_content, text="专辑封面", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        self.metadata_cover_preview_frame = tk.Frame(
            cover_content,
            width=COVER_PREVIEW_SIZE,
            height=COVER_PREVIEW_SIZE,
            background=INPUT_BACKGROUND,
            bd=0,
        )
        self.metadata_cover_preview_frame.pack(anchor="center", pady=(0, 4))
        self.metadata_cover_preview_frame.pack_propagate(False)
        self.metadata_cover_preview = tk.Label(
            self.metadata_cover_preview_frame,
            text="未载入封面",
            background=INPUT_BACKGROUND,
            foreground=TEXT_SECONDARY,
            font=("Microsoft YaHei UI", 9),
            compound="center",
        )
        self.metadata_cover_preview.pack(fill="both", expand=True)
        self.metadata_cover_name = ttk.Label(cover_content, text="", style="Hint.TLabel", wraplength=220)
        self.metadata_cover_name.pack(fill="x", pady=(4, 2))
        cover_actions = ttk.Frame(cover_content, style="Card.TFrame")
        cover_actions.pack(fill="x", pady=(2, 0))
        self.metadata_choose_cover_button = RoundedButton(
            cover_actions,
            "选择封面图片…",
            self._choose_metadata_cover,
            background=CARD_BACKGROUND,
        )
        self.metadata_choose_cover_button.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 3),
        )
        self.metadata_remove_cover_button = RoundedButton(
            cover_actions,
            "移除现有封面",
            self._remove_metadata_cover,
            background=CARD_BACKGROUND,
        )
        self.metadata_remove_cover_button.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(3, 0),
        )
        ttk.Label(cover_content, text="支持 JPEG / PNG 图片。", style="Hint.TLabel").pack(
            fill="x", pady=(2, 0)
        )
        self._metadata_action_buttons.extend(
            (self.metadata_choose_cover_button, self.metadata_remove_cover_button)
        )

        fields_panel = RoundedPanel(editor_body, padding=14, radius=16)
        fields_panel.pack(side="left", fill="both", expand=True)
        fields_content = fields_panel.content
        ttk.Label(fields_content, text="音乐信息", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 10))
        form_container = ttk.Frame(fields_content, style="Card.TFrame")
        form_container.pack(fill="both", expand=True)
        form_container.rowconfigure(0, weight=1)
        form_container.columnconfigure(0, weight=1)
        self.metadata_form_canvas = tk.Canvas(
            form_container,
            background=CARD_BACKGROUND,
            bd=0,
            highlightthickness=0,
            yscrollincrement=1,
        )
        self.metadata_form_canvas.grid(row=0, column=0, sticky="nsew")
        form_scrollbar = ttk.Scrollbar(
            form_container,
            orient="vertical",
            command=self.metadata_form_canvas.yview,
        )
        form_scrollbar.grid(row=0, column=1, sticky="ns")
        self.metadata_form_canvas.configure(yscrollcommand=form_scrollbar.set)
        form = ttk.Frame(self.metadata_form_canvas, style="Card.TFrame")
        metadata_form_window = self.metadata_form_canvas.create_window(
            (0, 0), window=form, anchor="nw"
        )
        form.bind(
            "<Configure>",
            lambda _event: self._schedule_metadata_form_scrollregion(),
        )
        self.metadata_form_canvas.bind(
            "<Configure>",
            lambda event: self._resize_metadata_form_window(metadata_form_window, event.width),
        )
        form.columnconfigure(1, weight=1)
        labels = (
            ("title", "标题"),
            ("artist", "歌手"),
            ("album", "专辑"),
            ("album_artist", "专辑艺术家"),
            ("year", "年份 / 日期"),
            ("genre", "流派"),
            ("track_number", "音轨编号"),
            ("composer", "作曲"),
            ("comment", "备注"),
        )
        for row, (field, label) in enumerate(labels):
            field_label = ttk.Label(form, text=label, style="Setting.TLabel")
            field_label.grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=4
            )
            field_label.bind("<MouseWheel>", self._scroll_metadata_form, add="+")
            if field == "comment":
                comment_container = ttk.Frame(form, style="Card.TFrame")
                comment_container.grid(row=row, column=1, sticky="ew", pady=4)
                comment_container.columnconfigure(0, weight=1)
                comment_container.rowconfigure(0, weight=1)
                self.metadata_comment_text = tk.Text(
                    comment_container,
                    height=4,
                    wrap="word",
                    font=("Microsoft YaHei UI", 9),
                    background=INPUT_BACKGROUND,
                    foreground=TEXT_PRIMARY,
                    insertbackground=TEXT_PRIMARY,
                    selectbackground=ui_color("selected_background"),
                    selectforeground=TEXT_PRIMARY,
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=9,
                    pady=7,
                    undo=True,
                )
                self.metadata_comment_text.grid(row=0, column=0, sticky="nsew")
                self.metadata_comment_scrollbar = ttk.Scrollbar(
                    comment_container,
                    orient="vertical",
                    command=self.metadata_comment_text.yview,
                )
                self.metadata_comment_scrollbar.grid(row=0, column=1, sticky="ns")
                self.metadata_comment_text.configure(
                    yscrollcommand=self.metadata_comment_scrollbar.set
                )
                self.metadata_comment_text.bind(
                    "<MouseWheel>", self._scroll_metadata_comment, add="+"
                )
                self._metadata_controls.append(self.metadata_comment_text)
            else:
                entry = ttk.Entry(
                    form, textvariable=self.metadata_values[field], style="Soft.TEntry"
                )
                entry.grid(row=row, column=1, sticky="ew", pady=4)
                entry.bind("<MouseWheel>", self._scroll_metadata_form, add="+")
                self._metadata_controls.append(entry)
        ttk.Label(form, text="内嵌歌词", style="Setting.TLabel").grid(
            row=len(labels), column=0, sticky="nw", padx=(0, 12), pady=(8, 4)
        )
        lyrics_container = ttk.Frame(form, style="Card.TFrame")
        lyrics_container.grid(
            row=len(labels), column=1, sticky="ew", pady=(8, 4)
        )
        lyrics_container.columnconfigure(0, weight=1)
        lyrics_container.rowconfigure(0, weight=1)
        self.metadata_lyrics_text = tk.Text(
            lyrics_container,
            height=9,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            background=INPUT_BACKGROUND,
            foreground=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            selectbackground=ui_color("selected_background"),
            selectforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=9,
            pady=7,
            undo=True,
        )
        self.metadata_lyrics_text.grid(row=0, column=0, sticky="nsew")
        self.metadata_lyrics_scrollbar = ttk.Scrollbar(
            lyrics_container,
            orient="vertical",
            command=self.metadata_lyrics_text.yview,
        )
        self.metadata_lyrics_scrollbar.grid(row=0, column=1, sticky="ns")
        self.metadata_lyrics_text.configure(
            yscrollcommand=self.metadata_lyrics_scrollbar.set
        )
        self.metadata_lyrics_text.bind(
            "<MouseWheel>", self._scroll_metadata_lyrics, add="+"
        )
        ttk.Label(
            lyrics_container,
            textvariable=self.metadata_lyrics_hint,
            style="Hint.TLabel",
            wraplength=650,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._metadata_controls.append(self.metadata_lyrics_text)
        form.bind("<MouseWheel>", self._scroll_metadata_form, add="+")
        self.metadata_form_canvas.bind("<MouseWheel>", self._scroll_metadata_form, add="+")

        tk.Frame(page, height=1, background=BORDER_COLOR, bd=0).pack(fill="x", pady=(10, 0))
        save_row = ttk.Frame(page, style="App.TFrame")
        save_row.pack(fill="x", pady=(10, 0))
        ttk.Label(
            save_row,
            text="按钮保存当前歌曲；按 Ctrl+S 保存所有已修改歌曲及内嵌歌词。",
            style="Subtitle.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.metadata_save_button = RoundedButton(
            save_row,
            "覆盖保存当前文件",
            self._save_metadata_audio,
            accent=True,
            background=APP_BACKGROUND,
        )
        self.metadata_save_button.pack(side="right")
        self._metadata_action_buttons.append(self.metadata_save_button)
        self.metadata_format_lyrics_button = RoundedButton(
            save_row,
            "统一歌词信息格式",
            self._format_active_embedded_lyrics,
            background=APP_BACKGROUND,
        )
        self.metadata_format_lyrics_button.pack(side="right", padx=(0, 8))
        self._metadata_action_buttons.append(self.metadata_format_lyrics_button)
        self._refresh_metadata_tabs()

    def _choose_metadata_audio(self) -> None:
        initial_directory = self._default_input_directory()
        paths = filedialog.askopenfilenames(
            title="选择要编辑标签的音频",
            initialdir=str(initial_directory),
            filetypes=[
                ("音频文件", "*.mp3 *.flac *.wav *.m4a *.ogg *.aac *.opus"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._open_metadata_documents(paths)

    def _new_metadata_document(self, path: Path, *, autolrc_linked: bool = False) -> dict:
        fields = {field: "" for field in self.metadata_values}
        document = {
            "id": self._next_metadata_document_id,
            "path": path,
            "fields": fields,
            "saved_fields": dict(fields),
            "embedded_lyrics": [],
            "saved_embedded_lyrics": [],
            "cover_data": None,
            "selected_cover": None,
            "selected_cover_name": "",
            "selected_cover_preview": None,
            "remove_cover": False,
            "loaded": False,
            "loading": False,
            "load_error": "",
            "load_token": 0,
            "preview_image": None,
            "preview_failed": False,
            "autolrc_linked": autolrc_linked,
        }
        self._next_metadata_document_id += 1
        return document

    def _open_metadata_documents(self, selected_paths) -> None:
        self._store_active_metadata_document()
        seen_paths: set[str] = set()
        to_load: list[tuple[Path, int]] = []
        target_document = None
        for selected_path in selected_paths:
            path = Path(selected_path).absolute()
            path_key = os.path.normcase(str(path))
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            document = next(
                (
                    item
                    for item in self._metadata_documents
                    if item.get("path")
                    and os.path.normcase(str(item["path"])) == path_key
                ),
                None,
            )
            if document is None:
                document = self._new_metadata_document(path)
                self._metadata_documents.append(document)
            target_document = document
            if not document.get("loaded") and not document.get("loading"):
                to_load.append((path, document["id"]))

        if target_document is None:
            return
        self._active_metadata_document_id = target_document["id"]
        self._render_active_metadata_document()
        for path, document_id in to_load:
            self._load_metadata_audio(path, document_id, refresh_tabs=False)
        self._refresh_metadata_tabs(reveal_active=True)
        if target_document.get("loading"):
            path = target_document.get("path")
            if path:
                self.status_text.set(f"正在读取音频标签：{path.name}")

    def _use_alignment_audio_for_metadata(self) -> None:
        path_text = self.audio_path.get().strip()
        if not path_text:
            messagebox.showwarning("先选音频", "打轴页还没有选择音频。", parent=self.root)
            return
        self._sync_autolrc_metadata_document(Path(path_text))

    def _rename_metadata_audio(self) -> None:
        source_path = Path(self.metadata_audio_path.get().strip())
        if not source_path.is_file():
            messagebox.showwarning("先选择音频", "请选择要重命名的音频。", parent=self.root)
            return

        suffix = source_path.suffix
        new_name = simpledialog.askstring(
            "重命名音频",
            f"输入新的文件名（扩展名 {suffix or '由当前文件保留'}）：",
            initialvalue=source_path.stem,
            parent=self.root,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if suffix and new_name.casefold().endswith(suffix.casefold()):
            new_name = new_name[: -len(suffix)]
        elif suffix and Path(new_name).suffix.casefold() in AUDIO_EXTENSIONS:
            messagebox.showwarning(
                "不能更改扩展名",
                f"重命名时会保留当前扩展名 {suffix}。",
                parent=self.root,
            )
            return
        if (
            not new_name
            or new_name.endswith((" ", "."))
            or any(ord(character) < 32 for character in new_name)
            or re.search(r'[<>:"/\\|?*]', new_name)
            or new_name.split(".", 1)[0].upper() in {
                "CON", "PRN", "AUX", "NUL",
                *(f"COM{index}" for index in range(1, 10)),
                *(f"LPT{index}" for index in range(1, 10)),
            }
        ):
            messagebox.showwarning(
                "文件名无效",
                "请输入有效文件名，不要包含路径、非法字符或扩展名。",
                parent=self.root,
            )
            return

        new_path = source_path.with_name(f"{new_name}{suffix}")
        same_path = os.path.normcase(str(source_path)) == os.path.normcase(str(new_path))
        if same_path and source_path.name == new_path.name:
            return
        if not same_path and new_path.exists():
            messagebox.showwarning(
                "文件名已存在",
                f"同目录中已存在“{new_path.name}”，请换一个名称。",
                parent=self.root,
            )
            return

        try:
            source_resolved = source_path.resolve()
            alignment_path_text = self.audio_path.get().strip()
            alignment_path = Path(alignment_path_text).resolve() if alignment_path_text else None
        except OSError as exc:
            messagebox.showerror("无法解析音频路径", str(exc), parent=self.root)
            return

        rename_alignment_audio = bool(
            alignment_path
            and os.path.normcase(str(alignment_path)) == os.path.normcase(str(source_resolved))
        )
        self._store_active_metadata_document()
        if rename_alignment_audio and self._player is not None:
            self._release_player()
        try:
            source_path.rename(new_path)
        except OSError as exc:
            messagebox.showerror("无法重命名音频", str(exc), parent=self.root)
            return

        document = self._active_metadata_document()
        if document is not None:
            document["path"] = new_path
        self.metadata_audio_path.set(str(new_path))
        if rename_alignment_audio:
            new_resolved = new_path.resolve()
            if self.feedback_session is not None:
                session_path = self.feedback_session.get("audio_path")
                if session_path and os.path.normcase(str(session_path)) == os.path.normcase(
                    str(source_resolved)
                ):
                    self.feedback_session["audio_path"] = new_resolved
            if self.reference_audio_path is not None and os.path.normcase(
                str(self.reference_audio_path)
            ) == os.path.normcase(str(source_resolved)):
                self.reference_audio_path = new_resolved
            self.audio_path.set(str(new_path))
        self._refresh_metadata_tabs(reveal_active=True)
        self.status_text.set(f"已重命名音频：{new_path.name}")

    def _active_metadata_document(self) -> dict | None:
        return next(
            (
                document
                for document in self._metadata_documents
                if document["id"] == self._active_metadata_document_id
            ),
            None,
        )

    def _store_active_metadata_document(self) -> None:
        document = self._active_metadata_document()
        if document is None:
            return
        self.metadata_values["comment"].set(
            self.metadata_comment_text.get("1.0", "end-1c")
        )
        document["fields"] = {
            field: variable.get() for field, variable in self.metadata_values.items()
        }
        edited_lyrics = self.metadata_lyrics_text.get("1.0", "end-1c").replace("\r\n", "\n").replace("\r", "\n")
        saved_lyrics = list(document.get("saved_embedded_lyrics", []))
        if not edited_lyrics.strip():
            current_lyrics = []
        elif saved_lyrics:
            current_lyrics = (
                saved_lyrics
                if edited_lyrics == saved_lyrics[0]
                else [edited_lyrics, *saved_lyrics[1:]]
            )
        else:
            current_lyrics = [edited_lyrics] if edited_lyrics.strip() else []
        document["embedded_lyrics"] = current_lyrics
        document["cover_data"] = self._metadata_cover_data
        document["selected_cover"] = self._metadata_selected_cover
        document["selected_cover_name"] = self._metadata_selected_cover_name
        document["selected_cover_preview"] = self._metadata_cover_photo
        document["remove_cover"] = self._metadata_remove_cover

    def _render_active_metadata_document(self) -> None:
        document = self._active_metadata_document()
        if document is None:
            self.metadata_audio_path.set("")
            for variable in self.metadata_values.values():
                variable.set("")
            self._set_metadata_comment_text("")
            self._set_metadata_lyrics_text("")
            self.metadata_lyrics_hint.set("打开音频后可查看或编辑内嵌歌词；LRC 时间戳会保留。")
            self._metadata_cover_data = None
            self._metadata_selected_cover = None
            self._metadata_selected_cover_name = ""
            self._metadata_remove_cover = False
            self._set_metadata_cover_preview(None)
            self.status_text.set("添加歌曲后即可编辑音频信息。")
            self._update_metadata_control_states()
            return

        path = document.get("path")
        self.metadata_audio_path.set(str(path) if path else "")
        for field, variable in self.metadata_values.items():
            variable.set(document["fields"].get(field, ""))
        self._set_metadata_comment_text(self.metadata_values["comment"].get())
        embedded_lyrics = list(document.get("embedded_lyrics", []))
        self._set_metadata_lyrics_text(embedded_lyrics[0] if embedded_lyrics else "")
        if len(embedded_lyrics) > 1:
            self.metadata_lyrics_hint.set(
                f"检测到 {len(embedded_lyrics)} 份内嵌歌词；此处编辑第一份，其他版本保存时会保留。"
            )
        elif embedded_lyrics:
            self.metadata_lyrics_hint.set("可直接编辑内嵌歌词；LRC 时间戳会随文本一起保存。")
        else:
            self.metadata_lyrics_hint.set("没有内嵌歌词；可在此粘贴歌词或 LRC（含时间戳）。")
        self._metadata_cover_data = document.get("cover_data")
        self._metadata_selected_cover = document.get("selected_cover")
        self._metadata_selected_cover_name = document.get("selected_cover_name", "")
        self._metadata_remove_cover = bool(document.get("remove_cover", False))

        if self._metadata_selected_cover is not None:
            preview_photo = document.get("selected_cover_preview")
            if preview_photo is not None:
                self._metadata_cover_photo = preview_photo
                self.metadata_cover_preview.configure(image=preview_photo, text="")
                self.metadata_cover_name.configure(text=self._metadata_selected_cover_name)
            else:
                self._set_metadata_cover_preview(
                    self._metadata_selected_cover, self._metadata_selected_cover_name
                )
        elif self._metadata_remove_cover:
            self._set_metadata_cover_preview(None, "保存时会移除现有封面")
        elif document.get("loaded"):
            self._set_metadata_cover_preview(
                self._metadata_cover_data,
                preview_image=document.get("preview_image"),
                preview_failed=bool(document.get("preview_failed", False)),
            )
        elif document.get("loading"):
            self._metadata_cover_photo = None
            self.metadata_cover_preview.configure(image="", text="正在读取标签…")
            self.metadata_cover_name.configure(text="")
        elif document.get("load_error"):
            self._metadata_cover_photo = None
            self.metadata_cover_preview.configure(image="", text="无法读取标签")
            self.metadata_cover_name.configure(text="")
        else:
            self._set_metadata_cover_preview(None)
        self._update_metadata_control_states()

    def _metadata_document_label(self, document: dict) -> str:
        path = document.get("path")
        label = path.name if path else "新歌曲"
        if document.get("autolrc_linked"):
            label = f"Auto-LRC · {label}"
        if document.get("loading"):
            label = f"… {label}"
        elif document.get("load_error"):
            label = f"! {label}"
        if len(label) > 30:
            label = label[:27] + "…"
        return label

    def _schedule_metadata_tabs_scrollregion(self) -> None:
        if self._metadata_tabs_scrollregion_after is None:
            self._metadata_tabs_scrollregion_after = self.root.after_idle(
                self._update_metadata_tabs_scrollregion
            )

    def _bind_metadata_tabs_wheel(self, widget) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, self._scroll_metadata_tabs_wheel_region, add="+")

    def _update_metadata_tabs_scrollregion(self, *, refresh_controls: bool = True) -> None:
        self._metadata_tabs_scrollregion_after = None
        try:
            canvas_width = self.metadata_tabs_canvas.winfo_width()
            canvas_height = self.metadata_tabs_canvas.winfo_height()
            content_width = max(canvas_width, self.metadata_tabs_inner.winfo_reqwidth())
            if (
                content_width != self._metadata_tabs_window_width
                or canvas_height != self._metadata_tabs_window_height
            ):
                self._metadata_tabs_window_width = content_width
                self._metadata_tabs_window_height = canvas_height
                self.metadata_tabs_canvas.itemconfigure(
                    self._metadata_tabs_window,
                    width=content_width,
                    height=canvas_height,
                )
            self.metadata_tabs_canvas.configure(
                scrollregion=self.metadata_tabs_canvas.bbox("all") or (0, 0, 0, 0)
            )
            if refresh_controls:
                self._update_metadata_control_states()
        except tk.TclError:
            pass

    def _schedule_metadata_form_scrollregion(self) -> None:
        if self._metadata_form_scrollregion_after is None:
            self._metadata_form_scrollregion_after = self.root.after_idle(
                self._update_metadata_form_scrollregion
            )

    def _update_metadata_form_scrollregion(self) -> None:
        self._metadata_form_scrollregion_after = None
        try:
            self.metadata_form_canvas.configure(
                scrollregion=self.metadata_form_canvas.bbox("all") or (0, 0, 0, 0)
            )
        except tk.TclError:
            pass

    def _resize_metadata_form_window(self, window_id: int, width: int) -> None:
        if width == self._metadata_form_window_width:
            return
        self._metadata_form_window_width = width
        self.metadata_form_canvas.itemconfigure(window_id, width=width)

    def _refresh_metadata_tabs(self, *, reveal_active: bool = False) -> None:
        document_ids = tuple(document["id"] for document in self._metadata_documents)
        rebuild = document_ids != self._metadata_tabs_order
        if rebuild:
            for child in self.metadata_tabs_inner.winfo_children():
                child.destroy()
            self._metadata_tab_controls.clear()
            self._metadata_tab_widgets.clear()
            self._metadata_tabs_order = document_ids

            if not document_ids:
                tk.Label(
                    self.metadata_tabs_inner,
                    text="添加歌曲后会在这里显示编辑标签",
                    background=APP_BACKGROUND,
                    foreground=TEXT_SECONDARY,
                    font=("Microsoft YaHei UI", 9),
                    anchor="w",
                ).pack(side="left", padx=10, pady=8)

            for document in self._metadata_documents:
                document_id = document["id"]
                tab = tk.Frame(self.metadata_tabs_inner, background=APP_BACKGROUND, bd=0)
                tab.pack(side="left", padx=(0, 6), pady=3)
                select_button = tk.Button(
                    tab,
                    command=lambda identifier=document_id: self._select_metadata_document(identifier),
                    background=APP_BACKGROUND,
                    foreground=ui_color("nav_foreground"),
                    activebackground=ui_color("nav_selected_background"),
                    activeforeground=TEXT_PRIMARY,
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=11,
                    pady=6,
                    cursor="hand2",
                )
                self._bind_metadata_tabs_wheel(tab)
                self._bind_metadata_tabs_wheel(select_button)
                select_button.bind(
                    "<Double-Button-1>",
                    lambda _event, identifier=document_id: self._play_metadata_document(identifier),
                    add="+",
                )
                select_button.pack(side="left", fill="y")
                close_button = tk.Button(
                    tab,
                    text="×",
                    command=lambda identifier=document_id: self._close_metadata_document(identifier),
                    background=APP_BACKGROUND,
                    foreground=TEXT_SECONDARY,
                    activebackground=ui_color("metadata_close_hover_background"),
                    activeforeground=ui_color("metadata_close_hover_foreground"),
                    relief="flat",
                    bd=0,
                    highlightthickness=0,
                    padx=7,
                    pady=5,
                    font=("Microsoft YaHei UI", 10),
                    cursor="hand2",
                )
                self._bind_metadata_tabs_wheel(close_button)
                close_button.pack(side="left", fill="y")
                self._metadata_tab_widgets[document_id] = {
                    "tab": tab,
                    "select": select_button,
                    "close": close_button,
                    "label": None,
                    "selected": None,
                }
                self._metadata_tab_controls.extend((select_button, close_button))

        active_tab = None
        for document in self._metadata_documents:
            document_id = document["id"]
            widgets = self._metadata_tab_widgets.get(document_id)
            if widgets is None:
                continue
            selected = document_id == self._active_metadata_document_id
            background = ui_color("nav_selected_background") if selected else APP_BACKGROUND
            label = self._metadata_document_label(document)
            if widgets["label"] != label:
                widgets["select"].configure(text=label)
                widgets["label"] = label
            if widgets["selected"] != selected:
                widgets["tab"].configure(background=background)
                widgets["select"].configure(
                    background=background,
                    foreground=ui_color("nav_selected_foreground") if selected else ui_color("nav_foreground"),
                    font=("Microsoft YaHei UI", 9, "bold" if selected else "normal"),
                )
                widgets["close"].configure(
                    background=background,
                    foreground=TEXT_SECONDARY,
                )
                widgets["selected"] = selected
            if selected:
                active_tab = widgets["tab"]

        self._schedule_metadata_tabs_scrollregion()
        self._update_metadata_control_states()
        if reveal_active and active_tab is not None:
            self.root.after_idle(lambda target=active_tab: self._ensure_metadata_tab_visible(target))

    def _ensure_metadata_tab_visible(self, tab) -> None:
        canvas = self.metadata_tabs_canvas
        if not tab.winfo_exists() or canvas.winfo_width() <= 1:
            return
        x1 = tab.winfo_x()
        x2 = x1 + tab.winfo_width()
        visible_left = canvas.canvasx(0)
        visible_right = canvas.canvasx(canvas.winfo_width())
        total_width = max(1, self.metadata_tabs_inner.winfo_width())
        if x1 < visible_left:
            canvas.xview_moveto(max(0, x1 / total_width))
        elif x2 > visible_right:
            canvas.xview_moveto(min(1, (x2 - canvas.winfo_width()) / total_width))

    def _scroll_metadata_tabs(self, event):
        if not self._metadata_tabs_can_scroll():
            return None
        direction = -1 if event.delta > 0 else 1
        self._scroll_metadata_tabs_by(direction * 3)
        return "break"

    def _scroll_metadata_tabs_by(self, units: int) -> None:
        if not self._metadata_tabs_can_scroll():
            return
        self.metadata_tabs_canvas.xview_scroll(units, "units")
        self._update_metadata_control_states()

    def _metadata_tabs_can_scroll(self) -> bool:
        if len(self._metadata_documents) <= 1 or not hasattr(self, "metadata_tabs_canvas"):
            return False
        try:
            canvas_width = self.metadata_tabs_canvas.winfo_width()
            content_width = max(
                self.metadata_tabs_inner.winfo_width(),
                self.metadata_tabs_inner.winfo_reqwidth(),
            )
            if canvas_width > 1 and content_width > canvas_width + 1:
                return True
            first, last = self.metadata_tabs_canvas.xview()
        except tk.TclError:
            return False
        return first > 0.001 or last < 0.999

    def _scroll_metadata_tabs_wheel_region(self, event):
        rail = getattr(self, "metadata_tabs_rail", None)
        if rail is None or not rail.winfo_ismapped():
            return None
        if not self._metadata_tabs_can_scroll():
            self._metadata_tabs_wheel_remainder = 0.0
            return None
        left = rail.winfo_rootx()
        top = rail.winfo_rooty()
        right = left + rail.winfo_width()
        bottom = top + rail.winfo_height()
        pointer_x = getattr(event, "x_root", -1)
        pointer_y = getattr(event, "y_root", -1)
        if not (left <= pointer_x < right and top <= pointer_y < bottom):
            return None

        # Refresh widths synchronously so wheel input works immediately after importing tabs.
        self._update_metadata_tabs_scrollregion(refresh_controls=False)

        button_number = getattr(event, "num", None)
        delta = getattr(event, "delta", 0)
        if button_number == 4:
            units = -3
        elif button_number == 5:
            units = 3
        elif delta:
            self._metadata_tabs_wheel_remainder -= delta * 3 / 120
            units = int(self._metadata_tabs_wheel_remainder)
            self._metadata_tabs_wheel_remainder -= units
        else:
            return "break"
        if units:
            self._scroll_metadata_tabs_by(units)
        return "break"

    def _remember_metadata_form_parent_scroll(self, event, source_widget=None) -> None:
        previous = self._metadata_form_parent_scroll_lock
        source_widgets = (
            list(previous.get("source_widgets", []))
            if previous is not None
            else []
        )
        if source_widget is not None and source_widget not in source_widgets:
            source_widgets.append(source_widget)
        self._metadata_form_parent_scroll_lock = {
            "x": getattr(event, "x_root", None),
            "y": getattr(event, "y_root", None),
            "deadline": time.monotonic() + _wheel_handoff_cooldown_for(self.root),
            "source_widgets": source_widgets,
        }

    def _metadata_form_parent_scroll_locked(self, event, current_widget) -> bool:
        lock = self._metadata_form_parent_scroll_lock
        if lock is None:
            return False
        same_pointer = (
            getattr(event, "x_root", None) == lock.get("x")
            and getattr(event, "y_root", None) == lock.get("y")
        )
        if same_pointer and time.monotonic() < lock.get("deadline", 0):
            return True
        self._clear_metadata_form_parent_scroll_lock(current_widget)
        return False

    def _release_metadata_form_parent_scroll_on_motion(self, event) -> None:
        lock = self._metadata_form_parent_scroll_lock
        if lock is None:
            return
        if (
            getattr(event, "x_root", None) != lock.get("x")
            or getattr(event, "y_root", None) != lock.get("y")
        ):
            self._clear_metadata_form_parent_scroll_lock()

    def _clear_metadata_form_parent_scroll_lock(self, current_widget=None) -> None:
        lock = self._metadata_form_parent_scroll_lock
        source_widgets = lock.get("source_widgets", []) if lock is not None else []
        widgets_to_reset = [
            *source_widgets,
            getattr(self, "metadata_comment_text", None),
            getattr(self, "metadata_lyrics_text", None),
            current_widget,
        ]
        for widget in widgets_to_reset:
            if widget is not None:
                try:
                    widget._parent_wheel_handoff_until = {}
                except (AttributeError, tk.TclError):
                    pass
        self._metadata_form_parent_scroll_lock = None

    def _scroll_metadata_form(self, event, *, source_widget=None):
        if not event.delta:
            return "break"
        self._remember_metadata_form_parent_scroll(event, source_widget)
        self._metadata_form_wheel_remainder -= event.delta * 30 / 120
        units = int(self._metadata_form_wheel_remainder)
        if units:
            self._metadata_form_wheel_remainder -= units
            self.metadata_form_canvas.yview_scroll(units, "units")
        return "break"

    def _scroll_metadata_comment(self, event):
        if not event.delta:
            return "break"
        if self._metadata_form_parent_scroll_locked(
            event, self.metadata_comment_text
        ):
            return self._scroll_metadata_form(
                event, source_widget=self.metadata_comment_text
            )
        first, last = self.metadata_comment_text.yview()
        can_scroll_comment = (event.delta > 0 and first > 0) or (
            event.delta < 0 and last < 1
        )
        if not can_scroll_comment:
            self._metadata_comment_wheel_remainder = 0.0
            _, allow_parent = ScrollablePage._wheel_boundary_handoff(
                self.metadata_comment_text, event
            )
            return (
                self._scroll_metadata_form(
                    event, source_widget=self.metadata_comment_text
                )
                if allow_parent
                else "break"
            )
        self._metadata_comment_wheel_remainder -= event.delta * 3 / 120
        units = int(self._metadata_comment_wheel_remainder)
        if units:
            self._metadata_comment_wheel_remainder -= units
            self.metadata_comment_text.yview_scroll(units, "units")
            ScrollablePage._wheel_boundary_handoff(self.metadata_comment_text, event)
        return "break"

    def _scroll_metadata_lyrics(self, event):
        if not event.delta:
            return "break"
        if self._metadata_form_parent_scroll_locked(
            event, self.metadata_lyrics_text
        ):
            return self._scroll_metadata_form(
                event, source_widget=self.metadata_lyrics_text
            )
        first, last = self.metadata_lyrics_text.yview()
        can_scroll_lyrics = (event.delta > 0 and first > 0) or (
            event.delta < 0 and last < 1
        )
        if not can_scroll_lyrics:
            self._metadata_lyrics_wheel_remainder = 0.0
            _, allow_parent = ScrollablePage._wheel_boundary_handoff(
                self.metadata_lyrics_text, event
            )
            return (
                self._scroll_metadata_form(
                    event, source_widget=self.metadata_lyrics_text
                )
                if allow_parent
                else "break"
            )
        self._metadata_lyrics_wheel_remainder -= event.delta * 3 / 120
        units = int(self._metadata_lyrics_wheel_remainder)
        if units:
            self._metadata_lyrics_wheel_remainder -= units
            self.metadata_lyrics_text.yview_scroll(units, "units")
            ScrollablePage._wheel_boundary_handoff(self.metadata_lyrics_text, event)
        return "break"

    def _set_metadata_comment_text(self, value: str) -> None:
        widget = getattr(self, "metadata_comment_text", None)
        if widget is None:
            return
        previous_state = widget.cget("state")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if value:
            widget.insert("1.0", value)
        widget.edit_modified(False)
        widget.yview_moveto(0)
        widget.configure(state=previous_state)

    def _set_metadata_lyrics_text(self, value: str) -> None:
        widget = getattr(self, "metadata_lyrics_text", None)
        if widget is None:
            return
        previous_state = widget.cget("state")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if value:
            widget.insert("1.0", value)
        widget.edit_modified(False)
        widget.yview_moveto(0)
        widget.configure(state=previous_state)

    def _select_metadata_document(self, document_id: int) -> None:
        if document_id == self._active_metadata_document_id:
            widgets = self._metadata_tab_widgets.get(document_id)
            if widgets is not None:
                self.root.after_idle(
                    lambda target=widgets["tab"]: self._ensure_metadata_tab_visible(target)
                )
            return
        self._store_active_metadata_document()
        self._active_metadata_document_id = document_id
        document = self._active_metadata_document()
        if document is None:
            return
        self._render_active_metadata_document()
        self._refresh_metadata_tabs(reveal_active=True)
        self._sync_playback_controls()
        path = document.get("path")
        if path and not document.get("loaded") and not document.get("loading"):
            self._load_metadata_audio(path, document_id)
        elif document.get("loading"):
            self.status_text.set(f"正在读取音频标签：{path.name}")

    def _play_metadata_document(self, document_id: int):
        self._select_metadata_document(document_id)
        document = next(
            (item for item in self._metadata_documents if item["id"] == document_id),
            None,
        )
        path = document.get("path") if document is not None else None
        if path:
            self._play_or_resume_playback(path)
        return "break"

    def _close_metadata_document(self, document_id: int) -> None:
        self._store_active_metadata_document()
        index = next(
            (index for index, document in enumerate(self._metadata_documents) if document["id"] == document_id),
            None,
        )
        if index is None:
            return
        was_active = document_id == self._active_metadata_document_id
        self._metadata_documents.pop(index)
        if was_active:
            if self._metadata_documents:
                replacement = self._metadata_documents[min(index, len(self._metadata_documents) - 1)]
                self._active_metadata_document_id = replacement["id"]
                self._render_active_metadata_document()
            else:
                self._active_metadata_document_id = None
                self._render_active_metadata_document()
        self._refresh_metadata_tabs(reveal_active=True)

    def _open_metadata_document(self, path: Path, *, autolrc_linked: bool = False) -> None:
        path = path.absolute()
        current_index = next(
            (
                index
                for index, item in enumerate(self._metadata_documents)
                if item.get("path") == path
            ),
            None,
        )
        if (
            autolrc_linked
            and current_index == 0
            and self._metadata_documents[0].get("autolrc_linked")
            and self._active_metadata_document_id == self._metadata_documents[0]["id"]
        ):
            return
        self._store_active_metadata_document()
        if autolrc_linked:
            for document in self._metadata_documents:
                document["autolrc_linked"] = False

        document = next(
            (item for item in self._metadata_documents if item.get("path") == path),
            None,
        )
        if document is None:
            document = self._new_metadata_document(path, autolrc_linked=autolrc_linked)
            if autolrc_linked:
                self._metadata_documents.insert(0, document)
            else:
                self._metadata_documents.append(document)
        else:
            document["autolrc_linked"] = autolrc_linked or document.get("autolrc_linked", False)
            if autolrc_linked:
                self._metadata_documents.remove(document)
                self._metadata_documents.insert(0, document)

        self._active_metadata_document_id = document["id"]
        self._render_active_metadata_document()
        self._refresh_metadata_tabs(reveal_active=True)
        if not document.get("loaded") and not document.get("loading"):
            self._load_metadata_audio(path, document["id"])

    def _sync_autolrc_metadata_document(self, path: Path) -> None:
        self._open_metadata_document(path, autolrc_linked=True)

    def _load_metadata_audio(
        self,
        path: Path,
        document_id: int | None = None,
        *,
        refresh_tabs: bool = True,
    ) -> None:
        path = path.absolute()
        document_id = document_id or self._active_metadata_document_id
        document = next(
            (item for item in self._metadata_documents if item["id"] == document_id),
            None,
        )
        if document is None:
            return
        if document.get("loading") and document.get("path") == path:
            return

        document["path"] = path
        document["loading"] = True
        document["load_error"] = ""
        document["load_token"] += 1
        load_token = document["load_token"]
        if document_id == self._active_metadata_document_id:
            self.metadata_audio_path.set(str(path))
            self.status_text.set(f"正在读取音频标签：{path.name}")
        if refresh_tabs:
            self._refresh_metadata_tabs(
                reveal_active=document_id == self._active_metadata_document_id
            )

        def work() -> None:
            metadata = None
            embedded_lyrics: list[str] = []
            error = ""
            missing = False
            preview_image = None
            preview_failed = False
            try:
                if not path.is_file():
                    raise FileNotFoundError
                metadata = read_audio_metadata(path)
                embedded_lyrics = read_embedded_lyrics(path)
                cover_data = metadata.get("cover_data")
                if cover_data:
                    try:
                        from PIL import Image

                        validate_cover_data(cover_data)
                        with Image.open(io.BytesIO(cover_data)) as image:
                            image.thumbnail((COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE))
                            preview_image = image.convert("RGB")
                    except Exception:
                        preview_failed = True
            except FileNotFoundError:
                error = "所选音频已移动或删除。"
                missing = True
            except Exception as exc:
                error = str(exc)
            try:
                self.root.after(
                    0,
                    self._metadata_audio_load_finished,
                    document_id,
                    path,
                    load_token,
                    metadata,
                    embedded_lyrics,
                    error,
                    missing,
                    preview_image,
                    preview_failed,
                )
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _metadata_audio_load_finished(
        self,
        document_id: int,
        path: Path,
        load_token: int,
        metadata: dict | None,
        embedded_lyrics: list[str],
        error: str,
        missing: bool,
        preview_image,
        preview_failed: bool,
    ) -> None:
        document = next(
            (item for item in self._metadata_documents if item["id"] == document_id),
            None,
        )
        if document is None or load_token != document.get("load_token"):
            return
        if error or metadata is None:
            document["loading"] = False
            document["load_error"] = error or "未知错误"
            document["loaded"] = False
            if document_id == self._active_metadata_document_id:
                self._render_active_metadata_document()
                self.status_text.set("音频标签读取失败。")
            self._refresh_metadata_tabs()
            if document_id == self._active_metadata_document_id and self._active_page == "audio":
                if missing:
                    messagebox.showwarning("找不到音频", error, parent=self.root)
                else:
                    messagebox.showerror("无法读取音频标签", error or "未知错误", parent=self.root)
            return

        document["loading"] = False
        document["loaded"] = True
        document["load_error"] = ""
        document["fields"] = {
            field: metadata["fields"].get(field, "") for field in self.metadata_values
        }
        document["saved_fields"] = dict(document["fields"])
        normalized_lyrics = [
            content.replace("\r\n", "\n").replace("\r", "\n")
            for content in embedded_lyrics
            if isinstance(content, str)
        ]
        document["embedded_lyrics"] = list(normalized_lyrics)
        document["saved_embedded_lyrics"] = list(normalized_lyrics)
        document["cover_data"] = metadata.get("cover_data")
        document["selected_cover"] = None
        document["selected_cover_name"] = ""
        document["selected_cover_preview"] = None
        document["remove_cover"] = False
        document["preview_image"] = preview_image
        document["preview_failed"] = preview_failed
        if document_id == self._active_metadata_document_id:
            self._render_active_metadata_document()
            self.status_text.set(f"已载入音频标签：{path.name}")
        self._refresh_metadata_tabs()

    def _update_metadata_control_states(self) -> None:
        busy = self._operation_busy
        document = self._active_metadata_document()
        loading = bool(document and document.get("loading"))
        has_loaded_document = bool(document and document.get("loaded"))
        control_state = "disabled" if busy or loading or not has_loaded_document else "normal"
        for control in self._metadata_controls:
            control.configure(state=control_state)
        if hasattr(self, "metadata_audio_entry"):
            self.metadata_audio_entry.configure(
                state="disabled" if busy or document is None else "readonly"
            )
        for button in self._metadata_action_buttons:
            is_audio_picker = button in (
                getattr(self, "metadata_choose_audio_button", None),
                getattr(self, "metadata_use_alignment_audio_button", None),
            )
            button.configure(
                state="disabled"
                if busy or (not is_audio_picker and (loading or not has_loaded_document))
                else "normal"
            )
        for control in self._metadata_tab_controls:
            control.configure(state="disabled" if busy else "normal")
        try:
            first, last = self.metadata_tabs_canvas.xview()
        except (AttributeError, tk.TclError):
            first, last = 0.0, 1.0
        can_scroll = len(self._metadata_documents) > 1 and (
            first > 0.001 or last < 0.999
        )
        left_button = getattr(self, "metadata_tabs_left_button", None)
        right_button = getattr(self, "metadata_tabs_right_button", None)
        if left_button is not None:
            left_button.configure(
                state="normal" if can_scroll and first > 0.001 and not busy else "disabled"
            )
        if right_button is not None:
            right_button.configure(
                state="normal" if can_scroll and last < 0.999 and not busy else "disabled"
            )

    def _set_metadata_cover_preview(
        self,
        image_data: bytes | None,
        name: str = "",
        *,
        preview_image=None,
        preview_failed: bool = False,
    ) -> None:
        self._metadata_cover_photo = None
        if not image_data:
            self.metadata_cover_preview.configure(image="", text="未设置封面")
            self.metadata_cover_name.configure(text=name)
            return
        if preview_failed:
            self.metadata_cover_preview.configure(image="", text="封面已载入\n无法显示预览")
            self.metadata_cover_name.configure(text=name or "当前内嵌封面")
            return
        try:
            from PIL import Image, ImageTk

            if preview_image is None:
                validate_cover_data(image_data)
                with Image.open(io.BytesIO(image_data)) as image:
                    image.thumbnail((COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE))
                    preview_image = image.convert("RGB")
            self._metadata_cover_photo = ImageTk.PhotoImage(preview_image)
            self.metadata_cover_preview.configure(image=self._metadata_cover_photo, text="")
            self.metadata_cover_name.configure(text=name or "当前内嵌封面")
        except Exception:
            self.metadata_cover_preview.configure(image="", text="封面已载入\n无法显示预览")
            self.metadata_cover_name.configure(text=name)

    def _choose_metadata_cover(self) -> None:
        initial_directory = self.cover_image_directory
        if not initial_directory.is_dir():
            current_audio = Path(self.metadata_audio_path.get().strip())
            initial_directory = current_audio.parent if current_audio.is_file() else ROOT_DIR
        path = filedialog.askopenfilename(
            title="选择专辑封面",
            initialdir=str(initial_directory),
            filetypes=[("封面图片", "*.jpg *.jpeg *.png"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            image_data = Path(path).read_bytes()
            validate_cover_data(image_data)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法载入封面图片", str(exc), parent=self.root)
            return
        except ImportError:
            messagebox.showerror(
                "缺少图片处理组件",
                "请运行 InstallAutoLrc.cmd 更新本机依赖后重试。",
                parent=self.root,
            )
            return
        self._metadata_selected_cover = image_data
        self._metadata_selected_cover_name = Path(path).name
        self._metadata_remove_cover = False
        self._set_metadata_cover_preview(image_data, self._metadata_selected_cover_name)

    def _choose_cover_image_directory(self) -> None:
        initial_directory = (
            self.cover_image_directory
            if self.cover_image_directory.is_dir()
            else ROOT_DIR
        )
        selected_directory = filedialog.askdirectory(
            title="设置封面图片选择器的默认文件夹",
            initialdir=str(initial_directory),
            mustexist=True,
            parent=self.root,
        )
        if not selected_directory:
            return
        self.cover_image_directory = Path(selected_directory).resolve()
        self.settings_cover_directory.set(str(self.cover_image_directory))
        self._save_settings()
        self.status_text.set(f"封面图片默认文件夹已设置为：{self.cover_image_directory}")

    def _default_input_directory(self) -> Path:
        if self.input_directory.is_dir():
            return self.input_directory
        fallback = ROOT_DIR / "input"
        return fallback if fallback.is_dir() else ROOT_DIR

    def _choose_input_directory(self) -> None:
        initial_directory = self._default_input_directory()
        selected_directory = filedialog.askdirectory(
            title="设置默认输入文件夹",
            initialdir=str(initial_directory),
            mustexist=True,
            parent=self.root,
        )
        if not selected_directory:
            return
        self.input_directory = Path(selected_directory).resolve()
        self.settings_input_directory.set(str(self.input_directory))
        self._save_settings()
        self.status_text.set(f"默认输入文件夹已设置为：{self.input_directory}")

    def _choose_output_directory(self) -> None:
        initial_directory = self.output_directory if self.output_directory.is_dir() else ROOT_DIR
        selected_directory = filedialog.askdirectory(
            title="设置默认输出文件夹",
            initialdir=str(initial_directory),
            mustexist=True,
            parent=self.root,
        )
        if not selected_directory:
            return
        self.output_directory = Path(selected_directory).resolve()
        self.settings_output_directory.set(str(self.output_directory))
        self._save_settings()
        self.status_text.set(f"默认输出文件夹已设置为：{self.output_directory}")

    def _remove_metadata_cover(self) -> None:
        self._metadata_selected_cover = None
        self._metadata_selected_cover_name = ""
        self._metadata_remove_cover = True
        self._set_metadata_cover_preview(None, "保存时会移除现有封面")

    def _handle_audio_save_shortcut(self, _event=None):
        if self._active_page != "audio":
            return None
        if self._operation_busy:
            return "break"
        self._save_all_metadata_audio()
        return "break"

    def _save_all_metadata_audio(self) -> None:
        self._store_active_metadata_document()
        snapshots = []
        invalid_tracks = []
        for document in self._metadata_documents:
            if not document.get("loaded") or document.get("loading"):
                continue
            fields = {
                key: str(document.get("fields", {}).get(key, "")).strip()
                for key in self.metadata_values
            }
            saved_fields = {
                key: str(document.get("saved_fields", {}).get(key, "")).strip()
                for key in self.metadata_values
            }
            selected_cover = document.get("selected_cover")
            remove_cover = bool(document.get("remove_cover"))
            embedded_lyrics = list(document.get("embedded_lyrics", []))
            lyrics_changed = embedded_lyrics != list(document.get("saved_embedded_lyrics", []))
            if (
                fields == saved_fields
                and selected_cover is None
                and not remove_cover
                and not lyrics_changed
            ):
                continue
            if fields["track_number"] and not re.fullmatch(r"\d+(?:/\d+)?", fields["track_number"]):
                invalid_tracks.append(document.get("path", Path("未知文件")).name)
                continue
            snapshots.append(
                {
                    "id": document["id"],
                    "path": Path(document["path"]),
                    "fields": fields,
                    "cover_data": selected_cover,
                    "remove_cover": remove_cover,
                    "embedded_lyrics": embedded_lyrics,
                    "lyrics_changed": lyrics_changed,
                }
            )

        if invalid_tracks:
            messagebox.showwarning(
                "音轨编号格式有误",
                "以下歌曲的音轨编号需要使用数字或“当前/总数”格式（例如 2/12），修正后再按 Ctrl+S：\n\n"
                + "\n".join(invalid_tracks),
                parent=self.root,
            )
            return
        if not snapshots:
            self.status_text.set("没有检测到已修改的标签、封面或歌词。")
            return

        self._set_busy(True)
        self.status_text.set(f"正在保存 {len(snapshots)} 个已修改的音频文件…")

        def work() -> None:
            saved = []
            errors = []
            for snapshot in snapshots:
                path = snapshot["path"]
                try:
                    if not path.is_file():
                        raise FileNotFoundError("音频文件已移动或删除。")
                    def edit_copy(temporary_path, current=snapshot) -> None:
                        write_audio_metadata(
                            temporary_path,
                            current["fields"],
                            current["cover_data"],
                            current["remove_cover"],
                        )
                        if current["lyrics_changed"]:
                            write_embedded_lyrics(
                                temporary_path, current["embedded_lyrics"]
                            )

                    _create_edited_audio_copy(path, path, edit_copy)
                    saved.append(snapshot)
                except Exception as exc:
                    errors.append((path.name, str(exc)))
            try:
                self.root.after(
                    0,
                    lambda completed=saved, failed=errors: self._metadata_save_all_finished(
                        completed, failed
                    ),
                )
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _metadata_save_all_finished(self, saved: list[dict], errors: list[tuple[str, str]]) -> None:
        self._set_busy(False)
        for snapshot in saved:
            document = next(
                (item for item in self._metadata_documents if item["id"] == snapshot["id"]),
                None,
            )
            if document is None:
                continue
            document["fields"] = dict(snapshot["fields"])
            document["saved_fields"] = dict(snapshot["fields"])
            if snapshot["lyrics_changed"]:
                document["embedded_lyrics"] = list(snapshot["embedded_lyrics"])
                document["saved_embedded_lyrics"] = list(snapshot["embedded_lyrics"])
            if snapshot["remove_cover"]:
                document["cover_data"] = None
                document["preview_image"] = None
                document["preview_failed"] = False
            elif snapshot["cover_data"] is not None:
                document["cover_data"] = snapshot["cover_data"]
                document["preview_image"] = None
                document["preview_failed"] = False
            document["selected_cover"] = None
            document["selected_cover_name"] = ""
            document["selected_cover_preview"] = None
            document["remove_cover"] = False
            self._load_metadata_audio(
                snapshot["path"], document["id"], refresh_tabs=False
            )
        self._refresh_metadata_tabs()
        if errors:
            self.status_text.set(f"已保存 {len(saved)} 个文件；{len(errors)} 个文件保存失败。")
            details = "\n".join(f"{name}：{error}" for name, error in errors)
            messagebox.showerror(
                "部分音频保存失败",
                f"成功保存 {len(saved)} 个文件，失败 {len(errors)} 个。\n\n{details}",
                parent=self.root,
            )
        else:
            self.status_text.set(f"已保存 {len(saved)} 个文件的修改。")

    def _save_metadata_audio(self) -> None:
        source_path = Path(self.metadata_audio_path.get().strip())
        if not source_path.is_file():
            messagebox.showwarning("先选择音频", "请选择要编辑标签的音频。", parent=self.root)
            return
        self._store_active_metadata_document()
        fields = {key: variable.get().strip() for key, variable in self.metadata_values.items()}
        if fields["track_number"] and not re.fullmatch(r"\d+(?:/\d+)?", fields["track_number"]):
            messagebox.showwarning(
                "音轨编号格式有误",
                "请使用数字或“当前/总数”格式，例如 2/12。",
                parent=self.root,
            )
            return

        cover_data = self._metadata_selected_cover
        remove_cover = self._metadata_remove_cover
        document = self._active_metadata_document()
        embedded_lyrics = list(document.get("embedded_lyrics", [])) if document else []
        lyrics_changed = bool(
            document
            and embedded_lyrics != list(document.get("saved_embedded_lyrics", []))
        )
        self._set_busy(True)
        self.status_text.set("正在保存音频标签…")

        def work() -> None:
            try:
                def edit_copy(path: Path) -> None:
                    write_audio_metadata(path, fields, cover_data, remove_cover)
                    if lyrics_changed:
                        write_embedded_lyrics(path, embedded_lyrics)

                _create_edited_audio_copy(
                    source_path,
                    source_path,
                    edit_copy,
                )
                self.root.after(
                    0,
                    lambda: self._metadata_save_finished(source_path),
                )
            except Exception as exc:
                error_message = str(exc)
                try:
                    self.root.after(0, lambda message=error_message: self._metadata_save_failed(message))
                except tk.TclError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _format_active_embedded_lyrics(self) -> None:
        document = self._active_metadata_document()
        if document is None or not document.get("loaded") or document.get("loading"):
            messagebox.showwarning("先选择音频", "请先在音频编辑页打开一首歌曲。", parent=self.root)
            return
        self._store_active_metadata_document()
        path = Path(document["path"])
        if not path.is_file():
            messagebox.showwarning("找不到音频", "所选音频已移动或删除。", parent=self.root)
            return

        embedded_contents = list(document.get("embedded_lyrics", []))
        formatted_contents = []
        original_contents = []
        formatted_count = 0
        audio_fields = document.get("saved_fields", {})
        for content in embedded_contents:
            if not content.strip():
                continue
            original_contents.append(content)
            parsed = _normalize_lyrics_content(
                content,
                {},
                audio_fields,
                self.lyric_template_language.get(),
            )
            lyric_count = sum(bool(line.strip()) for line in parsed[0])
            if lyric_count:
                formatted_contents.append(_render_lyrics_content(*parsed))
                formatted_count += 1
            else:
                formatted_contents.append(content)
        if not formatted_count:
            messagebox.showinfo(
                "没有可整理的内嵌歌词",
                "当前音频没有可识别的内嵌歌词。可先在打轴页导入歌词，再导出内嵌歌词音频。",
                parent=self.root,
            )
            return

        changed = any(
            formatted.strip() != original.replace("\r\n", "\n").strip()
            for formatted, original in zip(formatted_contents, original_contents)
        )
        if not changed:
            self.status_text.set(f"{path.name} 的内嵌歌词信息已经符合预设格式。")
            return

        if not messagebox.askyesno(
            "统一歌词信息格式",
            f"将按预设整理“{path.name}”的内嵌歌词信息，使用当前已保存音频标签中的歌名、歌手、专辑，并补入缺失的作曲署名；已有原唱、作词、作曲等署名、歌词文本和时间轴都会保留。\n\n"
            "确认后会直接更新此音频文件的内嵌歌词标签，其他音频标签保持不变。继续吗？",
            parent=self.root,
        ):
            return

        try:
            if self._player is not None and self._player_path == path.resolve():
                self._release_player()
        except OSError:
            pass
        self._set_busy(True)
        self.status_text.set(f"正在统一 {path.name} 的内嵌歌词信息格式…")

        def work() -> None:
            try:
                _create_edited_audio_copy(
                    path,
                    path,
                    lambda temporary_path: write_embedded_lyrics(
                        temporary_path,
                        formatted_contents,
                    ),
                )
                self.root.after(
                    0,
                    lambda contents=list(formatted_contents): self._metadata_lyrics_format_finished(
                        path, contents
                    ),
                )
            except Exception as exc:
                error_message = str(exc)
                try:
                    self.root.after(
                        0,
                        lambda message=error_message: self._metadata_lyrics_format_failed(message),
                    )
                except tk.TclError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _metadata_lyrics_format_finished(self, path: Path, contents: list[str]) -> None:
        self._set_busy(False)
        document = next(
            (item for item in self._metadata_documents if item.get("path") == path),
            None,
        )
        if document is not None:
            document["embedded_lyrics"] = list(contents)
            document["saved_embedded_lyrics"] = list(contents)
            if document["id"] == self._active_metadata_document_id:
                self._render_active_metadata_document()
        self.status_text.set(f"已统一 {path.name} 的内嵌歌词信息格式。")

    def _metadata_lyrics_format_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_text.set("统一内嵌歌词信息格式失败。")
        messagebox.showerror("无法统一歌词信息格式", message, parent=self.root)

    def _metadata_save_finished(self, source_path: Path) -> None:
        self._set_busy(False)
        self._load_metadata_audio(source_path)
        self.status_text.set(f"已覆盖保存原音频：{source_path}")

    def _metadata_save_failed(self, error_message: str) -> None:
        self._set_busy(False)
        self.status_text.set("音频标签保存失败。")
        messagebox.showerror("保存音频标签失败", error_message, parent=self.root)

    def _load_settings(self) -> None:
        settings_path = self.settings_file
        if not settings_path.is_file() and LEGACY_SETTINGS_FILE.is_file():
            settings_path = LEGACY_SETTINGS_FILE
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(settings, dict):
            return
        theme = settings.get("theme")
        if isinstance(theme, str) and theme in THEMES:
            self.theme = theme
        device_mode = settings.get("device_mode")
        if device_mode in {"自动", "GPU（CUDA）", "CPU"}:
            self.device_mode.set(device_mode)
        vocalize = settings.get("vocalize_enabled")
        terms_acknowledged = settings.get("demucs_model_terms_acknowledged") is True
        self.demucs_model_terms_acknowledged = terms_acknowledged
        if isinstance(vocalize, bool):
            self.vocalize_enabled.set(vocalize and terms_acknowledged)
        show_playback_milliseconds = settings.get("show_playback_milliseconds")
        if isinstance(show_playback_milliseconds, bool):
            self.show_playback_milliseconds.set(show_playback_milliseconds)
        lyric_draft_panel_height = settings.get("lyric_draft_panel_height")
        if isinstance(lyric_draft_panel_height, int) and not isinstance(lyric_draft_panel_height, bool):
            self.lyric_draft_panel_height.set(
                str(
                    max(
                        MIN_LYRIC_DRAFT_PANEL_HEIGHT,
                        min(MAX_LYRIC_DRAFT_PANEL_HEIGHT, lyric_draft_panel_height),
                    )
                )
            )
        wheel_handoff_cooldown_seconds = settings.get("wheel_handoff_cooldown_seconds")
        if (
            isinstance(wheel_handoff_cooldown_seconds, (int, float))
            and not isinstance(wheel_handoff_cooldown_seconds, bool)
            and math.isfinite(wheel_handoff_cooldown_seconds)
        ):
            self.wheel_handoff_cooldown_seconds.set(
                f"{_normalize_wheel_handoff_cooldown(wheel_handoff_cooldown_seconds):.1f}"
            )
        lyric_template_language = settings.get("lyric_template_language")
        if lyric_template_language in LYRIC_TEMPLATE_LABELS:
            self.lyric_template_language.set(lyric_template_language)
        audio_path = settings.get("audio_path")
        if isinstance(audio_path, str):
            self.audio_path.set(audio_path)
        geometry = settings.get("window_geometry")
        if isinstance(geometry, str) and re.fullmatch(r"\d{3,5}x\d{3,5}(?:[+-]\d+){0,2}", geometry):
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                pass
        boundary_checkpoint = settings.get("boundary_checkpoint")
        if isinstance(boundary_checkpoint, str) and boundary_checkpoint:
            checkpoint_path = Path(boundary_checkpoint)
            if checkpoint_path.is_file():
                self.boundary_checkpoint = checkpoint_path.resolve()
        training_data_directory = settings.get("training_data_directory")
        if isinstance(training_data_directory, str) and training_data_directory:
            training_path = Path(training_data_directory)
            if training_path.is_dir():
                self.training_data_directory = training_path.resolve()
        cover_image_directory = settings.get("cover_image_directory")
        if isinstance(cover_image_directory, str) and cover_image_directory:
            cover_path = Path(cover_image_directory)
            if cover_path.is_dir():
                self.cover_image_directory = cover_path.resolve()
        input_directory = settings.get("input_directory")
        if isinstance(input_directory, str) and input_directory:
            input_path = Path(input_directory)
            if input_path.is_dir():
                self.input_directory = input_path.resolve()
        output_directory = settings.get("output_directory")
        if isinstance(output_directory, str) and output_directory:
            output_path = Path(output_directory)
            if output_path.is_dir():
                self.output_directory = output_path.resolve()
        self._settings_were_loaded = True

    def _queue_settings_save(self, *_args) -> None:
        if self._settings_save_after is not None:
            try:
                self.root.after_cancel(self._settings_save_after)
            except tk.TclError:
                pass
        self._settings_save_after = self.root.after(400, self._save_settings)

    def _save_settings(self) -> None:
        self._settings_save_after = None
        settings = {
            "audio_path": self.audio_path.get().strip(),
            "theme": self.theme,
            "device_mode": self.device_mode.get(),
            "vocalize_enabled": bool(self.vocalize_enabled.get()),
            "demucs_model_terms_acknowledged": self.demucs_model_terms_acknowledged,
            "show_playback_milliseconds": bool(self.show_playback_milliseconds.get()),
            "lyric_draft_panel_height": self._lyric_draft_panel_height_value(),
            "wheel_handoff_cooldown_seconds": self._wheel_handoff_cooldown_value(),
            "lyric_template_language": self.lyric_template_language.get(),
            "window_geometry": self.root.geometry(),
            "boundary_checkpoint": str(self.boundary_checkpoint) if self.boundary_checkpoint else "",
            "training_data_directory": str(self.training_data_directory) if self.training_data_directory else "",
            "cover_image_directory": str(self.cover_image_directory),
            "input_directory": str(self.input_directory),
            "output_directory": str(self.output_directory),
        }
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = self.settings_file.with_suffix(".tmp")
            temporary_file.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_file.replace(self.settings_file)
        except OSError as exc:
            self.status_text.set(f"无法保存设置：{exc}")

    def _restore_default_settings(self) -> None:
        self.device_mode.set("自动")
        self.vocalize_enabled.set(False)
        self.demucs_model_terms_acknowledged = False
        self.show_playback_milliseconds.set(False)
        self.lyric_draft_panel_height.set(str(DEFAULT_LYRIC_DRAFT_PANEL_HEIGHT))
        self._apply_lyric_draft_panel_height()
        self.wheel_handoff_cooldown_seconds.set(
            f"{DEFAULT_WHEEL_HANDOFF_COOLDOWN_SECONDS:.1f}"
        )
        self._apply_wheel_handoff_cooldown()
        self.lyric_template_language.set("zh")
        self._update_lyric_template_preview()
        default_input_directory = ROOT_DIR / "input"
        self.input_directory = default_input_directory if default_input_directory.is_dir() else ROOT_DIR
        self.output_directory = ROOT_DIR / "output"
        self.cover_image_directory = ROOT_DIR / "covers"
        try:
            self.cover_image_directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.cover_image_directory = ROOT_DIR
        if hasattr(self, "settings_cover_directory"):
            self.settings_cover_directory.set(str(self.cover_image_directory))
        if hasattr(self, "settings_input_directory"):
            self.settings_input_directory.set(str(self.input_directory))
        if hasattr(self, "settings_output_directory"):
            self.settings_output_directory.set(str(self.output_directory))
        self.root.geometry("1180x760")
        if self._settings_save_after is not None:
            try:
                self.root.after_cancel(self._settings_save_after)
            except tk.TclError:
                pass
            self._settings_save_after = None
        self._settings_save_after = self.root.after(200, self._save_restored_settings)
        self.status_text.set("已恢复默认设置：运行选项、播放时间显示、歌词模板、输入/输出/封面文件夹和窗口布局。")

    def _save_restored_settings(self) -> None:
        self._settings_save_after = None
        self._save_settings()

    def _on_vocalization_toggle(self) -> None:
        if not self.vocalize_enabled.get() or self.demucs_model_terms_acknowledged:
            return
        accepted = messagebox.askyesno(
            "人声分离模型使用说明",
            "人声分离使用的 Demucs mdx_extra 预训练权重不随 Lyrics 发布。"
            "Demucs 维护者说明该权重仅供科学用途；启用后，首次打轴会从上游服务下载较大的模型文件。\n\n"
            "只有在你已阅读并接受该模型权重的适用条款、且使用范围符合条款时，才继续启用。",
            parent=self.root,
        )
        if not accepted:
            self.vocalize_enabled.set(False)
            return
        self.demucs_model_terms_acknowledged = True
        self._save_settings()

    def _choose_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="选择音频",
            initialdir=str(self._default_input_directory()),
            filetypes=[("音频文件", "*.mp3 *.flac *.wav *.m4a *.ogg *.aac *.opus"), ("所有文件", "*.*")],
        )
        if path:
            self._release_player()
            self.audio_path.set(path)
            self.player_note_text.set("音频已选择；双击歌词行可试听。")

    def _next_input_audio_file(self, deleted_path: Path) -> Path | None:
        input_directory = self._default_input_directory()
        try:
            candidates = sorted(
                (
                    path
                    for path in input_directory.iterdir()
                    if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
                ),
                key=lambda path: path.name.casefold(),
            )
            same_directory = deleted_path.resolve().parent == input_directory.resolve()
        except OSError:
            return None
        if not candidates:
            return None
        if same_directory:
            next_path = next(
                (path for path in candidates if path.name.casefold() > deleted_path.name.casefold()),
                None,
            )
            if next_path is not None:
                return next_path
        return candidates[0]

    def _delete_current_audio(self) -> None:
        audio_path = Path(self.audio_path.get().strip())
        if not audio_path.is_file():
            messagebox.showwarning("先选音频", "请选择本机上的音频文件。", parent=self.root)
            return
        try:
            source_path = audio_path.resolve()
        except OSError as exc:
            messagebox.showerror("无法访问音频", str(exc), parent=self.root)
            return
        if self._embedded_audio_exported_source != source_path:
            self._update_delete_audio_button_state()
            messagebox.showwarning(
                "请先导出内嵌音频",
                "成功导出当前音频的内嵌歌词版本后，才能删除当前音频。",
                parent=self.root,
            )
            return
        if self._embedded_audio_exported_output == source_path:
            messagebox.showwarning(
                "无法安全删除",
                "内嵌音频导出到了原音频的相同路径。请先把默认输出文件夹改为其他文件夹，再重新导出。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "删除当前音频",
            f"确定要永久删除当前音频文件吗？\n\n{audio_path}",
            parent=self.root,
        ):
            return

        self._release_player()
        try:
            audio_path.unlink()
        except OSError as exc:
            messagebox.showerror("删除音频失败", str(exc), parent=self.root)
            return

        next_audio = self._next_input_audio_file(audio_path)
        self._load_lyrics_into_editor([], [], [], False)
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self.audio_path.set(str(next_audio) if next_audio is not None else "")
        if next_audio is None:
            self.player_note_text.set("当前音频已删除；默认输入文件夹中没有其他音频。")
            self.status_text.set(f"已删除 {audio_path.name}；默认输入文件夹中没有其他音频文件。")
        else:
            self.player_note_text.set("已切换到下一首音频；请导入对应歌词。")
            self.status_text.set(f"已删除 {audio_path.name}，已自动选择下一首：{next_audio.name}")
        self._update_delete_audio_button_state()

    def _playback_target_audio_path(self) -> Path | None:
        if self._active_page == "audio":
            document = self._active_metadata_document()
            audio_path = document.get("path") if document is not None else None
        else:
            audio_path = self.audio_path.get().strip()
        if not audio_path:
            return None
        return Path(audio_path).absolute()

    def _ensure_player_open(
        self,
        on_ready=None,
        *,
        audio_path: Path | str | None = None,
    ) -> bool:
        audio = Path(audio_path) if audio_path is not None else self._playback_target_audio_path()
        if audio is None or not audio.is_file():
            messagebox.showwarning("先选音频", "请选择本机上的音频文件。", parent=self.root)
            return False
        try:
            audio = audio.resolve()
            if self._player is not None and self._player_path == audio:
                return True
            if self._player_loading:
                if self._player_loading_path == audio:
                    if on_ready is not None:
                        self._pending_player_action = on_ready
                    return False
                self._release_player()
            if self._player is not None:
                self._release_player()
        except Exception as exc:
            messagebox.showerror("无法播放音频", str(exc), parent=self.root)
            return False

        self._player_load_token += 1
        load_token = self._player_load_token
        self._player_loading = True
        self._player_loading_path = audio
        self._pending_player_action = on_ready
        self.player_note_text.set("正在准备音频播放器；首次使用可能稍慢，请稍候…")
        self._sync_playback_controls()

        def load_player() -> None:
            player = None
            duration_ms = 0
            error = None
            try:
                player = PygameAudioPlayer()
                duration_ms = player.open(audio)
            except Exception as exc:
                error = exc
                if player is not None:
                    try:
                        player.close()
                    except Exception:
                        pass
                    player = None

            def finish_loading() -> None:
                if load_token != self._player_load_token:
                    if player is not None:
                        try:
                            player.close()
                        except Exception:
                            pass
                    return

                if self._player_loading_path != audio:
                    if player is not None:
                        try:
                            player.close()
                        except Exception:
                            pass
                    self._player_loading = False
                    self._player_loading_path = None
                    self._pending_player_action = None
                    self._sync_playback_controls()
                    return

                self._player_loading = False
                self._player_loading_path = None
                if error is not None or player is None:
                    self._pending_player_action = None
                    self.player_note_text.set(f"播放器准备失败：{error or '未知错误'}")
                    self._sync_playback_controls()
                    messagebox.showerror(
                        "无法播放音频",
                        str(error or "音频播放器未能打开文件。"),
                        parent=self.root,
                    )
                    return

                self._player = player
                self._player_path = audio
                self.player_scale.set_range(max(1, duration_ms))
                self._set_player_position(0)
                self.player_note_text.set(
                    "点击“播放 / 继续”或“暂停”控制音乐；双击歌词行试听，或拖动播放条定位。"
                )
                pending_action = self._pending_player_action
                self._pending_player_action = None
                self._sync_playback_controls()
                if pending_action is not None:
                    pending_action()

            try:
                self.root.after(0, finish_loading)
            except Exception:
                if player is not None:
                    try:
                        player.close()
                    except Exception:
                        pass

        threading.Thread(target=load_player, daemon=True).start()
        return False

    @staticmethod
    def _format_player_time(position_ms: int) -> str:
        position_ms = max(0, int(position_ms))
        minutes, remainder = divmod(position_ms, 60_000)
        seconds, milliseconds = divmod(remainder, 1_000)
        return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

    def _set_player_position(self, position_ms: int) -> None:
        duration_ms = self._player.duration_ms if self._player is not None else 0
        position_ms = min(max(0, int(position_ms)), duration_ms) if duration_ms else max(0, int(position_ms))
        self.player_position.set(position_ms)
        self._refresh_player_time_display()
        if hasattr(self, "table"):
            self._update_active_lyric(position_ms)

    def _format_progress_time(self, position_ms: int) -> str:
        position_ms = max(0, int(position_ms))
        if self.show_playback_milliseconds.get():
            return self._format_player_time(position_ms)
        total_seconds = position_ms // 1_000
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _refresh_player_time_display(self, *_args) -> None:
        duration_ms = self._player.duration_ms if self._player is not None else 0
        try:
            position_ms = int(float(self.player_position.get()))
        except (tk.TclError, TypeError, ValueError):
            position_ms = 0
        self.player_time_text.set(
            f"{self._format_progress_time(position_ms)} / {self._format_progress_time(duration_ms)}"
        )

    @staticmethod
    def _time_text_to_ms(time_text: str) -> int | None:
        if not TIME_FORMAT.match(time_text):
            return None
        minutes, seconds, milliseconds = (int(part) for part in re.split(r"[:.]", time_text))
        return ((minutes * 60) + seconds) * 1_000 + milliseconds

    def _set_active_lyric(self, index: int | None, force_scroll: bool = False) -> None:
        previous_index = self._active_lyric_index
        self._active_lyric_index = index
        if previous_index == index and not force_scroll:
            return
        for item in self.table.get_children():
            tags = [tag for tag in self.table.item(item, "tags") if tag != "active"]
            if index is not None and item == str(index):
                if "pending" in tags:
                    tags.insert(tags.index("pending"), "active")
                else:
                    tags.append("active")
            self.table.item(item, tags=tuple(tags))
        if index is not None and (force_scroll or previous_index != index):
            self.table.selection_set(str(index))
            self.table.focus(str(index))
            self.table.see(str(index))
        elif index is None and previous_index is not None:
            self.table.selection_remove(self.table.selection())

    def _update_active_lyric(self, position_ms: int) -> None:
        lyrics = self._read_lyrics()
        times = self.draft_times if self.draft_times is not None else self.current_times
        timed_rows: list[tuple[int, int]] = []
        for index, lyric in enumerate(lyrics):
            if not lyric.strip() or index >= len(times):
                continue
            line_time_ms = self._time_text_to_ms(times[index])
            if line_time_ms is not None:
                timed_rows.append((line_time_ms, index))

        # All-zero placeholder times do not describe a real timeline. Keep the
        # manually auditioned row highlighted in that case.
        has_timeline = any(line_time_ms > 0 for line_time_ms, _ in timed_rows)
        active_index: int | None = None
        preview_index = self._preview_row_index
        if preview_index is not None:
            preview_time = next(
                (line_time_ms for line_time_ms, index in timed_rows if index == preview_index),
                None,
            )
            if preview_time is not None and (not has_timeline or position_ms < preview_time):
                active_index = preview_index
            else:
                self._preview_row_index = None

        if active_index is None and has_timeline:
            reached_rows = [row for row in timed_rows if row[0] <= position_ms]
            if reached_rows:
                active_index = max(reached_rows)[1]
        self._set_active_lyric(active_index)

    def _start_playback(
        self,
        position_ms: int,
        preview_row_index: int | None = None,
        audio_path: Path | str | None = None,
    ) -> bool:
        target_audio = Path(audio_path) if audio_path is not None else self._playback_target_audio_path()
        if not self._ensure_player_open(
            audio_path=target_audio,
            on_ready=lambda position=position_ms, index=preview_row_index, target=target_audio: self._start_playback(
                position, index, target
            ),
        ) or self._player is None:
            return False
        self._preview_row_index = preview_row_index
        position_ms = min(max(0, int(position_ms)), max(0, self._player.duration_ms))
        try:
            if self._player_state in {"playing", "paused"}:
                self._player.stop()
            self._player.seek_to(position_ms)
            self._player.play_from(position_ms)
            self._player_state = "playing"
            self._set_player_position(position_ms)
            self._sync_playback_controls()
            self._schedule_player_poll()
            if preview_row_index is not None:
                row_id = str(preview_row_index)
                self.table.selection_set(row_id)
                self.table.focus(row_id)
                self._set_active_lyric(preview_row_index, force_scroll=True)
                self.player_note_text.set(
                    f"试听第 {preview_row_index + 1} 行：从 {self._format_progress_time(position_ms)} 开始。"
                )
            return True
        except Exception as exc:
            self._preview_row_index = None
            try:
                self._player_state = self._player.mode()
            except Exception:
                self._player_state = "stopped"
            self._sync_playback_controls()
            self.player_note_text.set(f"播放失败：{exc}")
            return False

    def _play_or_resume_playback(self, audio_path: Path | str | None = None) -> None:
        target_audio = Path(audio_path) if audio_path is not None else self._playback_target_audio_path()
        if not self._ensure_player_open(
            on_ready=lambda target=target_audio: self._play_or_resume_playback(target),
            audio_path=target_audio,
        ) or self._player is None:
            return
        try:
            mode = self._player.mode()
            if mode == "paused":
                self._player.resume()
                self._player_state = "playing"
                self._sync_playback_controls()
                self._schedule_player_poll()
                self.player_note_text.set("正在播放。")
            elif mode == "stopped":
                self._start_playback(
                    int(self.player_position.get()),
                    audio_path=target_audio,
                )
        except Exception as exc:
            self.player_note_text.set(f"播放失败：{exc}")

    def _pause_playback(self) -> None:
        if self._player is None:
            return
        try:
            if self._player.mode() != "playing":
                return
            self._player.pause()
            self._player_state = "paused"
            self._cancel_player_poll()
            self._sync_playback_controls()
            self.player_note_text.set("已暂停。点击“播放 / 继续”可继续播放。")
        except Exception as exc:
            self.player_note_text.set(f"暂停失败：{exc}")

    def _sync_playback_controls(self) -> None:
        if not hasattr(self, "play_button") or not hasattr(self, "pause_button"):
            return
        is_playing = self._player_state == "playing"
        locked = self._busy or self._player_loading
        target_audio = self._playback_target_audio_path()
        same_track = False
        if target_audio is not None and self._player_path is not None:
            try:
                same_track = target_audio.resolve() == self._player_path
            except (OSError, RuntimeError):
                same_track = os.path.normcase(str(target_audio)) == os.path.normcase(
                    str(self._player_path)
                )
        self.play_button.configure(
            state="disabled" if locked or (is_playing and same_track) else "normal"
        )
        self.pause_button.configure(state="normal" if is_playing and not locked else "disabled")
        if hasattr(self, "stop_button"):
            self.stop_button.configure(state="disabled" if locked else "normal")
        if hasattr(self, "player_scale"):
            self.player_scale.set_enabled(not locked)

    def _stop_playback(self) -> None:
        self._cancel_player_poll()
        self._preview_row_index = None
        if self._player is not None:
            try:
                self._player.stop()
                self._player.seek_to(0)
            except Exception as exc:
                self.player_note_text.set(f"停止播放失败：{exc}")
        self._player_state = "stopped"
        self._sync_playback_controls()
        self._set_player_position(0)
        self._set_active_lyric(None)
        if self._player is not None:
            self.player_note_text.set("已停止。")

    def _begin_seek(self, _event=None) -> None:
        self._seeking = True

    def _finish_seek(self, _event=None) -> None:
        self._seeking = False
        self._preview_row_index = None
        if self._player is None:
            return
        position_ms = int(self.player_position.get())
        try:
            mode = self._player.mode()
            self._player.stop()
            self._player.seek_to(position_ms)
            if mode == "playing":
                self._player.play_from(position_ms)
                self._player_state = "playing"
                self._schedule_player_poll()
            elif mode == "paused":
                self._player.play_from(position_ms)
                self._player.pause()
                self._player_state = "paused"
                self._cancel_player_poll()
            else:
                self._player_state = "stopped"
            self._sync_playback_controls()
            self._set_player_position(position_ms)
        except Exception as exc:
            try:
                self._player_state = self._player.mode()
            except Exception:
                self._player_state = "stopped"
            self._sync_playback_controls()
            self.player_note_text.set(f"定位失败：{exc}")

    def _schedule_player_poll(self) -> None:
        if self._player_poll_id is None:
            self._player_poll_id = self.root.after(200, self._poll_player)

    def _cancel_player_poll(self) -> None:
        if self._player_poll_id is not None:
            try:
                self.root.after_cancel(self._player_poll_id)
            except tk.TclError:
                pass
            self._player_poll_id = None

    def _poll_player(self) -> None:
        self._player_poll_id = None
        if self._player is None:
            return
        try:
            position_ms = self._player.position_ms()
            mode = self._player.mode()
            if not self._seeking:
                self._set_player_position(position_ms)
            self._player_state = "playing" if mode == "playing" else "paused" if mode == "paused" else "stopped"
            self._sync_playback_controls()
            if self._player_state == "playing":
                self._schedule_player_poll()
            elif self._player_state == "stopped":
                self.player_note_text.set("播放结束。")
        except Exception as exc:
            self._player_state = "stopped"
            self._sync_playback_controls()
            self.player_note_text.set(f"播放状态读取失败：{exc}")

    def _play_row(self, row_id: str) -> None:
        time_text = self.table.set(row_id, "time")
        if not TIME_FORMAT.match(time_text):
            self.player_note_text.set("这一行没有有效时间戳。")
            return
        minutes, seconds, milliseconds = (int(part) for part in re.split(r"[:.]", time_text))
        line_time_ms = ((minutes * 60) + seconds) * 1_000 + milliseconds
        start_ms = max(0, line_time_ms - 500)
        self._start_playback(start_ms, preview_row_index=int(row_id))

    def _handle_table_double_click(self, event):
        region = self.table.identify("region", event.x, event.y)
        column = self.table.identify_column(event.x)
        row_id = self.table.identify_row(event.y)
        if region != "cell" or not row_id:
            return
        if column == "#3":
            self._edit_time_cell(event)
        elif column == "#2":
            self._play_row(row_id)
        elif column == "#5":
            self._apply_reference_time_to_row(int(row_id))
        return "break"

    def _release_player(self) -> None:
        self._cancel_player_poll()
        self._player_load_token += 1
        self._pending_player_action = None
        self._player_loading = False
        self._player_loading_path = None
        if self._player is not None:
            try:
                self._player.close()
            except Exception:
                pass
        self._player = None
        self._player_path = None
        self._player_state = "stopped"
        self._sync_playback_controls()
        self._preview_row_index = None
        self.player_scale.set_range(1)
        self._set_player_position(0)
        self._set_active_lyric(None)

    def _on_close(self) -> None:
        if self._training_process is not None and self._training_process.poll() is None:
            self._training_process.terminate()
        self._release_player()
        if self._settings_save_after is not None:
            try:
                self.root.after_cancel(self._settings_save_after)
            except tk.TclError:
                pass
            self._settings_save_after = None
        self._save_settings()
        self.root.destroy()
        if sys.platform == "win32" and self._native_window_icons:
            try:
                destroy_icon = ctypes.WinDLL("user32").DestroyIcon
                destroy_icon.argtypes = [ctypes.c_void_p]
                destroy_icon.restype = ctypes.c_int
                for small_icon, large_icon in self._native_window_icons.values():
                    destroy_icon(small_icon)
                    destroy_icon(large_icon)
                self._native_window_icons.clear()
            except (AttributeError, OSError, TypeError):
                pass

    def _import_lyrics(self) -> None:
        path_string = filedialog.askopenfilename(
            title="导入歌词或 LRC",
            initialdir=str(self._default_input_directory()),
            filetypes=[("歌词文件", "*.lrc *.txt"), ("所有文件", "*.*")],
        )
        if not path_string:
            return
        path = Path(path_string)
        try:
            lyrics, times, metadata, has_timeline = parse_lyrics_file(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法读取歌词", str(exc), parent=self.root)
            return

        self._load_lyrics_into_editor(lyrics, times, metadata, has_timeline)

    def _open_online_lyrics_search(self) -> None:
        audio_path = Path(self.audio_path.get().strip())
        if not audio_path.is_file():
            messagebox.showwarning("先选音频", "请先选择本机上的音频文件。", parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("在线搜索歌词")
        dialog.geometry("860x680")
        dialog.minsize(720, 560)
        dialog.transient(self.root)
        self.root.after_idle(self._apply_window_frame_theme)

        initial_values = {"title": audio_path.stem, "artist": "", "album": ""}
        state = {
            "dialog": dialog,
            "audio_path": audio_path.resolve(),
            "initial_values": initial_values,
            "variables": {
                key: tk.StringVar(value=value)
                for key, value in initial_values.items()
            },
            "audio_metadata": {},
            "duration": 0.0,
            "searching": False,
            "items": {},
            "search_generation": 0,
            "fetching_items": set(),
        }

        content = ttk.Frame(dialog, style="App.TFrame", padding=14)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text=f"当前音频：{audio_path.name}",
            style="CardTitle.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        query_frame = ttk.LabelFrame(content, text="搜索条件（填写临时关键字后，仅按关键字搜索）", padding=10)
        query_frame.pack(fill="x", pady=(0, 8))
        query_frame.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((
            ("title", "歌曲名称"),
            ("artist", "歌手"),
            ("album", "专辑"),
        )):
            ttk.Label(query_frame, text=label, style="Setting.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3
            )
            entry = ttk.Entry(
                query_frame,
                textvariable=state["variables"][key],
                style="Soft.TEntry",
            )
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            entry.bind(
                "<Return>",
                lambda _event, current_state=state: self._search_online_lyrics(current_state),
            )

        state["keyword_variable"] = tk.StringVar(value="")
        ttk.Label(query_frame, text="临时搜索关键字", style="Setting.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=3
        )
        keyword_entry = ttk.Entry(
            query_frame,
            textvariable=state["keyword_variable"],
            style="Soft.TEntry",
        )
        keyword_entry.grid(row=3, column=1, sticky="ew", pady=3)
        keyword_entry.bind(
            "<Return>",
            lambda _event, current_state=state: self._search_online_lyrics(current_state),
        )

        state["status"] = tk.StringVar(value="正在读取音频标签；也可以直接修改搜索条件。")
        action_row = ttk.Frame(query_frame, style="App.TFrame")
        action_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        state["search_button"] = RoundedButton(
            action_row,
            "搜索",
            lambda current_state=state: self._search_online_lyrics(current_state),
            accent=True,
        )
        state["search_button"].pack(side="right")
        ttk.Label(
            action_row,
            textvariable=state["status"],
            style="Hint.TLabel",
            wraplength=620,
        ).pack(side="left", fill="x", expand=True, padx=(0, 12))

        results_frame = ttk.LabelFrame(content, text="歌词候选（按当前搜索条件排序）", padding=6)
        results_frame.pack(fill="both", expand=True, pady=(0, 8))
        results_frame.rowconfigure(0, weight=1)
        results_frame.columnconfigure(0, weight=1)
        columns = ("title", "artist", "album", "source", "duration", "match", "lyrics")
        tree = ttk.Treeview(
            results_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=9,
        )
        for key, label in (
            ("title", "歌曲名称"),
            ("artist", "歌手"),
            ("album", "专辑"),
            ("source", "来源"),
            ("duration", "时长"),
            ("match", "相似度"),
            ("lyrics", "歌词"),
        ):
            tree.heading(key, text=label)
        tree.column("title", width=175, minwidth=120)
        tree.column("artist", width=130, minwidth=90)
        tree.column("album", width=155, minwidth=100)
        tree.column("source", width=72, minwidth=65, anchor="center", stretch=False)
        tree.column("duration", width=72, minwidth=65, anchor="center", stretch=False)
        tree.column("match", width=72, minwidth=65, anchor="center", stretch=False)
        tree.column("lyrics", width=72, minwidth=65, anchor="center", stretch=False)
        tree_scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        tree_scrollbar.grid(row=0, column=1, sticky="ns")
        state["tree"] = tree

        preview_frame = ttk.LabelFrame(content, text="歌词预览", padding=6)
        preview_frame.pack(fill="both", expand=True, pady=(0, 8))
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        preview = tk.Text(
            preview_frame,
            height=8,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            background=INPUT_BACKGROUND,
            foreground=TEXT_PRIMARY,
            selectbackground=ui_color("selected_background"),
            selectforeground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            state="disabled",
        )
        preview_scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=preview.yview)
        preview.configure(yscrollcommand=preview_scrollbar.set)
        preview.grid(row=0, column=0, sticky="nsew")
        preview_scrollbar.grid(row=0, column=1, sticky="ns")
        state["preview"] = preview
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, current_state=state: self._preview_online_lyrics(current_state),
        )
        tree.bind(
            "<Double-1>",
            lambda _event, current_state=state: self._import_online_lyrics(current_state),
        )

        footer = ttk.Frame(content, style="App.TFrame")
        footer.pack(fill="x")
        state["import_button"] = RoundedButton(
            footer,
            "导入选中歌词",
            lambda current_state=state: self._import_online_lyrics(current_state),
            accent=True,
        )
        state["import_button"].pack(side="right")
        state["import_button"].configure(state="disabled")
        RoundedButton(
            footer,
            "取消",
            lambda current_state=state: self._close_online_lyrics_dialog(current_state),
        ).pack(side="right", padx=(0, 8))
        dialog.bind(
            "<Escape>",
            lambda _event, current_state=state: self._close_online_lyrics_dialog(current_state),
        )
        dialog.protocol(
            "WM_DELETE_WINDOW",
            lambda current_state=state: self._close_online_lyrics_dialog(current_state),
        )
        dialog.grab_set()
        dialog.focus_set()

        def read_tags() -> None:
            metadata = read_track_metadata(audio_path)
            try:
                self.root.after(
                    0,
                    lambda current_state=state, values=metadata: self._online_lyrics_tags_loaded(
                        current_state, values
                    ),
                )
            except tk.TclError:
                pass

        threading.Thread(target=read_tags, daemon=True).start()

    def _online_lyrics_tags_loaded(self, state: dict, metadata: dict) -> None:
        dialog = state["dialog"]
        try:
            if not dialog.winfo_exists():
                return
        except tk.TclError:
            return
        state["audio_metadata"] = dict(metadata)
        for key, variable in state["variables"].items():
            if variable.get() == state["initial_values"][key]:
                variable.set(metadata.get(key) or state["initial_values"][key])
        state["duration"] = metadata.get("duration", 0.0)
        if not state["searching"]:
            state["status"].set("已读取音频标签；可修改搜索条件后搜索。")

    def _search_online_lyrics(self, state: dict) -> None:
        if state["searching"]:
            return
        title = state["variables"]["title"].get().strip()
        artist = state["variables"]["artist"].get().strip()
        album = state["variables"]["album"].get().strip()
        keyword = state["keyword_variable"].get().strip()
        if not keyword and not title:
            messagebox.showwarning(
                "缺少搜索内容",
                "请填写歌曲名称，或输入临时搜索关键字后搜索。",
                parent=state["dialog"],
            )
            return

        state["searching"] = True
        state["search_generation"] += 1
        state["items"] = {}
        state["search_button"].configure(state="disabled")
        state["import_button"].configure(state="disabled")
        if keyword:
            display_keyword = keyword if len(keyword) <= 36 else f"{keyword[:33]}…"
            state["status"].set(f"正在仅按关键字“{display_keyword}”搜索…")
        else:
            state["status"].set("正在并行搜索网易云、QQ 音乐、酷狗和 LRCLIB…")
        for item in state["tree"].get_children():
            state["tree"].delete(item)
        self._set_online_lyrics_preview(state, "")

        def work() -> None:
            results = None
            error = ""
            try:
                results = search_lyrics(
                    title=title,
                    artist=artist,
                    album=album,
                    duration=state["duration"],
                    keyword=keyword,
                )
            except Exception as exc:
                error = str(exc)
            try:
                self.root.after(
                    0,
                    lambda current_state=state, items=results, message=error: self._online_lyrics_search_finished(
                        current_state, items, message
                    ),
                )
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _online_lyrics_search_finished(
        self,
        state: dict,
        results: list[dict] | None,
        error: str,
    ) -> None:
        dialog = state["dialog"]
        try:
            if not dialog.winfo_exists():
                return
        except tk.TclError:
            return
        state["searching"] = False
        state["search_button"].configure(state="normal")
        if error:
            state["status"].set(f"搜索失败：{error}")
            return

        results = results or []
        for index, result in enumerate(results):
            item_id = f"{state['search_generation']}:{index}"
            state["items"][item_id] = result
            duration = result.get("duration")
            try:
                duration_seconds = max(0, int(float(duration or 0)))
            except (TypeError, ValueError):
                duration_seconds = 0
            minutes, seconds = divmod(duration_seconds, 60)
            lyric_type = self._online_lyrics_kind(result)
            values = (
                result.get("trackName", ""),
                result.get("artistName", ""),
                result.get("albumName", ""),
                result.get("_source_name", "LRCLIB"),
                f"{minutes}:{seconds:02d}" if duration_seconds else "未知",
                f"{round(result.get('_match_score', 0) * 100)}%",
                lyric_type,
            )
            state["tree"].insert("", "end", iid=item_id, values=values)

        if results:
            first_item = state["tree"].get_children()[0]
            state["tree"].selection_set(first_item)
            state["tree"].focus(first_item)
            self._preview_online_lyrics(state)
            source_order = ("网易云", "QQ 音乐", "酷狗", "LRCLIB")
            found_sources = {result.get("_source_name", "LRCLIB") for result in results}
            source_summary = "、".join(name for name in source_order if name in found_sources)
            state["status"].set(
                f"找到 {len(results)} 个歌词候选（{source_summary}）；选择候选后加载歌词。"
            )
        else:
            state["status"].set("没有找到可导入的歌词。可以修改歌名或歌手后重试。")

    def _preview_online_lyrics(self, state: dict) -> None:
        selected = state["tree"].selection()
        if not selected:
            state["import_button"].configure(state="disabled")
            self._set_online_lyrics_preview(state, "")
            return
        item_id = selected[0]
        result = state["items"].get(item_id)
        if not result:
            state["import_button"].configure(state="disabled")
            return
        content = result.get("syncedLyrics") or result.get("plainLyrics") or ""
        if content:
            state["import_button"].configure(state="normal")
            self._set_online_lyrics_preview(state, content)
            return

        state["import_button"].configure(state="disabled")
        source_name = result.get("_source_name", "歌词来源")
        if result.get("_lyrics_loaded"):
            message = result.get("_lyrics_error") or "该候选来源没有可用歌词。"
            self._set_online_lyrics_preview(state, message)
            return

        self._set_online_lyrics_preview(state, f"正在从{source_name}获取歌词…")
        if item_id in state["fetching_items"]:
            return
        state["fetching_items"].add(item_id)

        def work() -> None:
            content = ""
            error = ""
            try:
                content = fetch_lyrics(
                    result.get("_source", "lrclib"),
                    result.get("_candidate_id", ""),
                )
            except Exception as exc:
                error = str(exc)
            try:
                self.root.after(
                    0,
                    lambda current_state=state, current_item=item_id, lyrics=content, message=error: self._online_lyrics_preview_finished(
                        current_state, current_item, lyrics, message
                    ),
                )
            except tk.TclError:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _online_lyrics_kind(result: dict) -> str:
        if result.get("syncedLyrics"):
            return "同步歌词"
        if result.get("plainLyrics"):
            return "纯文本"
        if result.get("_lyrics_loaded"):
            return "无可用歌词"
        return "点选获取"

    def _online_lyrics_preview_finished(
        self,
        state: dict,
        item_id: str,
        content: str,
        error: str,
    ) -> None:
        state["fetching_items"].discard(item_id)
        result = state["items"].get(item_id)
        if not result:
            return
        if error:
            result["_lyrics_error"] = error
        else:
            result["_lyrics_loaded"] = True
            result["_lyrics_error"] = ""
            if content:
                _lines, _times, _metadata, has_timeline = parse_lyrics_content(content)
                result["syncedLyrics"] = content if has_timeline else ""
                result["plainLyrics"] = "" if has_timeline else content

        try:
            if state["tree"].exists(item_id):
                values = list(state["tree"].item(item_id, "values"))
                values[-1] = self._online_lyrics_kind(result)
                state["tree"].item(item_id, values=values)
            selected = state["tree"].selection()
            if not selected or selected[0] != item_id:
                return
        except tk.TclError:
            return

        fetched_content = result.get("syncedLyrics") or result.get("plainLyrics") or ""
        state["import_button"].configure(state="normal" if fetched_content else "disabled")
        if fetched_content:
            self._set_online_lyrics_preview(state, fetched_content)
        elif error:
            self._set_online_lyrics_preview(state, f"获取失败：{error}")
            state["status"].set(f"歌词获取失败：{error}")
        else:
            self._set_online_lyrics_preview(state, "该候选来源没有可用歌词。")

    @staticmethod
    def _set_online_lyrics_preview(state: dict, content: str) -> None:
        preview = state["preview"]
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        if content:
            preview.insert("1.0", content)
        preview.configure(state="disabled")

    def _import_online_lyrics(self, state: dict) -> None:
        selected = state["tree"].selection()
        if not selected:
            return
        result = state["items"].get(selected[0])
        if not result:
            return
        content = result.get("syncedLyrics") or result.get("plainLyrics") or ""
        audio_metadata = state.get("audio_metadata") or read_track_metadata(state["audio_path"])
        lyrics, times, metadata, has_timeline = _normalize_lyrics_content(
            content,
            result,
            audio_metadata,
            self.lyric_template_language.get(),
        )
        if not any(line.strip() for line in lyrics):
            messagebox.showwarning(
                "没有可导入的歌词",
                "所选候选没有可用的歌词内容，请换一个候选。",
                parent=state["dialog"],
            )
            return

        source_title = result.get("trackName") or "在线歌词"
        source_name = result.get("_source_name", "LRCLIB")
        self._load_lyrics_into_editor(
            lyrics,
            times,
            metadata,
            has_timeline,
            source=f"{source_name}（{source_title}）",
        )
        self.status_text.set(f"{self.status_text.get()} 歌词信息已按预设格式整理。")
        self._close_online_lyrics_dialog(state)

    @staticmethod
    def _close_online_lyrics_dialog(state: dict) -> None:
        dialog = state["dialog"]
        try:
            dialog.grab_release()
            dialog.destroy()
        except tk.TclError:
            pass

    def _import_embedded_lyrics(self) -> None:
        audio_path = Path(self.audio_path.get().strip())
        if not audio_path.is_file():
            messagebox.showwarning("先选音频", "请先选择本机上的音频文件。", parent=self.root)
            return

        embedded_contents = read_embedded_lyrics(audio_path)
        candidates = []
        for content in embedded_contents:
            if not content.strip():
                continue
            parsed = parse_lyrics_content(content)
            lyric_count = sum(
                bool(lyric.strip()) and not is_header_line(lyric)
                for lyric in parsed[0]
            )
            if lyric_count:
                candidates.append((parsed[3], lyric_count, parsed))

        if not candidates:
            messagebox.showinfo(
                "未找到可用的内嵌歌词",
                "支持读取 MP3 的 ID3 歌词、M4A 的 iTunes 歌词，以及 FLAC、Ogg、Opus 的歌词标签。",
                parent=self.root,
            )
            return

        _has_timeline, _lyric_count, parsed = max(candidates, key=lambda item: (item[0], item[1]))
        lyrics, times, metadata, has_timeline = parsed
        self._load_lyrics_into_editor(
            lyrics,
            times,
            metadata,
            has_timeline,
            source=f"音频内嵌歌词（{audio_path.name}）",
        )

    def _load_lyrics_into_editor(
        self,
        lyrics: list[str],
        times: list[str],
        metadata: list[str],
        has_timeline: bool,
        source: str | None = None,
    ) -> None:
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self.metadata = metadata
        self.current_times = times
        self.imported_lrc_has_timeline = has_timeline
        self.draft_times = list(times) if has_timeline else None
        self.feedback_session = None
        self._update_feedback_button_state()
        self._preview_row_index = None
        self._active_lyric_index = None
        self.manual_problem_rows.clear()
        self.pending_review_rows.clear()
        self.checked_rows.clear()
        self._clear_reference_comparison()
        self._update_problem_button_state()
        self.row_results = []
        header_lines = _metadata_header_lines(metadata)
        editor_lines = [*header_lines, "", *lyrics] if header_lines else list(lyrics)
        self._suppress_lyrics_modified = True
        try:
            self.lyrics_text.delete("1.0", "end")
            self.lyrics_text.insert("1.0", "\n".join(editor_lines))
            self.lyrics_text.edit_modified(False)
        finally:
            self._suppress_lyrics_modified = False
        self._editor_lyrics_snapshot = list(lyrics)
        self._refresh_table()
        self._refresh_lyrics_metadata_style()
        self._update_embedded_export_button_state()
        self._update_delete_audio_button_state()
        info_count = sum(bool(line.strip()) for line in metadata)
        status = f"已导入 {len(lyrics)} 行歌词。" if source is None else f"已从{source}导入 {len(lyrics)} 行歌词。"
        if has_timeline:
            status += "已载入 LRC 时间轴；双击歌词行可从对应时间试听检查。"
        elif source is not None:
            status += "未发现有效时间戳；可直接生成时间轴草稿。"
        if info_count:
            status += f"已显示 {info_count} 行曲目信息；这些信息不会进入草稿。"
        self.status_text.set(status)

    def _clear_reference_comparison(self) -> None:
        self.reference_times.clear()
        self.reference_path = None
        self.reference_audio_path = None

    def _invalidate_feedback_for_audio_change(self, *_args) -> None:
        try:
            current_audio = Path(self.audio_path.get().strip()).resolve()
        except OSError:
            current_audio = None
        if current_audio != self._embedded_audio_exported_source:
            self._embedded_audio_exported_source = None
            self._embedded_audio_exported_output = None
        if self.feedback_session is not None and current_audio != self.feedback_session.get("audio_path"):
            self.feedback_session = None
            self._update_feedback_button_state()
        if self.reference_audio_path is not None and current_audio != self.reference_audio_path:
            self._clear_reference_comparison()
            if hasattr(self, "table"):
                self._refresh_table()
        self._update_embedded_export_button_state()
        self._update_delete_audio_button_state()

    def _save_review_feedback(self) -> None:
        session = self.feedback_session
        if session is None:
            return

        lyrics = self._read_lyrics()
        if lyrics != session.get("lyrics"):
            messagebox.showwarning(
                "歌词已变化",
                "当前歌词与这次自动打轴时的歌词不同。请重新生成时间轴后再保存检查反馈。",
                parent=self.root,
            )
            return
        audio_path = Path(session["audio_path"])
        if not audio_path.is_file():
            messagebox.showwarning("找不到原音频", "原音频已移动或删除，无法保存这次训练样本。", parent=self.root)
            return
        try:
            if audio_path.stat().st_size > MAX_AUDIO_BYTES:
                messagebox.showwarning("音频文件较大", "音频超过 200 MB，无法复制到本机训练资料。", parent=self.root)
                return
        except OSError as exc:
            messagebox.showerror("无法读取原音频", str(exc), parent=self.root)
            return
        if audio_path.suffix.casefold() not in AUDIO_EXTENSIONS:
            messagebox.showwarning("音频格式不支持", "当前音频格式不能加入本地训练资料。", parent=self.root)
            return

        source_times = self.draft_times if self.draft_times is not None else self.current_times
        times = list(source_times) + ["00:00.000"] * max(0, len(lyrics) - len(source_times))
        model_times = list(session.get("model_times", []))
        model_times += ["00:00.000"] * max(0, len(lyrics) - len(model_times))
        use_reference_times = self.reference_audio_path == audio_path.resolve()
        marked_indices = {
            index for index in self.manual_problem_rows | self.pending_review_rows
            if 0 <= index < len(lyrics) and lyrics[index].strip() and not is_header_line(lyrics[index])
        }
        reference_resolved_indices = {
            index for index in marked_indices
            if use_reference_times and index in self.reference_times
        }
        excluded_indices = marked_indices - reference_resolved_indices
        training_rows: list[tuple[int, str, str]] = []
        trusted_times_ms: dict[int, int] = {}
        previous_ms = -1
        for index, lyric in enumerate(lyrics):
            if not lyric.strip() or is_header_line(lyric):
                continue
            if index in excluded_indices:
                continue
            timestamp = (
                self.reference_times.get(index, times[index])
                if use_reference_times
                else times[index]
            ).strip()
            timestamp_ms = self._time_text_to_ms(timestamp)
            if timestamp_ms is None:
                messagebox.showwarning(
                    "时间轴还不完整",
                    f"第 {index + 1} 行没有有效时间戳。修正后再保存检查反馈。",
                    parent=self.root,
                )
                return
            if timestamp_ms < previous_ms:
                messagebox.showwarning(
                    "时间顺序有误",
                    f"第 {index + 1} 行早于上一句。请先修正时间顺序再保存反馈。",
                    parent=self.root,
                )
                return
            previous_ms = timestamp_ms
            trusted_times_ms[index] = timestamp_ms
            training_rows.append((index, lyric, timestamp))
        if len(training_rows) < 5:
            messagebox.showwarning(
                "可用训练行太少",
                f"排除 {len(excluded_indices)} 行问题标记/待检查歌词后，只剩 {len(training_rows)} 行有效时间。"
                "至少需要 5 行有效歌词才能保存为训练样本。",
                parent=self.root,
            )
            return

        eligible_indices = [
            index for index, lyric in enumerate(lyrics)
            if lyric.strip() and not is_header_line(lyric)
        ]
        # Preserve a small supervised window around confirmed neighbors while masking
        # the full interval that contains any untrusted row in the training loss.
        ignored_regions: list[list[float]] = []
        cursor = 0
        while cursor < len(eligible_indices):
            if eligible_indices[cursor] not in excluded_indices:
                cursor += 1
                continue
            run_start = cursor
            while cursor < len(eligible_indices) and eligible_indices[cursor] in excluded_indices:
                cursor += 1
            previous_index = next(
                (eligible_indices[position] for position in range(run_start - 1, -1, -1)
                 if eligible_indices[position] in trusted_times_ms),
                None,
            )
            next_index = next(
                (eligible_indices[position] for position in range(cursor, len(eligible_indices))
                 if eligible_indices[position] in trusted_times_ms),
                None,
            )
            region_start = (
                max(0.0, trusted_times_ms[previous_index] / 1_000 + 0.25)
                if previous_index is not None else 0.0
            )
            region_end = (
                trusted_times_ms[next_index] / 1_000 - 0.25
                if next_index is not None else 1_000_000.0
            )
            if region_end > region_start:
                ignored_regions.append([region_start, region_end])

        negative_regions: list[list[float]] = []
        negative_line_numbers: list[int] = []
        for index in sorted(self.manual_problem_rows):
            if index < 0 or index >= len(lyrics) or not lyrics[index].strip() or is_header_line(lyrics[index]):
                continue
            wrong_time_ms = self._time_text_to_ms(times[index])
            if wrong_time_ms is None:
                continue
            # Do not push away a confirmed boundary belonging to this or another line.
            if any(abs(wrong_time_ms - trusted_ms) <= 400 for trusted_ms in trusted_times_ms.values()):
                continue
            wrong_time_seconds = wrong_time_ms / 1_000
            negative_regions.append([max(0.0, wrong_time_seconds - 0.2), wrong_time_seconds + 0.2])
            negative_line_numbers.append(index + 1)

        try:
            fingerprint = sha256_file(audio_path)
            feedback_directory = self.settings_file.parent / "feedback"
            feedback_directory.mkdir(parents=True, exist_ok=True)
            matching_metadata_paths: list[Path] = []
            for existing_metadata_path in feedback_directory.glob("*.feedback.json"):
                try:
                    existing_metadata = json.loads(existing_metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(existing_metadata, dict) and existing_metadata.get("audio_sha256") == fingerprint:
                    matching_metadata_paths.append(existing_metadata_path)

            def base_name_conflicts(candidate_base: str) -> bool:
                candidate_metadata_path = feedback_directory / f"{candidate_base}.feedback.json"
                candidate_metadata_hash = None
                if candidate_metadata_path.is_file():
                    try:
                        candidate_metadata = json.loads(candidate_metadata_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        return True
                    candidate_metadata_hash = (
                        candidate_metadata.get("audio_sha256")
                        if isinstance(candidate_metadata, dict)
                        else None
                    )
                    if candidate_metadata_hash != fingerprint:
                        return True

                matching_audio_exists = False
                for extension in AUDIO_EXTENSIONS:
                    candidate_audio = feedback_directory / f"{candidate_base}{extension}"
                    if not candidate_audio.is_file():
                        continue
                    if sha256_file(candidate_audio) != fingerprint:
                        return True
                    matching_audio_exists = True

                candidate_lrc = feedback_directory / f"{candidate_base}.manual.lrc"
                if candidate_lrc.is_file() and not matching_audio_exists and candidate_metadata_hash != fingerprint:
                    return True
                return False

            saved_base = audio_path.stem
            hash_length = 8
            while base_name_conflicts(saved_base):
                saved_base = f"{audio_path.stem} [{fingerprint[:hash_length]}]"
                if base_name_conflicts(saved_base):
                    hash_length = min(hash_length + 4, len(fingerprint))
                    saved_base = f"{audio_path.stem} [{fingerprint[:hash_length]}]"
                    if hash_length == len(fingerprint) and base_name_conflicts(saved_base):
                        raise FileExistsError("无法为这份训练反馈生成唯一文件名。")

            saved_audio = feedback_directory / f"{saved_base}{audio_path.suffix}"
            if not saved_audio.is_file():
                temporary_audio = saved_audio.with_suffix(saved_audio.suffix + ".tmp")
                shutil.copyfile(audio_path, temporary_audio)
                os.replace(temporary_audio, saved_audio)

            saved_lrc = feedback_directory / f"{saved_base}.manual.lrc"
            lrc_content = "\n".join(f"[{timestamp}]{lyric}" for _, lyric, timestamp in training_rows) + "\n"
            temporary_lrc = saved_lrc.with_suffix(saved_lrc.suffix + ".tmp")
            temporary_lrc.write_text(lrc_content, encoding="utf-8")
            os.replace(temporary_lrc, saved_lrc)

            corrections = []
            model_statuses = session.get("model_statuses", {})
            for index, lyric, human_time in training_rows:
                if model_statuses.get(index) == "skipped":
                    continue
                model_time = model_times[index]
                model_ms = self._time_text_to_ms(model_time)
                human_ms = self._time_text_to_ms(human_time)
                if model_ms is None or human_ms is None or model_ms == human_ms:
                    continue
                corrections.append(
                    {
                        "line": index + 1,
                        "model_time": model_time,
                        "human_time": human_time,
                        "delta_ms": human_ms - model_ms,
                    }
                )
            metadata = {
                "audio_sha256": fingerprint,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "vocal_separation": bool(session.get("vocalize")),
                "used_manual_lrc_reference": use_reference_times,
                "reviewed_line_count": len(training_rows),
                "corrected_line_count": len(corrections),
                "excluded_line_numbers": [index + 1 for index in sorted(excluded_indices)],
                "ignored_regions": ignored_regions,
                "negative_line_numbers": negative_line_numbers,
                "negative_regions": negative_regions,
                "corrections": corrections,
            }
            metadata_path = feedback_directory / f"{saved_base}.feedback.json"
            temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
            temporary_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary_metadata, metadata_path)

            for previous_metadata_path in matching_metadata_paths:
                previous_base = previous_metadata_path.name.removesuffix(".feedback.json")
                for extension in AUDIO_EXTENSIONS:
                    duplicate_audio = feedback_directory / f"{previous_base}{extension}"
                    if duplicate_audio == saved_audio or not duplicate_audio.is_file():
                        continue
                    if sha256_file(duplicate_audio) == fingerprint:
                        duplicate_audio.unlink()
                if previous_base != saved_base:
                    previous_lrc = feedback_directory / f"{previous_base}.manual.lrc"
                    if previous_lrc.is_file():
                        previous_lrc.unlink()
                    if previous_metadata_path.is_file():
                        previous_metadata_path.unlink()
        except OSError as exc:
            messagebox.showerror("无法保存训练反馈", str(exc), parent=self.root)
            return

        # Keep this review session available so the user can correct more rows and
        # submit the same song again. The feedback writer updates the existing
        # sample for this audio instead of creating a duplicate.
        self.feedback_session = session
        self._refresh_table()
        self._update_feedback_button_state()
        self._save_settings()
        feedback_count = sum(1 for _ in feedback_directory.glob("*.manual.lrc"))
        self.status_text.set(
            f"已在本机保存检查反馈：采纳 {len(training_rows)} 行，排除未对照问题/待检查 {len(excluded_indices)} 行，"
            f"加入 {len(negative_regions)} 个问题位置负反馈，"
            f"人工修正 {len(corrections)} 行；"
            f"累计 {feedback_count} 首。"
            + (
                "标记行保留在列表中，可继续检查后再次保存。"
                if excluded_indices
                else "可继续修正时间并再次提交以更新此样本。"
            )
            + "需要训练模型时，可在设置页手动启动反馈训练。"
        )

    def _import_reference_lrc(self) -> None:
        path_string = filedialog.askopenfilename(
            title="选择人工打轴 LRC 作为对照",
            initialdir=str(self._default_input_directory()),
            filetypes=[("LRC 歌词", "*.lrc"), ("所有文件", "*.*")],
        )
        if not path_string:
            return
        path = Path(path_string)
        try:
            reference_rows = parse_timed_lrc_reference(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法读取人工 LRC", str(exc), parent=self.root)
            return
        if not reference_rows:
            messagebox.showwarning(
                "没有可用的对照时间",
                "文件中没有找到带时间戳的歌词行。请选择人工打轴后的 .lrc 文件。",
                parent=self.root,
            )
            return

        lyrics = self._read_lyrics()
        lyric_indices = [
            index for index, lyric in enumerate(lyrics)
            if lyric.strip() and not is_header_line(lyric)
        ]
        current_texts = [_comparison_text(lyrics[index]) for index in lyric_indices]
        reference_texts = [_comparison_text(lyric) for lyric, _ in reference_rows]
        mapping: dict[int, str] = {}
        matcher = SequenceMatcher(None, current_texts, reference_texts, autojunk=False)
        for current_start, reference_start, length in matcher.get_matching_blocks():
            for offset in range(length):
                current_index = lyric_indices[current_start + offset]
                mapping[current_index] = reference_rows[reference_start + offset][1]
        if not mapping:
            messagebox.showwarning(
                "歌词无法匹配",
                "人工 LRC 中没有找到与当前歌词相同的行。请确认两份歌词版本一致。",
                parent=self.root,
            )
            return

        self.reference_times = mapping
        self.reference_path = path
        try:
            self.reference_audio_path = Path(self.audio_path.get().strip()).resolve()
        except OSError:
            self.reference_audio_path = None
        self._refresh_table()
        summary = self._reference_error_summary(
            self.draft_times if self.draft_times is not None else self.current_times
        )
        self.status_text.set(
            f"已载入人工 LRC 对照：{path.name}（匹配 {len(mapping)} 行）。{summary}"
        )
        matched = len(mapping)
        total = len(lyric_indices)
        messagebox.showinfo(
            "人工 LRC 对照已载入",
            f"已匹配 {matched}/{total} 行。表格会显示人工时间，以及模型时间减人工时间的差值；正数表示模型结果偏晚。"
            + (f"\n有 {total - matched} 行因歌词文字不同而未比较。" if matched < total else ""),
            parent=self.root,
        )

    def _initialize_model_from_folder(self) -> None:
        if not VENV_PYTHON.is_file():
            messagebox.showwarning(
                "需要先安装模型环境",
                "请先运行 InstallAutoLrc.cmd 安装本机训练依赖。",
                parent=self.root,
            )
            return
        initial_directory = self.training_data_directory
        if initial_directory is None or not initial_directory.is_dir():
            initial_directory = ROOT_DIR
        selected_directory = filedialog.askdirectory(
            title="选择模型初始化训练资料文件夹",
            initialdir=str(initial_directory),
            mustexist=True,
        )
        if not selected_directory:
            return

        self.training_data_directory = Path(selected_directory).resolve()
        if hasattr(self, "settings_training_directory"):
            self.settings_training_directory.set(str(self.training_data_directory))
        self._save_settings()
        self._train_boundary_model(
            [self.training_data_directory],
            initialize_from_pretrained=True,
        )

    def _train_boundary_model(
        self,
        data_directories: list[Path] | None = None,
        initialize_from_pretrained: bool = False,
    ) -> None:
        if not VENV_PYTHON.is_file():
            messagebox.showwarning(
                "需要先安装模型环境",
                "请先运行 InstallAutoLrc.cmd 安装本机训练依赖。",
                parent=self.root,
            )
            return
        feedback_directory = self.settings_file.parent / "feedback"
        if data_directories is None:
            data_directories = [feedback_directory]
            feedback_pair_count = self._count_feedback_training_pairs(feedback_directory)
            if feedback_pair_count < 2:
                messagebox.showwarning(
                    "反馈训练样本不足",
                    "训练只会读取本机 feedback 文件夹中的已检查歌曲。至少需要两首不同歌曲，"
                    "才能划分训练集和验证集。\n\n"
                    f"当前找到 {feedback_pair_count} 组配对音频和 LRC。\n"
                    f"feedback 位置：{feedback_directory}",
                    parent=self.root,
                )
                return
        if (
            not initialize_from_pretrained
            and feedback_directory.is_dir()
            and any(feedback_directory.glob("*.manual.lrc"))
        ):
            if all(directory.resolve() != feedback_directory.resolve() for directory in data_directories):
                data_directories = [*data_directories, feedback_directory]

        output_path = self.settings_file.parent / "models" / "boundary_personal.pt"
        device = {
            "自动": "auto",
            "GPU（CUDA）": "cuda",
            "CPU": "cpu",
        }.get(self.device_mode.get(), "auto")
        command = [
            str(VENV_PYTHON),
            "-u",
            str(ENGINE_DIR / "train_boundary.py"),
        ]
        for directory in data_directories:
            command.extend(("--data-dir", str(directory.resolve())))
        if (
            not initialize_from_pretrained
            and self.boundary_checkpoint is not None
            and self.boundary_checkpoint.is_file()
        ):
            command.extend(("--initial-checkpoint", str(self.boundary_checkpoint)))
        command.extend(("--output", str(output_path), "--device", device, "--epochs", "8"))
        if self.vocalize_enabled.get():
            command.append("--vocalize")

        self._set_busy(True, training=True)
        self.status_text.set(
            "正在从所选资料初始化本地模型…"
            if initialize_from_pretrained
            else "模型训练中…"
        )

        def work() -> None:
            process = None
            try:
                process = subprocess.Popen(
                    command,
                    cwd=ENGINE_DIR,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._training_process = process
                assert process.stdout is not None
                for _line in process.stdout:
                    pass
                return_code = process.wait()
                self.root.after(
                    0,
                    lambda code=return_code, initialization=initialize_from_pretrained: self._boundary_training_finished(
                        output_path, code == 0, initialization
                    ),
                )
            except Exception:
                if process is not None and process.poll() is None:
                    process.terminate()
                try:
                    self.root.after(
                        0,
                        lambda initialization=initialize_from_pretrained: self._boundary_training_finished(
                            output_path, False, initialization
                        ),
                    )
                except tk.TclError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _count_feedback_training_pairs(feedback_directory: Path) -> int:
        if not feedback_directory.is_dir():
            return 0
        audio_stems = {
            path.stem.casefold()
            for path in feedback_directory.iterdir()
            if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
        }
        return sum(
            1
            for lrc_path in feedback_directory.glob("*.manual.lrc")
            if lrc_path.name[: -len(".manual.lrc")].casefold() in audio_stems
        )

    def _boundary_training_finished(
        self,
        checkpoint_path: Path,
        succeeded: bool,
        initialize_from_pretrained: bool = False,
    ) -> None:
        self._training_process = None
        self._set_busy(False, training=True)
        if not succeeded or not checkpoint_path.is_file():
            if initialize_from_pretrained:
                self.status_text.set("模型初始化未完成；当前启用的模型保持不变，请检查训练资料后重试。")
            else:
                self.status_text.set("模型训练未完成；feedback 已保留，当前启用的模型没有改变。")
            return

        self.boundary_checkpoint = checkpoint_path.resolve()
        self._update_boundary_button_states()
        self._save_settings()
        self.status_text.set(
            "模型初始化完成；后续打轴将使用新的本地模型。"
            if initialize_from_pretrained
            else "本地边界模型训练完成；后续打轴将使用此模型。"
        )

    def _restore_pretrained_boundary_model(self) -> None:
        if self.boundary_checkpoint is None:
            return
        self.boundary_checkpoint = None
        self._update_boundary_button_states()
        self._save_settings()
        self.status_text.set("已切回 Auto-LRC 预训练边界模型；下次打轴时生效。")

    def _enable_saved_boundary_model(self) -> None:
        checkpoint_path = self.settings_file.parent / "models" / "boundary_personal.pt"
        if not checkpoint_path.is_file():
            self._update_boundary_button_states()
            messagebox.showwarning(
                "找不到本地模型",
                "本机还没有保存过训练模型。请先用人工 LRC 训练一个本地模型。",
                parent=self.root,
            )
            return
        self.boundary_checkpoint = checkpoint_path.resolve()
        self._save_settings()
        self._update_boundary_button_states()
        self.status_text.set("已重新启用保存的本地模型；下次打轴时生效。")

    def _model_preset_source_checkpoint(self) -> Path | None:
        if self.boundary_checkpoint is not None and self.boundary_checkpoint.is_file():
            return self.boundary_checkpoint.resolve()
        return None

    def _saved_local_model_checkpoint(self) -> Path | None:
        saved_checkpoint = self.settings_file.parent / "models" / "boundary_personal.pt"
        return saved_checkpoint.resolve() if saved_checkpoint.is_file() else None

    @staticmethod
    def _model_preset_files(directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        return sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.casefold() == ".pt"
            ),
            key=lambda path: path.stem.casefold(),
        )

    def _copy_model_checkpoint_to_preset(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".model-preset-",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        try:
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _initialize_model_presets(self) -> None:
        try:
            self.model_preset_directory.mkdir(parents=True, exist_ok=True)
            preset_files = self._model_preset_files(self.model_preset_directory)
            if not preset_files:
                source = (
                    self._model_preset_source_checkpoint()
                    or self._saved_local_model_checkpoint()
                )
                if source is not None:
                    first_preset = self.model_preset_directory / "预设1.pt"
                    self._copy_model_checkpoint_to_preset(source, first_preset)
                    self.settings_model_preset_status.set(
                        f"已将现有本地模型另存为「预设1」。预设保存在：{self.model_preset_directory}"
                    )
            self._refresh_model_presets()
        except OSError as exc:
            self.settings_model_preset_status.set(
                f"无法初始化模型预设文件夹：{exc}"
            )

    def _refresh_model_presets(self, preferred_name: str | None = None) -> None:
        if not hasattr(self, "model_preset_combo"):
            return
        previous_name = preferred_name or self.model_preset_selection.get()
        preset_files = self._model_preset_files(self.model_preset_directory)
        self._model_preset_paths = {path.stem: path.resolve() for path in preset_files}
        names = list(self._model_preset_paths)
        self.model_preset_combo.configure(values=names)
        if previous_name in names:
            selected_name = previous_name
        else:
            selected_name = names[0] if names else ""
        self.model_preset_selection.set(selected_name)
        if not names and hasattr(self, "settings_model_preset_status"):
            self.settings_model_preset_status.set(
                f"尚无已保存的模型预设。预设保存在：{self.model_preset_directory}"
            )
        self._update_model_preset_controls()

    def _selected_model_preset_path(self) -> Path | None:
        return self._model_preset_paths.get(self.model_preset_selection.get())

    @staticmethod
    def _validated_model_preset_name(raw_name: str) -> tuple[str | None, str | None]:
        name = raw_name.strip()
        if name.casefold().endswith(".pt"):
            name = name[:-3].rstrip()
        if not name:
            return None, "预设名称不能为空。"
        if len(name) > 80:
            return None, "预设名称不能超过 80 个字符。"
        if (
            name.endswith((" ", "."))
            or any(ord(character) < 32 for character in name)
            or re.search(r'[<>:"/\\|?*]', name)
        ):
            return None, '预设名称不能包含 < > : " / \\ | ? * 等字符，也不能以空格或句点结尾。'
        if name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }:
            return None, "此名称是 Windows 保留名称，请换一个名称。"
        return name, None

    def _next_model_preset_name(self) -> str:
        existing = {path.stem.casefold() for path in self._model_preset_files(self.model_preset_directory)}
        index = 1
        while f"预设{index}".casefold() in existing:
            index += 1
        return f"预设{index}"

    def _save_model_preset_as_new(self) -> None:
        source = self._model_preset_source_checkpoint()
        if source is None:
            messagebox.showwarning(
                "没有可保存的本地模型",
                "当前没有可用的本地模型权重。请先训练并启用本地模型，再保存预设。",
                parent=self.root,
            )
            return
        raw_name = simpledialog.askstring(
            "保存模型预设",
            "输入新预设名称：",
            initialvalue=self._next_model_preset_name(),
            parent=self.root,
        )
        if raw_name is None:
            return
        name, error = self._validated_model_preset_name(raw_name)
        if error:
            messagebox.showwarning("预设名称无效", error, parent=self.root)
            return
        assert name is not None
        if any(
            path.stem.casefold() == name.casefold()
            for path in self._model_preset_files(self.model_preset_directory)
        ):
            messagebox.showwarning(
                "预设已存在",
                "此名称已经有对应预设。请选择其他名称，或使用“覆盖所选预设”。",
                parent=self.root,
            )
            return
        destination = self.model_preset_directory / f"{name}.pt"
        try:
            self._copy_model_checkpoint_to_preset(source, destination)
        except OSError as exc:
            messagebox.showerror("保存模型预设失败", str(exc), parent=self.root)
            return
        self._refresh_model_presets(name)
        self.settings_model_preset_status.set(f"已保存模型预设「{name}」。")
        self.status_text.set(f"当前本地模型权重已保存为预设「{name}」。")

    def _overwrite_model_preset(self) -> None:
        destination = self._selected_model_preset_path()
        if destination is None:
            return
        source = self._model_preset_source_checkpoint()
        if source is None:
            messagebox.showwarning(
                "没有可保存的本地模型",
                "当前没有可用的本地模型权重，无法覆盖预设。",
                parent=self.root,
            )
            return
        if source == destination.resolve():
            messagebox.showinfo(
                "无需覆盖",
                "当前模型权重已经来自所选预设，无需再次覆盖。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "覆盖模型预设",
            f"确定用当前本地模型权重覆盖「{destination.stem}」吗？\n\n此操作无法撤销。",
            parent=self.root,
        ):
            return
        try:
            self._copy_model_checkpoint_to_preset(source, destination)
        except OSError as exc:
            messagebox.showerror("覆盖模型预设失败", str(exc), parent=self.root)
            return
        self._refresh_model_presets(destination.stem)
        self.settings_model_preset_status.set(f"已用当前本地模型覆盖预设「{destination.stem}」。")
        self.status_text.set(f"模型预设「{destination.stem}」已更新。")

    def _rename_model_preset(self) -> None:
        source_path = self._selected_model_preset_path()
        if source_path is None:
            return
        raw_name = simpledialog.askstring(
            "重命名模型预设",
            "输入新的预设名称：",
            initialvalue=source_path.stem,
            parent=self.root,
        )
        if raw_name is None:
            return
        name, error = self._validated_model_preset_name(raw_name)
        if error:
            messagebox.showwarning("预设名称无效", error, parent=self.root)
            return
        assert name is not None
        destination = self.model_preset_directory / f"{name}.pt"
        if destination.resolve() == source_path.resolve():
            return
        if any(
            path.resolve() != source_path.resolve()
            and path.stem.casefold() == name.casefold()
            for path in self._model_preset_files(self.model_preset_directory)
        ):
            messagebox.showwarning(
                "预设名称已占用",
                "另一个预设已经使用此名称。",
                parent=self.root,
            )
            return
        try:
            source_path.replace(destination)
        except OSError as exc:
            messagebox.showerror("重命名模型预设失败", str(exc), parent=self.root)
            return
        if self.boundary_checkpoint is not None and self.boundary_checkpoint.resolve() == source_path.resolve():
            self.boundary_checkpoint = destination.resolve()
            self._save_settings()
        self._refresh_model_presets(name)
        self.settings_model_preset_status.set(f"模型预设已重命名为「{name}」。")
        self.status_text.set(f"模型预设已重命名为「{name}」。")

    def _activate_model_preset(self) -> None:
        checkpoint_path = self._selected_model_preset_path()
        if checkpoint_path is None or not checkpoint_path.is_file():
            return
        self.boundary_checkpoint = checkpoint_path.resolve()
        self._save_settings()
        self._update_boundary_button_states()
        self.settings_model_preset_status.set(f"已启用模型预设「{checkpoint_path.stem}」。")
        self.status_text.set(f"已启用模型预设「{checkpoint_path.stem}」；下次打轴时生效。")

    def _update_model_preset_controls(self, busy: bool | None = None) -> None:
        if not hasattr(self, "model_preset_combo"):
            return
        if busy is None:
            busy = self._busy
        selected_path = self._selected_model_preset_path()
        source_path = self._model_preset_source_checkpoint()
        self.model_preset_combo.configure(state="disabled" if busy else "readonly")
        self.activate_model_preset_button.configure(
            state="normal"
            if selected_path is not None
            and selected_path.is_file()
            and not busy
            and (
                self.boundary_checkpoint is None
                or self.boundary_checkpoint.resolve() != selected_path.resolve()
            )
            else "disabled"
        )
        source_ready = source_path is not None and source_path.is_file() and not busy
        self.save_model_preset_button.configure(
            state="normal" if source_ready else "disabled"
        )
        self.overwrite_model_preset_button.configure(
            state="normal" if selected_path is not None and source_ready else "disabled"
        )
        self.rename_model_preset_button.configure(
            state="normal" if selected_path is not None and not busy else "disabled"
        )

    def _update_boundary_button_states(self, busy: bool | None = None) -> None:
        if busy is None:
            busy = self._busy
        if not hasattr(self, "reset_boundary_button") or not hasattr(self, "enable_boundary_button"):
            return
        local_model_exists = (self.settings_file.parent / "models" / "boundary_personal.pt").is_file()
        self.reset_boundary_button.configure(
            state="normal" if self.boundary_checkpoint is not None and not busy else "disabled"
        )
        self.enable_boundary_button.configure(
            state="normal"
            if self.boundary_checkpoint is None and local_model_exists and not busy
            else "disabled"
        )
        if hasattr(self, "initialize_model_button"):
            self.initialize_model_button.configure(state="disabled" if busy else "normal")
        if hasattr(self, "settings_model_status"):
            if self.boundary_checkpoint is not None:
                self.settings_model_status.set(f"当前使用本地模型：{self.boundary_checkpoint}")
            else:
                self.settings_model_status.set("当前使用随程序提供的预训练模型。")
        self._update_model_preset_controls(busy)

    def _read_lyrics(self) -> list[str]:
        content = self.lyrics_text.get("1.0", "end-1c").replace("\r\n", "\n").replace("\r", "\n")
        lyrics, _times, metadata, _has_timeline = parse_lyrics_content(content)
        self.metadata = metadata
        return lyrics

    def _refresh_lyrics_metadata_style(self) -> None:
        self.lyrics_text.tag_remove("metadata", "1.0", "end")
        header_line_count = next(
            (index for index, line in enumerate(self.metadata) if not line.strip()),
            0,
        )
        if header_line_count:
            self.lyrics_text.tag_add(
                "metadata",
                "1.0",
                f"{header_line_count + 1}.0",
            )

    def _on_lyrics_modified(self, _event=None) -> None:
        if not self.lyrics_text.edit_modified():
            return
        self.lyrics_text.edit_modified(False)
        if self._suppress_lyrics_modified:
            return
        if self._text_refresh_pending:
            return
        self._text_refresh_pending = True
        self.root.after_idle(self._lyrics_changed)

    def _lyrics_changed(self) -> None:
        self._text_refresh_pending = False
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self._update_delete_audio_button_state()
        previous_metadata = self.metadata
        previous_lyric_count = len(self._editor_lyrics_snapshot)
        lyrics = self._read_lyrics()
        metadata_changed = self.metadata != previous_metadata
        self._refresh_lyrics_metadata_style()
        lyrics_changed = lyrics != self._editor_lyrics_snapshot
        self._editor_lyrics_snapshot = list(lyrics)
        if not lyrics_changed:
            if metadata_changed:
                self.status_text.set("曲目信息已更新；右侧草稿保持不变。")
            return
        if self.feedback_session is not None and lyrics != self.feedback_session.get("lyrics"):
            self.feedback_session = None
            self._update_feedback_button_state()
        self.current_times = [
            self.current_times[index] if index < len(self.current_times) else "00:00.000"
            for index in range(len(lyrics))
        ]
        if len(lyrics) != previous_lyric_count:
            self.imported_lrc_has_timeline = False
        self.draft_times = None
        self._update_embedded_export_button_state()
        self._preview_row_index = None
        self._active_lyric_index = None
        self.manual_problem_rows.clear()
        self.pending_review_rows.clear()
        self.checked_rows.clear()
        self._clear_reference_comparison()
        self._update_problem_button_state()
        self.row_results = []
        self._refresh_table()

    def _refresh_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        lyrics = self._read_lyrics()
        output_times = self.draft_times if self.draft_times is not None else self.current_times
        output_times = output_times + ["00:00.000"] * max(0, len(lyrics) - len(output_times))
        status_by_index = {row["index"]: row for row in self.row_results}

        for index, lyric in enumerate(lyrics):
            row = status_by_index.get(index, {})
            status = row.get("status", "")
            is_problem = index in self.manual_problem_rows
            is_pending = index in self.pending_review_rows
            is_checked = index in self.checked_rows
            status_label = (
                "待检查" if is_pending else
                "问题标记" if is_problem else
                "已检查" if is_checked else
                {"aligned": "已对齐", "review": "需检查", "skipped": "已跳过"}.get(status, "")
            )
            tags = (status,) if status in {"review", "skipped"} else ()
            if is_problem:
                tags = (*tags, "problem")
            if is_checked:
                tags = (*tags, "checked")
            if index == self._active_lyric_index:
                tags = (*tags, "active")
            if is_pending:
                tags = (*tags, "pending")
            comparison = self._format_reference_comparison(index, output_times[index])
            self.table.insert(
                "",
                "end",
                iid=str(index),
                values=(index + 1, lyric, output_times[index], status_label, comparison),
                tags=tags,
            )

    def _format_reference_comparison(self, index: int, model_time: str) -> str:
        reference_time = self.reference_times.get(index)
        if reference_time is None:
            return ""
        reference_ms = self._time_text_to_ms(reference_time)
        model_ms = self._time_text_to_ms(model_time)
        if reference_ms is None or model_ms is None:
            return reference_time
        has_model_timeline = self.draft_times is not None or any(
            (self._time_text_to_ms(value) or 0) > 0 for value in self.current_times
        )
        if not has_model_timeline:
            return reference_time
        delta = (model_ms - reference_ms) / 1_000
        return f"{reference_time}  ({delta:+.3f}s)"

    def _reference_error_summary(self, model_times: list[str]) -> str:
        has_model_timeline = self.draft_times is not None or any(
            (self._time_text_to_ms(value) or 0) > 0 for value in self.current_times
        )
        if not self.reference_times or not has_model_timeline:
            return ""
        deltas = []
        for index, reference_time in self.reference_times.items():
            if index >= len(model_times):
                continue
            model_ms = self._time_text_to_ms(model_times[index])
            reference_ms = self._time_text_to_ms(reference_time)
            if model_ms is not None and reference_ms is not None:
                deltas.append(model_ms - reference_ms)
        if not deltas:
            return ""
        mean_absolute_seconds = sum(abs(delta) for delta in deltas) / len(deltas) / 1_000
        mean_signed_seconds = sum(deltas) / len(deltas) / 1_000
        return (
            f"人工对照 {len(deltas)} 行：平均绝对偏差 {mean_absolute_seconds:.3f}s，"
            f"平均偏差 {mean_signed_seconds:+.3f}s。"
        )

    def _build_problem_segments(self, lyrics: list[str], times: list[str]) -> tuple[list[dict], str | None]:
        marked = sorted(
            index for index in self.manual_problem_rows
            if 0 <= index < len(lyrics) and index not in self.pending_review_rows
        )
        if not marked:
            return [], "请先在歌词行上按中键标记需要重新打轴的行。"

        groups: list[list[int]] = []
        for index in marked:
            if not groups or index != groups[-1][-1] + 1:
                groups.append([index])
            else:
                groups[-1].append(index)

        segments = []
        for group in groups:
            start_index = max(0, group[0] - 1)
            end_index = min(len(lyrics) - 1, group[-1] + 1)
            if start_index in self.pending_review_rows:
                start_index = group[0]
            if end_index in self.pending_review_rows:
                end_index = group[-1]
            start_time = self._time_text_to_ms(times[start_index]) if start_index < len(times) else None
            end_time = self._time_text_to_ms(times[end_index]) if end_index < len(times) else None
            if start_time is None or end_time is None:
                return [], f"第 {start_index + 1}–{end_index + 1} 行缺少有效时间戳，请先生成整首时间轴草稿。"
            if end_index > start_index and end_time <= start_time:
                return [], f"第 {start_index + 1}–{end_index + 1} 行的时间顺序无效，请先检查时间轴。"

            window_start = 0 if start_index == 0 else max(0, start_time - 1_500)
            if end_index == len(lyrics) - 1:
                window_end = None
            else:
                next_time = self._time_text_to_ms(times[end_index + 1]) if end_index + 1 < len(times) else None
                window_end = next_time + 250 if next_time is not None and next_time > end_time else end_time + 5_000
            if window_end is not None and window_end <= window_start:
                return [], f"第 {start_index + 1}–{end_index + 1} 行无法确定有效音频范围，请先检查时间轴。"

            segments.append(
                {
                    "start": start_index,
                    "end": end_index,
                    "marked": tuple(group),
                    "audio_window_ms": (window_start, window_end),
                }
            )
        return segments, None

    def _realign_problem_segments(self) -> None:
        lyrics = self._read_lyrics()
        base_times = self.draft_times if self.draft_times is not None else self.current_times
        times = list(base_times) + ["00:00.000"] * max(0, len(lyrics) - len(base_times))
        segments, error = self._build_problem_segments(lyrics, times)
        if error:
            messagebox.showwarning("无法重打问题段", error, parent=self.root)
            return

        audio = Path(self.audio_path.get().strip())
        if not audio.is_file():
            messagebox.showwarning("先选音频", "请选择本机上的音频文件。", parent=self.root)
            return
        try:
            if audio.stat().st_size > MAX_AUDIO_BYTES:
                messagebox.showwarning("音频文件较大", "当前原型限制音频文件不超过 200 MB。", parent=self.root)
                return
        except OSError as exc:
            messagebox.showerror("无法读取音频", str(exc), parent=self.root)
            return

        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self._set_busy(True)
        total_segments = len(segments)
        marked_count = sum(len(segment["marked"]) for segment in segments)
        self.status_text.set(f"准备重新打轴 {total_segments} 个问题段、{marked_count} 行…")
        use_vocal_separation = self.vocalize_enabled.get()
        device_preference = {
            "自动": "auto",
            "GPU（CUDA）": "cuda",
            "CPU": "cpu",
        }.get(self.device_mode.get(), "auto")
        suffix = audio.suffix.lower() or ".audio"

        def work() -> None:
            try:
                audio_bytes = audio.read_bytes()
                updated_times = list(times)
                row_updates: dict[int, dict] = {}
                failures: list[str] = []
                completed = 0
                model_error: str | None = None
                selected_device = ""
                for segment_number, segment in enumerate(segments, start=1):
                    start_index = segment["start"]
                    end_index = segment["end"]
                    self.root.after(
                        0,
                        lambda current=segment_number, first=start_index + 1, last=end_index + 1: self.status_text.set(
                            f"正在重新打轴问题段 {current}/{total_segments}（第 {first}–{last} 行）…"
                        ),
                    )
                    segment_lyrics = lyrics[start_index : end_index + 1]
                    segment_times = times[start_index : end_index + 1]
                    try:
                        result = run_alignment(
                            audio_bytes,
                            suffix,
                            segment_lyrics,
                            segment_times,
                            vocalize=use_vocal_separation,
                            device=device_preference,
                            audio_window_ms=segment["audio_window_ms"],
                            boundary_checkpoint=self.boundary_checkpoint,
                        )
                    except VocalModelUnavailableError as exc:
                        if completed == 0:
                            model_error = str(exc)
                        else:
                            failures.append(f"第 {segment_number} 段：{exc}")
                        break
                    except Exception as exc:
                        failures.append(f"第 {segment_number} 段（第 {start_index + 1}–{end_index + 1} 行）：{exc}")
                        continue

                    completed += 1
                    selected_device = result["device"]
                    result_rows = {row["index"]: row for row in result["rows"]}
                    for global_index in segment["marked"]:
                        local_index = global_index - start_index
                        updated_times[global_index] = result["times"][local_index]
                        if local_index in result_rows:
                            row_updates[global_index] = {**result_rows[local_index], "index": global_index}

                if model_error is not None:
                    self.root.after(0, lambda message=model_error: self._alignment_failed(message, offer_vocal_install=True))
                elif completed == 0:
                    self.root.after(0, lambda: self._problem_segments_failed(failures, total_segments))
                else:
                    self.root.after(
                        0,
                        lambda: self._problem_segments_completed(
                            updated_times, row_updates, completed, total_segments, failures, selected_device
                        ),
                    )
            except Exception as exc:
                message = str(exc)
                self.root.after(0, lambda error=message: self._alignment_failed(error))

        threading.Thread(target=work, daemon=True).start()

    def _problem_segments_failed(self, failures: list[str], total_segments: int) -> None:
        self._set_busy(False)
        self.status_text.set(f"问题段重新打轴失败：0/{total_segments} 段完成。")
        self.player_note_text.set("；".join(failures) if failures else "没有问题段可以重新打轴。")

    def _problem_segments_completed(
        self,
        updated_times: list[str],
        row_updates: dict[int, dict],
        completed: int,
        total_segments: int,
        failures: list[str],
        device: str,
    ) -> None:
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self._set_busy(False)
        self.draft_times = updated_times
        self.imported_lrc_has_timeline = False
        self._update_embedded_export_button_state()
        self._update_delete_audio_button_state()
        completed_rows = set(row_updates)
        if self.feedback_session is not None:
            model_times = self.feedback_session.get("model_times", [])
            while len(model_times) < len(updated_times):
                model_times.append("00:00.000")
            model_statuses = self.feedback_session.setdefault("model_statuses", {})
            for index in completed_rows:
                model_times[index] = updated_times[index]
                if index in row_updates:
                    model_statuses[index] = row_updates[index].get("status", "aligned")
        self.manual_problem_rows.difference_update(completed_rows)
        self.pending_review_rows.update(completed_rows)
        self.checked_rows.difference_update(completed_rows)
        existing_rows = {row["index"]: row for row in self.row_results}
        existing_rows.update(row_updates)
        self.row_results = [existing_rows[index] for index in sorted(existing_rows)]
        self._refresh_table()
        self._update_active_lyric(int(self.player_position.get()))
        self.status_text.set(
            f"问题段重新打轴完成：{completed}/{total_segments} 段。运行设备：{device}。"
            + self._reference_error_summary(updated_times)
        )
        self.player_note_text.set(
            "；".join(failures)
            if failures
            else "重打行已转为黄色待检查；试听后可中键确认为已检查。整首检查完可点“确认检查并学习”。"
        )

    def _align(self) -> None:
        audio = Path(self.audio_path.get().strip())
        lyrics = self._read_lyrics()
        if not audio.is_file():
            messagebox.showwarning("先选音频", "请选择本机上的音频文件。", parent=self.root)
            return
        if not lyrics or not any(line.strip() for line in lyrics):
            messagebox.showwarning("先输入歌词", "请输入歌词，或导入 .lrc / .txt 文件。", parent=self.root)
            return
        try:
            audio_size = audio.stat().st_size
        except OSError as exc:
            messagebox.showerror("无法读取音频", str(exc), parent=self.root)
            return
        if audio_size > MAX_AUDIO_BYTES:
            messagebox.showwarning("音频文件较大", "当前原型限制音频文件不超过 200 MB。", parent=self.root)
            return

        current_times = [
            self.current_times[index] if index < len(self.current_times) else "00:00.000"
            for index in range(len(lyrics))
        ]
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self.feedback_session = None
        self._update_feedback_button_state()
        self.draft_times = None
        self.row_results = []
        self.pending_review_rows.clear()
        self.checked_rows.clear()
        self._refresh_table()
        self.status_text.set("模型正在分析音频，首次加载可能需要一些时间…")
        self._set_busy(True)
        use_vocal_separation = self.vocalize_enabled.get()
        device_preference = {
            "自动": "auto",
            "GPU（CUDA）": "cuda",
            "CPU": "cpu",
        }.get(self.device_mode.get(), "auto")

        def work() -> None:
            try:
                result = run_alignment(
                    audio.read_bytes(),
                    audio.suffix.lower() or ".audio",
                    lyrics,
                    current_times,
                    vocalize=use_vocal_separation,
                    device=device_preference,
                    boundary_checkpoint=self.boundary_checkpoint,
                )
                self.root.after(
                    0,
                    lambda current=result, path=audio, source_lyrics=list(lyrics), separated=use_vocal_separation: self._alignment_succeeded(
                        current, path, source_lyrics, separated
                    ),
                )
            except VocalModelUnavailableError as exc:
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self._alignment_failed(message, offer_vocal_install=True),
                )
            except Exception as exc:
                error_message = str(exc)
                self.root.after(0, lambda message=error_message: self._alignment_failed(message))

        threading.Thread(target=work, daemon=True).start()

    def _set_busy(self, busy: bool, *, training: bool = False) -> None:
        if training:
            self._training_busy = busy
        else:
            self._operation_busy = busy
        self._busy = self._training_busy or self._operation_busy
        operation_busy = self._operation_busy
        state = "disabled" if self._busy else "normal"
        self.progress.pack(side="right", padx=(10, 0)) if self._busy else self.progress.pack_forget()
        for button in (
            self.import_button,
            self.online_lyrics_button,
            self.reference_button,
            self.align_button,
            self.export_button,
            self.embedded_export_button,
            self.feedback_button,
        ):
            button.configure(state=state)
        settings_state = "disabled" if operation_busy else "normal"
        self.train_boundary_button.configure(state="disabled" if self._busy else "normal")
        self.initialize_model_button.configure(state="disabled" if self._busy else "normal")
        self.choose_cover_directory_button.configure(state=settings_state)
        self.reset_settings_button.configure(state=settings_state)
        self._update_boundary_button_states(busy=self._busy)
        self._update_problem_button_state(busy=self._busy)
        self._update_feedback_button_state(busy=self._busy)
        self._update_embedded_export_button_state(busy=self._busy)
        self._update_delete_audio_button_state(busy=self._busy)
        for _item, button, _indicator in getattr(self, "nav_buttons", {}).values():
            button.configure(state="disabled" if operation_busy else "normal")
        self._update_metadata_control_states()
        self.vocalize_checkbox.configure(state=settings_state)
        self.playback_precision_checkbox.configure(state=settings_state)
        self.device_combo.configure(state="disabled" if operation_busy else "readonly")
        self.audio_entry.configure(state=state)
        self.audio_button.configure(state=state)
        self.lyrics_text.configure(state=state)
        self.table.state(["disabled"] if self._busy else ["!disabled"])
        self._sync_playback_controls()
        if self._busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _update_problem_button_state(self, busy: bool = False) -> None:
        if hasattr(self, "realign_problem_button"):
            self.realign_problem_button.configure(
                state="normal"
                if (self.manual_problem_rows - self.pending_review_rows) and not busy
                else "disabled"
            )

    def _update_feedback_button_state(self, busy: bool = False) -> None:
        if hasattr(self, "feedback_button"):
            self.feedback_button.configure(
                state="normal" if self.feedback_session is not None and not busy else "disabled"
            )

    def _update_embedded_export_button_state(self, busy: bool = False) -> None:
        if not hasattr(self, "embedded_export_button"):
            return
        audio_path = Path(self.audio_path.get().strip())
        has_lyrics = False
        if hasattr(self, "lyrics_text"):
            try:
                has_lyrics = any(line.strip() for line in self._read_lyrics())
            except tk.TclError:
                pass
        available = has_lyrics and audio_path.is_file()
        self.embedded_export_button.configure(
            state="normal" if available and not busy else "disabled"
        )

    def _update_delete_audio_button_state(self, busy: bool = False) -> None:
        if not hasattr(self, "delete_audio_button"):
            return
        audio_path = Path(self.audio_path.get().strip())
        try:
            current_audio = audio_path.resolve() if audio_path.is_file() else None
        except OSError:
            current_audio = None
        available = current_audio is not None and current_audio == self._embedded_audio_exported_source
        available = available and self._embedded_audio_exported_output != current_audio
        self.delete_audio_button.configure(
            state="normal" if available and not (busy or self._busy) else "disabled"
        )

    def _alignment_succeeded(
        self, result: dict, audio_path: Path, lyrics: list[str], vocalize: bool
    ) -> None:
        self._embedded_audio_exported_source = None
        self._embedded_audio_exported_output = None
        self._set_busy(False)
        self.draft_times = result["times"]
        self.imported_lrc_has_timeline = False
        self._update_embedded_export_button_state()
        self._update_delete_audio_button_state()
        self.row_results = result["rows"]
        self.manual_problem_rows.clear()
        self.pending_review_rows.clear()
        self.checked_rows.clear()
        self._update_problem_button_state()
        self.feedback_session = {
            "audio_path": audio_path.resolve(),
            "lyrics": list(lyrics),
            "model_times": list(result["times"]),
            "model_statuses": {row["index"]: row.get("status", "aligned") for row in result["rows"]},
            "vocalize": vocalize,
        }
        self._update_feedback_button_state()
        self._refresh_table()
        self._update_active_lyric(int(self.player_position.get()))
        self.status_text.set(
            f"草稿完成：{result['aligned_count']} 行已处理，"
            f"{result['review_count']} 行需检查，{result['skipped_count']} 行跳过。"
            f" 运行设备：{result['device']}。"
            + self._reference_error_summary(result["times"])
            + " 检查修正后，点击“确认检查并学习”可用这次结果继续训练。"
        )

    def _alignment_failed(self, message: str, offer_vocal_install: bool = False) -> None:
        self._set_busy(False)
        self.status_text.set("自动打轴没有完成。")
        if offer_vocal_install and messagebox.askyesno(
            "需要安装人声分离模型",
            "人声分离模型尚未下载或加载失败。要现在打开安装程序下载模型吗？\n\n"
            "安装完成后，请回到本窗口重新点击刚才使用的打轴按钮。",
            parent=self.root,
        ):
            self._open_vocal_model_installer()
            return
        messagebox.showerror("自动打轴失败", message, parent=self.root)

    def _open_vocal_model_installer(self) -> None:
        installer = ROOT_DIR / "InstallAutoLrc.cmd"
        if not installer.is_file():
            messagebox.showerror(
                "找不到安装程序",
                f"未找到安装入口：{installer}",
                parent=self.root,
            )
            return
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(installer), "--vocal-model"],
                cwd=ROOT_DIR,
            )
            self.status_text.set("已打开人声分离模型安装程序；完成后请重新生成草稿。")
        except OSError as exc:
            messagebox.showerror("无法打开安装程序", str(exc), parent=self.root)

    def _edit_time_cell(self, event) -> None:
        if self._time_editor is not None:
            self._commit_time_edit()
        region = self.table.identify("region", event.x, event.y)
        column = self.table.identify_column(event.x)
        row_id = self.table.identify_row(event.y)
        if region != "cell" or column != "#3" or not row_id:
            return
        bounds = self.table.bbox(row_id, "time")
        if not bounds:
            return
        x, y, width, height = bounds
        entry = ttk.Entry(self.table, justify="center")
        entry.insert(0, self.table.set(row_id, "time"))
        entry.place(x=x, y=y, width=width, height=height)
        entry.select_range(0, "end")
        entry.focus_set()
        entry.bind("<Return>", lambda _event: self._commit_time_edit())
        entry.bind("<Escape>", lambda _event: self._cancel_time_edit())
        entry.bind("<FocusOut>", lambda _event: self._commit_time_edit())
        self._time_editor = entry
        self._editing_index = int(row_id)

    def _commit_time_edit(self) -> None:
        if self._time_editor is None:
            return
        value = self._time_editor.get().strip()
        index = self._editing_index
        self._time_editor.destroy()
        self._time_editor = None
        if not TIME_FORMAT.match(value):
            messagebox.showwarning("时间格式不正确", "请使用 mm:ss.mmm 格式，例如 01:23.450。", parent=self.root)
            self._refresh_table()
            return
        self._set_row_time(index, value)

    def _set_row_time(self, index: int, value: str) -> None:
        previous_value = (
            self.draft_times[index]
            if self.draft_times is not None and index < len(self.draft_times)
            else self.current_times[index]
            if index < len(self.current_times)
            else "00:00.000"
        )
        if self.draft_times is not None:
            while len(self.draft_times) <= index:
                self.draft_times.append("00:00.000")
            self.draft_times[index] = value
        while len(self.current_times) <= index:
            self.current_times.append("00:00.000")
        self.current_times[index] = value
        if value != previous_value:
            self.manual_problem_rows.discard(index)
            self.pending_review_rows.discard(index)
            self.checked_rows.discard(index)
        self._refresh_table()
        self._update_problem_button_state()
        self._update_active_lyric(int(self.player_position.get()))

    def _apply_reference_time_to_row(self, index: int) -> None:
        reference_time = self.reference_times.get(index)
        if reference_time is None:
            return
        if self._time_editor is not None:
            self._commit_time_edit()
        self._set_row_time(index, reference_time)
        self.status_text.set(
            f"已将第 {index + 1} 行草稿时间更新为人工 LRC 时间 {reference_time}。"
        )

    def _cancel_time_edit(self) -> None:
        if self._time_editor is not None:
            self._time_editor.destroy()
            self._time_editor = None
            self._refresh_table()

    def _show_row_reason(self, _event=None) -> None:
        selection = self.table.selection()
        if not selection:
            self.reason_label.configure(text="")
            self.reason_label.pack_forget()
            return
        index = int(selection[0])
        row = next((item for item in self.row_results if item["index"] == index), None)
        reason = row.get("reason", "") if row else ""
        if index in self.manual_problem_rows:
            reason = (reason + "  " if reason else "") + "已标记为问题行；当前错误时间会尝试作为负反馈，有匹配人工 LRC 时也会使用人工时间作正确标注。再次中键可取消。"
        elif index in self.pending_review_rows:
            reason = (reason + "  " if reason else "") + "重打已完成，待试听检查；没有匹配人工时间时会排除训练，中键可确认为已检查。"
        elif index in self.checked_rows:
            reason = (reason + "  " if reason else "") + "已确认检查；再次中键可标记为问题行。"
        self.reason_label.configure(text=reason)
        if reason.strip():
            if self.reason_label.winfo_manager() != "pack":
                self.reason_label.pack(fill="x", anchor="w", pady=(0, 6), before=self.table_panel)
        else:
            self.reason_label.pack_forget()

    def _handle_table_middle_click(self, event):
        region = self.table.identify("region", event.x, event.y)
        row_id = self.table.identify_row(event.y)
        if region == "cell" and row_id:
            self._toggle_problem_mark(int(row_id))
        return "break"

    def _toggle_problem_mark(self, index: int) -> None:
        row_id = str(index)
        if not self.table.exists(row_id):
            return
        if index in self.pending_review_rows:
            self.pending_review_rows.remove(index)
            self.checked_rows.add(index)
        elif index in self.manual_problem_rows:
            self.manual_problem_rows.remove(index)
        else:
            self.checked_rows.discard(index)
            self.manual_problem_rows.add(index)
        row = next((item for item in self.row_results if item["index"] == index), {})
        status = row.get("status", "")
        status_label = (
            "待检查" if index in self.pending_review_rows else
            "问题标记" if index in self.manual_problem_rows else
            "已检查" if index in self.checked_rows else
            {"aligned": "已对齐", "review": "需检查", "skipped": "已跳过"}.get(status, "")
        )
        values = list(self.table.item(row_id, "values"))
        values[3] = status_label
        tags = [
            tag for tag in self.table.item(row_id, "tags")
            if tag not in {"problem", "pending", "checked"}
        ]
        if index in self.manual_problem_rows:
            tags.append("problem")
        if index in self.checked_rows:
            tags.append("checked")
        if index == self._active_lyric_index:
            if "pending" in tags:
                tags.insert(tags.index("pending"), "active")
            elif "active" not in tags:
                tags.append("active")
        self.table.item(row_id, values=values, tags=tuple(tags))
        self._update_problem_button_state()
        if self.table.selection() == (row_id,):
            self._show_row_reason()
        if index in self.checked_rows:
            note = f"已将第 {index + 1} 行确认为已检查；播放继续。"
        elif index in self.manual_problem_rows:
            note = f"已标记第 {index + 1} 行为问题行；播放继续。再次中键可取消标记。"
        else:
            note = f"已取消第 {index + 1} 行的问题标记；播放继续。"
        self.player_note_text.set(note)

    def _scroll_table_horizontally(self, event):
        if not event.delta:
            return "break"
        steps = max(1, round(abs(event.delta) / 120 * 30))
        self.table.xview_scroll(-steps if event.delta > 0 else steps, "units")
        return "break"

    def _toggle_editor_hint(self, section: str) -> None:
        if section == "lyrics":
            self.lyrics_hint_expanded = not self.lyrics_hint_expanded
            if self.lyrics_hint_expanded:
                self.lyrics_hint.pack(
                    anchor="w",
                    fill="x",
                    pady=(4, 8),
                    before=self.lyrics_input_panel,
                )
                self.lyrics_hint_button.configure(text="收起说明  −")
            else:
                self.lyrics_hint.pack_forget()
                self.lyrics_hint_button.configure(text="展开说明  +")
            return

        self.draft_hint_expanded = not self.draft_hint_expanded
        if self.draft_hint_expanded:
            before = (
                self.reason_label
                if self.reason_label.winfo_manager() == "pack"
                else self.table_panel
            )
            self.draft_hint.pack(
                anchor="w",
                fill="x",
                pady=(4, 8),
                before=before,
            )
            self.draft_hint_button.configure(text="收起说明  −")
        else:
            self.draft_hint.pack_forget()
            self.draft_hint_button.configure(text="展开说明  +")

    def _wheel_handoff_cooldown_value(self) -> float:
        return _normalize_wheel_handoff_cooldown(
            self.wheel_handoff_cooldown_seconds.get()
        )

    def _apply_wheel_handoff_cooldown(self, *_args) -> None:
        try:
            value = float(self.wheel_handoff_cooldown_seconds.get())
        except (tk.TclError, TypeError, ValueError):
            return
        if not math.isfinite(value):
            return
        self.root._wheel_handoff_cooldown_seconds = (
            _normalize_wheel_handoff_cooldown(value)
        )

    def _commit_wheel_handoff_cooldown(self, _event=None):
        value = self._wheel_handoff_cooldown_value()
        self.wheel_handoff_cooldown_seconds.set(f"{value:.1f}")
        self._apply_wheel_handoff_cooldown()
        return None

    def _lyric_draft_panel_height_value(self) -> int:
        try:
            height = int(self.lyric_draft_panel_height.get())
        except (AttributeError, tk.TclError, TypeError, ValueError):
            height = DEFAULT_LYRIC_DRAFT_PANEL_HEIGHT
        return max(
            MIN_LYRIC_DRAFT_PANEL_HEIGHT,
            min(MAX_LYRIC_DRAFT_PANEL_HEIGHT, height),
        )

    def _apply_lyric_draft_panel_height(self, *_args) -> None:
        try:
            height = int(self.lyric_draft_panel_height.get())
        except (AttributeError, tk.TclError, TypeError, ValueError):
            return
        height = max(
            MIN_LYRIC_DRAFT_PANEL_HEIGHT,
            min(MAX_LYRIC_DRAFT_PANEL_HEIGHT, height),
        )
        if hasattr(self, "lyric_draft_split"):
            self.lyric_draft_split.configure(height=height)

    def _commit_lyric_draft_panel_height(self, _event=None):
        self.lyric_draft_panel_height.set(str(self._lyric_draft_panel_height_value()))
        self._apply_lyric_draft_panel_height()
        return None

    def _fit_draft_hint(self, event) -> None:
        wraplength = max(160, event.width - 4)
        if abs(int(self.draft_hint.cget("wraplength")) - wraplength) > 8:
            self.draft_hint.configure(wraplength=wraplength)

    @staticmethod
    def _fit_hint_label(event) -> None:
        wraplength = max(160, event.width - 4)
        try:
            if abs(int(event.widget.cget("wraplength")) - wraplength) > 8:
                event.widget.configure(wraplength=wraplength)
        except (tk.TclError, TypeError, ValueError):
            pass

    def _export_lrc(self) -> None:
        rendered = self._render_lrc_content()
        if rendered is None:
            return
        lrc_content, metadata_status = rendered
        audio = Path(self.audio_path.get().strip())
        default_name = audio.with_suffix(".lrc").name if audio.name else "lyrics.lrc"
        output_directory = self.output_directory
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("无法访问输出文件夹", str(exc), parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            title="导出歌词时间轴",
            initialdir=str(output_directory),
            defaultextension=".lrc",
            initialfile=default_name,
            filetypes=[("LRC 歌词", "*.lrc")],
        )
        if not path:
            return
        try:
            Path(path).write_text(lrc_content, encoding="utf-8-sig")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self.status_text.set(f"已导出：{path}{metadata_status}")

    def _render_untimed_lyrics_content(self, lyrics: list[str]) -> str:
        metadata_lines = [line for line in self.metadata if line.strip() and not META_TAG.match(line)]
        metadata_tags = [line for line in self.metadata if line.strip() and META_TAG.match(line)]
        output = list(metadata_tags)
        if metadata_lines:
            output.extend(metadata_lines)
            output.append("")
        output.extend(lyrics)
        return "\n".join(output) + "\n"

    def _render_lrc_content(
        self,
        *,
        allow_untimed_fallback: bool = False,
    ) -> tuple[str, str] | None:
        lyrics = self._read_lyrics()
        if not lyrics:
            messagebox.showwarning("没有歌词", "请先输入或导入歌词。", parent=self.root)
            return None

        times = self.draft_times if self.draft_times is not None else self.current_times
        times = times + ["00:00.000"] * max(0, len(lyrics) - len(times))
        metadata_lines = [line for line in self.metadata if line.strip() and not META_TAG.match(line)]
        metadata_tags = [line for line in self.metadata if line.strip() and META_TAG.match(line)]
        output = list(metadata_tags)
        metadata_status = ""
        if metadata_lines:
            first_vocal_index = next((index for index, lyric in enumerate(lyrics) if lyric.strip()), None)
            first_vocal_time = times[first_vocal_index] if first_vocal_index is not None else "00:00.000"
            minutes, seconds, milliseconds = (int(part) for part in re.split(r"[:.]", first_vocal_time))
            first_vocal_ms = ((minutes * 60) + seconds) * 1_000 + milliseconds
            minimum_intro_ms = len(metadata_lines) * MIN_METADATA_INTERVAL_MS
            if first_vocal_ms < minimum_intro_ms:
                if allow_untimed_fallback:
                    return (
                        self._render_untimed_lyrics_content(lyrics),
                        "曲目信息前奏不足，已改为内嵌不带时间戳的歌词。",
                    )
                messagebox.showwarning(
                    "前奏时间不足",
                    f"第一句歌词从 {first_vocal_time} 开始，无法在此之前为 {len(metadata_lines)} 行曲目信息各留出至少 0.5 秒。\n\n"
                    "请先确认第一句歌词的时间，或减少曲目信息行数后再导出。歌词时间不会被自动推迟。",
                    parent=self.root,
                )
                return None
            output.extend(
                f"[{self._format_player_time((first_vocal_ms * index) // len(metadata_lines))}]{line}"
                for index, line in enumerate(metadata_lines)
            )
            output.append("")
            metadata_status = f" 曲目信息已均匀排在首句歌词（{first_vocal_time}）之前。"
        output.extend(f"[{times[index]}]{lyric}" if lyric else "" for index, lyric in enumerate(lyrics))
        return "\n".join(output) + "\n", metadata_status

    def _export_embedded_audio(self) -> None:
        audio_path = Path(self.audio_path.get().strip())
        if not audio_path.is_file():
            messagebox.showwarning("先选音频", "请选择本机上的音频文件。", parent=self.root)
            return
        has_timeline = self.draft_times is not None or self.imported_lrc_has_timeline
        if not has_timeline:
            lyrics = self._read_lyrics()
            if not lyrics:
                messagebox.showwarning("没有歌词", "请先输入或导入歌词。", parent=self.root)
                return
            lrc_content = self._render_untimed_lyrics_content(lyrics)
            metadata_status = "当前没有时间轴，已内嵌不带时间戳的歌词。"
        else:
            rendered = self._render_lrc_content(allow_untimed_fallback=True)
            if rendered is None:
                return
            lrc_content, metadata_status = rendered

        output_directory = self.output_directory
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
            output_path = output_directory / audio_path.name
        except OSError as exc:
            messagebox.showerror("无法访问输出文件夹", str(exc), parent=self.root)
            return

        if output_path.exists() and not messagebox.askyesno(
            "覆盖已有音频",
            f"{output_path.name} 已存在。要用新的内嵌草稿音频替换它吗？",
            parent=self.root,
        ):
            return

        try:
            source_audio_path = audio_path.resolve()
        except OSError as exc:
            messagebox.showerror("无法访问音频", str(exc), parent=self.root)
            return
        self._set_busy(True)
        self.status_text.set("正在复制音频并写入歌词…")

        def work() -> None:
            try:
                _create_edited_audio_copy(
                    audio_path,
                    output_path,
                    lambda path: write_embedded_lyrics(path, lrc_content),
                )
                self.root.after(
                    0,
                    lambda: self._embedded_audio_export_finished(
                        output_path,
                        source_audio_path,
                        metadata_status,
                    ),
                )
            except Exception as exc:
                error_message = str(exc)
                try:
                    self.root.after(0, lambda message=error_message: self._embedded_audio_export_failed(message))
                except tk.TclError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _embedded_audio_export_finished(
        self,
        output_path: Path,
        source_audio_path: Path,
        metadata_status: str,
    ) -> None:
        self._embedded_audio_exported_source = source_audio_path
        self._embedded_audio_exported_output = output_path.resolve()
        self._set_busy(False)
        self._update_delete_audio_button_state()
        status = f"已生成内嵌草稿 LRC 的音频：{output_path}{metadata_status}"
        if self._embedded_audio_exported_output == source_audio_path:
            status += " 导出位置与原音频相同；请更改输出文件夹后重新导出，才能删除原音频。"
        self.status_text.set(status)

    def _embedded_audio_export_failed(self, error_message: str) -> None:
        self._set_busy(False)
        self.status_text.set("内嵌草稿音频生成失败。")
        messagebox.showerror("导出内嵌音频失败", error_message, parent=self.root)

def main() -> None:
    if sys.platform == "win32":
        try:
            set_app_id = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID
            set_app_id.argtypes = [ctypes.c_wchar_p]
            set_app_id.restype = ctypes.c_long
            set_app_id(WINDOWS_APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    DesktopAligner(root)
    root.mainloop()


if __name__ == "__main__":
    main()

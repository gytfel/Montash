"""Графический интерфейс Montash (customtkinter)."""

from __future__ import annotations

import os
import queue
import shutil
import sys
import tempfile
import threading
import traceback
from dataclasses import replace
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from . import __version__
from .clips import apply_text_edit, build_clips, rechunk
from .download import (
    VIDEO_EXTS, collect_videos, disk_full_message, download, free_mb, is_disk_full, is_url,
)
from .ffmpeg_tools import probe_duration
from .ai_scenes import AI_MODELS, AiOptions
from .film import MODES, replace_shot, set_shot_time
from .models import FILL_MODES, FORMATS, LOOKS, Clip, RenderSettings, SubtitleStyle, fmt_time
from .montage import Montage, build_montage, natural_sort_key
from .paths import downloads_dir
from .project import load_settings, save_settings
from .render import render_clip, render_frame, render_montage
from .script import natural_duration, split_sentences
from .transcribe import LANGUAGES, MODELS, transcribe

ACCENT = "#7C4DFF"
PANEL = "#1B1E2B"
BG = "#12141C"
MUTED = "#9AA0B8"
FONTS = [
    "Montserrat Black", "Montserrat", "Bebas Neue", "Impact", "Arial Black",
    "Arial", "Roboto", "Oswald", "Comic Sans MS", "Segoe UI Black",
]
VIDEO_TYPES = [("Видео", " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))), ("Все файлы", "*.*")]
AUDIO_TYPES = [("Аудио", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"), ("Все файлы", "*.*")]
TAB_CLIPS = "Клипы из видео"
TAB_FILM = "Фильм → клип"
DROP_HINT = "…или перетащите видео, папку или ссылку прямо в окно"

# Перетаскивание файлов из Проводника. Без tkinterdnd2 программа работает и так.
try:
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

    _DnDBase = TkinterDnD.DnDWrapper
except Exception:  # noqa: BLE001 — нет пакета или его бинарников под эту систему
    DND_FILES = DND_TEXT = TkinterDnD = None

    class _DnDBase:
        pass


def _invert(d: dict) -> dict:
    return {v: k for k, v in d.items()}


def _parse_time(text: str) -> float:
    parts = text.strip().replace(",", ".").split(":")
    value = 0.0
    for p in parts:
        value = value * 60 + float(p)
    return value


def _fmt_precise(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{m}:{s:04.1f}"


class Section(ctk.CTkFrame):
    def __init__(self, master, title: str):
        super().__init__(master, fg_color=PANEL, corner_radius=12)
        ctk.CTkLabel(
            self, text=title.upper(), font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED, anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 4))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x", padx=12, pady=(0, 12))


class LabeledSlider(ctk.CTkFrame):
    def __init__(self, master, label, from_, to, value, step=1, fmt="{:.0f}", command=None):
        super().__init__(master, fg_color="transparent")
        self.fmt = fmt
        self.command = command
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label, anchor="w").pack(side="left")
        self.value_label = ctk.CTkLabel(top, text=fmt.format(value), text_color=ACCENT)
        self.value_label.pack(side="right")
        steps = max(1, int(round((to - from_) / step)))
        self.slider = ctk.CTkSlider(
            self, from_=from_, to=to, number_of_steps=steps, command=self._changed,
            button_color=ACCENT, progress_color=ACCENT,
        )
        self.slider.set(value)
        self.slider.pack(fill="x", pady=(2, 6))

    def _changed(self, v):
        self.value_label.configure(text=self.fmt.format(v))
        if self.command:
            self.command(v)

    def get(self) -> float:
        return float(self.slider.get())

    def set(self, v: float) -> None:
        self.slider.set(v)
        self.value_label.configure(text=self.fmt.format(v))

    def set_range(self, to: float, step: float = 0.1) -> None:
        self.slider.configure(to=max(step, to), number_of_steps=max(1, int(to / step)))


class ColorButton(ctk.CTkFrame):
    def __init__(self, master, label, color, command=None):
        super().__init__(master, fg_color="transparent")
        self.color = color
        self.command = command
        ctk.CTkLabel(self, text=label, anchor="w").pack(side="left")
        self.button = ctk.CTkButton(
            self, text=color, width=96, fg_color=color, hover_color=color,
            text_color=self._text_color(color), command=self._pick,
        )
        self.button.pack(side="right")

    @staticmethod
    def _text_color(color: str) -> str:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return "#000000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#FFFFFF"

    def _pick(self):
        _, hex_color = colorchooser.askcolor(color=self.color, title="Выбор цвета")
        if hex_color:
            self.set(hex_color.upper())
            if self.command:
                self.command()

    def set(self, color: str) -> None:
        self.color = color
        self.button.configure(
            text=color, fg_color=color, hover_color=color, text_color=self._text_color(color)
        )


class FileSlot(ctk.CTkFrame):
    """Необязательный файл (озвучка / музыка) с кнопками выбора и сброса."""

    def __init__(self, master, label: str, filetypes, command=None):
        super().__init__(master, fg_color="transparent")
        self.path: str | None = None
        self.filetypes = filetypes
        self.command = command
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text=label, command=self._pick).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="✕", width=32, fg_color="#3A3F55", command=self.clear).pack(side="left", padx=(4, 0))
        self.label = ctk.CTkLabel(self, text="не выбрано", text_color=MUTED, anchor="w")
        self.label.pack(fill="x")

    def _pick(self):
        path = filedialog.askopenfilename(filetypes=self.filetypes)
        if path:
            self.path = path
            self.label.configure(text=Path(path).name)
            if self.command:
                self.command()

    def clear(self):
        self.path = None
        self.label.configure(text="не выбрано")
        if self.command:
            self.command()


class UrlDialog(ctk.CTkToplevel):
    """Окно для ссылки на видео; ссылка из буфера обмена подставляется сама."""

    def __init__(self, master, folder: str):
        super().__init__(master)
        self.title("Видео по ссылке")
        self.geometry("580x250")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.result: tuple[str, str] | None = None
        self.folder = folder
        ctk.CTkLabel(
            self, justify="left", anchor="w", wraplength=540,
            text="Вставьте ссылку на видео: YouTube, VK Видео, Rutube, TikTok, Twitch, Vimeo "
            "или прямую ссылку на файл.",
        ).pack(fill="x", padx=16, pady=(16, 8))
        self.entry = ctk.CTkEntry(self, placeholder_text="https://…")
        self.entry.pack(fill="x", padx=16)
        folder_row = ctk.CTkFrame(self, fg_color="transparent")
        folder_row.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkButton(folder_row, text="Изменить…", width=100, fg_color="#3A3F55", command=self._pick_folder).pack(side="right")
        self.folder_label = ctk.CTkLabel(folder_row, anchor="w", justify="left", wraplength=420, text_color=MUTED)
        self.folder_label.pack(side="left", fill="x", expand=True)
        self._show_folder()
        try:
            clip = self.clipboard_get().strip()
            if is_url(clip):
                self.entry.insert(0, clip)
        except Exception:  # noqa: BLE001 — в буфере не текст
            pass
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(row, text="Отмена", width=100, fg_color="#3A3F55", command=self.destroy).pack(side="right")
        ctk.CTkButton(row, text="⬇ Скачать", width=120, fg_color=ACCENT, command=self._ok).pack(side="right", padx=8)
        self.entry.bind("<Return>", lambda _: self._ok())
        self.bind("<Escape>", lambda _: self.destroy())
        self.transient(master)
        self.after(100, self._focus)

    def _focus(self):
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001 — окно ещё не показано
            pass
        self.entry.focus_set()

    def _show_folder(self):
        try:
            free = f" (свободно {free_mb(self.folder) / 1024:.1f} ГБ)"
        except OSError:
            free = ""
        self.folder_label.configure(text=f"Сохранить в: {self.folder}{free}")

    def _pick_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Папка для скачанных видео", initialdir=self.folder)
        if folder:
            self.folder = folder
            self._show_folder()

    def _ok(self):
        url = self.entry.get().strip()
        if not is_url(url):
            self.entry.configure(border_color="#FF5252")
            return
        self.result = (url, self.folder)
        self.destroy()

    @classmethod
    def ask(cls, master, folder: str) -> tuple[str, str] | None:
        dialog = cls(master, folder)
        master.wait_window(dialog)
        return dialog.result


class PreviewPane(ctk.CTkFrame):
    """Превью кадра + ползунок времени."""

    def __init__(self, master, placeholder: str, on_change):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkLabel(self, text=placeholder, text_color=MUTED)
        self.label.grid(row=0, column=0, sticky="nsew")
        self.label.bind("<Configure>", lambda _: on_change(300))
        self.time = LabeledSlider(self, "Момент превью", 0, 1, 0, step=0.1, fmt="{:.1f} с", command=lambda _: on_change(250))
        self.time.grid(row=1, column=0, sticky="ew")
        self._image = None

    def show(self, path: str) -> None:
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            return
        box_w = max(100, self.label.winfo_width() - 10)
        box_h = max(100, self.label.winfo_height() - 10)
        scale = min(box_w / img.width, box_h / img.height)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        self._image = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        self.label.configure(image=self._image, text="")

    def error(self, text: str) -> None:
        self.label.configure(image=None, text=f"Ошибка превью:\n{text}")


class App(ctk.CTk, _DnDBase):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"Montash Studio v{__version__}")
        self.geometry("1480x900")
        self.minsize(1240, 780)
        self.configure(fg_color=BG)

        self.style, self.render_settings, app_cfg = load_settings()
        self.app_cfg = app_cfg
        # Вкладка «Клипы из видео»
        self.videos: list[tuple[str, float]] = []  # (путь, длительность)
        self.words_cache: dict = {}  # (путь, модель, язык) -> слова
        self.clips: list[Clip] = []
        self.clip_labels: dict[str, Clip] = {}
        self.current: Clip | None = None
        # Вкладка «Фильм → клип»
        self.film_files: list[str] = []
        self.analysis_cache: dict = {}
        self.montage: Montage | None = None
        self.shot_rows: list[dict] = []

        self.preview_dir = tempfile.mkdtemp(prefix="montash_")
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.action_buttons: list[ctk.CTkButton] = []
        self._preview_job = None
        self._preview_running = False
        self._preview_pending = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self.tabs = ctk.CTkTabview(
            self, fg_color="transparent", segmented_button_selected_color=ACCENT,
            command=lambda: self._schedule_preview(50),
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        self._build_clips_tab(self.tabs.add(TAB_CLIPS))
        self._build_film_tab(self.tabs.add(TAB_FILM))
        self.tabs.set(self.app_cfg.get("tab", TAB_CLIPS) if self.app_cfg.get("tab") in (TAB_CLIPS, TAB_FILM) else TAB_CLIPS)
        self._build_shared(row=1, column=1)
        self._build_status()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._enable_drop()
        if sys.platform == "win32":
            self.bind_all("<Control-KeyPress>", self._ctrl_keys_any_layout, add="+")
        self.after(100, self._poll_events)

    # ---------------------------------------------------------------- layout
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=48)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(
            bar, text="MONTASH STUDIO", font=ctk.CTkFont(size=18, weight="bold"),
            text_color=ACCENT,
        ).pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(
            bar, text="авто-клипы • караоке-субтитры • Shorts / TikTok / Reels",
            text_color=MUTED,
        ).pack(side="left", padx=8)

    def _action_button(self, master, **kwargs) -> ctk.CTkButton:
        button = ctk.CTkButton(master, **kwargs)
        self.action_buttons.append(button)
        return button

    def _big_button(self, master, text, command, green=False) -> ctk.CTkButton:
        colors = (
            dict(fg_color="#00C853", hover_color="#00A844", text_color="#000000")
            if green else dict(fg_color=ACCENT)
        )
        return self._action_button(
            master, text=text, height=44, font=ctk.CTkFont(size=14, weight="bold"),
            command=command, **colors,
        )

    @staticmethod
    def _scrollable(master, **kwargs) -> ctk.CTkScrollableFrame:
        kwargs.setdefault("fg_color", "transparent")
        return ctk.CTkScrollableFrame(
            master, scrollbar_button_color="#4A4F6A", scrollbar_button_hover_color=ACCENT, **kwargs
        )

    def _side_column(self, tab, width: int, button_text: str, command) -> ctk.CTkScrollableFrame:
        """Прокручиваемая колонка настроек; главная кнопка закреплена под ней и видна всегда.

        Ширина задаётся через width виджета: CustomTkinter масштабирует её вместе с
        интерфейсом (масштаб Windows 125–150%), а minsize у grid — нет.
        """
        tab.grid_columnconfigure(0, weight=0)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        holder = ctk.CTkFrame(tab, fg_color="transparent")
        holder.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        holder.grid_rowconfigure(0, weight=1)
        holder.grid_columnconfigure(0, weight=1)
        col = self._scrollable(holder, width=width)
        col.grid(row=0, column=0, sticky="nsew")
        self._big_button(holder, button_text, command).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        return col

    def _build_clips_tab(self, tab):
        col = self._side_column(tab, 320, "⚡ Анализировать и нарезать", self._analyze)

        s = Section(col, "Источник")
        s.pack(fill="x", pady=(0, 10))
        self._source_buttons(s.body, self._choose_video, self._choose_video_folder, lambda: self._ask_link("clips"))
        ctk.CTkLabel(s.body, text=DROP_HINT, text_color=MUTED, wraplength=265, anchor="w", justify="left").pack(fill="x", pady=(4, 0))
        self.file_label = ctk.CTkLabel(s.body, text="Видео не выбрано", text_color=MUTED, wraplength=265, anchor="w", justify="left")
        self.file_label.pack(fill="x", pady=(6, 0))

        s = Section(col, "Нарезка")
        s.pack(fill="x", pady=(0, 10))
        self.count_slider = LabeledSlider(s.body, "Клипов с каждого видео", 1, 30, self.app_cfg.get("count", 5))
        self.count_slider.pack(fill="x")
        self.dur_slider = LabeledSlider(s.body, "Хронометраж клипа, сек", 5, 180, self.app_cfg.get("duration", 30), fmt="{:.0f} с")
        self.dur_slider.pack(fill="x")
        self.subs_enabled = ctk.BooleanVar(value=self.app_cfg.get("subs", True))
        ctk.CTkCheckBox(s.body, text="Делать субтитры по речи", variable=self.subs_enabled, fg_color=ACCENT).pack(anchor="w")

        s = Section(col, "Редактор субтитров")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            s.body, text="Одна строка = одна фраза на экране. Пустая строка удаляет фразу.",
            text_color=MUTED, justify="left", anchor="w", wraplength=265,
        ).pack(fill="x")
        self.subs_box = ctk.CTkTextbox(s.body, height=220, wrap="word")
        self.subs_box.pack(fill="x", pady=6)
        ctk.CTkButton(s.body, text="Применить текст", fg_color=ACCENT, command=self._apply_text).pack(fill="x")

        center = ctk.CTkFrame(tab, fg_color=PANEL, corner_radius=12)
        center.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(center, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Клип:").pack(side="left")
        self.clip_var = ctk.StringVar(value="—")
        self.clip_menu = ctk.CTkOptionMenu(top, values=["—"], variable=self.clip_var, command=self._select_clip, width=260)
        self.clip_menu.pack(side="left", padx=8)
        self._action_button(top, text="Рендер клипа", width=120, command=lambda: self._render_clips(only_current=True)).pack(side="right")

        self.clips_preview = PreviewPane(center, "Выберите видео и нажмите «Анализировать»", self._schedule_preview)
        self.clips_preview.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self._big_button(center, "🎬 Рендер всех клипов", self._render_clips, green=True).grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _build_film_tab(self, tab):
        col = self._side_column(tab, 350, "⚡ Собрать клип", self._build_montage)

        s = Section(col, "Файлы фильма")
        s.pack(fill="x", pady=(0, 10))
        self._source_buttons(s.body, self._add_film_files, self._add_film_folder, lambda: self._ask_link("film"), self._clear_film_files)
        ctk.CTkLabel(s.body, text=DROP_HINT, text_color=MUTED, wraplength=295, anchor="w", justify="left").pack(fill="x", pady=(4, 0))
        self.film_label = ctk.CTkLabel(s.body, text="Файлы не выбраны", text_color=MUTED, wraplength=295, anchor="w", justify="left")
        self.film_label.pack(fill="x", pady=(6, 0))

        s = self.montage_section = Section(col, "Монтаж")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Как подбирать кадры", anchor="w").pack(fill="x")
        self.mode_var = ctk.StringVar(value=_invert(MODES).get(self.app_cfg.get("film_mode", "text"), next(iter(MODES))))
        ctk.CTkOptionMenu(s.body, values=list(MODES), variable=self.mode_var, command=lambda _: self._mode_changed()).pack(fill="x", pady=(2, 6))
        self.film_dur = LabeledSlider(s.body, "Длина клипа", 15, 180, self.app_cfg.get("film_duration", 60), step=5, fmt="{:.0f} с", command=lambda _: self._update_script_info())
        self.film_dur.pack(fill="x")
        self.shot_len = LabeledSlider(s.body, "Макс. длина одного плана", 1.5, 8, self.app_cfg.get("shot_len", 3.5), step=0.5, fmt="{:.1f} с")
        self.shot_len.pack(fill="x")

        # Показывается только в ИИ-режиме (см. _mode_changed).
        s = self.ai_section = Section(col, "ИИ-подбор сцен")
        ctk.CTkLabel(
            s.body, wraplength=295, justify="left", anchor="w", text_color=MUTED,
            text="Ключевые кадры фильма отправляются в Claude API — нужен интернет и ключ с console.anthropic.com. "
            "Первый анализ фильма ≈ $0.3–0.5 (Opus), повторные сборки — центы.",
        ).pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(s.body, text="Ключ API Anthropic", anchor="w").pack(fill="x")
        self.api_key = ctk.CTkEntry(s.body, show="•", placeholder_text="sk-ant-… (или ANTHROPIC_API_KEY)")
        self.api_key.pack(fill="x", pady=(2, 4))
        if self.app_cfg.get("api_key"):
            self.api_key.insert(0, self.app_cfg["api_key"])
        self.remember_key = ctk.BooleanVar(value=bool(self.app_cfg.get("api_key")))
        ctk.CTkCheckBox(s.body, text="Запомнить ключ", variable=self.remember_key, fg_color=ACCENT).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(s.body, text="Модель", anchor="w").pack(fill="x")
        self.ai_model_var = ctk.StringVar(value=_invert(AI_MODELS).get(self.app_cfg.get("ai_model", ""), next(iter(AI_MODELS))))
        ctk.CTkOptionMenu(s.body, values=list(AI_MODELS), variable=self.ai_model_var).pack(fill="x", pady=(2, 0))

        s = Section(col, "Текст про фильм")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            s.body, text="Текст пойдёт в субтитры. Пишите по ходу сюжета — кадры подбираются в том же порядке.",
            text_color=MUTED, justify="left", anchor="w", wraplength=295,
        ).pack(fill="x")
        self.script_box = ctk.CTkTextbox(s.body, height=180, wrap="word")
        self.script_box.pack(fill="x", pady=6)
        self.script_box.insert("1.0", self.app_cfg.get("script", ""))
        self.script_box.bind("<KeyRelease>", lambda _: self._update_script_info())
        self.script_info = ctk.CTkLabel(s.body, text="", text_color=MUTED, anchor="w", justify="left", wraplength=295)
        self.script_info.pack(fill="x")
        ctk.CTkButton(s.body, text="Обновить только субтитры", fg_color="#3A3F55", command=self._retime_script).pack(fill="x", pady=(6, 0))

        s = Section(col, "Звук")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            s.body, text="Звук и музыка фильма всегда отключаются. Можно добавить свою озвучку и/или музыку.",
            text_color=MUTED, justify="left", anchor="w", wraplength=295,
        ).pack(fill="x", pady=(0, 6))
        self.voice_slot = FileSlot(s.body, "🎙 Своя озвучка", AUDIO_TYPES, self._update_script_info)
        self.voice_slot.pack(fill="x", pady=(0, 6))
        self.music_slot = FileSlot(s.body, "🎵 Фоновая музыка", AUDIO_TYPES)
        self.music_slot.pack(fill="x", pady=(0, 6))
        self.music_vol = LabeledSlider(s.body, "Громкость музыки", 0, 100, self.app_cfg.get("music_volume", 25), fmt="{:.0f}%")
        self.music_vol.pack(fill="x")

        self._mode_changed()

        center = ctk.CTkFrame(tab, fg_color=PANEL, corner_radius=12)
        center.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        center.grid_rowconfigure(0, weight=3)
        center.grid_rowconfigure(2, weight=2)
        center.grid_columnconfigure(0, weight=1)

        self.film_preview = PreviewPane(center, "Добавьте файлы фильма, вставьте текст\nи нажмите «Собрать клип»", self._schedule_preview)
        self.film_preview.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            center, text="ПЛАНЫ: фраза · файл · таймкод в фильме · ↻ другой кадр",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=MUTED, anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=12)
        self.shots_frame = self._scrollable(center, fg_color=BG)
        self.shots_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        self._big_button(center, "🎬 Сохранить клип", self._render_montage, green=True).grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        self._update_script_info()

    def _build_shared(self, row: int, column: int):
        col = self._scrollable(self, width=340)
        col.grid(row=row, column=column, sticky="nsew", padx=(6, 12), pady=12)
        st = self.style

        s = Section(col, "Распознавание речи")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Модель Whisper (больше = точнее, медленнее)", anchor="w", justify="left", wraplength=300).pack(fill="x")
        self.model_var = ctk.StringVar(value=self.app_cfg.get("model", "small"))
        ctk.CTkOptionMenu(s.body, values=MODELS, variable=self.model_var).pack(fill="x", pady=(2, 6))
        ctk.CTkLabel(s.body, text="Язык", anchor="w").pack(fill="x")
        self.lang_var = ctk.StringVar(value=self.app_cfg.get("language", "Авто"))
        ctk.CTkOptionMenu(s.body, values=list(LANGUAGES), variable=self.lang_var).pack(fill="x", pady=(2, 0))

        s = Section(col, "Формат")
        s.pack(fill="x", pady=(0, 10))
        self.format_var = ctk.StringVar(value=self.render_settings.format_name)
        ctk.CTkOptionMenu(s.body, values=list(FORMATS), variable=self.format_var, command=lambda _: self._settings_changed()).pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(s.body, text="Заполнение кадра", anchor="w").pack(fill="x")
        self.fill_var = ctk.StringVar(value=_invert(FILL_MODES)[self.render_settings.fill])
        ctk.CTkOptionMenu(s.body, values=list(FILL_MODES), variable=self.fill_var, command=lambda _: self._settings_changed()).pack(fill="x", pady=(2, 0))

        s = Section(col, "Видеоэффекты")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Цветокоррекция", anchor="w").pack(fill="x")
        self.look_var = ctk.StringVar(value=_invert(LOOKS)[self.render_settings.look])
        ctk.CTkOptionMenu(s.body, values=list(LOOKS), variable=self.look_var, command=lambda _: self._settings_changed()).pack(fill="x", pady=(2, 6))
        self.vignette_var = ctk.BooleanVar(value=self.render_settings.vignette)
        ctk.CTkCheckBox(s.body, text="Виньетка", variable=self.vignette_var, fg_color=ACCENT, command=self._settings_changed).pack(anchor="w")

        s = Section(col, "Типографика и караоке")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Шрифт (установленный в системе или из папки fonts)", anchor="w", wraplength=300, justify="left").pack(fill="x")
        self.font_var = ctk.StringVar(value=st.font)
        ctk.CTkComboBox(s.body, values=FONTS, variable=self.font_var, command=lambda _: self._style_changed()).pack(fill="x", pady=(2, 8))
        self.font_var.trace_add("write", lambda *_: self._style_changed())

        self.primary_btn = ColorButton(s.body, "Основной цвет текста", st.primary_color, self._style_changed)
        self.primary_btn.pack(fill="x", pady=3)
        self.highlight_btn = ColorButton(s.body, "Цвет активного слова", st.highlight_color, self._style_changed)
        self.highlight_btn.pack(fill="x", pady=3)
        self.outline_btn = ColorButton(s.body, "Цвет обводки", st.outline_color, self._style_changed)
        self.outline_btn.pack(fill="x", pady=(3, 8))

        self.size_slider = LabeledSlider(s.body, "Размер шрифта", 30, 200, st.size, command=lambda _: self._style_changed())
        self.size_slider.pack(fill="x")
        self.outline_slider = LabeledSlider(s.body, "Толщина обводки", 0, 20, st.outline, step=0.5, fmt="{:.1f}", command=lambda _: self._style_changed())
        self.outline_slider.pack(fill="x")
        self.pos_slider = LabeledSlider(s.body, "Позиция (% от верха)", 5, 95, st.position, fmt="{:.0f}%", command=lambda _: self._style_changed())
        self.pos_slider.pack(fill="x")
        self.words_slider = LabeledSlider(s.body, "Слов на экране", 1, 8, st.words_per_chunk, command=lambda _: self._words_changed())
        self.words_slider.pack(fill="x")

        self.upper_var = ctk.BooleanVar(value=st.uppercase)
        self.bold_var = ctk.BooleanVar(value=st.bold)
        self.pop_var = ctk.BooleanVar(value=st.pop)
        for text, var in (("ЗАГЛАВНЫЕ БУКВЫ", self.upper_var), ("Жирный", self.bold_var), ("Анимация «прыжок» слова", self.pop_var)):
            ctk.CTkCheckBox(s.body, text=text, variable=var, fg_color=ACCENT, command=self._style_changed).pack(anchor="w", pady=2)

    def _build_status(self):
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.status = ctk.CTkLabel(bar, text="Готов к работе", anchor="w")
        self.status.pack(side="left", padx=16, pady=6)
        self.progress = ctk.CTkProgressBar(bar, width=320, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(side="right", padx=16)

    # ------------------------------------------------------------- settings
    def _collect_style(self) -> SubtitleStyle:
        return SubtitleStyle(
            font=self.font_var.get().strip() or "Arial",
            size=int(self.size_slider.get()),
            primary_color=self.primary_btn.color,
            highlight_color=self.highlight_btn.color,
            outline_color=self.outline_btn.color,
            outline=self.outline_slider.get(),
            shadow=self.style.shadow,
            position=self.pos_slider.get(),
            uppercase=self.upper_var.get(),
            bold=self.bold_var.get(),
            words_per_chunk=int(self.words_slider.get()),
            pop=self.pop_var.get(),
        )

    def _collect_render(self) -> RenderSettings:
        return replace(
            self.render_settings,
            format_name=self.format_var.get(),
            fill=FILL_MODES[self.fill_var.get()],
            look=LOOKS[self.look_var.get()],
            vignette=self.vignette_var.get(),
        )

    def _style_changed(self):
        self.style = self._collect_style()
        self._schedule_preview()

    def _settings_changed(self):
        self.render_settings = self._collect_render()
        self._schedule_preview()

    def _words_changed(self):
        self.style = self._collect_style()
        for clip in self.clips:
            rechunk(clip, self.style.words_per_chunk)
        self._fill_subs_box()
        self._schedule_preview()

    # ------------------------------------------------- источники видео
    def _source_buttons(self, master, on_files, on_folder, on_link, on_clear=None):
        top = ctk.CTkFrame(master, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkButton(top, text="📂 Файлы", fg_color=ACCENT, command=on_files).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(top, text="📁 Папка", fg_color=ACCENT, command=on_folder).pack(side="left", fill="x", expand=True, padx=(4, 0))
        bottom = ctk.CTkFrame(master, fg_color="transparent")
        bottom.pack(fill="x", pady=(4, 0))
        ctk.CTkButton(bottom, text="🔗 По ссылке", fg_color=ACCENT, command=on_link).pack(side="left", fill="x", expand=True)
        if on_clear:
            ctk.CTkButton(bottom, text="Очистить", width=90, fg_color="#3A3F55", command=on_clear).pack(side="left", padx=(4, 0))

    def _enable_drop(self):
        if TkinterDnD is None:
            return
        try:
            TkinterDnD._require(self)
            # Регистрируем всё окно: tkdnd ищет цель от элемента под курсором вверх.
            self.drop_target_register(DND_FILES, DND_TEXT)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:  # noqa: BLE001 — tkdnd не загрузился, работаем без перетаскивания
            traceback.print_exc()

    def _on_drop(self, event):
        data = str(event.data).strip()
        target = "film" if self.tabs.get() == TAB_FILM else "clips"
        if is_url(data):
            self._download_link(data, target)
        else:
            self._receive_videos(list(self.tk.splitlist(data)), target)
        return getattr(event, "action", "copy")

    def _receive_videos(self, paths: list[str], target: str):
        videos = collect_videos(paths)
        if not videos:
            self._set_status("Среди перетащенного нет видеофайлов")
            return
        if target == "film":
            # Новые файлы — в конец списка, в своём естественном порядке (часть 1, 2, …).
            self.film_files = list(dict.fromkeys(self.film_files + sorted(videos, key=natural_sort_key)))
            self._update_film_label()
            self._set_status(f"Файлов фильма: {len(self.film_files)}")
        else:
            self._set_videos(videos)

    def _ask_link(self, target: str):
        answer = UrlDialog.ask(self, self.app_cfg.get("download_dir") or str(downloads_dir()))
        if answer:
            url, self.app_cfg["download_dir"] = answer
            self._download_link(url, target)

    def _download_link(self, url: str, target: str):
        if self.busy:
            messagebox.showinfo("Montash", "Дождитесь окончания текущей операции")
            return
        out_dir = self.app_cfg.get("download_dir") or str(downloads_dir())

        def job():
            path = download(url, out_dir, self._progress)
            self.events.put(("downloaded", (target, path)))

        self._run_bg(job, "Скачивание видео…")

    def _ctrl_keys_any_layout(self, event):
        """Ctrl+C/V/X/A при русской раскладке: Tk в Windows сам их не понимает."""
        if event.keysym.lower() in ("c", "v", "x", "a"):
            return None
        action = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>", 65: "<<SelectAll>>"}.get(event.keycode)
        if action:
            event.widget.event_generate(action)
            return "break"
        return None

    # ---------------------------------------------------- tab: clips from video
    def _choose_video(self):
        paths = filedialog.askopenfilenames(title="Выберите видео", filetypes=VIDEO_TYPES)
        if paths:
            self._set_videos(list(paths))

    def _choose_video_folder(self):
        folder = filedialog.askdirectory(title="Папка с видео")
        if folder:
            self._receive_videos([folder], "clips")

    def _set_videos(self, paths: list[str]):
        videos, failed = [], []
        for path in paths:
            try:
                videos.append((path, probe_duration(path)))
            except Exception:  # noqa: BLE001 — битый или не видео
                failed.append(Path(path).name)
        if failed:
            messagebox.showwarning("Montash", "Не удалось открыть:\n" + "\n".join(failed[:10]))
        if not videos:
            return
        self.videos = videos
        self.clips, self.clip_labels, self.current = [], {}, None
        self.clip_menu.configure(values=["—"])
        self.clip_var.set("—")
        self._fill_subs_box()
        if len(videos) == 1:
            path, duration = videos[0]
            text = f"{Path(path).name}\nДлительность: {fmt_time(duration)}"
        else:
            total = sum(d for _, d in videos)
            names = [f"{i + 1}. {Path(p).name}" for i, (p, _) in enumerate(videos[:8])]
            if len(videos) > 8:
                names.append(f"…и ещё {len(videos) - 8}")
            text = f"{len(videos)} видео, всего {fmt_time(total)}:\n" + "\n".join(names)
        self.file_label.configure(text=text)
        self._set_status("Видео загружено. Нажмите «Анализировать и нарезать».")

    def _analyze(self):
        if self.busy:
            return
        if not self.videos:
            messagebox.showwarning("Montash", "Сначала выберите видео")
            return
        self.style = self._collect_style()
        count = int(self.count_slider.get())
        clip_dur = self.dur_slider.get()
        need_subs = self.subs_enabled.get()
        model, lang = self.model_var.get(), LANGUAGES[self.lang_var.get()]
        videos, cache = list(self.videos), self.words_cache
        wpc = self.style.words_per_chunk

        def job():
            clips, n_words = [], 0
            for k, (path, duration) in enumerate(videos):
                words = []
                if need_subs:
                    key = (path, model, lang)
                    if key not in cache:
                        prefix = f"[{k + 1}/{len(videos)}] " if len(videos) > 1 else ""
                        cache[key] = transcribe(
                            path, model, lang, duration,
                            progress=lambda f, m: self._progress((k + f) / len(videos), prefix + m),
                        )
                    words = cache[key]
                n_words += len(words)
                for clip in build_clips(words, duration, count, clip_dur, wpc):
                    clip.source = path
                    clips.append(clip)
            self.events.put(("analyzed", (clips, n_words)))

        self._run_bg(job, "Анализ…")

    def _render_clips(self, only_current: bool = False):
        if self.busy:
            return
        if not self.clips:
            messagebox.showwarning("Montash", "Сначала проанализируйте видео")
            return
        out_dir = filedialog.askdirectory(title="Папка для готовых клипов", initialdir=self.app_cfg.get("out_dir"))
        if not out_dir:
            return
        self.app_cfg["out_dir"] = out_dir
        clips = [self.current] if only_current and self.current else list(self.clips)
        style, settings = self._collect_style(), self._collect_render()
        subs = self.subs_enabled.get()

        def job():
            for i, clip in enumerate(clips):
                stem = Path(clip.source).stem
                self._progress(i / len(clips), f"Рендер: {stem}, клип {clip.index + 1} ({i + 1}/{len(clips)})…")
                out = os.path.join(out_dir, f"{stem}_clip{clip.index + 1}.mp4")
                render_clip(clip.source, clip, style, settings, out, with_subtitles=subs)
            self.events.put(("rendered", out_dir))

        self._run_bg(job, "Рендер…")

    def _select_clip(self, label: str):
        self.current = self.clip_labels.get(label)
        if self.current:
            self.clips_preview.time.set_range(self.current.duration)
            first = self.current.chunks[0].start + 0.05 if self.current.chunks else 0
            self.clips_preview.time.set(first)
        self._fill_subs_box()
        self._schedule_preview(50)

    def _fill_subs_box(self):
        self.subs_box.delete("1.0", "end")
        if self.current:
            self.subs_box.insert("1.0", "\n".join(c.text for c in self.current.chunks))

    def _apply_text(self):
        if not self.current:
            return
        lines = self.subs_box.get("1.0", "end").rstrip("\n").split("\n")
        chunks = self.current.chunks
        if len(lines) != len(chunks):
            messagebox.showwarning(
                "Редактор субтитров",
                f"Строк должно быть {len(chunks)} (сейчас {len(lines)}).\n"
                "Чтобы удалить фразу — оставьте строку пустой, не удаляя её.",
            )
            return
        new_chunks = [c for c in (apply_text_edit(ch, ln) for ch, ln in zip(chunks, lines)) if c]
        self.current.chunks = new_chunks
        self._fill_subs_box()
        self._schedule_preview(50)
        self._set_status("Текст субтитров обновлён")

    # ------------------------------------------------------ tab: film -> clip
    def _add_film_files(self):
        paths = filedialog.askopenfilenames(title="Файлы фильма", filetypes=VIDEO_TYPES)
        if paths:
            self._receive_videos(list(paths), "film")

    def _add_film_folder(self):
        folder = filedialog.askdirectory(title="Папка с файлами фильма")
        if folder:
            self._receive_videos([folder], "film")

    def _clear_film_files(self):
        self.film_files = []
        self.montage = None
        self._fill_shot_rows()
        self._update_film_label()

    def _update_film_label(self):
        if not self.film_files:
            self.film_label.configure(text="Файлы не выбраны")
            return
        names = "\n".join(f"{i + 1}. {Path(p).name}" for i, p in enumerate(self.film_files))
        self.film_label.configure(text=names)

    def _mode_changed(self):
        if MODES[self.mode_var.get()] == "ai":
            self.ai_section.pack(fill="x", pady=(0, 10), after=self.montage_section)
        else:
            self.ai_section.pack_forget()

    def _script_text(self) -> str:
        return self.script_box.get("1.0", "end").strip()

    def _update_script_info(self):
        text = self._script_text()
        if not text:
            self.script_info.configure(text="Вставьте текст про фильм")
            return
        words = sum(len(s) for s in split_sentences(text))
        natural = natural_duration(text)
        if self.voice_slot.path:
            hint = "длина клипа = длина озвучки"
        else:
            target = self.film_dur.get()
            ratio = natural / target
            hint = (
                "текст длинноват — субтитры будут быстрыми" if ratio > 1.35
                else "текста мало — фразы будут висеть долго" if ratio < 0.6
                else "темп нормальный"
            )
        self.script_info.configure(text=f"{words} слов, вслух ≈ {natural:.0f} с — {hint}")

    def _build_montage(self):
        if self.busy:
            return
        if not self.film_files:
            messagebox.showwarning("Montash", "Добавьте файлы фильма")
            return
        text = self._script_text()
        if not text:
            messagebox.showwarning("Montash", "Вставьте текст про фильм")
            return
        files = list(self.film_files)
        total, mode, max_shot = self.film_dur.get(), MODES[self.mode_var.get()], self.shot_len.get()
        voice = self.voice_slot.path
        model, lang = self.model_var.get(), LANGUAGES[self.lang_var.get()]
        cache = self.analysis_cache
        key = self.api_key.get().strip()
        if mode == "ai" and not (key or os.environ.get("ANTHROPIC_API_KEY")):
            messagebox.showwarning("Montash", "Для ИИ-подбора сцен введите ключ API Anthropic\n(раздел «ИИ-подбор сцен»).")
            return
        ai = AiOptions(key or None, AI_MODELS[self.ai_model_var.get()])

        def job():
            montage = build_montage(
                files, text, total, mode, max_shot, voice, model, lang,
                progress=self._progress, cached=cache, ai=ai,
            )
            self.events.put(("montage", montage))

        self._run_bg(job, "Анализ фильма (первый раз может занять несколько минут)…")

    def _retime_script(self):
        if not self.montage:
            messagebox.showinfo("Montash", "Сначала соберите клип")
            return
        text = self._script_text()
        if not text:
            return
        self.montage.retime(text)
        self._schedule_preview(50)
        self._set_status("Субтитры обновлены, кадры оставлены без изменений")

    def _fill_shot_rows(self):
        for row in self.shot_rows:
            row["frame"].destroy()
        self.shot_rows = []
        if not self.montage:
            return
        names = [f"{i + 1}. {Path(p).name}" for i, p in enumerate(self.montage.files)]
        for i, shot in enumerate(self.montage.shots):
            frame = ctk.CTkFrame(self.shots_frame, fg_color=PANEL, corner_radius=8)
            frame.pack(fill="x", pady=2)
            sentence = self.montage.sentences[shot.sentence].text if shot.sentence < len(self.montage.sentences) else ""
            info = ctk.CTkLabel(
                frame, anchor="w", cursor="hand2",
                text=f"{fmt_time(shot.out_start)}  {sentence[:46] + ('…' if len(sentence) > 46 else '')}",
            )
            info.pack(side="left", fill="x", expand=True, padx=8)
            info.bind("<Button-1>", lambda _, i=i: self._jump_to_shot(i))
            ctk.CTkButton(frame, text="↻", width=34, fg_color="#3A3F55", command=lambda i=i: self._replace_shot(i)).pack(side="right", padx=(2, 6), pady=4)
            entry = ctk.CTkEntry(frame, width=90)
            entry.insert(0, _fmt_precise(shot.src_start))
            entry.pack(side="right", padx=2)
            entry.bind("<Return>", lambda _, i=i: self._set_shot_time(i))
            entry.bind("<FocusOut>", lambda _, i=i: self._set_shot_time(i))
            file_var = ctk.StringVar(value=names[shot.file_index])
            if len(names) > 1:
                ctk.CTkOptionMenu(frame, values=names, variable=file_var, width=120, command=lambda _, i=i: self._set_shot_time(i)).pack(side="right", padx=2)
            self.shot_rows.append({"frame": frame, "entry": entry, "file": file_var, "names": names})

    def _refresh_shot_row(self, i: int):
        shot, row = self.montage.shots[i], self.shot_rows[i]
        row["entry"].delete(0, "end")
        row["entry"].insert(0, _fmt_precise(shot.src_start))
        row["file"].set(row["names"][shot.file_index])

    def _jump_to_shot(self, i: int):
        shot = self.montage.shots[i]
        self.film_preview.time.set(shot.out_start + min(0.5, shot.duration / 2))
        self._schedule_preview(50)
        if shot.note:
            self._set_status(f"План {i + 1}, ИИ: {shot.note}")

    def _replace_shot(self, i: int):
        if not self.montage:
            return
        if replace_shot(self.montage.index, self.montage.shots, i):
            self._refresh_shot_row(i)
            self._jump_to_shot(i)
        else:
            self._set_status("Других подходящих кадров не найдено")

    def _set_shot_time(self, i: int):
        if not self.montage or i >= len(self.shot_rows):
            return
        row, shot = self.shot_rows[i], self.montage.shots[i]
        try:
            seconds = _parse_time(row["entry"].get())
        except ValueError:
            self._refresh_shot_row(i)
            return
        file_index = row["names"].index(row["file"].get())
        if (file_index, round(seconds, 1)) == (shot.file_index, round(shot.src_start, 1)):
            return
        set_shot_time(self.montage.index, shot, file_index, seconds)
        self._refresh_shot_row(i)
        self._jump_to_shot(i)

    def _render_montage(self):
        if self.busy:
            return
        if not self.montage:
            messagebox.showwarning("Montash", "Сначала соберите клип")
            return
        stem = Path(self.montage.files[0]).stem
        out = filedialog.asksaveasfilename(
            title="Сохранить клип", defaultextension=".mp4", initialfile=f"{stem}_clip.mp4",
            initialdir=self.app_cfg.get("out_dir"), filetypes=[("MP4", "*.mp4")],
        )
        if not out:
            return
        self.app_cfg["out_dir"] = str(Path(out).parent)
        m = self.montage
        style, settings = self._collect_style(), self._collect_render()
        segments, chunks = m.segments(), m.chunks(style.words_per_chunk)
        voice, music, volume = self.voice_slot.path, self.music_slot.path, self.music_vol.get() / 100

        def job():
            self._progress(0.1, f"Рендер клипа из {len(segments)} планов…")
            render_montage(segments, chunks, m.total, style, settings, out, voice, music, volume)
            self.events.put(("rendered", str(Path(out).parent)))

        self._run_bg(job, "Рендер…")

    # --------------------------------------------------------------- preview
    def _schedule_preview(self, delay: int = 400):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(delay, self._start_preview)

    def _preview_request(self):
        """(панель, функция рендера кадра) для активной вкладки или None."""
        style, settings = self._collect_style(), self._collect_render()
        if self.tabs.get() == TAB_FILM:
            if not self.montage or not self.montage.shots:
                return None
            m, pane = self.montage, self.film_preview
            at = min(pane.time.get(), m.total - 0.05)
            shot = m.shot_at(at)
            src = m.files[shot.file_index]
            src_time = shot.src_start + (at - shot.out_start)
            chunks = m.chunks(style.words_per_chunk)
            return pane, lambda out: render_frame(src, src_time, chunks, at, m.total, style, settings, out)
        if not self.current:
            return None
        clip, pane, src = self.current, self.clips_preview, self.current.source
        at = max(0.0, min(pane.time.get(), clip.duration - 0.05))
        return pane, lambda out: render_frame(src, clip.start + at, clip.chunks, at, clip.duration, style, settings, out)

    def _start_preview(self):
        self._preview_job = None
        request = self._preview_request()
        if not request:
            return
        if self._preview_running:
            self._preview_pending = True
            return
        self._preview_running = True
        pane, fn = request
        out = os.path.join(self.preview_dir, "preview.png")

        def job():
            try:
                fn(out)
                self.events.put(("preview", (pane, out)))
            except Exception as e:
                self.events.put(("preview_error", (pane, str(e))))

        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------ background
    def _run_bg(self, fn, status: str):
        self.busy = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self._set_status(status)
        self.progress.set(0)

        def wrapper():
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                message = str(e)
                if is_disk_full(message):
                    message = disk_full_message()
                self.events.put(("error", message))
            finally:
                self.events.put(("done", None))

        threading.Thread(target=wrapper, daemon=True).start()

    def _progress(self, frac: float, msg: str):
        self.events.put(("progress", (frac, msg)))

    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress":
                    self.progress.set(data[0])
                    self._set_status(data[1])
                elif kind == "analyzed":
                    self.clips, n_words = data
                    many = len({c.source for c in self.clips}) > 1
                    self.clip_labels = {
                        (f"{Path(c.source).stem[:24]} · {c.label}" if many else c.label): c for c in self.clips
                    }
                    labels = list(self.clip_labels) or ["—"]
                    self.clip_menu.configure(values=labels)
                    self.clip_var.set(labels[0])
                    self.current = None
                    if self.clips:
                        self._select_clip(labels[0])
                    self.progress.set(1)
                    self._set_status(f"Найдено клипов: {len(self.clips)}, слов: {n_words}")
                elif kind == "downloaded":
                    target, path = data
                    self.progress.set(1)
                    self._receive_videos([path], target)
                    self._set_status(f"Скачано: {Path(path).name} (папка {Path(path).parent})")
                elif kind == "montage":
                    self.montage = data
                    self._fill_shot_rows()
                    self.film_preview.time.set_range(data.total)
                    self.film_preview.time.set(0.3)
                    self._schedule_preview(50)
                    self.progress.set(1)
                    ai_cost = f" ИИ-подбор: ≈ ${data.ai_cost:.2f}." if data.ai_cost else ""
                    self._set_status(
                        f"Клип собран: {data.total:.0f} с, {len(data.shots)} планов.{ai_cost} "
                        "Проверьте кадры и нажмите «Сохранить клип»."
                    )
                elif kind == "rendered":
                    self.progress.set(1)
                    self._set_status(f"Готово! Сохранено в {data}")
                    if messagebox.askyesno("Montash", "Рендер завершён. Открыть папку?"):
                        _open_folder(data)
                elif kind == "error":
                    self._set_status("Ошибка")
                    messagebox.showerror("Ошибка", data)
                elif kind == "done":
                    self.busy = False
                    for b in self.action_buttons:
                        b.configure(state="normal")
                elif kind == "preview":
                    self._preview_running = False
                    data[0].show(data[1])
                    if self._preview_pending:
                        self._preview_pending = False
                        self._start_preview()
                elif kind == "preview_error":
                    self._preview_running = False
                    self._preview_pending = False
                    data[0].error(data[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _on_close(self):
        self.app_cfg.update(
            model=self.model_var.get(), language=self.lang_var.get(),
            subs=self.subs_enabled.get(), count=int(self.count_slider.get()),
            duration=int(self.dur_slider.get()), tab=self.tabs.get(),
            script=self._script_text(), film_mode=MODES[self.mode_var.get()],
            film_duration=int(self.film_dur.get()), shot_len=self.shot_len.get(),
            music_volume=int(self.music_vol.get()), ai_model=AI_MODELS[self.ai_model_var.get()],
            api_key=self.api_key.get().strip() if self.remember_key.get() else "",
        )
        save_settings(self._collect_style(), self._collect_render(), self.app_cfg)
        shutil.rmtree(self.preview_dir, ignore_errors=True)
        self.destroy()


def _open_folder(path: str) -> None:
    import subprocess
    import sys

    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()

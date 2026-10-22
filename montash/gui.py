"""Графический интерфейс Montash (customtkinter)."""

from __future__ import annotations

import os
import queue
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
from .ffmpeg_tools import probe_duration
from .models import FILL_MODES, FORMATS, LOOKS, Clip, RenderSettings, SubtitleStyle
from .project import load_settings, save_settings
from .render import render_clip, render_preview
from .transcribe import LANGUAGES, MODELS, transcribe

ACCENT = "#7C4DFF"
PANEL = "#1B1E2B"
BG = "#12141C"
FONTS = [
    "Montserrat Black", "Montserrat", "Bebas Neue", "Impact", "Arial Black",
    "Arial", "Roboto", "Oswald", "Comic Sans MS", "Segoe UI Black",
]


def _invert(d: dict) -> dict:
    return {v: k for k, v in d.items()}


class Section(ctk.CTkFrame):
    def __init__(self, master, title: str):
        super().__init__(master, fg_color=PANEL, corner_radius=12)
        ctk.CTkLabel(
            self, text=title.upper(), font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#9AA0B8", anchor="w",
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


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"Montash Studio v{__version__}")
        self.geometry("1440x880")
        self.minsize(1200, 760)
        self.configure(fg_color=BG)

        self.style, self.render_settings, app_cfg = load_settings()
        self.app_cfg = app_cfg
        self.video_path: str | None = None
        self.duration = 0.0
        self.words = []
        self.clips: list[Clip] = []
        self.current: Clip | None = None
        self.preview_dir = tempfile.mkdtemp(prefix="montash_")
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self._preview_job = None
        self._preview_running = False
        self._preview_pending = False

        self.grid_columnconfigure(0, weight=0, minsize=330)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0, minsize=380)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_left()
        self._build_center()
        self._build_right()
        self._build_status()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)

    # ---------------------------------------------------------------- layout
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=48)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        ctk.CTkLabel(
            bar, text="MONTASH STUDIO", font=ctk.CTkFont(size=18, weight="bold"),
            text_color=ACCENT,
        ).pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(
            bar, text="авто-клипы • караоке-субтитры • Shorts / TikTok / Reels",
            text_color="#9AA0B8",
        ).pack(side="left", padx=8)

    def _build_left(self):
        col = ctk.CTkScrollableFrame(self, fg_color="transparent")
        col.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=12)

        s = Section(col, "Источник")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(s.body, text="📂 Выбрать видео", fg_color=ACCENT, command=self._choose_video).pack(fill="x")
        self.file_label = ctk.CTkLabel(s.body, text="Файл не выбран", text_color="#9AA0B8", wraplength=280, anchor="w", justify="left")
        self.file_label.pack(fill="x", pady=(6, 0))

        s = Section(col, "Распознавание речи")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Модель Whisper (больше = точнее, медленнее)", anchor="w").pack(fill="x")
        self.model_var = ctk.StringVar(value=self.app_cfg.get("model", "small"))
        ctk.CTkOptionMenu(s.body, values=MODELS, variable=self.model_var).pack(fill="x", pady=(2, 6))
        ctk.CTkLabel(s.body, text="Язык", anchor="w").pack(fill="x")
        self.lang_var = ctk.StringVar(value=self.app_cfg.get("language", "Авто"))
        ctk.CTkOptionMenu(s.body, values=list(LANGUAGES), variable=self.lang_var).pack(fill="x", pady=(2, 6))
        self.subs_enabled = ctk.BooleanVar(value=self.app_cfg.get("subs", True))
        ctk.CTkCheckBox(s.body, text="Делать субтитры", variable=self.subs_enabled, fg_color=ACCENT).pack(anchor="w")

        s = Section(col, "Нарезка")
        s.pack(fill="x", pady=(0, 10))
        self.count_slider = LabeledSlider(s.body, "Количество клипов", 1, 30, self.app_cfg.get("count", 5))
        self.count_slider.pack(fill="x")
        self.dur_slider = LabeledSlider(s.body, "Хронометраж клипа, сек", 5, 180, self.app_cfg.get("duration", 30), fmt="{:.0f} с")
        self.dur_slider.pack(fill="x")

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

        self.analyze_btn = ctk.CTkButton(
            col, text="⚡ Анализировать и нарезать", height=44, fg_color=ACCENT,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._analyze,
        )
        self.analyze_btn.pack(fill="x", pady=(4, 0))

    def _build_center(self):
        col = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=12)
        col.grid(row=1, column=1, sticky="nsew", padx=6, pady=12)
        col.grid_rowconfigure(1, weight=1)
        col.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(col, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(top, text="Клип:").pack(side="left")
        self.clip_var = ctk.StringVar(value="—")
        self.clip_menu = ctk.CTkOptionMenu(top, values=["—"], variable=self.clip_var, command=self._select_clip, width=260)
        self.clip_menu.pack(side="left", padx=8)
        self.render_one_btn = ctk.CTkButton(top, text="Рендер клипа", width=120, command=lambda: self._render(only_current=True))
        self.render_one_btn.pack(side="right")

        self.preview_label = ctk.CTkLabel(col, text="Выберите видео и нажмите «Анализировать»", text_color="#9AA0B8")
        self.preview_label.grid(row=1, column=0, sticky="nsew", padx=12, pady=6)
        self.preview_label.bind("<Configure>", lambda _: self._schedule_preview(300))

        bottom = ctk.CTkFrame(col, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 12))
        self.time_slider = LabeledSlider(bottom, "Момент превью", 0, 1, 0, step=0.1, fmt="{:.1f} с", command=lambda _: self._schedule_preview(250))
        self.time_slider.pack(fill="x")
        self.render_all_btn = ctk.CTkButton(
            bottom, text="🎬 Рендер всех клипов", height=44, fg_color="#00C853", hover_color="#00A844",
            text_color="#000000", font=ctk.CTkFont(size=14, weight="bold"), command=self._render,
        )
        self.render_all_btn.pack(fill="x", pady=(6, 0))

    def _build_right(self):
        col = ctk.CTkScrollableFrame(self, fg_color="transparent")
        col.grid(row=1, column=2, sticky="nsew", padx=(6, 12), pady=12)
        st = self.style

        s = Section(col, "Редактор субтитров")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            s.body, text="Одна строка = одна фраза на экране.\nПустая строка удаляет фразу.",
            text_color="#9AA0B8", justify="left", anchor="w",
        ).pack(fill="x")
        self.subs_box = ctk.CTkTextbox(s.body, height=220, wrap="word")
        self.subs_box.pack(fill="x", pady=6)
        ctk.CTkButton(s.body, text="Применить текст", fg_color=ACCENT, command=self._apply_text).pack(fill="x")

        s = Section(col, "Типографика и караоке")
        s.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(s.body, text="Шрифт (установленный в системе или из папки fonts)", anchor="w", wraplength=320, justify="left").pack(fill="x")
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
        bar.grid(row=2, column=0, columnspan=3, sticky="ew")
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

    # --------------------------------------------------------------- actions
    def _choose_video(self):
        path = filedialog.askopenfilename(
            title="Выберите видео",
            filetypes=[("Видео", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            self.duration = probe_duration(path)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return
        self.video_path = path
        self.words, self.clips, self.current = [], [], None
        mins, secs = divmod(int(self.duration), 60)
        self.file_label.configure(text=f"{Path(path).name}\nДлительность: {mins}:{secs:02d}")
        self._set_status("Видео загружено. Нажмите «Анализировать и нарезать».")

    def _analyze(self):
        if self.busy:
            return
        if not self.video_path:
            messagebox.showwarning("Montash", "Сначала выберите видео")
            return
        self.style = self._collect_style()
        count = int(self.count_slider.get())
        clip_dur = self.dur_slider.get()
        need_subs = self.subs_enabled.get()
        model, lang = self.model_var.get(), LANGUAGES[self.lang_var.get()]
        path, duration = self.video_path, self.duration
        wpc = self.style.words_per_chunk
        cached = self.words if self.app_cfg.get("_words_for") == (path, model, lang) else None

        def job():
            words = []
            if need_subs:
                if cached is not None:
                    words = cached
                else:
                    words = transcribe(path, model, lang, duration, progress=self._progress)
            clips = build_clips(words, duration, count, clip_dur, wpc)
            self.events.put(("analyzed", (words, clips, (path, model, lang) if need_subs else None)))

        self._run_bg(job, "Анализ…")

    def _render(self, only_current: bool = False):
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
        src, subs = self.video_path, self.subs_enabled.get()
        stem = Path(src).stem

        def job():
            for i, clip in enumerate(clips):
                self._progress(i / len(clips), f"Рендер клипа {clip.index + 1} ({i + 1}/{len(clips)})…")
                out = os.path.join(out_dir, f"{stem}_clip{clip.index + 1}.mp4")
                render_clip(src, clip, style, settings, out, with_subtitles=subs)
            self.events.put(("rendered", out_dir))

        self._run_bg(job, "Рендер…")

    def _select_clip(self, label: str):
        for clip in self.clips:
            if clip.label == label:
                self.current = clip
                break
        if self.current:
            self.time_slider.slider.configure(
                to=max(0.1, self.current.duration),
                number_of_steps=max(1, int(self.current.duration * 10)),
            )
            first = self.current.chunks[0].start + 0.05 if self.current.chunks else 0
            self.time_slider.set(first)
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

    # --------------------------------------------------------------- preview
    def _schedule_preview(self, delay: int = 400):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(delay, self._start_preview)

    def _start_preview(self):
        self._preview_job = None
        if not (self.video_path and self.current):
            return
        if self._preview_running:
            self._preview_pending = True
            return
        self._preview_running = True
        clip, style, settings = self.current, self._collect_style(), self._collect_render()
        at, src = self.time_slider.get(), self.video_path
        out = os.path.join(self.preview_dir, "preview.png")

        def job():
            try:
                render_preview(src, clip, style, settings, at, out)
                self.events.put(("preview", out))
            except Exception as e:
                self.events.put(("preview_error", str(e)))

        threading.Thread(target=job, daemon=True).start()

    def _show_preview(self, path: str):
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            return
        box_w = max(100, self.preview_label.winfo_width() - 10)
        box_h = max(100, self.preview_label.winfo_height() - 10)
        scale = min(box_w / img.width, box_h / img.height)
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        self._preview_img = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        self.preview_label.configure(image=self._preview_img, text="")

    # ------------------------------------------------------------ background
    def _run_bg(self, fn, status: str):
        self.busy = True
        for b in (self.analyze_btn, self.render_all_btn, self.render_one_btn):
            b.configure(state="disabled")
        self._set_status(status)
        self.progress.set(0)

        def wrapper():
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                self.events.put(("error", str(e)))
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
                    self.words, self.clips, key = data
                    self.app_cfg["_words_for"] = key
                    labels = [c.label for c in self.clips] or ["—"]
                    self.clip_menu.configure(values=labels)
                    self.clip_var.set(labels[0])
                    self.current = None
                    if self.clips:
                        self._select_clip(labels[0])
                    self.progress.set(1)
                    self._set_status(f"Найдено клипов: {len(self.clips)}, слов: {len(self.words)}")
                elif kind == "rendered":
                    self.progress.set(1)
                    self._set_status(f"Готово! Клипы сохранены в {data}")
                    if messagebox.askyesno("Montash", "Рендер завершён. Открыть папку?"):
                        _open_folder(data)
                elif kind == "error":
                    self._set_status("Ошибка")
                    messagebox.showerror("Ошибка", data)
                elif kind == "done":
                    self.busy = False
                    for b in (self.analyze_btn, self.render_all_btn, self.render_one_btn):
                        b.configure(state="normal")
                elif kind == "preview":
                    self._preview_running = False
                    self._show_preview(data)
                    if self._preview_pending:
                        self._preview_pending = False
                        self._start_preview()
                elif kind == "preview_error":
                    self._preview_running = False
                    self._preview_pending = False
                    self.preview_label.configure(image=None, text=f"Ошибка превью:\n{data}")
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _on_close(self):
        self.app_cfg.update(
            model=self.model_var.get(), language=self.lang_var.get(),
            subs=self.subs_enabled.get(), count=int(self.count_slider.get()),
            duration=int(self.dur_slider.get()),
        )
        self.app_cfg.pop("_words_for", None)
        save_settings(self._collect_style(), self._collect_render(), self.app_cfg)
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

"""Фильм + текст -> один клип: анализ, тайминг текста, подбор кадров."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .analysis import analyze_video
from .ffmpeg_tools import probe_duration
from .film import FilmIndex, Shot, plan_shots, subtitle_chunks
from .models import Chunk, Word
from .render import Segment
from .script import Sentence, time_by_voice, time_evenly

Progress = Callable[[float, str], None]


@dataclass
class Montage:
    files: list[str]
    index: FilmIndex
    sentences: list[Sentence]
    shots: list[Shot]
    total: float
    voice_words: list[Word] | None = None

    def segments(self) -> list[Segment]:
        return [Segment(self.files[s.file_index], s.src_start, s.duration) for s in self.shots]

    def chunks(self, words_per_chunk: int) -> list[Chunk]:
        return subtitle_chunks(self.sentences, words_per_chunk)

    def retime(self, text: str) -> None:
        """Обновляет текст субтитров, не трогая подобранные кадры."""
        if self.voice_words is not None:
            self.sentences = time_by_voice(text, self.voice_words, self.total)
        else:
            self.sentences = time_evenly(text, self.total)

    def shot_at(self, t: float) -> Shot:
        for shot in self.shots:
            if shot.out_start <= t < shot.out_end:
                return shot
        return self.shots[-1]


def natural_sort_key(path: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", path)]


def build_montage(
    files: list[str],
    text: str,
    total: float,
    mode: str = "text",
    max_shot: float = 3.5,
    voice: str | None = None,
    model: str = "small",
    language: str | None = None,
    progress: Progress | None = None,
    cached: dict | None = None,
) -> Montage:
    """`cached` — словарь путь -> VideoAnalysis, чтобы не анализировать повторно."""

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    if not text.strip():
        raise ValueError("Вставьте текст про фильм")
    files = sorted(files, key=natural_sort_key)
    cached = cached if cached is not None else {}

    analyses = []
    for i, path in enumerate(files):
        if path not in cached:
            share = 0.7 / len(files)
            cached[path] = analyze_video(path, lambda f, m: report(i * share + f * share, m))
        analyses.append(cached[path])
    index = FilmIndex(analyses)

    words = None
    if voice:
        from .transcribe import transcribe

        total = probe_duration(voice) + 0.3
        words = transcribe(voice, model, language, total, lambda f, m: report(0.7 + 0.25 * f, "Озвучка: " + m))
        sentences = time_by_voice(text, words, total)
    else:
        sentences = time_evenly(text, total)

    report(0.97, "Подбор кадров…")
    shots = plan_shots(index, sentences, total, mode, max_shot)
    report(1.0, f"Готово: {len(sentences)} фраз, {len(shots)} планов, {total:.0f} с")
    return Montage(files, index, sentences, shots, total, words)

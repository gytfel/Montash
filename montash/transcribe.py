"""Распознавание речи с таймингом каждого слова (faster-whisper)."""

from __future__ import annotations

import os
import tempfile
from typing import Callable

from .ffmpeg_tools import extract_audio
from .models import Word

MODELS = ["tiny", "base", "small", "medium", "large-v3"]
LANGUAGES = {
    "Авто": None,
    "Русский": "ru",
    "English": "en",
    "Українська": "uk",
    "Deutsch": "de",
    "Español": "es",
}

_model_cache: dict[str, object] = {}


def transcribe(
    video_path: str,
    model_name: str = "small",
    language: str | None = None,
    duration: float | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> list[Word]:
    from faster_whisper import WhisperModel

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    report(0.0, "Извлечение аудио…")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(video_path, wav)

        if model_name not in _model_cache:
            report(0.05, f"Загрузка модели {model_name} (первый раз скачивается)…")
            _model_cache[model_name] = WhisperModel(model_name, device="auto", compute_type="int8")
        model = _model_cache[model_name]

        report(0.1, "Распознавание речи…")
        segments, info = model.transcribe(
            wav, language=language, word_timestamps=True, vad_filter=True
        )
        total = duration or info.duration or 1.0
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if text:
                    words.append(Word(float(w.start), float(w.end), text))
            report(0.1 + 0.9 * min(1.0, seg.end / total), f"Распознано {int(seg.end)} / {int(total)} с")
    report(1.0, f"Готово: {len(words)} слов")
    return words

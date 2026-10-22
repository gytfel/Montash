"""Текст сценария: разбиение на фразы и тайминг слов."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .models import Word

# Примерная скорость чтения вслух (символов в секунду) — для подсказок.
READING_CPS = 14.0
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_PAUSE_WEIGHT = 4.0  # пауза после фразы, в «символах»


@dataclass
class Sentence:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def split_sentences(text: str) -> list[list[str]]:
    result = []
    for part in _SENTENCE_RE.split(text.strip()):
        tokens = part.split()
        if tokens:
            result.append(tokens)
    return result


def natural_duration(text: str) -> float:
    sentences = split_sentences(text)
    chars = sum(len(t) + 1 for s in sentences for t in s) + _PAUSE_WEIGHT * len(sentences)
    return chars / READING_CPS


def time_evenly(text: str, total: float) -> list[Sentence]:
    """Растягивает текст на `total` секунд пропорционально длине слов."""
    sentences = split_sentences(text)
    weights: list[list[float]] = [[len(t) + 1.0 for t in s] for s in sentences]
    total_weight = sum(sum(w) for w in weights) + _PAUSE_WEIGHT * len(sentences)
    if not sentences or total <= 0:
        return []
    k = total / total_weight
    t = 0.0
    result = []
    for tokens, ws in zip(sentences, weights):
        words = []
        for token, w in zip(tokens, ws):
            words.append(Word(t, t + w * k * 0.92, token))
            t += w * k
        t += _PAUSE_WEIGHT * k
        result.append(Sentence(words))
    return result


def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower().replace("ё", "е"))


def time_by_voice(text: str, asr_words: list[Word], total: float) -> list[Sentence]:
    """Привязывает слова сценария к распознанной озвучке.

    Совпавшие слова получают время из распознавания, остальные
    интерполируются между соседями.
    """
    sentences = split_sentences(text)
    tokens = [t for s in sentences for t in s]
    if not tokens:
        return []
    if not asr_words:
        return time_evenly(text, total)

    starts: list[float | None] = [None] * len(tokens)
    ends: list[float | None] = [None] * len(tokens)
    matcher = SequenceMatcher(
        None, [_norm(t) for t in tokens], [_norm(w.text) for w in asr_words], autojunk=False
    )
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                starts[i1 + k] = asr_words[j1 + k].start
                ends[i1 + k] = asr_words[j1 + k].end
        elif op == "replace":
            a, b = asr_words[j1].start, asr_words[j2 - 1].end
            step = (b - a) / (i2 - i1)
            for k in range(i2 - i1):
                starts[i1 + k] = a + k * step
                ends[i1 + k] = a + (k + 1) * step

    # Интерполяция слов, которых нет в озвучке.
    n = len(tokens)
    i = 0
    while i < n:
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < n and starts[j] is None:
            j += 1
        left = ends[i - 1] if i > 0 else 0.0
        right = starts[j] if j < n else max(left, total)
        step = max(0.05, (right - left) / (j - i))
        for k in range(i, j):
            starts[k] = left + (k - i) * step
            ends[k] = left + (k - i + 1) * step
        i = j

    result = []
    pos = 0
    for s in sentences:
        words = [Word(float(starts[pos + k]), float(ends[pos + k]), t) for k, t in enumerate(s)]
        pos += len(s)
        result.append(Sentence(words))
    return result

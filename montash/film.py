"""Сборка одного клипа из фильма по тексту сценария.

Два режима подбора кадров:
- "text": текст идёт по сюжету, поэтому фразы раскладываются по фильму
  хронологически, а внутри своего участка берётся самый динамичный план;
- "highlights": берутся самые интересные моменты всего фильма
  (движение, частые склейки, громкий звук) в хронологическом порядке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .analysis import STEP, VideoAnalysis
from .clips import make_chunks
from .models import Chunk
from .script import Sentence

MODES = {"Под текст (по сюжету)": "text", "Интересные моменты": "highlights"}

_EXCLUDED = -1e3
_CUT_PENALTY = 0.35
_GAP = int(2.0 / STEP)  # минимальный зазор между выбранными планами


@dataclass
class Shot:
    file_index: int
    src_start: float  # секунды в файле
    duration: float
    out_start: float  # секунды в готовом клипе
    sentence: int
    region: tuple[int, int] = (0, 0)  # где искать замену (индексы отсчётов)
    rejected: list[int] = field(default_factory=list)

    @property
    def out_end(self) -> float:
        return self.out_start + self.duration


def _smooth(x: np.ndarray, size: int) -> np.ndarray:
    if len(x) == 0:
        return x
    kernel = np.ones(size, dtype=np.float32) / size
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def _z(x: np.ndarray) -> np.ndarray:
    if len(x) == 0:
        return x
    med = np.median(x)
    spread = np.percentile(x, 75) - np.percentile(x, 25)
    if spread <= 1e-6:
        spread = float(x.std()) or 1.0
    return ((x - med) / spread).astype(np.float32)


class FilmIndex:
    """Все файлы фильма как одна общая шкала отсчётов."""

    def __init__(self, analyses: list[VideoAnalysis]):
        if not analyses:
            raise ValueError("Нет файлов фильма")
        self.analyses = analyses
        self.offsets = np.cumsum([0] + [a.samples for a in analyses])
        self.n = int(self.offsets[-1])
        self.file_of = np.repeat(np.arange(len(analyses)), [a.samples for a in analyses])

        motion = np.concatenate([a.motion for a in analyses])
        loud = np.concatenate([a.loudness for a in analyses])
        bright = np.concatenate([a.brightness for a in analyses])
        cuts = np.concatenate([a.cuts for a in analyses]).astype(bool)
        cuts[self.offsets[:-1]] = True
        self.cuts = cuts

        cut_density = _smooth(cuts.astype(np.float32), int(4 / STEP))
        interest = (
            _z(_smooth(motion, int(2 / STEP)))
            + 0.6 * _z(_smooth(loud, int(2 / STEP)))
            + 0.3 * _z(cut_density)
        )
        smooth_bright = _smooth(bright, int(1 / STEP))
        interest[smooth_bright < 25] -= 4.0  # очень тёмные кадры
        interest[smooth_bright < 12] = _EXCLUDED  # чёрный экран, затемнения
        lo, hi = self.usable_range()
        interest[:lo] = _EXCLUDED
        interest[hi:] = _EXCLUDED
        self.interest = interest
        self._interest_cs = np.concatenate([[0.0], np.cumsum(interest, dtype=np.float64)])
        self._cuts_cs = np.concatenate([[0], np.cumsum(cuts)])

    def usable_range(self) -> tuple[int, int]:
        """Без вступления и финальных титров (для длинного видео)."""
        if self.n * STEP < 20 * 60:
            return 0, self.n
        return int(self.n * 0.02), int(self.n * 0.92)

    def to_local(self, idx: int) -> tuple[int, float]:
        fi = int(self.file_of[min(max(idx, 0), self.n - 1)])
        return fi, (idx - int(self.offsets[fi])) * STEP

    def to_global(self, file_index: int, seconds: float) -> int:
        return int(self.offsets[file_index]) + int(round(seconds / STEP))

    def scores(self, length: int, blocked: np.ndarray) -> np.ndarray:
        """Оценка окна длиной `length` для каждого возможного начала."""
        count = self.n - length + 1
        if count <= 0:
            return np.full(0, -np.inf)
        starts = np.arange(count)
        mean = (self._interest_cs[starts + length] - self._interest_cs[starts]) / length
        inner_cuts = self._cuts_cs[starts + length] - self._cuts_cs[np.minimum(starts + 1, self.n)]
        score = mean - _CUT_PENALTY * inner_cuts
        score += 0.15 * self.cuts[starts]  # план, начинающийся со склейки, смотрится чище
        same_file = self.file_of[starts] == self.file_of[starts + length - 1]
        blocked_cs = np.concatenate([[0], np.cumsum(blocked)])
        free = (blocked_cs[starts + length] - blocked_cs[starts]) == 0
        score[~(same_file & free)] = -np.inf
        return score

    def best(self, length: int, blocked: np.ndarray, region: tuple[int, int]) -> int | None:
        found = self.best_scored(length, blocked, region)
        return found[0] if found else None

    def best_scored(
        self, length: int, blocked: np.ndarray, region: tuple[int, int]
    ) -> tuple[int, float] | None:
        score = self.scores(length, blocked)
        lo, hi = max(0, region[0]), min(len(score), region[1])
        if hi <= lo:
            return None
        idx = lo + int(np.argmax(score[lo:hi]))
        if not (np.isfinite(score[idx]) and score[idx] > _EXCLUDED / 2):
            return None
        return idx, float(score[idx])


def plan_shots(
    index: FilmIndex,
    sentences: list[Sentence],
    total: float,
    mode: str = "text",
    max_shot: float = 3.5,
) -> list[Shot]:
    shots = _split_into_shots(sentences, total, max_shot)
    if not shots:
        return []
    blocked = np.zeros(index.n, dtype=bool)
    lo, hi = index.usable_range()

    if mode == "highlights":
        length = max(1, int(math.ceil(max(s.duration for s in shots) / STEP)))
        picks = []
        for _ in shots:
            start = index.best(length, blocked, (lo, hi))
            if start is None:
                start = index.best(length, np.zeros_like(blocked), (0, index.n))
            if start is None:
                raise RuntimeError("Фильм слишком короткий для такого клипа")
            picks.append(start)
            _block(blocked, start, length)
        picks.sort()
        for shot, start in zip(shots, picks):
            shot.region = (lo, hi)
            _assign(index, shot, start)
        return shots

    span = hi - lo
    for shot in shots:
        length = max(1, int(math.ceil(shot.duration / STEP)))
        center = lo + span * (shot.out_start + shot.duration / 2) / total
        half = max(span / len(shots) * 0.75, length)
        shot.region = (int(center - half), int(center + half))
        start = index.best(length, blocked, shot.region)
        if start is None:
            start = index.best(length, blocked, (lo, hi))
        if start is None:
            start = index.best(length, np.zeros_like(blocked), (0, index.n))
        if start is None:
            raise RuntimeError("Фильм слишком короткий для такого клипа")
        _assign(index, shot, start)
        _block(blocked, start, length)
    return shots


def replace_shot(index: FilmIndex, shots: list[Shot], i: int) -> bool:
    """Подбирает другой план для shots[i]. Возвращает False, если нечего предложить."""
    shot = shots[i]
    length = max(1, int(math.ceil(shot.duration / STEP)))
    blocked = np.zeros(index.n, dtype=bool)
    for j, other in enumerate(shots):
        if j != i:
            _block(blocked, index.to_global(other.file_index, other.src_start), length_of(other))
    current = index.to_global(shot.file_index, shot.src_start)
    shot.rejected.append(current)
    for r in shot.rejected:
        _block(blocked, r, length)
    # Рядом по сюжету — если там нет ничего сравнимого с лучшим по фильму.
    near = index.best_scored(length, blocked, shot.region)
    anywhere = index.best_scored(length, blocked, index.usable_range())
    if near and (not anywhere or near[1] >= anywhere[1] - 1.5):
        start = near[0]
    elif anywhere:
        start = anywhere[0]
    else:
        shot.rejected = [current]
        return False
    _assign(index, shot, start)
    return True


def set_shot_time(index: FilmIndex, shot: Shot, file_index: int, seconds: float) -> None:
    duration = index.analyses[file_index].duration
    shot.file_index = file_index
    shot.src_start = max(0.0, min(seconds, duration - shot.duration))


def length_of(shot: Shot) -> int:
    return max(1, int(math.ceil(shot.duration / STEP)))


def subtitle_chunks(sentences: list[Sentence], words_per_chunk: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for s in sentences:
        chunks.extend(make_chunks(s.words, words_per_chunk))
    return chunks


def _split_into_shots(sentences: list[Sentence], total: float, max_shot: float) -> list[Shot]:
    shots = []
    for i, s in enumerate(sentences):
        start = 0.0 if i == 0 else s.start
        end = sentences[i + 1].start if i + 1 < len(sentences) else total
        span = end - start
        if span <= 0.05:
            continue
        parts = max(1, math.ceil(span / max_shot - 1e-9))
        for k in range(parts):
            shots.append(Shot(0, 0.0, span / parts, start + k * span / parts, i))
    return shots


def _assign(index: FilmIndex, shot: Shot, start: int) -> None:
    fi, seconds = index.to_local(start)
    set_shot_time(index, shot, fi, seconds)


def _block(blocked: np.ndarray, start: int, length: int) -> None:
    blocked[max(0, start - _GAP): start + length + _GAP] = True

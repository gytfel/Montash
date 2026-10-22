"""Выбор фрагментов для клипов и разбиение слов на чанки."""

from __future__ import annotations

from .models import Chunk, Clip, Word

# Пауза между словами, после которой начинается новый чанк.
_CHUNK_BREAK_GAP = 0.6
_SENTENCE_END = (".", "!", "?", "…")


def select_clips(
    words: list[Word], total_duration: float, count: int, clip_duration: float
) -> list[tuple[float, float]]:
    """Возвращает до `count` непересекающихся окон длиной `clip_duration`.

    Окна выбираются по плотности речи: предпочтение отдаётся местам, где
    больше всего слов. Без речи — равномерно по видео.
    """
    if count <= 0 or total_duration <= 0:
        return []
    clip_duration = min(clip_duration, total_duration)

    if not words:
        return _evenly_spaced(total_duration, count, clip_duration)

    # Кандидаты начинаются с начала слова (чуть раньше), чтобы клип не
    # обрывал фразу на старте.
    candidates: set[float] = set()
    for i, w in enumerate(words):
        prev_end = words[i - 1].end if i else 0.0
        if i == 0 or w.start - prev_end > 0.3 or words[i - 1].text.endswith(_SENTENCE_END):
            candidates.add(max(0.0, w.start - 0.15))
    scored = []
    for start in candidates:
        start = min(start, total_duration - clip_duration)
        end = start + clip_duration
        n = sum(1 for w in words if w.start >= start and w.end <= end)
        scored.append((n, -start, start, end))
    scored.sort(reverse=True)

    picked: list[tuple[float, float]] = []
    for _, _, start, end in scored:
        if all(end <= s or start >= e for s, e in picked):
            picked.append((start, end))
            if len(picked) == count:
                break

    # Если речи мало, добиваем оставшиеся клипы свободными промежутками.
    if len(picked) < count:
        for start, end in _evenly_spaced(total_duration, count * 4, clip_duration):
            if all(end <= s or start >= e for s, e in picked):
                picked.append((start, end))
                if len(picked) == count:
                    break

    return sorted(picked)


def _evenly_spaced(total: float, count: int, length: float) -> list[tuple[float, float]]:
    max_fit = max(1, int(total // length))
    count = min(count, max_fit)
    if count == 1:
        start = (total - length) / 2
        return [(start, start + length)]
    step = (total - length) / (count - 1)
    return [(i * step, i * step + length) for i in range(count)]


def words_in_range(words: list[Word], start: float, end: float) -> list[Word]:
    """Слова внутри окна со временем относительно `start`."""
    result = []
    for w in words:
        if w.end <= start or w.start >= end:
            continue
        result.append(Word(max(0.0, w.start - start), min(end, w.end) - start, w.text))
    return result


def make_chunks(words: list[Word], words_per_chunk: int) -> list[Chunk]:
    words_per_chunk = max(1, words_per_chunk)
    chunks: list[Chunk] = []
    current: list[Word] = []
    for w in words:
        if current and (
            len(current) >= words_per_chunk
            or w.start - current[-1].end > _CHUNK_BREAK_GAP
            or current[-1].text.endswith(_SENTENCE_END)
        ):
            chunks.append(Chunk(current))
            current = []
        current.append(w)
    if current:
        chunks.append(Chunk(current))
    return chunks


def build_clips(
    words: list[Word],
    total_duration: float,
    count: int,
    clip_duration: float,
    words_per_chunk: int,
) -> list[Clip]:
    clips = []
    for i, (start, end) in enumerate(select_clips(words, total_duration, count, clip_duration)):
        clip_words = words_in_range(words, start, end)
        clips.append(Clip(i, start, end, make_chunks(clip_words, words_per_chunk)))
    return clips


def rechunk(clip: Clip, words_per_chunk: int) -> None:
    words = [w for c in clip.chunks for w in c.words]
    clip.chunks = make_chunks(words, words_per_chunk)


def apply_text_edit(chunk: Chunk, new_text: str) -> Chunk | None:
    """Заменяет текст чанка, сохраняя тайминг.

    Если количество слов совпадает — тайминг каждого слова остаётся прежним,
    иначе длительность чанка делится между новыми словами поровну.
    Пустой текст удаляет чанк (возвращает None).
    """
    tokens = new_text.split()
    if not tokens:
        return None
    if len(tokens) == len(chunk.words):
        return Chunk([Word(w.start, w.end, t) for w, t in zip(chunk.words, tokens)])
    start, end = chunk.start, chunk.end
    step = (end - start) / len(tokens)
    return Chunk(
        [Word(start + i * step, start + (i + 1) * step, t) for i, t in enumerate(tokens)]
    )

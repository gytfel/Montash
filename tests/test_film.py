import numpy as np

from montash.analysis import STEP, VideoAnalysis
from montash.film import FilmIndex, plan_shots, replace_shot, subtitle_chunks
from montash.models import Word
from montash.script import split_sentences, time_by_voice, time_evenly

TEXT = "Герой просыпается в пустом городе. Он ищет выход!\nВ финале он находит ответ."


def _analysis(seconds: float, dark=(), action=()) -> VideoAnalysis:
    n = int(seconds / STEP)
    motion = np.full(n, 2.0, dtype=np.float32)
    loud = np.full(n, 100.0, dtype=np.float32)
    bright = np.full(n, 120.0, dtype=np.float32)
    cuts = np.zeros(n, dtype=bool)
    cuts[:: int(6 / STEP)] = True
    for a, b in dark:
        bright[int(a / STEP): int(b / STEP)] = 2
    for a, b in action:
        motion[int(a / STEP): int(b / STEP)] = 12
        loud[int(a / STEP): int(b / STEP)] = 3000
    return VideoAnalysis("f.mp4", seconds, motion, loud, bright, cuts)


def test_split_sentences():
    assert [len(s) for s in split_sentences(TEXT)] == [5, 3, 5]


def test_time_evenly_fills_duration():
    sentences = time_evenly(TEXT, 30)
    assert sentences[0].start == 0
    assert 28 < sentences[-1].end <= 30
    starts = [w.start for s in sentences for w in s.words]
    assert starts == sorted(starts)


def test_time_by_voice_uses_recognised_timing():
    asr = [Word(1.0, 1.3, "Герой"), Word(1.4, 2.0, "просыпается"), Word(5.0, 5.4, "выход")]
    sentences = time_by_voice(TEXT, asr, 10)
    words = [w for s in sentences for w in s.words]
    assert (words[0].start, words[1].start) == (1.0, 1.4)
    assert words[7].text == "выход!" and words[7].start == 5.0
    assert all(a.start <= b.start for a, b in zip(words, words[1:]))


def test_plan_text_mode_is_chronological_and_skips_black():
    index = FilmIndex([_analysis(120, dark=[(40, 80)]), _analysis(60)])
    sentences = time_evenly(TEXT, 20)
    shots = plan_shots(index, sentences, 20, "text", 3.0)
    assert abs(sum(s.duration for s in shots) - 20) < 1e-6
    positions = [index.to_global(s.file_index, s.src_start) for s in shots]
    assert positions == sorted(positions)
    for s in shots:
        if s.file_index == 0:
            assert not (40 <= s.src_start + s.duration and s.src_start <= 80 - 1)


def test_plan_highlights_prefers_action():
    index = FilmIndex([_analysis(300, action=[(200, 230)])])
    shots = plan_shots(index, time_evenly("Одна фраза. Вторая фраза.", 6), 6, "highlights", 3.0)
    assert any(195 <= s.src_start <= 230 for s in shots)


def test_replace_shot_changes_position():
    index = FilmIndex([_analysis(120)])
    shots = plan_shots(index, time_evenly(TEXT, 12), 12, "text", 3.0)
    before = shots[1].src_start
    assert replace_shot(index, shots, 1)
    assert shots[1].src_start != before


def test_subtitle_chunks_do_not_cross_sentences():
    chunks = subtitle_chunks(time_evenly(TEXT, 10), 4)
    assert [c.text for c in chunks][:2] == ["Герой просыпается в пустом", "городе."]

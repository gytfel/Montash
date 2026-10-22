from montash.clips import apply_text_edit, build_clips, make_chunks, select_clips
from montash.models import Chunk, SubtitleStyle, Word
from montash.subtitles import build_ass, hex_to_ass


def _words(start, texts, step=0.4):
    return [Word(start + i * step, start + i * step + 0.3, t) for i, t in enumerate(texts)]


def test_hex_to_ass():
    assert hex_to_ass("#FFE600") == "&H0000E6FF"


def test_select_clips_prefers_speech_and_no_overlap():
    words = _words(50, ["слово"] * 40)
    clips = select_clips(words, 120, 3, 20)
    assert len(clips) == 3
    assert any(s <= 50 <= e for s, e in clips)
    for (s1, e1), (s2, e2) in zip(clips, clips[1:]):
        assert e1 <= s2
    assert all(abs((e - s) - 20) < 1e-6 for s, e in clips)


def test_select_clips_without_words_is_even():
    assert select_clips([], 100, 2, 10) == [(0.0, 10.0), (90.0, 100.0)]


def test_clip_longer_than_video():
    assert select_clips([], 5, 3, 30) == [(0.0, 5.0)]


def test_chunks_break_on_sentence_and_size():
    chunks = make_chunks(_words(0, ["a", "b.", "c", "d", "e", "f"]), 3)
    assert [c.text for c in chunks] == ["a b.", "c d e", "f"]


def test_build_clips_relative_times():
    clips = build_clips(_words(30, ["x"] * 10), 100, 1, 10, 3)
    assert clips[0].chunks[0].start < 1.0


def test_apply_text_edit():
    ch = Chunk(_words(0, ["a", "b"]))
    same = apply_text_edit(ch, "c d")
    assert [w.start for w in same.words] == [w.start for w in ch.words]
    more = apply_text_edit(ch, "x y z")
    assert len(more.words) == 3 and more.start == ch.start and more.end == ch.end
    assert apply_text_edit(ch, "   ") is None


def test_ass_highlights_each_word():
    chunks = make_chunks(_words(0, ["раз", "два", "три"]), 3)
    ass = build_ass(chunks, SubtitleStyle(uppercase=True), 1080, 1920, 10)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 3
    assert "&H0000E6FF" in dialogues[1] and "ДВА" in dialogues[1]
    assert r"\pos(540,1344)" in dialogues[0]

import json
from types import SimpleNamespace

import pytest

from montash.film import Candidate, FilmIndex, plan_shots, replace_shot, select_keyframes
from montash.script import time_evenly
from test_film import _analysis

pytest.importorskip("anthropic")
import montash.ai_scenes as ai  # noqa: E402


class FakeClient:
    def __init__(self, reply):
        self.requests = []
        self.reply = reply
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            model=kwargs["model"],
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(self.reply(kwargs)))],
            usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0),
        )


def test_keyframes_skip_black_and_cover_film():
    index = FilmIndex([_analysis(120, dark=[(30, 60)])])
    frames = select_keyframes(index)
    assert frames and all(not 30 <= f.time < 60 for f in frames)
    assert all(f.shot_start <= f.time <= f.shot_end for f in frames)
    assert len(select_keyframes(index, max_frames=5)) == 5


def test_plan_uses_ai_candidates_and_replace_cycles_them():
    index = FilmIndex([_analysis(300)])
    sentences = time_evenly("Первая фраза. Вторая фраза.", 6)
    candidates = [
        [Candidate(0, 200.0, 198.0, 206.0, "погоня"), Candidate(0, 250.0, 248.0, 256.0, "взрыв")],
        [Candidate(0, 20.0, 18.0, 26.0, "разговор")],
    ]
    shots = plan_shots(index, sentences, 6, "ai", 3.0, candidates)
    assert [s.note for s in shots] == ["погоня", "разговор"]
    assert 198.0 <= shots[0].src_start <= 203.0
    assert replace_shot(index, shots, 0)
    assert shots[0].note == "взрыв" and 248.0 <= shots[0].src_start <= 253.0
    assert replace_shot(index, shots, 0)  # варианты ИИ кончились — обычный подбор
    assert shots[0].note == ""


def test_match_scenes_request_and_parsing(monkeypatch):
    index = FilmIndex([_analysis(120)])

    def fake_describe(client, model, files, frames, usage, progress):
        for i, f in enumerate(frames):
            f.note = f"кадр {i}"

    monkeypatch.setattr(ai, "describe_frames", fake_describe)
    client = FakeClient(lambda kw: {"phrases": [{"index": 0, "frame_ids": [2, 2, 999, 1]}, {"index": 7, "frame_ids": [0]}]})
    result, cost = ai.match_scenes(["f.mp4"], index, ["фраза"], [1], ai.AiOptions("k"), client=client)

    assert [c.note for c in result[0]] == ["кадр 2", "кадр 1"]
    assert cost == pytest.approx(4.0)  # 1M входных токенов Opus 5.5
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert '0 [1]: "фраза"' in req["messages"][0]["content"][0]["text"]

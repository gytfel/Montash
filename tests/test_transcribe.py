import sys
import types
import wave

import numpy as np
import pytest

import montash.transcribe as mt


def _write_wav(path, samples, rate=16000, channels=1):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(np.asarray(samples, dtype=np.int16).tobytes())


def test_load_wav_scales_to_float(tmp_path):
    path = tmp_path / "a.wav"
    _write_wav(path, [0, 16384, -32768, 32767])
    audio = mt.load_wav(str(path))
    assert audio.dtype == np.float32
    assert audio.tolist() == pytest.approx([0.0, 0.5, -1.0, 32767 / 32768])

    _write_wav(path, [0, 0], rate=44100)
    with pytest.raises(RuntimeError):
        mt.load_wav(str(path))


def test_transcribe_passes_array_not_file(tmp_path, monkeypatch):
    """faster-whisper не должен сам открывать файл: новые версии av с ним несовместимы."""
    received = {}

    class FakeModel:
        def __init__(self, name, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            received["audio"] = audio
            word = types.SimpleNamespace(word=" привет", start=0.1, end=0.4)
            return iter([types.SimpleNamespace(end=0.5, words=[word])]), types.SimpleNamespace(duration=0.5)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(mt, "_model_cache", {})
    monkeypatch.setattr(mt, "extract_audio", lambda src, dst: _write_wav(dst, np.zeros(8000)))
    monkeypatch.setattr(mt, "models_dir", lambda: tmp_path)

    words = mt.transcribe("video.mp4", "tiny")
    assert [w.text for w in words] == ["привет"]
    assert isinstance(received["audio"], np.ndarray) and len(received["audio"]) == 8000

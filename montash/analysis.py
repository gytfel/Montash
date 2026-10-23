"""Анализ фильма: склейки, движение, громкость и яркость с шагом STEP секунд.

Результат кэшируется в ~/.montash_cache, чтобы долгий анализ полнометражного
фильма выполнялся только один раз.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg_tools import _CREATE_NO_WINDOW, ffmpeg_path, probe_duration
from .paths import cache_dir

STEP = 0.25  # секунд на один отсчёт
_FPS = round(1 / STEP)
_W, _H = 64, 36
_AUDIO_RATE = 4000
_CACHE_VERSION = 1


@dataclass
class VideoAnalysis:
    path: str
    duration: float
    motion: np.ndarray  # средняя разница соседних кадров
    loudness: np.ndarray  # RMS звука
    brightness: np.ndarray  # средняя яркость кадра (0..255)
    cuts: np.ndarray  # bool: в этом отсчёте начинается новый план

    @property
    def samples(self) -> int:
        return len(self.motion)


def cache_stem(path: str) -> str:
    """Имя кэша для файла: меняется, если файл заменили."""
    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{_CACHE_VERSION}"
    return hashlib.sha1(key.encode()).hexdigest()


def _cache_path(path: str) -> Path:
    return cache_dir() / (cache_stem(path) + ".npz")


def analyze_video(
    path: str, progress: Callable[[float, str], None] | None = None
) -> VideoAnalysis:
    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    name = Path(path).name
    cache = _cache_path(path)
    if cache.is_file():
        try:
            d = np.load(cache)
            report(1.0, f"{name}: анализ взят из кэша")
            return VideoAnalysis(
                path, float(d["duration"]), d["motion"], d["loudness"], d["brightness"], d["cuts"]
            )
        except Exception:
            pass

    duration = probe_duration(path)
    frames = _read_frames(path, duration, lambda f: report(0.9 * f, f"{name}: анализ картинки {int(f * 100)}%"))
    report(0.9, f"{name}: анализ звука…")
    loud = _read_loudness(path)

    n = len(frames)
    if n == 0:
        raise RuntimeError(f"Не удалось прочитать видео: {name}")
    flat = frames.reshape(n, -1).astype(np.int16)
    brightness = flat.mean(axis=1).astype(np.float32)
    diff = np.zeros(n, dtype=np.float32)
    diff[1:] = np.abs(flat[1:] - flat[:-1]).mean(axis=1)

    # Склейка — резкий скачок разницы относительно локального фона.
    background = _moving_median(diff, 17)
    cuts = (diff > np.maximum(background * 3.0, 18.0)) & (diff > 10.0)
    cuts[0] = True
    motion = np.where(cuts, background, diff).astype(np.float32)

    loudness = np.zeros(n, dtype=np.float32)
    m = min(n, len(loud))
    loudness[:m] = loud[:m]

    result = VideoAnalysis(path, duration, motion, loudness, brightness, cuts)
    try:
        np.savez_compressed(
            cache, duration=duration, motion=motion, loudness=loudness,
            brightness=brightness, cuts=cuts,
        )
    except OSError:
        pass
    report(1.0, f"{name}: анализ готов")
    return result


def _read_frames(path: str, duration: float, progress: Callable[[float], None]) -> np.ndarray:
    # Сначала с аппаратным декодированием, при неудаче — программно.
    frames = _read_frames_with(path, duration, progress, ["-hwaccel", "auto", "-skip_frame", "noref"])
    if len(frames) == 0:
        frames = _read_frames_with(path, duration, progress, [])
    return frames


def _read_frames_with(
    path: str, duration: float, progress: Callable[[float], None], decode_args: list[str]
) -> np.ndarray:
    frame_size = _W * _H
    args = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "quiet", *decode_args,
        "-i", path, "-map", "0:v:0", "-an", "-sn",
        "-vf", f"fps={_FPS},scale={_W}:{_H}:flags=area,format=gray",
        "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW
    )
    expected = max(1, int(duration * _FPS))
    chunks = []
    total = 0
    batch = frame_size * 400
    assert proc.stdout is not None
    while True:
        data = proc.stdout.read(batch)
        if not data:
            break
        chunks.append(data)
        total += len(data)
        progress(min(1.0, total / frame_size / expected))
    proc.wait()
    buf = b"".join(chunks)
    n = len(buf) // frame_size
    return np.frombuffer(buf[: n * frame_size], dtype=np.uint8).reshape(n, _H, _W)


def _read_loudness(path: str) -> np.ndarray:
    window = int(_AUDIO_RATE * STEP)
    args = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", path,
        "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", str(_AUDIO_RATE), "-f", "s16le", "-",
    ]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW
    )
    out = []
    rest = b""
    assert proc.stdout is not None
    while True:
        data = proc.stdout.read(window * 2 * 1000)
        if not data:
            break
        data = rest + data
        usable = len(data) // (window * 2) * window * 2
        rest = data[usable:]
        samples = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32).reshape(-1, window)
        out.append(np.sqrt((samples ** 2).mean(axis=1)))
    proc.wait()
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


def _moving_median(x: np.ndarray, size: int) -> np.ndarray:
    if len(x) < size:
        return np.full_like(x, float(np.median(x)) if len(x) else 0.0)
    pad = size // 2
    padded = np.pad(x, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.median(windows, axis=1).astype(np.float32)

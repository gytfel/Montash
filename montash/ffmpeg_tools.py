"""Обёртки над ffmpeg / ffprobe."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def app_dir() -> Path:
    """Папка рядом с exe (или корень проекта при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find(name: str) -> str | None:
    exe = name + (".exe" if sys.platform == "win32" else "")
    for candidate in (app_dir() / exe, app_dir() / "ffmpeg" / exe, app_dir() / "ffmpeg" / "bin" / exe):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def ffmpeg_path() -> str:
    path = _find("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg не найден. Положите ffmpeg.exe рядом с программой или добавьте его в PATH."
        )
    return path


def run(args: list[str], cwd: str | os.PathLike | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg завершился с ошибкой:\n{tail}")
    return proc


def probe_duration(path: str) -> float:
    ffprobe = _find("ffprobe")
    if ffprobe:
        out = run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    # Без ffprobe: парсим вывод `ffmpeg -i`.
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    for line in proc.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            h, m, s = line.split(",")[0].split()[1].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError("Не удалось определить длительность видео")


def extract_audio(src: str, dst: str) -> None:
    run([ffmpeg_path(), "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", dst])

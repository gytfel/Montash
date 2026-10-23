"""Видео по ссылке (yt-dlp) и сбор видеофайлов из папок."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import app_dir, ffmpeg_path
from .paths import downloads_dir

MIN_FREE_MB = 300  # меньше — даже короткое видео может не поместиться
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".wmv", ".flv", ".mpg", ".mpeg"}

Progress = Callable[[float, str], None]


class DownloadError(RuntimeError):
    pass


def is_disk_full(message: str) -> bool:
    low = message.lower()
    return "errno 28" in low or "no space left" in low or "not enough space" in low or "winerror 112" in low


def free_mb(path: str | os.PathLike) -> float:
    """Свободное место (МБ) на диске, где лежит или будет лежать `path`."""
    p = Path(path).resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 2**20


def disk_full_message(path: str | os.PathLike | None = None) -> str:
    drive = Path(path).resolve().drive if path else ""
    where = f"на диске {drive}" if drive else "на диске"
    return (
        f"Закончилось место {where}.\n\n"
        "Освободите место (корзина, «Загрузки», старые видео) или выберите папку на другом "
        "диске: для скачанных видео — в окне «По ссылке», для готовых клипов — при сохранении."
    )


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", text.strip()))


def collect_videos(paths: list[str]) -> list[str]:
    """Видеофайлы из списка путей; папки раскрываются (без вложенных)."""
    from .montage import natural_sort_key

    result = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            inside = [str(f) for f in path.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
            result.extend(sorted(inside, key=natural_sort_key))
        elif path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            result.append(str(path))
    return list(dict.fromkeys(result))


def download(url: str, out_dir: str | os.PathLike | None = None, progress: Progress | None = None) -> str:
    """Скачивает одно видео (до 1080p) и возвращает путь к файлу.

    По умолчанию — в папку downloads рядом с программой.
    """
    out_dir = out_dir or downloads_dir()
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloadError("Не установлен yt-dlp: pip install yt-dlp") from e

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    def on_progress(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        done = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        what = "звук" if (d.get("info_dict") or {}).get("vcodec") == "none" else "видео"
        size = f"{done / 2**20:.0f} МБ" + (f" из {total / 2**20:.0f} МБ" if total else "")
        report(min(0.99, done / total) if total else 0.0, f"Скачивание ({what}): {size}")

    def on_postprocess(d: dict) -> None:
        if d.get("status") == "started" and d.get("postprocessor") == "Merger":
            report(0.99, "Склейка видео и звука…")

    if free_mb(out_dir) < MIN_FREE_MB:
        raise DownloadError(disk_full_message(out_dir))
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    opts = {
        # До 1080p, по возможности H.264 в mp4 — быстрее анализ и рендер.
        "format": "bv*+ba/b",
        "format_sort": ["res:1080", "vcodec:h264", "ext:mp4:m4a"],
        "merge_output_format": "mp4/mkv",
        "outtmpl": os.path.join(str(out_dir), "%(title).80B [%(id)s].%(ext)s"),
        "windowsfilenames": True,
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _SilentLogger(),
        "progress_hooks": [on_progress],
        "postprocessor_hooks": [on_postprocess],
        "ffmpeg_location": ffmpeg_path(),
        "concurrent_fragment_downloads": 4,
    }
    deno = _deno_path()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}

    report(0.0, "Получение информации о видео…")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        if is_disk_full(str(e)):
            raise DownloadError(disk_full_message(out_dir)) from e
        raise DownloadError(_explain(str(e))) from e

    if info and info.get("entries"):
        info = next((x for x in info["entries"] if x), None)
    downloads = (info or {}).get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if not path or not os.path.isfile(path):
        raise DownloadError("Не удалось скачать видео по этой ссылке")
    report(1.0, f"Скачано: {Path(path).name}")
    return path


def _deno_path() -> str | None:
    """JS-движок Deno нужен yt-dlp для YouTube: рядом с exe или из pip-пакета deno."""
    local = app_dir() / ("deno.exe" if os.name == "nt" else "deno")
    if local.is_file():
        return str(local)
    try:
        import deno

        return deno.find_deno_bin()
    except Exception:
        return shutil.which("deno")


def _explain(message: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", message).replace("ERROR: ", "").strip()
    low = text.lower()
    if "unsupported url" in low:
        return "Этот сайт не поддерживается. Попробуйте прямую ссылку на страницу с видео."
    if "drm" in low:
        return "Видео защищено от копирования (DRM), скачать его нельзя."
    if "private" in low or "sign in" in low or "login" in low or "log in" in low:
        return "Видео недоступно без входа в аккаунт (приватное или с ограничением)."
    if "404" in low or "not found" in low:
        return "По ссылке ничего не найдено (ошибка 404). Проверьте ссылку."
    if "not available" in low or "unavailable" in low:
        return "Видео недоступно (удалено или закрыто в вашем регионе)."
    if "unable to download" in low or "connection" in low or "timed out" in low:
        return f"Не удалось скачать: проблема с соединением.\n\n{text}"
    return f"Не удалось скачать видео:\n\n{text}\n\nЕсли это YouTube — попробуйте обновить yt-dlp (перезапустите run.bat)."


class _SilentLogger:
    # Без консоли (exe с --windowed) sys.stdout = None: yt-dlp не должен туда писать.
    def debug(self, msg: str) -> None:
        pass

    info = warning = debug

    def error(self, msg: str) -> None:
        pass

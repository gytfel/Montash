"""Где программа хранит свои данные.

Всё — модели распознавания речи, кэш анализа фильмов, настройки, временные
файлы и скачанные видео — лежит в папке data рядом с программой, а не в
профиле пользователя на системном диске (он часто маленький и забитый).
Если рядом с программой писать нельзя (например, она в Program Files),
используется папка .montash в профиле пользователя.
"""

from __future__ import annotations

import functools
import os
import shutil
import tempfile
import time
from pathlib import Path

from .ffmpeg_tools import app_dir

_LEGACY_CACHE = Path.home() / ".montash_cache"
LEGACY_SETTINGS = Path.home() / ".montash.json"


@functools.cache
def data_dir() -> Path:
    for base in (app_dir() / "data", Path.home() / ".montash"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return base
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "montash"


def _sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    return _sub("cache")


def models_dir() -> Path:
    return _sub("models")


def downloads_dir() -> Path:
    return app_dir() / "downloads" if data_dir().parent == app_dir() else data_dir() / "downloads"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def setup() -> None:
    """Вызывается при запуске: временные файлы — в data/tmp, старый кэш — сюда же."""
    tmp = _sub("tmp")
    _remove_stale(tmp)
    tempfile.tempdir = str(tmp)
    for var in ("TMP", "TEMP", "TMPDIR"):
        os.environ[var] = str(tmp)  # и для ffmpeg / faster-whisper
    # Прочие файлы huggingface_hub (кроме самих моделей) — тоже не в профиль.
    os.environ.setdefault("HF_HOME", str(models_dir()))
    _move_legacy_cache()


def _remove_stale(tmp: Path, max_age_hours: float = 24) -> None:
    # Остатки после аварийного завершения. Свежие не трогаем: может работать вторая копия.
    limit = time.time() - max_age_hours * 3600
    for entry in tmp.iterdir():
        try:
            if entry.stat().st_mtime < limit:
                shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        except OSError:
            pass


def _move_legacy_cache() -> None:
    """Кэш прежних версий (~/.montash_cache) переносим к программе, освобождая место."""
    if not _LEGACY_CACHE.is_dir() or _LEGACY_CACHE.resolve() == cache_dir().resolve():
        return
    for entry in _LEGACY_CACHE.iterdir():
        target = cache_dir() / entry.name
        try:
            if target.exists():
                entry.unlink()
            else:
                shutil.move(str(entry), str(target))
        except OSError:
            return
    try:
        _LEGACY_CACHE.rmdir()
    except OSError:
        pass

"""Сохранение настроек между запусками."""

from __future__ import annotations

import json
from dataclasses import fields

from .models import RenderSettings, SubtitleStyle
from .paths import LEGACY_SETTINGS, settings_path


def _load_into(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def load_settings() -> tuple[SubtitleStyle, RenderSettings, dict]:
    try:
        path = settings_path() if settings_path().is_file() else LEGACY_SETTINGS
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            _load_into(SubtitleStyle, data.get("style", {})),
            _load_into(RenderSettings, data.get("render", {})),
            data.get("app", {}),
        )
    except Exception:
        return SubtitleStyle(), RenderSettings(), {}


def save_settings(style: SubtitleStyle, render: RenderSettings, app: dict) -> None:
    from .models import to_dict

    try:
        settings_path().write_text(
            json.dumps(
                {"style": to_dict(style), "render": to_dict(render), "app": app},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass

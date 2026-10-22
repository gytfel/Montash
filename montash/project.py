"""Сохранение настроек между запусками."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .models import RenderSettings, SubtitleStyle

CONFIG_PATH = Path.home() / ".montash.json"


def _load_into(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def load_settings() -> tuple[SubtitleStyle, RenderSettings, dict]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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
        CONFIG_PATH.write_text(
            json.dumps(
                {"style": to_dict(style), "render": to_dict(render), "app": app},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass

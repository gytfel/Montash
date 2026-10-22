"""Консольный режим: montash video.mp4 -n 5 -d 30 -o out/"""

from __future__ import annotations

import argparse
from pathlib import Path

from .clips import build_clips
from .ffmpeg_tools import probe_duration
from .models import FORMATS, LOOKS, RenderSettings, SubtitleStyle
from .render import render_clip
from .transcribe import MODELS, transcribe


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="montash", description="Нарезка клипов с авто-субтитрами")
    p.add_argument("video")
    p.add_argument("-n", "--count", type=int, default=3, help="количество клипов")
    p.add_argument("-d", "--duration", type=float, default=30, help="длительность клипа, сек")
    p.add_argument("-o", "--out", default="clips", help="папка для результата")
    p.add_argument("--format", choices=list(FORMATS), default=RenderSettings.format_name)
    p.add_argument("--fill", choices=["crop", "blur", "fit"], default="crop")
    p.add_argument("--look", choices=list(LOOKS.values()), default="none")
    p.add_argument("--model", choices=MODELS, default="small")
    p.add_argument("--lang", default=None, help="ru, en, … (по умолчанию — авто)")
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--font", default=SubtitleStyle.font)
    p.add_argument("--size", type=int, default=SubtitleStyle.size)
    p.add_argument("--color", default=SubtitleStyle.primary_color)
    p.add_argument("--highlight", default=SubtitleStyle.highlight_color)
    p.add_argument("--outline-color", default=SubtitleStyle.outline_color)
    p.add_argument("--position", type=float, default=SubtitleStyle.position, help="%% от верха")
    p.add_argument("--words", type=int, default=SubtitleStyle.words_per_chunk)
    a = p.parse_args(argv)

    style = SubtitleStyle(
        font=a.font, size=a.size, primary_color=a.color, highlight_color=a.highlight,
        outline_color=a.outline_color, position=a.position, words_per_chunk=a.words,
    )
    settings = RenderSettings(format_name=a.format, fill=a.fill, look=a.look)

    duration = probe_duration(a.video)
    words = []
    if not a.no_subs:
        words = transcribe(
            a.video, a.model, a.lang, duration,
            progress=lambda f, m: print(f"[{f * 100:5.1f}%] {m}"),
        )
    clips = build_clips(words, duration, a.count, a.duration, style.words_per_chunk)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for clip in clips:
        path = out / f"{Path(a.video).stem}_clip{clip.index + 1}.mp4"
        print(f"Рендер {clip.label} -> {path}")
        render_clip(a.video, clip, style, settings, str(path), with_subtitles=not a.no_subs)
    print("Готово.")


if __name__ == "__main__":
    main()

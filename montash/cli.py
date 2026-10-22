"""Консольный режим.

    montash video.mp4 -n 5 -d 30 -o out/
    montash film --text script.txt part1.mkv part2.mkv -d 60 -o clip.mp4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .clips import build_clips
from .ffmpeg_tools import probe_duration
from .models import FORMATS, LOOKS, RenderSettings, SubtitleStyle
from .render import render_clip
from .transcribe import MODELS, transcribe


def _add_style_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--format", choices=list(FORMATS), default=RenderSettings.format_name)
    p.add_argument("--fill", choices=["crop", "blur", "fit"], default="crop")
    p.add_argument("--look", choices=list(LOOKS.values()), default="none")
    p.add_argument("--model", choices=MODELS, default="small")
    p.add_argument("--lang", default=None, help="ru, en, … (по умолчанию — авто)")
    p.add_argument("--font", default=SubtitleStyle.font)
    p.add_argument("--size", type=int, default=SubtitleStyle.size)
    p.add_argument("--color", default=SubtitleStyle.primary_color)
    p.add_argument("--highlight", default=SubtitleStyle.highlight_color)
    p.add_argument("--outline-color", default=SubtitleStyle.outline_color)
    p.add_argument("--position", type=float, default=SubtitleStyle.position, help="%% от верха")
    p.add_argument("--words", type=int, default=SubtitleStyle.words_per_chunk)


def _style(a: argparse.Namespace) -> tuple[SubtitleStyle, RenderSettings]:
    style = SubtitleStyle(
        font=a.font, size=a.size, primary_color=a.color, highlight_color=a.highlight,
        outline_color=a.outline_color, position=a.position, words_per_chunk=a.words,
    )
    return style, RenderSettings(format_name=a.format, fill=a.fill, look=a.look)


def _print_progress(frac: float, msg: str) -> None:
    print(f"[{frac * 100:5.1f}%] {msg}")


def film_main(argv: list[str]) -> None:
    from .film import MODES
    from .montage import build_montage
    from .render import render_montage

    p = argparse.ArgumentParser(prog="montash film", description="Один клип из фильма по тексту")
    p.add_argument("files", nargs="+", help="файлы фильма (части сортируются по номеру)")
    p.add_argument("--text", required=True, help="txt-файл с текстом про фильм")
    p.add_argument("-d", "--duration", type=float, default=60, help="длина клипа, сек")
    p.add_argument("--mode", choices=list(MODES.values()), default="text")
    p.add_argument("--shot", type=float, default=3.5, help="макс. длина плана, сек")
    p.add_argument("--voice", help="своя озвучка текста (необязательно)")
    p.add_argument("--music", help="фоновая музыка (необязательно)")
    p.add_argument("--music-volume", type=float, default=0.25)
    p.add_argument("-o", "--out", default="film_clip.mp4")
    _add_style_args(p)
    a = p.parse_args(argv)
    style, settings = _style(a)

    text = Path(a.text).read_text(encoding="utf-8")
    m = build_montage(
        a.files, text, a.duration, a.mode, a.shot, a.voice, a.model, a.lang, _print_progress
    )
    for shot in m.shots:
        print(f"  {shot.out_start:6.2f}s  {Path(m.files[shot.file_index]).name} @ {shot.src_start:.2f}s")
    print(f"Рендер -> {a.out}")
    render_montage(
        m.segments(), m.chunks(style.words_per_chunk), m.total, style, settings, a.out,
        a.voice, a.music, a.music_volume,
    )
    print("Готово.")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "film":
        film_main(argv[1:])
        return
    p = argparse.ArgumentParser(prog="montash", description="Нарезка клипов с авто-субтитрами")
    p.add_argument("video")
    p.add_argument("-n", "--count", type=int, default=3, help="количество клипов")
    p.add_argument("-d", "--duration", type=float, default=30, help="длительность клипа, сек")
    p.add_argument("-o", "--out", default="clips", help="папка для результата")
    p.add_argument("--no-subs", action="store_true")
    _add_style_args(p)
    a = p.parse_args(argv)
    style, settings = _style(a)

    duration = probe_duration(a.video)
    words = []
    if not a.no_subs:
        words = transcribe(
            a.video, a.model, a.lang, duration,
            progress=_print_progress,
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

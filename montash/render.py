"""Рендер клипов: обрезка, приведение к формату, цветокор, субтитры."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .ffmpeg_tools import app_dir, ffmpeg_path, run
from .models import Clip, RenderSettings, SubtitleStyle
from .subtitles import build_ass

_LOOK_FILTERS = {
    "none": "",
    "cinematic": "eq=contrast=1.12:saturation=0.85:gamma=0.95,colorbalance=rs=-0.04:bs=0.06:rh=0.06:bh=-0.04",
    "warm": "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.05:bm=-0.05,eq=saturation=1.1",
    "cold": "colorbalance=rs=-0.06:bs=0.08:rm=-0.04:bm=0.06",
    "bw": "hue=s=0,eq=contrast=1.15",
    "vibrant": "eq=saturation=1.35:contrast=1.06",
}


def video_filter(settings: RenderSettings, ass_name: str | None) -> str:
    w, h = settings.size
    if settings.fill == "blur":
        chain = (
            f"[0:v]split[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:3[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    elif settings.fill == "fit":
        chain = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
        )
    else:
        chain = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"

    extra = ["setsar=1"]
    look = _LOOK_FILTERS.get(settings.look, "")
    if look:
        extra.append(look)
    if settings.vignette:
        extra.append("vignette=PI/5")
    if ass_name:
        fonts = app_dir() / "fonts"
        extra.append(f"ass={ass_name}" + (":fontsdir=fonts" if fonts.is_dir() else ""))
    return chain + "," + ",".join(extra) + "[v]"


def _prepare_workdir(tmp: str) -> None:
    # Шрифты копируются во временную папку, чтобы фильтру ass не нужны были
    # абсолютные пути (в Windows их экранирование в ffmpeg очень капризное).
    fonts = app_dir() / "fonts"
    if fonts.is_dir():
        dst = Path(tmp) / "fonts"
        dst.mkdir(exist_ok=True)
        for f in fonts.iterdir():
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                (dst / f.name).write_bytes(f.read_bytes())


def render_clip(
    src: str,
    clip: Clip,
    style: SubtitleStyle,
    settings: RenderSettings,
    out_path: str,
    with_subtitles: bool = True,
) -> None:
    w, h = settings.size
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workdir(tmp)
        ass_name = None
        if with_subtitles and clip.chunks:
            ass_name = "subs.ass"
            Path(tmp, ass_name).write_text(
                build_ass(clip.chunks, style, w, h, clip.duration), encoding="utf-8"
            )
        args = [
            ffmpeg_path(), "-y",
            "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", os.path.abspath(src),
            "-filter_complex", video_filter(settings, ass_name),
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            os.path.abspath(out_path),
        ]
        run(args, cwd=tmp)


def render_preview(
    src: str,
    clip: Clip,
    style: SubtitleStyle,
    settings: RenderSettings,
    at: float,
    out_png: str,
) -> None:
    """Один кадр клипа в момент `at` (секунды от начала клипа) с субтитрами."""
    w, h = settings.size
    at = max(0.0, min(at, clip.duration - 0.05))
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workdir(tmp)
        ass_name = None
        if clip.chunks:
            ass_name = "subs.ass"
            Path(tmp, ass_name).write_text(
                build_ass(clip.chunks, style, w, h, clip.duration), encoding="utf-8"
            )
        # Быстрый seek до кадра, затем сдвиг временных меток, чтобы субтитры
        # отображались на правильном моменте клипа.
        filt = video_filter(settings, None).replace("[v]", "")
        filt += f",setpts=PTS+{at:.3f}/TB"
        if ass_name:
            fonts = ":fontsdir=fonts" if (Path(tmp) / "fonts").is_dir() else ""
            filt += f",ass={ass_name}{fonts}"
        filt += "[v]"
        args = [
            ffmpeg_path(), "-y",
            "-ss", f"{clip.start + at:.3f}", "-i", os.path.abspath(src),
            "-filter_complex", filt, "-map", "[v]",
            "-frames:v", "1", os.path.abspath(out_png),
        ]
        run(args, cwd=tmp)

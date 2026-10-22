"""Рендер клипов: обрезка, приведение к формату, цветокор, субтитры."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_tools import app_dir, ffmpeg_path, run
from .models import Chunk, Clip, RenderSettings, SubtitleStyle
from .subtitles import build_ass

_LOOK_FILTERS = {
    "none": "",
    "cinematic": "eq=contrast=1.12:saturation=0.85:gamma=0.95,colorbalance=rs=-0.04:bs=0.06:rh=0.06:bh=-0.04",
    "warm": "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rm=0.05:bm=-0.05,eq=saturation=1.1",
    "cold": "colorbalance=rs=-0.06:bs=0.08:rm=-0.04:bm=0.06",
    "bw": "hue=s=0,eq=contrast=1.15",
    "vibrant": "eq=saturation=1.35:contrast=1.06",
}


def _fill_chain(settings: RenderSettings, src: str, out: str) -> str:
    """Приводит видео [src] к размеру формата, результат — метка [out]."""
    w, h = settings.size
    if settings.fill == "blur":
        return (
            f"[{src}]split[{out}a][{out}b];"
            f"[{out}a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=25:3[{out}bg];"
            f"[{out}b]scale={w}:{h}:force_original_aspect_ratio=decrease[{out}fg];"
            f"[{out}bg][{out}fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]"
        )
    if settings.fill == "fit":
        return (
            f"[{src}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]"
        )
    return f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[{out}]"


def _finish_chain(settings: RenderSettings, ass_name: str | None, pts_offset: float = 0.0) -> str:
    """Цветокор, виньетка и субтитры (без меток)."""
    extra = []
    look = _LOOK_FILTERS.get(settings.look, "")
    if look:
        extra.append(look)
    if settings.vignette:
        extra.append("vignette=PI/5")
    if pts_offset:
        extra.append(f"setpts=PTS+{pts_offset:.3f}/TB")
    if ass_name:
        extra.append(f"ass={ass_name}" + (":fontsdir=fonts" if (app_dir() / "fonts").is_dir() else ""))
    return ",".join(extra) or "null"


def video_filter(settings: RenderSettings, ass_name: str | None) -> str:
    return _fill_chain(settings, "0:v", "f") + f";[f]{_finish_chain(settings, ass_name)}[v]"


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


def _write_ass(tmp: str, chunks: list[Chunk], style: SubtitleStyle, settings: RenderSettings, duration: float) -> str | None:
    if not chunks:
        return None
    w, h = settings.size
    Path(tmp, "subs.ass").write_text(build_ass(chunks, style, w, h, duration), encoding="utf-8")
    return "subs.ass"


def render_clip(
    src: str,
    clip: Clip,
    style: SubtitleStyle,
    settings: RenderSettings,
    out_path: str,
    with_subtitles: bool = True,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workdir(tmp)
        ass_name = _write_ass(tmp, clip.chunks, style, settings, clip.duration) if with_subtitles else None
        args = [
            ffmpeg_path(), "-y",
            "-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", os.path.abspath(src),
            "-filter_complex", video_filter(settings, ass_name),
            "-map", "[v]", "-map", "0:a?",
            *_encode_args(settings),
            os.path.abspath(out_path),
        ]
        run(args, cwd=tmp)


def render_frame(
    src: str,
    src_time: float,
    chunks: list[Chunk],
    out_time: float,
    duration: float,
    style: SubtitleStyle,
    settings: RenderSettings,
    out_png: str,
) -> None:
    """Один кадр `src` в момент `src_time` с субтитрами момента `out_time`."""
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workdir(tmp)
        ass_name = _write_ass(tmp, chunks, style, settings, duration)
        # Быстрый seek до кадра, затем сдвиг временных меток, чтобы субтитры
        # отображались на правильном моменте клипа.
        filt = _fill_chain(settings, "0:v", "f") + f";[f]{_finish_chain(settings, ass_name, out_time)}[v]"
        args = [
            ffmpeg_path(), "-y",
            "-ss", f"{max(0.0, src_time):.3f}", "-i", os.path.abspath(src),
            "-filter_complex", filt, "-map", "[v]",
            "-frames:v", "1", os.path.abspath(out_png),
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
    at = max(0.0, min(at, clip.duration - 0.05))
    render_frame(src, clip.start + at, clip.chunks, at, clip.duration, style, settings, out_png)


@dataclass
class Segment:
    path: str
    start: float
    duration: float


def render_montage(
    segments: list[Segment],
    chunks: list[Chunk],
    total: float,
    style: SubtitleStyle,
    settings: RenderSettings,
    out_path: str,
    voice: str | None = None,
    music: str | None = None,
    music_volume: float = 0.25,
) -> None:
    """Склеивает фрагменты в один клип. Звук фильма не используется."""
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workdir(tmp)
        ass_name = _write_ass(tmp, chunks, style, settings, total)
        args = [ffmpeg_path(), "-y"]
        parts = []
        for i, seg in enumerate(segments):
            args += ["-ss", f"{seg.start:.3f}", "-t", f"{seg.duration:.3f}", "-i", os.path.abspath(seg.path)]
            parts.append(
                _fill_chain(settings, f"{i}:v", f"s{i}")
                + f";[s{i}]fps=30,trim=duration={seg.duration:.3f},setpts=PTS-STARTPTS[c{i}]"
            )
        n = len(segments)
        concat = "".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]"
        graph = ";".join(parts + [concat, f"[cat]{_finish_chain(settings, ass_name)}[v]"])

        audio_inputs = []
        if voice:
            args += ["-i", os.path.abspath(voice)]
            audio_inputs.append(f"[{n + len(audio_inputs)}:a]volume=1.0[va]")
        if music:
            args += ["-stream_loop", "-1", "-i", os.path.abspath(music)]
            audio_inputs.append(f"[{n + len(audio_inputs)}:a]volume={music_volume:.2f}[ma]")
        if not audio_inputs:
            # Тихая дорожка: соцсети плохо принимают видео совсем без звука.
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            graph += f";[{n}:a]anull[a]"
        elif len(audio_inputs) == 1:
            graph += ";" + audio_inputs[0].rsplit("[", 1)[0] + "[a]"
        else:
            graph += ";" + ";".join(audio_inputs) + ";[va][ma]amix=inputs=2:duration=longest:normalize=0[a]"

        args += [
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            *_encode_args(settings),
            os.path.abspath(out_path),
        ]
        run(args, cwd=tmp)


def _encode_args(settings: RenderSettings) -> list[str]:
    return [
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
    ]

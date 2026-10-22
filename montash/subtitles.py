"""Генерация ASS-субтитров с подсветкой слова, которое звучит сейчас."""

from __future__ import annotations

from .models import Chunk, SubtitleStyle

# Сколько держать последний чанк на экране после последнего слова.
_TAIL_HOLD = 0.4


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (формат цветов ASS)."""
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"Некорректный цвет: {color}")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(
    chunks: list[Chunk], style: SubtitleStyle, width: int, height: int, duration: float
) -> str:
    primary = hex_to_ass(style.primary_color)
    highlight = hex_to_ass(style.highlight_color)
    outline = hex_to_ass(style.outline_color)
    shadow = hex_to_ass("#000000", 0x80)
    bold = -1 if style.bold else 0
    # Размер шрифта задаётся для кадра 1080 по короткой стороне.
    scale = min(width, height) / 1080
    size = max(8, round(style.size * scale))
    outline_w = round(style.outline * scale, 1)
    shadow_w = round(style.shadow * scale, 1)
    x = width // 2
    y = round(height * style.position / 100)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font},{size},{primary},{highlight},{outline},{shadow},{bold},0,0,0,100,100,0,0,1,{outline_w},{shadow_w},5,{round(width * 0.06)},{round(width * 0.06)},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for ci, chunk in enumerate(chunks):
        next_start = chunks[ci + 1].start if ci + 1 < len(chunks) else None
        chunk_end = chunk.end + _TAIL_HOLD
        if next_start is not None:
            chunk_end = min(chunk_end, next_start)
        chunk_end = min(chunk_end, duration)
        if chunk_end <= chunk.start:
            continue

        texts = [_escape(w.text.upper() if style.uppercase else w.text) for w in chunk.words]
        for wi, word in enumerate(chunk.words):
            start = chunk.start if wi == 0 else word.start
            end = chunk.words[wi + 1].start if wi + 1 < len(chunk.words) else chunk_end
            if end <= start:
                continue
            parts = []
            for j, t in enumerate(texts):
                if j == wi:
                    pop = (
                        r"\fscx112\fscy112\t(0,120,\fscx100\fscy100)" if style.pop else ""
                    )
                    parts.append(f"{{\\c{highlight}{pop}}}{t}{{\\c{primary}\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            text = f"{{\\an5\\pos({x},{y})}}" + " ".join(parts)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"

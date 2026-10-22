from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Chunk:
    """Группа слов, которая показывается на экране одновременно."""

    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Clip:
    index: int
    start: float
    end: float
    # Время слов в чанках — относительно начала клипа.
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"Клип {self.index + 1}: {fmt_time(self.start)} – {fmt_time(self.end)}"


FORMATS = {
    "Shorts / TikTok / Reels (9:16)": (1080, 1920),
    "Квадрат (1:1)": (1080, 1080),
    "YouTube (16:9)": (1920, 1080),
}

FILL_MODES = {
    "Обрезать по центру": "crop",
    "Размытый фон": "blur",
    "Чёрные полосы": "fit",
}

LOOKS = {
    "Без цветокора": "none",
    "Кинематограф": "cinematic",
    "Тёплый": "warm",
    "Холодный": "cold",
    "Ч/Б": "bw",
    "Сочный": "vibrant",
}


@dataclass
class SubtitleStyle:
    font: str = "Montserrat Black"
    size: int = 90
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    outline_color: str = "#000000"
    outline: float = 6.0
    shadow: float = 2.0
    position: float = 70.0  # % от верха кадра
    uppercase: bool = True
    bold: bool = True
    words_per_chunk: int = 3
    pop: bool = True  # «прыжок» активного слова


@dataclass
class RenderSettings:
    format_name: str = "Shorts / TikTok / Reels (9:16)"
    fill: str = "crop"
    look: str = "none"
    vignette: bool = False
    crf: int = 20
    preset: str = "veryfast"

    @property
    def size(self) -> tuple[int, int]:
        return FORMATS[self.format_name]


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def to_dict(obj) -> dict:
    return asdict(obj)

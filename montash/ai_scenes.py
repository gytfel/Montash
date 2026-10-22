"""Необязательный ИИ-подбор сцен через Claude API.

1. Из каждого плана фильма берётся ключевой кадр (512 px по ширине). Кадры
   пачками отправляются Claude, он коротко описывает каждый. Описания
   кэшируются на диске, поэтому за один фильм платится один раз.
2. Claude получает фразы текста и список описаний и для каждой фразы
   возвращает подходящие кадры, лучшие первыми.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from .analysis import CACHE_DIR, cache_stem
from .ffmpeg_tools import _CREATE_NO_WINDOW, ffmpeg_path
from .film import Candidate, FilmIndex, select_keyframes
from .models import fmt_time

AI_MODELS = {
    "Claude Opus 5.5 (точнее)": "claude-opus-5-5",
    "Claude Sonnet 5.5 (дешевле)": "claude-sonnet-5-5",
}
# $ за 1 млн токенов (вход, выход) — только для примерной оценки расходов.
_PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}
_BATCH = 40
_WORKERS = 3
# Если модель отклонит запрос, API сам повторит его на рекомендованной модели.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

Progress = Callable[[float, str], None]

_CAPTION_SYSTEM = (
    "Ты помогаешь монтажёру роликов-пересказов фильмов. Тебе показывают кадры одного фильма. "
    "Опиши каждый кадр одной фразой по-русски, до 25 слов: кто в кадре (пол, возраст, внешность, "
    "одежда — чтобы одного персонажа можно было узнать в разных кадрах), что происходит, где, "
    "важные предметы, характер сцены (погоня, драка, разговор, романтика, саспенс). "
    "Имён не придумывай. Если кадр — титры, текст или чёрный экран, так и напиши."
)

_MATCH_SYSTEM = (
    "Ты — монтажёр коротких вертикальных роликов-пересказов фильмов. По описаниям кадров "
    "ты подбираешь под каждую фразу закадрового текста кадры, которые её иллюстрируют."
)

_CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "description": {"type": "string"}},
                "required": ["id", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["frames"],
    "additionalProperties": False,
}

_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "phrases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "frame_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["index", "frame_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["phrases"],
    "additionalProperties": False,
}


@dataclass
class AiOptions:
    api_key: str | None = None  # None — ключ из ANTHROPIC_API_KEY
    model: str = "claude-opus-5-5"


class AiError(RuntimeError):
    pass


class _Usage:
    def __init__(self):
        self.cost = 0.0
        self._lock = threading.Lock()

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        price_in, price_out = _PRICES.get(model, _PRICES["claude-opus-5-5"])
        with self._lock:
            self.cost += (input_tokens * price_in + output_tokens * price_out) / 1e6


def match_scenes(
    files: list[str],
    index: FilmIndex,
    phrases: list[str],
    shots_needed: list[int],
    options: AiOptions,
    progress: Progress | None = None,
    client=None,
) -> tuple[list[list[Candidate]], float]:
    """Варианты кадров для каждой фразы (лучшие первыми) и примерная стоимость в $."""

    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    client = client or _make_client(options)
    usage = _Usage()
    frames = select_keyframes(index)
    if not frames:
        raise AiError("В фильме не нашлось подходящих кадров для анализа")

    describe_frames(client, options.model, files, frames, usage, lambda f, m: report(0.85 * f, m))
    described = [i for i, f in enumerate(frames) if f.note]
    if not described:
        raise AiError("ИИ не смог описать кадры фильма — попробуйте другую модель")

    report(0.87, "ИИ подбирает сцены под текст…")
    lines = []
    for i in described:
        f = frames[i]
        part = f"ч.{f.file_index + 1} " if len(files) > 1 else ""
        lines.append(f"{i} | {part}{fmt_time(f.time)} | {f.note}")
    phrase_lines = [f'{i} [{n}]: "{p}"' for i, (p, n) in enumerate(zip(phrases, shots_needed))]
    prompt = (
        "Кадры фильма в порядке сюжета (id | время | описание):\n"
        + "\n".join(lines)
        + "\n\nФразы закадрового текста (номер [сколько планов нужно]: текст):\n"
        + "\n".join(phrase_lines)
        + "\n\nДля каждой фразы выбери кадры, которые лучше всего показывают то, о чём в ней "
        "говорится: те же персонажи, действия, места и предметы. Верни для каждой фразы "
        "на 3–5 id больше, чем нужно планов, лучшие первыми. Не используй один кадр для разных "
        "фраз. Текст обычно идёт по порядку сюжета: если прямого соответствия нет, бери кадры "
        "с подходящим настроением из той части фильма, о которой идёт речь. Не бери титры и "
        "чёрный экран. Используй только id из списка."
    )
    data = _call(client, options.model, _MATCH_SYSTEM, [{"type": "text", "text": prompt}], _MATCH_SCHEMA, "medium", usage)
    if data is None:
        raise AiError("ИИ не смог подобрать сцены — попробуйте другую модель или режим")

    valid = set(described)
    result: list[list[Candidate]] = [[] for _ in phrases]
    for item in data.get("phrases", []):
        i = item.get("index")
        if not isinstance(i, int) or not 0 <= i < len(phrases):
            continue
        seen = set()
        for fid in item.get("frame_ids", []):
            if fid in valid and fid not in seen:
                seen.add(fid)
                result[i].append(frames[fid])
    report(1.0, f"ИИ подобрал сцены (≈ ${usage.cost:.2f})")
    return result, usage.cost


def describe_frames(
    client,
    model: str,
    files: list[str],
    frames: list[Candidate],
    usage: _Usage,
    progress: Progress,
) -> None:
    """Заполняет frame.note, используя кэш описаний на диске."""
    caches = {fi: _load_captions(path) for fi, path in enumerate(files)}
    todo = []
    for i, f in enumerate(frames):
        note = caches[f.file_index].get(_key(f.time))
        if note:
            f.note = note
        else:
            todo.append(i)
    if not todo:
        progress(1.0, "Описания кадров взяты из кэша")
        return

    with tempfile.TemporaryDirectory() as tmp:
        done = [0]
        lock = threading.Lock()

        def grab(i: int) -> None:
            f = frames[i]
            _thumbnail(files[f.file_index], f.time, os.path.join(tmp, f"{i}.jpg"))
            with lock:
                done[0] += 1
                progress(0.2 * done[0] / len(todo), f"Кадры для ИИ: {done[0]} / {len(todo)}")

        with ThreadPoolExecutor(4) as pool:
            list(pool.map(grab, todo))

        batches = [todo[k:k + _BATCH] for k in range(0, len(todo), _BATCH)]
        described = [0]

        def caption(batch: list[int]) -> None:
            notes = _caption_batch(client, model, tmp, frames, batch, usage)
            with lock:
                for i, note in notes.items():
                    frames[i].note = note
                    caches[frames[i].file_index][_key(frames[i].time)] = note
                described[0] += len(batch)
                progress(0.2 + 0.8 * described[0] / len(todo), f"ИИ описывает кадры: {described[0]} / {len(todo)}")

        try:
            with ThreadPoolExecutor(_WORKERS) as pool:
                list(pool.map(caption, batches))
        finally:
            # Сохраняем и при ошибке: уже оплаченные описания не пропадут.
            for fi, path in enumerate(files):
                _save_captions(path, caches[fi])


def _caption_batch(client, model: str, tmp: str, frames: list[Candidate], batch: list[int], usage: _Usage) -> dict[int, str]:
    content = []
    for i in batch:
        jpg = os.path.join(tmp, f"{i}.jpg")
        if not os.path.isfile(jpg):
            continue
        with open(jpg, "rb") as fh:
            data = base64.standard_b64encode(fh.read()).decode("ascii")
        content.append({"type": "text", "text": f"Кадр {i} ({fmt_time(frames[i].time)}):"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    if not content:
        return {}
    content.append({"type": "text", "text": "Опиши каждый кадр. В поле id укажи номер кадра."})
    data = _call(client, model, _CAPTION_SYSTEM, content, _CAPTION_SCHEMA, "low", usage)
    if data is None:
        # Отказ или обрезанный ответ: пробуем половинками, чтобы потерять меньше кадров.
        if len(batch) > 4:
            half = len(batch) // 2
            return {
                **_caption_batch(client, model, tmp, frames, batch[:half], usage),
                **_caption_batch(client, model, tmp, frames, batch[half:], usage),
            }
        return {}
    wanted = set(batch)
    return {
        item["id"]: item["description"].strip()
        for item in data.get("frames", [])
        if item.get("id") in wanted and str(item.get("description", "")).strip()
    }


def _make_client(options: AiOptions):
    try:
        import anthropic
    except ImportError as e:
        raise AiError("Не установлен пакет anthropic: pip install anthropic") from e
    kwargs = {"max_retries": 4}
    if options.api_key:
        kwargs["api_key"] = options.api_key
    return anthropic.Anthropic(**kwargs)


def _call(client, model: str, system: str, content: list, schema: dict, effort: str, usage: _Usage) -> dict | None:
    """Один запрос со структурированным JSON-ответом. None — отказ или обрезанный ответ."""
    import anthropic

    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[_FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AiError("Ключ API не подошёл. Проверьте его в разделе «ИИ-подбор сцен».") from e
    except anthropic.PermissionDeniedError as e:
        raise AiError(f"У ключа API нет доступа к модели {model}: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise AiError("Превышен лимит запросов к API. Подождите минуту и попробуйте снова.") from e
    except anthropic.BadRequestError as e:
        raise AiError(f"API отклонил запрос: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AiError(f"Ошибка API ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AiError("Нет соединения с api.anthropic.com. Проверьте интернет.") from e
    except TypeError as e:
        # SDK не нашёл ни ключа, ни профиля авторизации.
        if "authentication" in str(e):
            raise AiError("Не указан ключ API Anthropic") from e
        raise

    if response.usage:
        usage.add(response.model, response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def _thumbnail(path: str, seconds: float, out: str) -> None:
    subprocess.run(
        [
            ffmpeg_path(), "-v", "error", "-y", "-ss", f"{seconds:.2f}", "-i", path,
            "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "5", out,
        ],
        capture_output=True,
        creationflags=_CREATE_NO_WINDOW,
    )


def _key(seconds: float) -> str:
    return f"{seconds:.2f}"


def _captions_path(path: str):
    return CACHE_DIR / f"{cache_stem(path)}_captions.json"


def _load_captions(path: str) -> dict[str, str]:
    try:
        return json.loads(_captions_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_captions(path: str, captions: dict[str, str]) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _captions_path(path).write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

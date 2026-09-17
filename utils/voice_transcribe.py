"""Транскрибация голосовых и кружков для !цитата.

Голос на 10 секунд — это секунды CPU на base-модели, поэтому живём
без внешних API: faster-whisper крутится прямо в контейнере бота,
модель (base, ~150МБ) качается один раз в HF_HUB_CACHE (см. Dockerfile
и volume whisper-cache в docker-compose.yml).

Правила экономии на нашем сервере (4 CPU, RAM впритык):
  • модель грузится лениво при первой транскрибации и остаётся в памяти;
  • одновременно распознаём только один файл (asyncio.Lock);
  • тяжёлая работа — в executor, чтобы не стопать поллинг;
  • длинные файлы отклоняем до скачивания (см. MAX_SEC).
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
MAX_SEC = int(os.getenv("VOICE_QUOTE_MAX_SEC", "180"))
# Временная заглушка: сервер занят, расшифровку выключаем без удаления кода.
# Вернуть: VOICE_QUOTE_ENABLED=1 в окружении + рестарт.
ENABLED = os.getenv("VOICE_QUOTE_ENABLED", "0") == "1"
# Подсказка декодеру: стиль и словарь чата. Коротко (до ~200 символов)
# и только частое — длинный/специфичный промпт модель начинает
# галлюцинировать (вставлять слова подсказки, которых не говорили).
# Меняется без кода: WHISPER_PROMPT в .env / окружении.
PROMPT = os.getenv(
    "WHISPER_PROMPT",
    "Разговорная русская речь, студенческий чат. Сокращения: ИК, ИТиАБД, "
    "ВШУ, ФинФак, ЮрФак, МЭО, НАБ, СНиМК, ФЭБ, ПК, КВС, Уч.-соц.ком. "
    "Мат и сленг дословно, без цензуры. Имена и фамилии.",
)

_lock = asyncio.Lock()
_model = None


def _load_model():
    """Синглтон модели. Держим в памяти: перезагрузка на каждый чих
    жрала бы секунды и дёргала диск."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info("Загружаю whisper-модель %s (cpu, int8)", MODEL_NAME)
        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    return _model


def _transcribe_sync(path: str) -> str:
    model = _load_model()
    segments, _info = model.transcribe(
        path, language="ru", beam_size=5, vad_filter=True,
        initial_prompt=PROMPT or None,
    )
    return "".join(s.text for s in segments).strip()


def _strip_trailing_period(text: str) -> str:
    """Снять точку в конце последнего предложения: в разговорной речи
    её нет. «?» и «!» не трогаем — это интонация. Хитрость: если последнее
    слово само с точками внутри («уч.-соц.ком.», «т.д.») — это сокращение,
    точку оставляем, иначе «ком.» превратился бы в «ком»."""
    s = text.rstrip()
    if not s or s[-1] not in (".", "…"):
        return s
    head, _, last = s.rpartition(" ")
    core = last.rstrip(".…")
    if "." in core or "…" in core:
        return s
    return (head + " " + core if head else core).rstrip()


async def transcribe(path: str) -> str:
    """Текст из аудиофайла. По одному за раз — иначе RAM скакнёт."""
    loop = asyncio.get_event_loop()
    async with _lock:
        text = await loop.run_in_executor(None, _transcribe_sync, path)
    return _strip_trailing_period(text)

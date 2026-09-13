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
    segments, _info = model.transcribe(path, beam_size=5, vad_filter=True)
    return "".join(s.text for s in segments).strip()


async def transcribe(path: str) -> str:
    """Текст из аудиофайла. По одному за раз — иначе RAM скакнёт."""
    loop = asyncio.get_event_loop()
    async with _lock:
        return await loop.run_in_executor(None, _transcribe_sync, path)

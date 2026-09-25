"""Подсчёт сообщений в чате.

Middleware висит на всех входящих сообщениях (outer — то есть срабатывает
раньше фильтров, поэтому команды тоже считаются) и просто увеличивает
счётчик, после чего передаёт сообщение дальше по цепочке. На поведение
остальных команд это не влияет.

Пишем не на каждое сообщение, а пачками: во флудилке сообщения идут
очередями, и отдельный коммит на каждое — лишняя нагрузка на sqlite.
Копим в памяти и сбрасываем раз в FLUSH_SECONDS (или когда накопилось
FLUSH_ITEMS записей). Команды статистики перед чтением зовут flush_stats(),
чтобы показывать свежие цифры.
"""
import asyncio
import logging
from datetime import date, datetime
from time import monotonic
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ContentType
from aiogram.types import Message

from database.engine import async_session_maker
from database.stats_dao import StatsDAO
from utils.helpers import MSK, moscow_today

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 5.0
FLUSH_ITEMS = 100

# Считаем «сообщением» только то, что человек реально написал или прислал.
# Список белый, а не чёрный: так в статистику точно не попадут служебные
# события (вход/выход из чата, закреп, смена аватарки и т.п.).
COUNTED = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.VIDEO_NOTE,
    ContentType.ANIMATION,
    ContentType.STICKER,
    ContentType.VOICE,
    ContentType.AUDIO,
    ContentType.DOCUMENT,
    ContentType.POLL,
    ContentType.LOCATION,
    ContentType.VENUE,
    ContentType.CONTACT,
    ContentType.DICE,
    ContentType.STORY,
    ContentType.GAME,
    ContentType.PAID_MEDIA,
}

GROUP_CHATS = {"group", "supergroup"}

_counts: dict[tuple[int, int, date], int] = {}
_users: dict[int, tuple[str | None, str, datetime]] = {}
_lock = asyncio.Lock()
_last_flush = monotonic()


def remember(message: Message) -> None:
    """Увеличить счётчик в памяти. Ничего не пишет в базу."""
    user = message.from_user
    if user is None or user.is_bot:
        return
    if message.chat.type not in GROUP_CHATS:
        return
    if message.content_type not in COUNTED:
        return

    key = (message.chat.id, user.id, moscow_today())
    _counts[key] = _counts.get(key, 0) + 1
    # naive-время по Москве — как и везде в проекте, чтобы не тащить tz в базу.
    _users[user.id] = (user.username, user.full_name, datetime.now(MSK).replace(tzinfo=None))


async def flush_stats() -> None:
    """Сбросить накопленное в базу. Безопасно звать когда угодно."""
    global _last_flush
    async with _lock:
        _last_flush = monotonic()
        if not _counts and not _users:
            return
        counts, users = dict(_counts), dict(_users)
        _counts.clear()
        _users.clear()
        try:
            async with async_session_maker() as session:
                await StatsDAO(session).bump(counts, users)
        except Exception:
            # Если база не ответила — возвращаем счётчики в буфер и пробуем
            # в следующий раз. Терять сообщения из-за одной осечки не хочется.
            for key, value in counts.items():
                _counts[key] = _counts.get(key, 0) + value
            for user_id, value in users.items():
                _users.setdefault(user_id, value)
            logger.exception("Не смог записать статистику сообщений")


async def _flush_if_due() -> None:
    if len(_counts) >= FLUSH_ITEMS or monotonic() - _last_flush >= FLUSH_SECONDS:
        await flush_stats()


class MessageCounterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            remember(event)
            await _flush_if_due()
        except Exception:
            # Что бы ни случилось со статистикой — сообщение должно дойти
            # до обычных хендлеров.
            logger.exception("Ошибка счётчика сообщений")
        return await handler(event, data)

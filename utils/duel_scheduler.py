"""Фоновая задача дуэлей: воскрешения и протухшие флаги.

Раз в полминуты забираем тех, чей час смерти вышел, и пишем во флуд.
Флаги старше 3 дней сносим молча — человек просто снова уязвим.
Одна осечка задачу не убивает: цикл вечный, как дни рождения.
"""
import asyncio
import logging

from aiogram import Bot

from database.duel_dao import DuelDAO
from database.engine import async_session_maker
from utils.helpers import msk_now

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 30


def _who(user_id: int, username: str | None, display: str | None) -> str:
    import html as _html

    tag = (username or "").strip().lstrip("@")
    if tag:
        return f"@{tag}"
    name = _html.escape((display or "").strip() or "боец")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


async def duel_tick(bot: Bot) -> tuple[int, int]:
    """Один проход: воскресить due, снести протухшие флаги.
    Возвращает (воскрешено, снято флагов) — удобно для тестов."""
    now = msk_now()
    revived, expired = 0, 0
    async with async_session_maker() as session:
        dao = DuelDAO(session)
        for soul in await dao.due_for_resurrection(now):
            who = _who(soul.user_id, soul.username, soul.display)
            try:
                await bot.send_message(
                    soul.chat_id,
                    f"Возрадуемся! {who} воскрес!\nУдачи в следующий раз",
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Не смог воскресить %s в чате %s", soul.user_id, soul.chat_id)
                continue
            await dao.revive(soul)
            revived += 1
        expired = await dao.expire_flags(now)
    return revived, expired


async def duel_worker(bot: Bot) -> None:
    while True:
        try:
            await duel_tick(bot)
        except Exception:
            logger.exception("Ошибка в задаче дуэлей")
        await asyncio.sleep(INTERVAL_SECONDS)

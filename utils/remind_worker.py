"""Воркер напоминалок: раз в 30 секунд шлёт просроченные в личку.

Живёт рядом с duel_worker в main.py. Перезапуск не страшен: состояние
в базе, висяки подхватываются. Заблокировавших бота помечаем выполненными,
чтобы не крутить вечный цикл (и пишем в лог).
"""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from database.engine import async_session_maker
from database.remind_dao import RemindDAO
from utils.helpers import msk_now

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


def _late_text(fire_at, now) -> str:
    late = int((now - fire_at).total_seconds() // 60)
    if late < 5:
        return ""
    if late < 60:
        return f" (просрочено на {late} мин)"
    hours, rest = divmod(late, 60)
    tail = f" {rest} мин" if rest else ""
    return f" (просрочено на {hours} ч{tail})"


async def _tick(bot: Bot) -> None:
    now = msk_now()
    async with async_session_maker() as session:
        due = await RemindDAO(session).due(now)
    for row in due:
        text = f"⏰ Напоминалка: {row.text}{_late_text(row.fire_at, now)}"
        try:
            await bot.send_message(row.user_id, text)
        except TelegramForbiddenError:
            logger.info("Напоминалка %s: юзер заблокировал бота", row.id)
        except Exception:
            logger.exception("Напоминалка %s не ушла, попробую позже", row.id)
            continue
        async with async_session_maker() as session:
            await RemindDAO(session).mark_done(row.id)


async def remind_worker(bot: Bot) -> None:
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            await _tick(bot)
        except Exception:
            logger.exception("Воркер напоминалок упал на тике")

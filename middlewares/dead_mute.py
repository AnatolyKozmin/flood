"""Мёртвые молчат: трём все сообщения погибших в дуэли/рулетке.

Висит outer-мидлварью раньше счётчика (см. main.py): сообщение мёртвого
не должно ни попасть в !топ, ни сработать командой — оно просто исчезает.
Поэтому цепочку дальше не зовём (return без handler).

Важно: удалять чужие сообщения бот может только админом чата с правом
«удаление сообщений». Без прав удаление молча не срабатывает, а человек
продолжает писать — в логах это не отследить, так что проверь права.
"""
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from database.duel_dao import DuelDAO
from database.engine import async_session_maker


class DeadMuteMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if (
            event.chat.type in ("group", "supergroup")
            and event.from_user is not None
            and not event.from_user.is_bot
        ):
            async with async_session_maker() as session:
                dead = await DuelDAO(session).is_dead(event.chat.id, event.from_user.id)
            if dead is not None:
                try:
                    await event.delete()
                except TelegramAPIError:
                    pass  # нет прав админа — сообщение останется, ничего не поделать
                return None
        return await handler(event, data)

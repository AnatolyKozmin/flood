"""Мёртвые и замученные молчат: трём их сообщения.

Висит outer-мидлварью раньше счётчика (см. main.py): сообщение мёртвого
не должно ни попасть в !топ, ни сработать командой — оно просто исчезает.
Поэтому цепочку дальше не зовём (return без handler).

Два источника тишины: смерть в дуэли (час, с воскрешением) и теневой мут
из !модерации (бессрочно, только там и видно). Проверка дешёвая —
два точечных SELECT по первичным ключам на сообщение.

Важно: удалять чужие сообщения бот может только админом чата с правом
«удаление сообщений». Без прав удаление молча не срабатывает, а человек
продолжает писать — в логах это не отследить, так что проверь права.
"""
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from database.duel_dao import DuelDAO
from database.engine import async_session_maker
from database.mod_dao import MuteDAO


class DeadMuteMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        if (
            event.chat.type in ("group", "supergroup")
            and event.from_user is not None
            and not event.from_user.is_bot
        ):
            async with async_session_maker() as session:
                silenced = (
                    await DuelDAO(session).is_dead(event.chat.id, event.from_user.id)
                    is not None
                    or await MuteDAO(session).is_muted(event.from_user.id) is not None
                )
            if silenced:
                try:
                    await event.delete()
                except TelegramAPIError:
                    pass  # нет прав админа — сообщение останется, ничего не поделать
                return None
        return await handler(event, data)

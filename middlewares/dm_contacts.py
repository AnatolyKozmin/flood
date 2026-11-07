"""Запоминаем всех, кто пишет боту в личку (таблица bot_contacts).

outer — срабатывает раньше фильтров и команд, на поведение не влияет. Пишем
не чаще раза в TTL на человека: в личке переписка идёт короткими очередями.
"""
import logging
from time import monotonic
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.contact_models import BotContact
from database.engine import async_session_maker
from utils.helpers import msk_now

logger = logging.getLogger(__name__)

TTL = 600.0
_seen: dict[int, float] = {}


class DMContactsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        if event.chat.type == "private" and user is not None and not user.is_bot:
            now = monotonic()
            if now - _seen.get(user.id, -TTL) >= TTL:
                _seen[user.id] = now
                try:
                    await _remember(user.id, user.username, user.full_name)
                except Exception as exc:  # статистика не должна ломать ответ
                    logger.warning("bot_contacts: %s", exc)
        return await handler(event, data)


async def _remember(tg_id: int, username: str | None, full_name: str | None) -> None:
    values = {"tg_id": tg_id, "username": username, "full_name": full_name, "last_seen": msk_now()}
    query = sqlite_insert(BotContact).values(**values).on_conflict_do_update(
        index_elements=[BotContact.tg_id],
        set_={k: v for k, v in values.items() if k != "tg_id"},
    )
    async with async_session_maker() as session:
        await session.execute(query)
        await session.commit()

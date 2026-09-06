"""Включение отложенного доступа при первом заходе человека в бота.

Владелец может выдать админку по @тегу человеку, который ещё ни разу не
писал боту. Сразу это не сработает: Bot API не умеет превращать тег в
числовой id, а без id доступ выдать некому. Поэтому тег кладётся в
pending_admins, а этот middleware ловит первое же сообщение в личку и
превращает запись в настоящего админа.

Работает только в личных чатах и только когда у человека есть @тег —
в остальных случаях сразу отдаёт управление дальше, ничего не спрашивая
у базы.
"""
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from database.admin_dao import PendingDAO
from database.engine import async_session_maker

logger = logging.getLogger(__name__)


class AdminPromoteMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user
        # Личка + есть тег: только тут запись вообще может сработать.
        # Во флуде проверять нечего, поэтому лишних запросов к базе нет.
        if (
            user is not None
            and not user.is_bot
            and user.username
            and event.chat.type == "private"
        ):
            try:
                async with async_session_maker() as session:
                    claimed = await PendingDAO(session).claim(user.id, user.username)
                if claimed:
                    logger.info("Отложенный админ активирован: %s (%s)", user.id, user.username)
                    try:
                        await event.answer(
                            "🛠 Тебе выдали доступ к админ-панели.\n"
                            "Открыть — команда <code>!админка</code>.",
                            parse_mode="HTML",
                        )
                    except TelegramAPIError:
                        pass
            except Exception:
                # Доступ — дело не срочное: что бы ни случилось, сообщение
                # должно дойти до обычных хендлеров.
                logger.exception("Не смог проверить отложенный доступ")

        return await handler(event, data)

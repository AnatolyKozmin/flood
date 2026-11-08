import logging
from aiogram import BaseMiddleware
from database.engine import async_session_maker
from database.quote_media import remember_message

logger = logging.getLogger(__name__)


def should_remember(event, raw_state=None):
    if event.chat.type in {"group", "supergroup"}:
        return True
    # В личке также нужны исходные сообщения до вызова /цитата.
    # Ответы регистрационной/другой FSM-формы не архивируем.
    return (event.chat.type == "private" and not raw_state
            and event.from_user is not None and not event.from_user.is_bot
            and event.content_type in {"text", "photo", "video", "animation", "video_note"})


async def record_quote_message(event, raw_state=None):
    if should_remember(event, raw_state):
        try:
            async with async_session_maker() as session:
                await remember_message(session, event)
                await session.commit()
        except Exception:
            logger.exception("Не сохранил связь сообщений для цитаты")


class QuoteHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        await record_quote_message(event, data.get("raw_state"))
        return await handler(event, data)

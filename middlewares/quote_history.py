import logging
from aiogram import BaseMiddleware
from database.engine import async_session_maker
from database.quote_media import remember_message

logger = logging.getLogger(__name__)


class QuoteHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if event.chat.type in {"group", "supergroup"}:
            try:
                async with async_session_maker() as session:
                    await remember_message(session, event)
                    await session.commit()
            except Exception:
                logger.exception("Не сохранил связь сообщений для цитаты")
        return await handler(event, data)

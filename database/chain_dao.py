"""Запись и чтение цепочек сообщений."""
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.chain_models import ChainMessage
from utils.helpers import msk_now

TEXT_MAX = 2000
KEEP_DAYS = 30


class ChainDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bump(self, rows: list[dict]) -> None:
        """Пачка записей: вставка или обновление по (chat_id, message_id)."""
        if not rows:
            return
        for row in rows:
            stmt = sqlite_insert(ChainMessage).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[ChainMessage.chat_id, ChainMessage.message_id],
                set_=dict(text=row["text"], username=row["username"],
                          display=row["display"], user_id=row["user_id"],
                          reply_to_id=row["reply_to_id"]),
            )
            await self.session.execute(stmt)
        await self.session.commit()

    async def get(self, chat_id: int, message_id: int) -> ChainMessage | None:
        return await self.session.get(ChainMessage, (chat_id, message_id))

    async def prune(self, chat_id: int) -> None:
        """Старье 30+ дней — долой, раз в flush и так дёшево."""
        cutoff = msk_now() - timedelta(days=KEEP_DAYS)
        await self.session.execute(
            delete(ChainMessage).where(
                ChainMessage.chat_id == chat_id,
                ChainMessage.created_at < cutoff,
            )
        )
        await self.session.commit()

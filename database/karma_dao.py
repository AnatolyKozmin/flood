"""Запросы кармы: поблагодарить, топ."""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.karma_models import Karma


class KarmaDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def thank(
        self, chat_id: int, user_id: int, username: str, display: str
    ) -> int:
        """+1 кармы. Возвращает новый итог."""
        row = (await self.session.execute(
            select(Karma).where(
                Karma.chat_id == chat_id, Karma.user_id == user_id
            )
        )).scalars().first()
        if row is None:
            # Счётчик дублируем и в питоне: default=0 срабатывает только
            # в базе, а прибавляем мы раньше flush.
            row = Karma(chat_id=chat_id, user_id=user_id, points=0)
            self.session.add(row)
        row.username = username or ""
        row.display = display or ""
        row.points = (row.points or 0) + 1
        await self.session.commit()
        return row.points

    async def top(self, chat_id: int, limit: int = 10) -> list[Karma]:
        """Топ по карме, больше очков — выше."""
        query = (
            select(Karma)
            .where(Karma.chat_id == chat_id)
            .order_by(Karma.points.desc(), Karma.user_id)
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def total_thanks(self, chat_id: int) -> int:
        query = select(func.sum(Karma.points)).where(Karma.chat_id == chat_id)
        return int((await self.session.execute(query)).scalar_one() or 0)

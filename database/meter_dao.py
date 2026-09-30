"""Писюны: раз в сутки -10..+10, топ по размеру."""
from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.meter_models import Meter

START_SIZE = 0


class MeterDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, user_id: int) -> Meter | None:
        return await self.session.get(Meter, user_id)

    async def play(self, user_id: int, username: str, display: str,
                   today: date, delta: int) -> tuple[str, Meter]:
        """Ход: ('cool'|'same'|'up'|'down', строка). Новеньким — старт."""
        row = await self.session.get(Meter, user_id)
        if row is None:
            row = Meter(user_id=user_id, username=username or "",
                        display=display or "", size=START_SIZE,
                        last_played=None)
            self.session.add(row)
        if row.last_played == today:
            await self.session.commit()
            return "cool", row
        row.username = username or row.username
        row.display = display or row.display
        row.size += delta
        row.last_played = today
        await self.session.commit()
        if delta > 0:
            return "up", row
        if delta < 0:
            return "down", row
        return "same", row

    async def top(self, limit: int = 10) -> list[Meter]:
        query = select(Meter).order_by(desc(Meter.size)).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

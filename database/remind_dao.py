"""Напоминалки: создать, список, удалить, due для воркера."""
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.remind_models import Remind
from utils.helpers import msk_now

MAX_ACTIVE = 10


class RemindDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def active_count(self, user_id: int) -> int:
        query = select(func.count()).select_from(Remind).where(
            Remind.user_id == user_id, Remind.done.is_(False))
        return int((await self.session.execute(query)).scalar_one() or 0)

    async def create(self, user_id: int, text: str,
                     fire_at: datetime) -> Remind | None:
        """None — уперся в лимит активных."""
        if await self.active_count(user_id) >= MAX_ACTIVE:
            return None
        row = Remind(user_id=user_id, text=text[:500], fire_at=fire_at,
                     done=False, created_at=msk_now())
        self.session.add(row)
        await self.session.commit()
        return row

    async def list_active(self, user_id: int) -> list[Remind]:
        query = select(Remind).where(
            Remind.user_id == user_id, Remind.done.is_(False)
        ).order_by(Remind.fire_at)
        return list((await self.session.execute(query)).scalars().all())

    async def delete(self, remind_id: int, user_id: int) -> bool:
        """Удалить только свою. False — нет такой."""
        result = await self.session.execute(
            delete(Remind).where(Remind.id == remind_id,
                                 Remind.user_id == user_id))
        await self.session.commit()
        return bool(result.rowcount)

    async def due(self, now: datetime) -> list[Remind]:
        query = select(Remind).where(
            Remind.done.is_(False), Remind.fire_at <= now
        ).order_by(Remind.fire_at)
        return list((await self.session.execute(query)).scalars().all())

    async def mark_done(self, remind_id: int) -> None:
        row = await self.session.get(Remind, remind_id)
        if row is not None:
            row.done = True
            await self.session.commit()

"""Свечки: кто где горит сегодня и само зажигание."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.candle_models import CandleLight
from utils.helpers import msk_now


class CandleDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def day_lights(self, day: str) -> list[CandleLight]:
        query = select(CandleLight).where(CandleLight.day == day).order_by(
            CandleLight.slot)
        return list((await self.session.execute(query)).scalars().all())

    async def user_light(self, day: str, user_id: int) -> CandleLight | None:
        query = select(CandleLight).where(
            CandleLight.day == day, CandleLight.user_id == user_id)
        return (await self.session.execute(query)).scalars().first()

    async def light(
        self, day: str, slot: int, user_id: int, username: str, display: str,
    ) -> str:
        """Зажечь. 'ok' | 'taken' (слот заняли) | 'already' (уже горит)."""
        if await self.user_light(day, user_id) is not None:
            return "already"
        self.session.add(CandleLight(
            day=day, slot=slot, user_id=user_id,
            username=username or "", display=display or "",
            lit_at=msk_now(),
        ))
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return "taken"
        return "ok"

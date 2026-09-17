"""Учёт партий дурака и топ для !покертоп."""
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.durak_models import DurakStat
from utils.helpers import msk_now


class DurakDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self, user_id: int, username: str, display: str, outcome: str,
    ) -> None:
        """outcome: 'win' | 'loss' | 'draw'. Строка одна на человека."""
        row = await self.session.get(DurakStat, user_id)
        if row is None:
            # Нули явно: default=0 срабатывает только в INSERT, а атрибут
            # до refresh остаётся None и += падает.
            row = DurakStat(
                user_id=user_id, username=username or "",
                display=display or "", wins=0, losses=0, draws=0,
                updated_at=msk_now(),
            )
            self.session.add(row)
        row.username = username or row.username
        row.display = display or row.display
        if outcome == "win":
            row.wins += 1
        elif outcome == "loss":
            row.losses += 1
        else:
            row.draws += 1
        row.updated_at = msk_now()
        await self.session.commit()

    async def top(self, limit: int = 10) -> list[DurakStat]:
        query = (
            select(DurakStat)
            .order_by(desc(DurakStat.wins), DurakStat.losses, DurakStat.draws)
            .limit(limit)
        )
        return list((await self.session.execute(query)).scalars().all())

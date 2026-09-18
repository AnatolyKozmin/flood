"""Бар: налитое, пустые бары, личные баны, топ."""
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.bar_models import BarBan, BarCooldown, BarPour
from utils.helpers import msk_now


class BarDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def pour(self, chat_id: int, user_id: int,
                   username: str, display: str, amount: int,
                   ) -> tuple[int, int]:
        """+amount бокалов. Возвращает (личный счёт, сумма чата)."""
        row = await self.session.get(BarPour, (chat_id, user_id))
        if row is None:
            # Нули явно: default=0 срабатывает только в INSERT.
            row = BarPour(chat_id=chat_id, user_id=user_id, count=0,
                          username=username or "", display=display or "",
                          updated_at=msk_now())
            self.session.add(row)
        row.count += amount
        row.username = username or row.username
        row.display = display or row.display
        row.updated_at = msk_now()
        await self.session.commit()
        total = await self.total(chat_id)
        return row.count, total

    async def total(self, chat_id: int) -> int:
        query = select(func.coalesce(func.sum(BarPour.count), 0)).where(
            BarPour.chat_id == chat_id)
        return int((await self.session.execute(query)).scalar() or 0)

    async def cooldown_until(self, chat_id: int) -> datetime | None:
        row = await self.session.get(BarCooldown, chat_id)
        return row.until if row is not None else None

    async def set_cooldown(self, chat_id: int, until: datetime) -> None:
        row = await self.session.get(BarCooldown, chat_id)
        if row is None:
            row = BarCooldown(chat_id=chat_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def reset(self, chat_id: int) -> None:
        """Новая поставка: счётчики в ноль, простой снят. Баны живут сами."""
        await self.session.execute(
            delete(BarPour).where(BarPour.chat_id == chat_id))
        await self.session.execute(
            delete(BarCooldown).where(BarCooldown.chat_id == chat_id))
        await self.session.commit()

    async def contributors(self, chat_id: int) -> list[int]:
        query = select(BarPour.user_id).where(
            BarPour.chat_id == chat_id, BarPour.count > 0)
        return list((await self.session.execute(query)).scalars().all())

    async def ban_until(self, chat_id: int, user_id: int) -> datetime | None:
        row = await self.session.get(BarBan, (chat_id, user_id))
        return row.until if row is not None else None

    async def set_ban(self, chat_id: int, user_id: int, until: datetime) -> None:
        row = await self.session.get(BarBan, (chat_id, user_id))
        if row is None:
            row = BarBan(chat_id=chat_id, user_id=user_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def top(self, limit: int = 10) -> list:
        """Топ по всем чатам: (user_id, username, display, всего)."""
        query = (
            select(BarPour.user_id, BarPour.username, BarPour.display,
                   func.sum(BarPour.count).label("total"))
            .group_by(BarPour.user_id)
            .order_by(func.sum(BarPour.count).desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).all())

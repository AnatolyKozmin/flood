"""Косячки: шмальнули, косяк скурен, личные баны, топ."""
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.shmal_models import ShmalBan, ShmalCooldown, ShmalPuff
from utils.helpers import msk_now


class ShmalDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def puff(self, chat_id: int, user_id: int,
                   username: str, display: str, amount: int,
                   ) -> tuple[int, int]:
        """+amount. Возвращает (личный счёт, сумма чата)."""
        row = await self.session.get(ShmalPuff, (chat_id, user_id))
        if row is None:
            # Нули явно: default=0 срабатывает только в INSERT.
            row = ShmalPuff(chat_id=chat_id, user_id=user_id, count=0,
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
        query = select(func.coalesce(func.sum(ShmalPuff.count), 0)).where(
            ShmalPuff.chat_id == chat_id)
        return int((await self.session.execute(query)).scalar() or 0)

    async def cooldown_until(self, chat_id: int) -> datetime | None:
        row = await self.session.get(ShmalCooldown, chat_id)
        return row.until if row is not None else None

    async def set_cooldown(self, chat_id: int, until: datetime) -> None:
        row = await self.session.get(ShmalCooldown, chat_id)
        if row is None:
            row = ShmalCooldown(chat_id=chat_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def reset(self, chat_id: int) -> None:
        """Новый косяк: счётчики в ноль, простой снят. Баны живут сами."""
        await self.session.execute(
            delete(ShmalPuff).where(ShmalPuff.chat_id == chat_id))
        await self.session.execute(
            delete(ShmalCooldown).where(ShmalCooldown.chat_id == chat_id))
        await self.session.commit()

    async def contributors(self, chat_id: int) -> list[int]:
        query = select(ShmalPuff.user_id).where(
            ShmalPuff.chat_id == chat_id, ShmalPuff.count > 0)
        return list((await self.session.execute(query)).scalars().all())

    async def ban_until(self, chat_id: int, user_id: int) -> datetime | None:
        row = await self.session.get(ShmalBan, (chat_id, user_id))
        return row.until if row is not None else None

    async def set_ban(self, chat_id: int, user_id: int, until: datetime) -> None:
        row = await self.session.get(ShmalBan, (chat_id, user_id))
        if row is None:
            row = ShmalBan(chat_id=chat_id, user_id=user_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def top(self, limit: int = 10) -> list:
        """Топ по всем чатам: (user_id, username, display, всего)."""
        query = (
            select(ShmalPuff.user_id, ShmalPuff.username, ShmalPuff.display,
                   func.sum(ShmalPuff.count).label("total"))
            .group_by(ShmalPuff.user_id)
            .order_by(func.sum(ShmalPuff.count).desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).all())

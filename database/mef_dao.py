"""Порошок: рассыпали, всё пересыпано, личные баны, топ."""
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.mef_models import MefBan, MefCooldown, MefPuff
from utils.helpers import msk_now


class MefDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def puff(self, chat_id: int, user_id: int,
                   username: str, display: str, amount: int,
                   ) -> tuple[int, int]:
        """+amount. Возвращает (личный счёт, сумма чата)."""
        row = await self.session.get(MefPuff, (chat_id, user_id))
        if row is None:
            # Нули явно: default=0 срабатывает только в INSERT.
            row = MefPuff(chat_id=chat_id, user_id=user_id, count=0,
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
        query = select(func.coalesce(func.sum(MefPuff.count), 0)).where(
            MefPuff.chat_id == chat_id)
        return int((await self.session.execute(query)).scalar() or 0)

    async def cooldown_until(self, chat_id: int) -> datetime | None:
        row = await self.session.get(MefCooldown, chat_id)
        return row.until if row is not None else None

    async def set_cooldown(self, chat_id: int, until: datetime) -> None:
        row = await self.session.get(MefCooldown, chat_id)
        if row is None:
            row = MefCooldown(chat_id=chat_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def reset(self, chat_id: int) -> None:
        """Новая порция: счётчики в ноль, простой снят. Баны живут сами."""
        await self.session.execute(
            delete(MefPuff).where(MefPuff.chat_id == chat_id))
        await self.session.execute(
            delete(MefCooldown).where(MefCooldown.chat_id == chat_id))
        await self.session.commit()

    async def contributors(self, chat_id: int) -> list[int]:
        query = select(MefPuff.user_id).where(
            MefPuff.chat_id == chat_id, MefPuff.count > 0)
        return list((await self.session.execute(query)).scalars().all())

    async def ban_until(self, chat_id: int, user_id: int) -> datetime | None:
        row = await self.session.get(MefBan, (chat_id, user_id))
        return row.until if row is not None else None

    async def set_ban(self, chat_id: int, user_id: int, until: datetime) -> None:
        row = await self.session.get(MefBan, (chat_id, user_id))
        if row is None:
            row = MefBan(chat_id=chat_id, user_id=user_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def top(self, limit: int = 10) -> list:
        """Топ по всем чатам: (user_id, username, display, всего)."""
        query = (
            select(MefPuff.user_id, MefPuff.username, MefPuff.display,
                   func.sum(MefPuff.count).label("total"))
            .group_by(MefPuff.user_id)
            .order_by(func.sum(MefPuff.count).desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).all())

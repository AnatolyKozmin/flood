"""Затяжки и перезарядки кальяна, топ курильщиков."""
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.kalik_models import KalikBan, KalikCooldown, KalikPuff
from utils.helpers import msk_now


class KalikDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def puff(self, chat_id: int, user_id: int,
                   username: str, display: str, amount: int,
                   ) -> tuple[int, int]:
        """+amount затяжек. Возвращает (личный счёт, сумма чата)."""
        row = await self.session.get(KalikPuff, (chat_id, user_id))
        if row is None:
            # Нули явно: default=0 срабатывает только в INSERT.
            row = KalikPuff(chat_id=chat_id, user_id=user_id, count=0,
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
        query = select(func.coalesce(func.sum(KalikPuff.count), 0)).where(
            KalikPuff.chat_id == chat_id)
        return int((await self.session.execute(query)).scalar() or 0)

    async def cooldown_until(self, chat_id: int) -> datetime | None:
        row = await self.session.get(KalikCooldown, chat_id)
        return row.until if row is not None else None

    async def set_cooldown(self, chat_id: int, until: datetime) -> None:
        row = await self.session.get(KalikCooldown, chat_id)
        if row is None:
            row = KalikCooldown(chat_id=chat_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def reset(self, chat_id: int) -> None:
        """Новая чаша: счётчики в ноль, перезарядка снята. Баны живут сами."""
        await self.session.execute(
            delete(KalikPuff).where(KalikPuff.chat_id == chat_id))
        await self.session.execute(
            delete(KalikCooldown).where(KalikCooldown.chat_id == chat_id))
        await self.session.commit()

    async def contributors(self, chat_id: int) -> list[int]:
        """Кто курил в текущей чаше."""
        query = select(KalikPuff.user_id).where(
            KalikPuff.chat_id == chat_id, KalikPuff.count > 0)
        return list((await self.session.execute(query)).scalars().all())

    async def ban_until(self, chat_id: int, user_id: int) -> datetime | None:
        row = await self.session.get(KalikBan, (chat_id, user_id))
        return row.until if row is not None else None

    async def set_ban(self, chat_id: int, user_id: int, until: datetime) -> None:
        row = await self.session.get(KalikBan, (chat_id, user_id))
        if row is None:
            row = KalikBan(chat_id=chat_id, user_id=user_id, until=until)
            self.session.add(row)
        else:
            row.until = until
        await self.session.commit()

    async def top(self, limit: int = 10) -> list:
        """Топ по всем чатам: (user_id, username, display, всего)."""
        query = (
            select(KalikPuff.user_id, KalikPuff.username, KalikPuff.display,
                   func.sum(KalikPuff.count).label("total"))
            .group_by(KalikPuff.user_id)
            .order_by(func.sum(KalikPuff.count).desc())
            .limit(limit)
        )
        return list((await self.session.execute(query)).all())

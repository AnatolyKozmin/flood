"""Запросы теневой модерации: муты и заместитель."""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.mod_models import AdminMute, ModDeputy
from utils.helpers import msk_now


class MuteDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_muted(self, user_id: int) -> AdminMute | None:
        query = select(AdminMute).where(AdminMute.user_id == user_id)
        return (await self.session.execute(query)).scalars().first()

    async def all(self) -> list[AdminMute]:
        query = select(AdminMute).order_by(AdminMute.muted_at)
        return list((await self.session.execute(query)).scalars().all())

    async def mute(
        self, user_id: int, username: str, display: str,
        activist_id: int | None, muted_by: int,
    ) -> bool:
        """Замутить. False — уже замучен."""
        if await self.is_muted(user_id) is not None:
            return False
        self.session.add(AdminMute(
            user_id=user_id, username=username or "", display=display or "",
            activist_id=activist_id, muted_by=muted_by, muted_at=msk_now(),
        ))
        await self.session.commit()
        return True

    async def unmute(self, user_id: int) -> bool:
        """Размутить. False — и не был замучен."""
        result = await self.session.execute(
            delete(AdminMute).where(AdminMute.user_id == user_id)
        )
        await self.session.commit()
        return bool(result.rowcount)


class DeputyDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> ModDeputy | None:
        return (await self.session.execute(select(ModDeputy))).scalars().first()

    async def grant(self, tg_id: int, username: str, granted_by: int) -> bool:
        """Выдать доступ. False — место занято (сначала забери)."""
        if await self.get() is not None:
            return False
        self.session.add(ModDeputy(
            tg_id=tg_id, username=username or "",
            granted_by=granted_by, granted_at=msk_now(),
        ))
        await self.session.commit()
        return True

    async def revoke(self) -> bool:
        """Забрать доступ. False — и так никого."""
        result = await self.session.execute(delete(ModDeputy))
        await self.session.commit()
        return bool(result.rowcount)

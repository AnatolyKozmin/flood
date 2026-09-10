"""Запросы для дуэлей: смерти, флаги, воскрешения."""
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.duel_models import DeadSoul, WhiteFlag
from utils.helpers import msk_now

DEATH_TTL = timedelta(hours=1)
FLAG_TTL = timedelta(days=3)


class DuelDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── смерти ──

    async def is_dead(self, chat_id: int, user_id: int) -> DeadSoul | None:
        query = select(DeadSoul).where(
            DeadSoul.chat_id == chat_id, DeadSoul.user_id == user_id
        )
        return (await self.session.execute(query)).scalars().first()

    async def kill(
        self, chat_id: int, user_id: int, username: str, display: str,
        now: datetime | None = None,
    ) -> DeadSoul:
        """Убить на час. Если запись уже есть (перестраховка — хендлеры
        мёртвых не вызывают), просто продлеваем воскрешение."""
        now = now or msk_now()
        soul = await self.is_dead(chat_id, user_id)
        if soul is None:
            soul = DeadSoul(
                chat_id=chat_id, user_id=user_id,
                username=username or "", display=display or "",
                dies_at=now, resurrect_at=now + DEATH_TTL,
            )
            self.session.add(soul)
        else:
            soul.dies_at = now
            soul.resurrect_at = now + DEATH_TTL
        await self.session.commit()
        return soul

    async def due_for_resurrection(
        self, now: datetime | None = None
    ) -> list[DeadSoul]:
        now = now or msk_now()
        query = select(DeadSoul).where(DeadSoul.resurrect_at <= now)
        return list((await self.session.execute(query)).scalars().all())

    async def revive(self, soul: DeadSoul) -> None:
        await self.session.delete(soul)
        await self.session.commit()

    # ── флаги ──

    async def get_flag(self, chat_id: int, user_id: int) -> WhiteFlag | None:
        query = select(WhiteFlag).where(
            WhiteFlag.chat_id == chat_id, WhiteFlag.user_id == user_id
        )
        return (await self.session.execute(query)).scalars().first()

    async def raise_flag(
        self, chat_id: int, user_id: int, username: str, display: str,
        now: datetime | None = None,
    ) -> bool:
        """Поднять флаг. False — он уже поднят (повтор). Поднятие заново
        не продлевает: флаг живёт 3 дня с первого поднятия."""
        if await self.get_flag(chat_id, user_id) is not None:
            return False
        self.session.add(WhiteFlag(
            chat_id=chat_id, user_id=user_id,
            username=username or "", display=display or "",
            raised_at=now or msk_now(),
        ))
        await self.session.commit()
        return True

    async def lower_flag(self, chat_id: int, user_id: int) -> bool:
        """Опустить флаг. False — его и не было."""
        flag = await self.get_flag(chat_id, user_id)
        if flag is None:
            return False
        await self.session.delete(flag)
        await self.session.commit()
        return True

    async def expire_flags(self, now: datetime | None = None) -> int:
        """Снести протухшие (старше 3 дней). Возвращает сколько снёс."""
        now = now or msk_now()
        result = await self.session.execute(
            delete(WhiteFlag).where(WhiteFlag.raised_at <= now - FLAG_TTL)
        )
        await self.session.commit()
        return result.rowcount or 0

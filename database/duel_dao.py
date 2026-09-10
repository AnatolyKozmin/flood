"""Запросы для дуэлей: смерти, флаги, воскрешения."""
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.duel_models import DeadSoul, DuelStat, WhiteFlag
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

    async def list_dead(self, chat_id: int) -> list[DeadSoul]:
        """Все мёртвые чата — для !кладбище. Ближайшее воскрешение первым."""
        query = (
            select(DeadSoul)
            .where(DeadSoul.chat_id == chat_id)
            .order_by(DeadSoul.resurrect_at)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def revive(self, soul: DeadSoul) -> None:
        await self.session.delete(soul)
        await self.session.commit()

    async def list_all(self) -> list[DeadSoul]:
        """Все мёртвые во всех чатах — для админской кнопки воскрешения."""
        query = select(DeadSoul).order_by(DeadSoul.resurrect_at)
        return list((await self.session.execute(query)).scalars().all())

    async def revive_all(self) -> int:
        """Воскресить всех сразу. Возвращает сколько воскресил."""
        result = await self.session.execute(delete(DeadSoul))
        await self.session.commit()
        return result.rowcount or 0

    # ── флаги ──

    async def get_flag(self, chat_id: int, user_id: int) -> WhiteFlag | None:
        query = select(WhiteFlag).where(
            WhiteFlag.chat_id == chat_id, WhiteFlag.user_id == user_id
        )
        return (await self.session.execute(query)).scalars().first()

    async def list_flags(self, chat_id: int) -> list[WhiteFlag]:
        """Все поднятые флаги чата — для !мирные. Давно поднятые первыми
        (они раньше и слетят)."""
        query = (
            select(WhiteFlag)
            .where(WhiteFlag.chat_id == chat_id)
            .order_by(WhiteFlag.raised_at)
        )
        return list((await self.session.execute(query)).scalars().all())

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

    # ── счёт ──

    async def _stat_row(
        self, chat_id: int, user_id: int, username: str, display: str
    ) -> DuelStat:
        row = (await self.session.execute(
            select(DuelStat).where(
                DuelStat.chat_id == chat_id, DuelStat.user_id == user_id
            )
        )).scalars().first()
        if row is None:
            # Счётчики дублируем и в питоне: default=0 срабатывает только
            # в базе, а прибавляем мы раньше flush.
            row = DuelStat(
                chat_id=chat_id, user_id=user_id,
                duel_wins=0, duel_losses=0, roulette_wins=0, roulette_losses=0,
            )
            self.session.add(row)
        row.username = username or ""
        row.display = display or ""
        return row

    async def record_duel(
        self,
        chat_id: int,
        winner: tuple[int, str, str],
        loser: tuple[int, str, str],
    ) -> None:
        """Победа — противник умер, поражение — умер сам."""
        w = await self._stat_row(chat_id, winner[0], winner[1], winner[2])
        w.duel_wins += 1
        l = await self._stat_row(chat_id, loser[0], loser[1], loser[2])
        l.duel_losses += 1
        await self.session.commit()

    async def record_roulette(
        self, chat_id: int, user: tuple[int, str, str], survived: bool
    ) -> None:
        """В рулетке победа — остался жив, поражение — умер."""
        row = await self._stat_row(chat_id, user[0], user[1], user[2])
        if survived:
            row.roulette_wins += 1
        else:
            row.roulette_losses += 1
        await self.session.commit()

    async def top_fighters(
        self, chat_id: int, limit: int = 3
    ) -> tuple[list[DuelStat], list[DuelStat]]:
        """(топ победителей, топ проигравших) — суммарно дуэли + рулетка."""
        rows = (await self.session.execute(
            select(DuelStat).where(DuelStat.chat_id == chat_id)
        )).scalars().all()
        winners = sorted(
            (r for r in rows if r.duel_wins + r.roulette_wins > 0),
            key=lambda r: (-(r.duel_wins + r.roulette_wins), r.user_id),
        )[:limit]
        losers = sorted(
            (r for r in rows if r.duel_losses + r.roulette_losses > 0),
            key=lambda r: (-(r.duel_losses + r.roulette_losses), r.user_id),
        )[:limit]
        return winners, losers

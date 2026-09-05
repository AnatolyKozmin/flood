"""Запросы к статистике сообщений."""
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.stats_models import MessageStat, StatsUser


class StatsDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bump(
        self,
        counts: dict[tuple[int, int, date], int],
        users: dict[int, tuple[str | None, str, datetime]],
    ) -> None:
        """Записать накопленные счётчики пачкой.

        UPSERT: если строка на «чат + человек + день» уже есть — прибавляем,
        если нет — создаём. Одним запросом на всю пачку, без чтения перед записью.
        """
        if users:
            rows = [
                {"user_id": uid, "username": username, "full_name": full_name, "last_seen": seen}
                for uid, (username, full_name, seen) in users.items()
            ]
            stmt = sqlite_insert(StatsUser).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "username": stmt.excluded.username,
                    "full_name": stmt.excluded.full_name,
                    "last_seen": stmt.excluded.last_seen,
                },
            )
            await self.session.execute(stmt)

        if counts:
            rows = [
                {"chat_id": chat_id, "user_id": user_id, "day": day, "count": n}
                for (chat_id, user_id, day), n in counts.items()
            ]
            stmt = sqlite_insert(MessageStat).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id", "user_id", "day"],
                set_={"count": MessageStat.count + stmt.excluded.count},
            )
            await self.session.execute(stmt)

        await self.session.commit()

    async def leaderboard(self, chat_id: int, since: date | None = None) -> list[tuple[int, int]]:
        """[(user_id, сколько сообщений), ...] по убыванию — весь чат целиком.

        Возвращаем всех, а не top-N: людей в чате десятки, зато из одного
        списка сразу считаются и место конкретного человека, и общий итог,
        и проценты — без пачки отдельных запросов.
        """
        total = func.sum(MessageStat.count)
        query = select(MessageStat.user_id, total.label("n")).where(MessageStat.chat_id == chat_id)
        if since is not None:
            query = query.where(MessageStat.day >= since)
        query = query.group_by(MessageStat.user_id).order_by(total.desc())
        rows = (await self.session.execute(query)).all()
        return [(row.user_id, int(row.n or 0)) for row in rows]

    async def first_day(self, chat_id: int) -> date | None:
        """День, с которого вообще есть счётчики (бот не видит историю до себя)."""
        query = select(func.min(MessageStat.day)).where(MessageStat.chat_id == chat_id)
        return (await self.session.execute(query)).scalar_one_or_none()

    async def best_day(self, chat_id: int, user_id: int) -> tuple[date, int] | None:
        """Самый разговорчивый день человека."""
        query = (
            select(MessageStat.day, MessageStat.count)
            .where(MessageStat.chat_id == chat_id, MessageStat.user_id == user_id)
            .order_by(MessageStat.count.desc(), MessageStat.day.desc())
            .limit(1)
        )
        row = (await self.session.execute(query)).first()
        return (row.day, int(row.count)) if row else None

    async def active_days(self, chat_id: int, user_id: int) -> int:
        """Сколько дней человек вообще что-то писал."""
        query = select(func.count()).select_from(MessageStat).where(
            MessageStat.chat_id == chat_id, MessageStat.user_id == user_id
        )
        return int((await self.session.execute(query)).scalar_one() or 0)

    async def users(self, user_ids: list[int]) -> dict[int, StatsUser]:
        if not user_ids:
            return {}
        query = select(StatsUser).where(StatsUser.user_id.in_(user_ids))
        return {u.user_id: u for u in (await self.session.execute(query)).scalars().all()}

    async def user_by_username(self, username: str) -> StatsUser | None:
        """Найти по @тегу. Теги сравниваем без '@' и без учёта регистра —
        как в ActivistsDAO.get_by_username(): в базе актива они вразнобой."""
        needle = (username or "").strip().lstrip("@").lower()
        if not needle:
            return None
        query = select(StatsUser).where(
            func.lower(func.replace(StatsUser.username, "@", "")) == needle
        )
        return (await self.session.execute(query)).scalars().first()

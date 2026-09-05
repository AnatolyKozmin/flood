"""Запросы для голосования за цитаты и батла."""
import random
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Quotes
from database.quotes_extra_models import BattleRound, BattleSession, QuoteVote
from utils.helpers import MSK

BATTLE_ROUNDS = 10


def _now() -> datetime:
    """Наивное время по Москве — как везде в проекте."""
    return datetime.now(MSK).replace(tzinfo=None)


class VotesDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def toggle(self, quote_id: int, user_id: int) -> tuple[bool, int]:
        """Поставить или снять сердечко. Возвращает (голос стоит, сколько всего).

        Повторное нажатие снимает голос — это не накрутка, а исправление
        промаха. Второй раз «за» один и тот же человек всё равно не проголосует:
        не даст уникальный индекс.
        """
        existing = await self.session.execute(
            select(QuoteVote).where(
                QuoteVote.quote_id == quote_id, QuoteVote.user_id == user_id
            )
        )
        row = existing.scalars().first()
        if row is not None:
            await self.session.execute(
                delete(QuoteVote).where(QuoteVote.id == row.id)
            )
            await self.session.commit()
            return False, await self.count(quote_id)

        self.session.add(QuoteVote(quote_id=quote_id, user_id=user_id, voted_at=_now()))
        try:
            await self.session.commit()
        except IntegrityError:
            # Гонка: два нажатия подряд. Голос уже есть — это не ошибка.
            await self.session.rollback()
        return True, await self.count(quote_id)

    async def count(self, quote_id: int) -> int:
        query = select(func.count()).select_from(QuoteVote).where(
            QuoteVote.quote_id == quote_id
        )
        return int((await self.session.execute(query)).scalar_one() or 0)

    async def counts(self, quote_ids: list[int]) -> dict[int, int]:
        if not quote_ids:
            return {}
        query = (
            select(QuoteVote.quote_id, func.count().label("n"))
            .where(QuoteVote.quote_id.in_(quote_ids))
            .group_by(QuoteVote.quote_id)
        )
        rows = (await self.session.execute(query)).all()
        return {row.quote_id: int(row.n) for row in rows}

    async def top(self, limit: int = 10, since: date | None = None) -> list[tuple[int, int]]:
        """[(quote_id, голосов), ...] по убыванию."""
        query = select(QuoteVote.quote_id, func.count().label("n"))
        if since is not None:
            query = query.where(QuoteVote.voted_at >= datetime.combine(since, datetime.min.time()))
        query = (
            query.group_by(QuoteVote.quote_id)
            .order_by(func.count().desc(), QuoteVote.quote_id.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(query)).all()
        return [(row.quote_id, int(row.n)) for row in rows]

    async def voted_by(self, quote_id: int, user_id: int) -> bool:
        query = select(QuoteVote.id).where(
            QuoteVote.quote_id == quote_id, QuoteVote.user_id == user_id
        )
        return (await self.session.execute(query)).first() is not None


class BattleDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def quote_ids(self) -> list[int]:
        return [q for (q,) in (await self.session.execute(select(Quotes.id))).all()]

    async def quotes_by_ids(self, ids: list[int]) -> dict[int, Quotes]:
        if not ids:
            return {}
        rows = (await self.session.execute(
            select(Quotes).where(Quotes.id.in_(ids))
        )).scalars().all()
        return {q.id: q for q in rows}

    async def start(self, user_id: int, chat_id: int) -> BattleSession:
        session = BattleSession(
            user_id=user_id, chat_id=chat_id, round=0, seen="", started_at=_now()
        )
        self.session.add(session)
        await self.session.commit()
        return session

    async def get(self, session_id: int) -> BattleSession | None:
        return await self.session.get(BattleSession, session_id)

    @staticmethod
    def _key(a: int, b: int) -> str:
        return f"{min(a, b)}-{max(a, b)}"

    async def next_pair(self, battle: BattleSession) -> tuple[int, int] | None:
        """Случайная пара, которой ещё не было в этой сессии."""
        ids = await self.quote_ids()
        if len(ids) < 2:
            return None
        seen = set(battle.seen.split(",")) if battle.seen else set()

        pairs = [
            (a, b)
            for i, a in enumerate(ids)
            for b in ids[i + 1:]
            if self._key(a, b) not in seen
        ]
        if not pairs:
            return None
        left, right = random.choice(pairs)
        if random.random() < 0.5:          # чтобы «первая» не была всегда сверху
            left, right = right, left
        return left, right

    async def set_pair(self, battle: BattleSession, left: int, right: int) -> None:
        battle.left_id, battle.right_id = left, right
        key = self._key(left, right)
        battle.seen = f"{battle.seen},{key}" if battle.seen else key
        await self.session.commit()

    async def record(self, battle: BattleSession, winner_id: int) -> None:
        """Записать выбор и сдвинуть раунд."""
        self.session.add(BattleRound(
            session_id=battle.id,
            left_id=battle.left_id,
            right_id=battle.right_id,
            winner_id=winner_id,
            decided_at=_now(),
        ))
        battle.round += 1
        await self.session.commit()

    async def finish(self, battle: BattleSession) -> None:
        battle.finished_at = _now()
        await self.session.commit()

    async def session_scores(self, session_id: int) -> list[tuple[int, int]]:
        """Кто сколько раз победил внутри одной сессии."""
        query = (
            select(BattleRound.winner_id, func.count().label("n"))
            .where(BattleRound.session_id == session_id)
            .group_by(BattleRound.winner_id)
            .order_by(func.count().desc())
        )
        rows = (await self.session.execute(query)).all()
        return [(row.winner_id, int(row.n)) for row in rows]

    async def top_by_wins(self, limit: int = 3) -> list[tuple[int, int, int]]:
        """[(quote_id, побед, сравнений), ...] — прямо из сыгранных раундов.

        Отдельной таблицы с рейтингом больше нет: battle_rounds и так хранит
        каждый выбор, а раундов — десяток на сессию, так что считать в
        питоне дешевле, чем поддерживать вторую копию тех же данных.
        """
        rows = (await self.session.execute(
            select(BattleRound.left_id, BattleRound.right_id, BattleRound.winner_id)
        )).all()

        wins: dict[int, int] = {}
        shown: dict[int, int] = {}
        for left, right, winner in rows:
            shown[left] = shown.get(left, 0) + 1
            shown[right] = shown.get(right, 0) + 1
            wins[winner] = wins.get(winner, 0) + 1

        # По числу побед. Цитата с одной случайной победой не обгонит ту,
        # что выиграла семь раз, — а это ровно то, чего хотелось от рейтинга.
        ranked = sorted(shown, key=lambda q: (-wins.get(q, 0), -shown[q], q))
        return [(q, wins.get(q, 0), shown[q]) for q in ranked[:limit]]

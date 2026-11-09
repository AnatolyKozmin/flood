"""Запросы для голосования за цитаты и батла."""
import random
from datetime import date, datetime, timedelta

from sqlalchemy import case, delete, func, literal, select, text, union_all
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Quotes
from database.quotes_extra_models import BattleRound, BattleSession, QuoteReaction, QuoteVote
from utils.helpers import MSK

BATTLE_ROUNDS = 10

# Брошенный батл перестаёт блокировать новые через полчаса: иначе одна
# недожатая сессия навсегда закрывала бы !батл всему чату.
BATTLE_STALE = timedelta(minutes=30)


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

    async def react(self, quote_id: int, display_id: str, user_id: int, value: int) -> None:
        stmt = insert(QuoteReaction).values(quote_id=quote_id, display_id=display_id,
                                           user_id=user_id, value=value, voted_at=_now())
        await self.session.execute(stmt.on_conflict_do_update(
            index_elements=[QuoteReaction.quote_id, QuoteReaction.display_id, QuoteReaction.user_id],
            set_={"value": stmt.excluded.value, "voted_at": stmt.excluded.voted_at},
            where=QuoteReaction.value != stmt.excluded.value,
        ))
        await self.session.commit()

    @staticmethod
    def _reactions(since: date | None = None, quote_id: int | None = None):
        legacy = select(QuoteVote.quote_id, literal(1).label("value"))
        current = select(QuoteReaction.quote_id, QuoteReaction.value)
        if since is not None:
            start = datetime.combine(since, datetime.min.time())
            legacy = legacy.where(QuoteVote.voted_at >= start)
            current = current.where(QuoteReaction.voted_at >= start)
        if quote_id is not None:
            legacy = legacy.where(QuoteVote.quote_id == quote_id)
            current = current.where(QuoteReaction.quote_id == quote_id)
        return union_all(legacy, current).subquery()

    async def rating(self, quote_id: int) -> tuple[int, int]:
        votes = self._reactions(quote_id=quote_id)
        likes, dislikes = (await self.session.execute(select(
            func.count(case((votes.c.value == 1, 1))),
            func.count(case((votes.c.value == -1, 1))),
        ))).one()
        return likes, dislikes

    async def top(self, limit: int = 10, since: date | None = None, *, bottom: bool = False) -> list[tuple[int, int, int]]:
        """Цитаты с положительным или отрицательным балансом за период."""
        votes = self._reactions(since)
        score = func.sum(votes.c.value)
        query = select(votes.c.quote_id,
                       func.count(case((votes.c.value == 1, 1))),
                       func.count(case((votes.c.value == -1, 1))))
        query = (query.join(Quotes, Quotes.id == votes.c.quote_id)
                 .group_by(votes.c.quote_id)
                 .having(score < 0 if bottom else score > 0)
                 .order_by(score.asc() if bottom else score.desc(), votes.c.quote_id.desc())
                 .limit(limit))
        return [tuple(row) for row in (await self.session.execute(query)).all()]

    async def voted_by(self, quote_id: int, user_id: int) -> bool:
        query = select(QuoteVote.id).where(
            QuoteVote.quote_id == quote_id, QuoteVote.user_id == user_id
        )
        return (await self.session.execute(query)).first() is not None


class BattleDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def quote_ids(self) -> list[int]:
        from database.quote_media import QuoteJob
        query = select(Quotes.id).where(~select(QuoteJob.id).where(
            QuoteJob.quote_id == Quotes.id, QuoteJob.state != "done").exists())
        return [q for (q,) in (await self.session.execute(query)).all()]

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

    async def active_in_chat(self, chat_id: int) -> BattleSession | None:
        """Незаконченный батл в чате — батл делает только один человек за раз.

        Протухшие (стартовали раньше BATTLE_STALE) молча закрываем: их кнопки
        всё равно уже утонули во флуде, а блокировать чат они не должны."""
        now = _now()
        rows = (await self.session.execute(
            select(BattleSession)
            .where(
                BattleSession.chat_id == chat_id,
                BattleSession.finished_at.is_(None),
            )
            .order_by(BattleSession.started_at.desc())
        )).scalars().all()

        alive: BattleSession | None = None
        stale = False
        for battle in rows:
            if battle.started_at is not None and battle.started_at >= now - BATTLE_STALE:
                if alive is None:
                    alive = battle
            else:
                battle.finished_at = now
                stale = True
        if stale:
            await self.session.commit()
        return alive

    async def get(self, session_id: int) -> BattleSession | None:
        return await self.session.get(BattleSession, session_id)

    @staticmethod
    def _key(a: int, b: int) -> str:
        return f"{min(a, b)}-{max(a, b)}"

    async def ensure_pool_columns(self) -> None:
        """Дотянуть колонки кругового турнира на живой базе.

        create_all создаёт только новые таблицы, колонки в существующие
        не добавляет — поэтому ALTER руками. Идемпотентно: повторный
        вызов падает в OperationalError и молча проходит."""
        for column in ("pool", "queue"):
            try:
                await self.session.execute(
                    text(f"ALTER TABLE battle_sessions ADD COLUMN {column} VARCHAR(512) DEFAULT ''")
                )
            except OperationalError:
                pass
        await self.session.commit()

    async def setup_round_robin(self, battle: BattleSession, ids: list[int]) -> bool:
        """Круговой турнир: пул из 5 случайных цитат, все 10 пар
        в случайном порядке. False — цитат меньше пяти."""
        if len(ids) < 5:
            return False
        pool = random.sample(ids, 5)
        pairs = [(a, b) for i, a in enumerate(pool) for b in pool[i + 1:]]
        random.shuffle(pairs)
        battle.pool = ",".join(map(str, pool))
        battle.queue = ",".join(f"{a}-{b}" for a, b in pairs)
        await self.session.commit()
        return True

    async def next_queued_pair(self, battle: BattleSession) -> tuple[int, int] | None:
        """Следующая пара турнира. None — пары кончились, время итога."""
        if not battle.queue:
            return None
        head, _, rest = battle.queue.partition(",")
        battle.queue = rest
        try:
            left, right = (int(x) for x in head.split("-"))
        except ValueError:
            # Битая запись — пропускаем, очередь всё равно убывает.
            return await self.next_queued_pair(battle)
        if random.random() < 0.5:  # кто сверху — случайно, не по жребию пула
            left, right = right, left
        await self.session.commit()
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

    async def top_by_points(self, limit: int = 3) -> list[tuple[int, int, int]]:
        """[(quote_id, баллов, сравнений), ...] — по накопленным баллам.

        Балл — за каждую выигранную пару, а не за победу в батле целиком.
        Разница принципиальная: цитата, которая раз за разом остаётся второй,
        набирает больше, чем та, что один раз взяла первое место. Победы в
        сессиях (session_scores) в общий зачёт не идут — они только объявляют
        победителя конкретного батла.

        Считаем по battle_rounds: там уже лежит каждый выбор, а раундов
        десяток на сессию — дешевле сложить в питоне, чем держать вторую
        копию тех же данных.
        """
        rows = (await self.session.execute(
            select(BattleRound.left_id, BattleRound.right_id, BattleRound.winner_id)
        )).all()

        points: dict[int, int] = {}
        shown: dict[int, int] = {}
        for left, right, winner in rows:
            shown[left] = shown.get(left, 0) + 1
            shown[right] = shown.get(right, 0) + 1
            points[winner] = points.get(winner, 0) + 1

        # При равенстве баллов выше та, которой хватило меньше сравнений:
        # одинаковый счёт с меньшего числа попыток — сильнее.
        ranked = sorted(shown, key=lambda q: (-points.get(q, 0), shown[q], q))
        return [(q, points.get(q, 0), shown[q]) for q in ranked[:limit]]

    async def battles_played(self) -> int:
        """Сколько батлов доиграли до конца."""
        query = select(func.count()).select_from(BattleSession).where(
            BattleSession.finished_at.is_not(None)
        )
        return int((await self.session.execute(query)).scalar_one() or 0)

"""Разовый сид маттопа: победы, сыгранные до появления зачёта.

Ставит минимум 1 победу указанным людям в главном чате (самый активный
по счётчику сообщений). Идемпотентно: у кого победа уже есть (сыграл
по-настоящему) — не трогаем, повторный запуск ничего не задвоит.

Запуск на сервере:
    docker compose exec bot env PYTHONPATH=/app python scripts/seed_mathtop.py
Можно указать чат явно: ... seed_mathtop.py -1001234567890
"""
import asyncio
import sys

from sqlalchemy import func, select

from database.duel_dao import DuelDAO
from database.engine import async_session_maker, init_db
from database.models import Activists
from database.profile_models import ActivistLink
from database.stats_dao import StatsDAO
from database.stats_models import MessageStat

SEED_FIOS = [
    "Ислам Каппушев",
    "Николай Брагинец",
    "Валентина Трунова",
]


async def _resolve(session, fio: str) -> tuple[str, int] | None:
    """(тег, tg_id) по ФИО. tg_id ищем по надёжности: привязка анкеты,
    потом статистика писавших. Без tg_id не возвращаем никого: общий
    user_id=0 склеил бы разных людей в одну строку."""
    needle = set(fio.casefold().split())
    rows = (await session.execute(select(Activists))).scalars().all()
    for activist in rows:
        if needle <= set((activist.fio or "").casefold().split()):
            tag = (activist.tg_username or "").strip().lstrip("@")
            if not tag:
                continue
            link = (await session.execute(
                select(ActivistLink).where(ActivistLink.activist_id == activist.id)
            )).scalars().first()
            if link is not None:
                return tag, int(link.tg_id)
            found = await StatsDAO(session).user_by_username(tag)
            if found is not None:
                return tag, int(found.user_id)
            return None
    return None


async def _main_chat(session) -> int | None:
    if len(sys.argv) > 1:
        return int(sys.argv[1])
    row = (await session.execute(
        select(MessageStat.chat_id, func.sum(MessageStat.count).label("n"))
        .group_by(MessageStat.chat_id)
        .order_by(func.sum(MessageStat.count).desc())
        .limit(1)
    )).first()
    return row.chat_id if row else None


async def main() -> None:
    await init_db()
    async with async_session_maker() as session:
        chat_id = await _main_chat(session)
        if chat_id is None:
            print("Не нашёл чат: статистика пуста. Укажи id явно.")
            raise SystemExit(1)
        print(f"Чат: {chat_id}")

        dao = DuelDAO(session)
        for fio in SEED_FIOS:
            resolved = await _resolve(session, fio)
            if resolved is None:
                print(f"⚠️ {fio}: нет в базе актива или нет tg_id, пропускаю")
                continue
            username, tg_id = resolved
            row = await dao._math_row(chat_id, tg_id, username, "")
            if row.wins == 0:
                row.wins = 1
                print(f"✅ {fio} (@{username}): победа засчитана")
            else:
                print(f"⏭ {fio} (@{username}): уже есть победы, не трогаю")
        await session.commit()

        winners, _ = await dao.top_math(chat_id)
        print("Топ:", [(w.username, w.wins) for w in winners])


if __name__ == "__main__":
    asyncio.run(main())

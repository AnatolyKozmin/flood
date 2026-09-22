"""Общие штуки для статистики сообщений: имена, числа, строка для !инфо."""
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Activists
from database.stats_dao import StatsDAO
from middlewares.message_counter import flush_stats
from utils.format import field

# Узкий пробел: '1 234' читается лучше, чем '1234', и не ломает вёрстку в телеге.
_THIN_SPACE = " "


def fmt_num(value: int) -> str:
    return f"{value:,}".replace(",", _THIN_SPACE)


def plural(value: int, one: str, few: str, many: str) -> str:
    """'1 сообщение / 2 сообщения / 5 сообщений'."""
    if value % 10 == 1 and value % 100 != 11:
        return one
    if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        return few
    return many


async def build_names(session, user_ids: list[int]) -> dict[int, str]:
    """Имя для каждого user_id: ФИО из базы актива (сначала по tg_id,
    потом по тегу — скрытые профили тоже находятся) → имя из телеги → @тег."""
    from utils.names import fio_name

    stats_users = await StatsDAO(session).users(user_ids)

    names: dict[int, str] = {}
    for user_id in user_ids:
        user = stats_users.get(user_id)
        username = (user.username or "").strip().lstrip("@") if user else ""
        fio = await fio_name(session, user_id, username)
        if fio:
            names[user_id] = fio
        elif user and user.full_name:
            names[user_id] = user.full_name
        elif username:
            names[user_id] = f"@{username}"
        else:
            names[user_id] = f"id{user_id}"
    return names


async def find_activists(session, query: str) -> list:
    """Активисты по @тегу, фамилии или полному ФИО.

    Сравниваем в Python через casefold(): SQLite LOWER() не понимает кириллицу.
    """
    needle = query.strip().lstrip("@").strip().casefold()
    if not needle:
        return []
    found = []
    for activist in (await session.execute(select(Activists))).scalars().all():
        username = (activist.tg_username or "").lstrip("@").strip().casefold()
        fio = (activist.fio or "").strip().casefold()
        if (username and username == needle) or fio == needle or (fio and fio.split()[0] == needle):
            found.append(activist)
    return found


async def activist_stats_line(chat_id: int, activist) -> str | None:
    """Строка «💬 Сообщений: 1 234 · 3-е место» для карточки !инфо.

    None, если человек ни разу не писал в этом чате или команду позвали
    не в группе. Тег не обязателен: скрытого находим через привязку анкеты.
    """
    username = (activist.tg_username or "").strip().lstrip("@")

    await flush_stats()
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        user = await dao.user_by_username(username) if username else None
        if user is None:
            user_id = await _tg_id_by_activist(session, activist.id)
            if user_id is None:
                return None
        else:
            user_id = user.user_id
        board = await dao.leaderboard(chat_id)

    for place, (uid, count) in enumerate(board, start=1):
        if uid == user_id:
            return field("Сообщений", f"{fmt_num(count)} · {place}-е место", "💬")
    return None


async def _tg_id_by_activist(session, activist_id: int) -> int | None:
    """tg_id по привязке анкеты — для скрытых профилей без @тега."""
    from database.profile_models import ActivistLink

    link = (await session.execute(
        select(ActivistLink).where(ActivistLink.activist_id == activist_id)
    )).scalars().first()
    return int(link.tg_id) if link is not None else None

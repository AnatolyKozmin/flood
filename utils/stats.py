"""Общие штуки для статистики сообщений: имена, числа, строка для !инфо."""
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Activists
from database.stats_dao import StatsDAO
from middlewares.message_counter import flush_stats
from utils.format import field
from utils.helpers import first_last

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


def _activist_name(activist) -> str:
    return first_last(activist.fio) if activist and activist.fio else ""


async def fio_by_username(session) -> dict[str, str]:
    """{'тег в нижнем регистре': 'Имя Фамилия'} по всей базе активистов.

    В базе теги записаны вразнобой (где-то с '@', где-то нет, разный
    регистр) — приводим к одному виду, иначе половина людей в топе
    останется с ником вместо имени.
    """
    activists = (await session.execute(select(Activists))).scalars().all()
    out: dict[str, str] = {}
    for activist in activists:
        key = (activist.tg_username or "").strip().lstrip("@").casefold()
        name = _activist_name(activist)
        if key and name:
            out.setdefault(key, name)
    return out


async def build_names(session, user_ids: list[int]) -> dict[int, str]:
    """Имя для каждого user_id: ФИО из базы актива → имя из телеги → @тег."""
    stats_users = await StatsDAO(session).users(user_ids)
    by_username = await fio_by_username(session)

    names: dict[int, str] = {}
    for user_id in user_ids:
        user = stats_users.get(user_id)
        username = (user.username or "").strip().lstrip("@").casefold() if user else ""
        fio = by_username.get(username) if username else None
        if fio:
            names[user_id] = fio
        elif user and user.full_name:
            names[user_id] = user.full_name
        elif user and user.username:
            names[user_id] = f"@{user.username}"
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

    None, если у активиста нет тега, он ни разу не писал в этом чате или
    команду позвали не в группе.
    """
    username = (activist.tg_username or "").strip().lstrip("@")
    if not username:
        return None

    await flush_stats()
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        user = await dao.user_by_username(username)
        if user is None:
            return None
        board = await dao.leaderboard(chat_id)

    for place, (user_id, count) in enumerate(board, start=1):
        if user_id == user.user_id:
            return field("Сообщений", f"{fmt_num(count)} · {place}-е место", "💬")
    return None

"""Статистика по сообщениям чата: !топ и !стата.

Считает middleware (middlewares/message_counter.py), здесь только чтение и
оформление. Важно: бот видит сообщения только с того момента, как его
добавили и запустили — историю чата телеграм ботам не отдаёт.
"""
import html
from datetime import date, timedelta

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.stats_dao import StatsDAO
from middlewares.message_counter import flush_stats
from utils.format import DIVIDER
from utils.helpers import moscow_today
from utils.stats import build_names, find_activists, fmt_num, plural


top_router = Router()

TOP_CMD = "!топ"
STATS_CMD = "!стата"
STATS_ALIASES = ("!стата", "!статистика")

TOP_LIMIT = 10
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

# Сколько дней назад начинается период. None — за всё время.
PERIODS: dict[str, tuple[int | None, str]] = {
    "": (None, "за всё время"),
    "всё": (None, "за всё время"),
    "все": (None, "за всё время"),
    "всего": (None, "за всё время"),
    "вообще": (None, "за всё время"),
    "сегодня": (0, "за сегодня"),
    "день": (0, "за сегодня"),
    "сутки": (0, "за сегодня"),
    "неделя": (6, "за неделю"),
    "неделю": (6, "за неделю"),
    "нед": (6, "за неделю"),
    "месяц": (29, "за месяц"),
    "мес": (29, "за месяц"),
}

# Слова-украшения: «!топ болтунов» должен работать так же, как «!топ».
FILLER = {"болтунов", "болтун", "флудеров", "флудер", "чата", "чате", "по", "общению", "сообщениям"}

GROUP_ONLY = "Эта команда для группового чата — в личке считать нечего 🙂"


class FirstWord(BaseFilter):
    """Команда — ровно первое слово сообщения (как в !цитата)."""

    def __init__(self, *commands: str) -> None:
        self.commands = {c.strip().casefold() for c in commands}

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().split(maxsplit=1)
        return bool(parts) and parts[0].casefold() in self.commands


def _args(message: Message, command: str) -> str:
    return message.text.strip()[len(command):].strip()


def _is_group(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


def _period(raw: str) -> tuple[int | None, str] | None:
    """Разбор хвоста команды. None — если написали что-то непонятное."""
    words = [w for w in raw.casefold().split() if w not in FILLER]
    if not words:
        return PERIODS[""]
    return PERIODS.get(words[0])


def _since(days_back: int | None) -> date | None:
    return None if days_back is None else moscow_today() - timedelta(days=days_back)


def _msgs(count: int) -> str:
    return f"{fmt_num(count)} {plural(count, 'сообщение', 'сообщения', 'сообщений')}"


def _share(count: int, total: int) -> str:
    if not total:
        return "0%"
    percent = count / total * 100
    # У самых тихих доля округляется в ноль — «<1%» честнее, чем «0%».
    # '<' экранируем сразу: строка уходит в сообщение с parse_mode="HTML".
    return f"{round(percent)}%" if percent >= 0.5 else "&lt;1%"


def _place(place: int) -> str:
    return MEDALS.get(place, f"{place}.")


@top_router.message(FirstWord(TOP_CMD))
async def top_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    period = _period(_args(message, TOP_CMD))
    if period is None:
        await message.reply(
            "Не понял период. Так можно:\n"
            "<code>!топ</code> — за всё время\n"
            "<code>!топ сегодня</code> · <code>!топ неделя</code> · <code>!топ месяц</code>",
            parse_mode="HTML",
        )
        return

    days_back, title = period
    await flush_stats()  # чтобы в топе были и сообщения последних секунд
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        board = await dao.leaderboard(message.chat.id, _since(days_back))
        if not board:
            await message.reply(f"Пока нечего показывать — сообщений {title} я не насчитал.")
            return
        names = await build_names(session, [user_id for user_id, _ in board[:TOP_LIMIT]])
        first_day = await dao.first_day(message.chat.id) if days_back is None else None

    total = sum(count for _, count in board)
    lines = [f"🏆 <b>Топ болтунов</b> — {title}", DIVIDER]
    for place, (user_id, count) in enumerate(board[:TOP_LIMIT], start=1):
        name = html.escape(names[user_id])
        lines.append(f"{_place(place)} {name} — {fmt_num(count)} · {_share(count, total)}")

    lines += ["", f"Всего {_msgs(total)} от {len(board)} "
                  f"{plural(len(board), 'человека', 'человек', 'человек')}"]

    # Своё место — если сам не попал в десятку.
    author = message.from_user
    if author is not None:
        mine = next(((p, n) for p, (uid, n) in enumerate(board, start=1) if uid == author.id), None)
        if mine and mine[0] > TOP_LIMIT:
            lines.append(f"Ты на {mine[0]}-м месте — {fmt_num(mine[1])}")

    if first_day:
        lines.append(f"<i>Считаю с {first_day.strftime('%d.%m.%Y')} — историю чата бот не видит</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def _target_user_id(message: Message, query: str) -> tuple[int | None, str | None]:
    """Кого показываем: (user_id, текст ошибки).

    По порядку: явный запрос (@тег или фамилия) → автор сообщения, на которое
    ответили → сам автор команды.
    """
    if query:
        async with async_session_maker() as session:
            dao = StatsDAO(session)
            user = await dao.user_by_username(query)
            if user:
                return user.user_id, None
            # Не нашли по тегу — вдруг это фамилия из базы актива.
            activists = await find_activists(session, query)
            for activist in activists:
                tag = (activist.tg_username or "").strip().lstrip("@")
                if tag and (found := await dao.user_by_username(tag)):
                    return found.user_id, None
        clean = html.escape(query.lstrip("@"))
        return None, f"Не нашёл «{clean}» среди тех, кто писал в чат при мне."

    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        return reply.from_user.id, None
    if message.from_user:
        return message.from_user.id, None
    return None, "Не могу понять, чью статистику показывать."


@top_router.message(FirstWord(*STATS_ALIASES))
async def stats_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    command = message.text.strip().split(maxsplit=1)[0]
    query = _args(message, command)

    await flush_stats()
    user_id, error = await _target_user_id(message, query)
    if error:
        await message.reply(error)
        return

    today = moscow_today()
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        board = await dao.leaderboard(message.chat.id)
        week = dict(await dao.leaderboard(message.chat.id, today - timedelta(days=6)))
        day = dict(await dao.leaderboard(message.chat.id, today))
        names = await build_names(session, [user_id])
        best = await dao.best_day(message.chat.id, user_id)
        active = await dao.active_days(message.chat.id, user_id)
        first_day = await dao.first_day(message.chat.id)

    place = next((i for i, (uid, _) in enumerate(board, start=1) if uid == user_id), None)
    count = next((n for uid, n in board if uid == user_id), 0)
    name = html.escape(names[user_id])

    if not count:
        since = f" (считаю с {first_day.strftime('%d.%m.%Y')})" if first_day else ""
        await message.reply(f"У {name} пока ни одного сообщения{since}.")
        return

    total = sum(n for _, n in board)
    lines = [
        f"💬 <b>Статистика — {name}</b>",
        DIVIDER,
        f"📊 <b>Всего:</b> {_msgs(count)} · {_share(count, total)} чата",
        f"🏅 <b>Место:</b> {place} из {len(board)}",
    ]
    if active:
        lines.append(
            f"📅 <b>Дней в эфире:</b> {active} · "
            f"в среднем {fmt_num(round(count / active))} в день"
        )
    if best:
        best_day, best_count = best
        lines.append(f"🔥 <b>Ударный день:</b> {best_day.strftime('%d.%m.%Y')} — {fmt_num(best_count)}")
    lines.append(
        f"📈 <b>За неделю:</b> {fmt_num(week.get(user_id, 0))} · "
        f"<b>сегодня:</b> {fmt_num(day.get(user_id, 0))}"
    )

    await message.answer("\n".join(lines), parse_mode="HTML")

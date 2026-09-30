"""!метр и !топ метров — в личке и во флуде.

Раз в сутки (московские) случайные -10..+10 к размеру. Старт — 10 СМ.
Размер общий, не по чатам. В минус уходить можно — так даже смешнее.
"""
import html
import random

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.meter_dao import MeterDAO
from utils.format import DIVIDER
from utils.helpers import moscow_today
from utils.names import display_name

meter_router = Router()

COOLDOWN = (
    "Твой писюн еще не готов к росту. Попробуй завтра.",
    "Твой аппарат еще не готов к росту. Попробуй завтра.",
)


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _name(user) -> str:
    """«Фамилия Имя»."""
    fio = f"{user.last_name or ''} {user.first_name or ''}".strip()
    return html.escape(fio or user.full_name)


@meter_router.message(Exact("!метр"))
async def meter_cmd(message: Message):
    today = moscow_today()
    delta = random.randint(-10, 10)
    tag = (message.from_user.username or "").lstrip("@")
    async with async_session_maker() as session:
        dao = MeterDAO(session)
        display = await display_name(session, message.from_user.id, tag,
                                     message.from_user.full_name)
        outcome, row = await dao.play(
            message.from_user.id, tag, display, today, delta)
    if outcome == "cool":
        await message.reply(random.choice(COOLDOWN))
        return
    if outcome == "up":
        await message.reply(
            f"Твой писюн был увеличен на {delta} СМ\n"
            f"Теперь его размер — {row.size} СМ\n\n"
            f"Следующая попытка завтра :)")
        return
    if outcome == "down":
        await message.reply(
            f"Твоя пиписька была уменьшена на {delta} СМ\n\n"
            f"Теперь его размер — {row.size} СМ\n\n"
            f"Следующая попытка завтра :)")
        return
    await message.reply(
        f"Твой аппарат остался таким же. Может оно и к лучшему.\n\n"
        f"Его размер — {row.size} СМ\n\n"
        f"Следующая попытка завтра :)")


@meter_router.message(Exact("!топ метров"))
async def metertop_cmd(message: Message):
    async with async_session_maker() as session:
        top = await MeterDAO(session).top()
    if not top:
        await message.reply("Метров ещё ни у кого нет. Начни с !метр 📏")
        return
    lines = ["📏 <b>Топ метров</b>", DIVIDER]
    for i, row in enumerate(top, 1):
        name = html.escape(row.display or "без имени")
        lines.append(f"{i}. {name} — {row.size} СМ")
    await message.answer("\n".join(lines), parse_mode="HTML")

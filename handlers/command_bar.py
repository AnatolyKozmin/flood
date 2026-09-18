"""!налить и !топ бара — в личке и во флуде, зеркало калика.

Каждый вызов !налить — случайные 1–10 бокалов. Текст: «Фамилия Имя выпил
N бокалов». Как только сумма чата добивает 50 — бар пуст: счётчики в ноль
и час простоя, а вызов отвечает «Егор и Анатолий поехали закупаться для
следующей порции напитков». Весь бар в одно лицо — соло-бан: тот ждёт
2 часа, «Фамилия Имя всё выпил весь бар, следующая порция без тебя».
Топ — общий по всем чатам.
"""
import html
import random
from datetime import timedelta

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.bar_dao import BarDAO
from database.engine import async_session_maker
from utils.format import DIVIDER
from utils.helpers import msk_now
from utils.stats import plural

bar_router = Router()

EMPTY_AT = 50
COOLDOWN = timedelta(hours=1)
SOLO_BAN = timedelta(hours=2)
EMPTY_TEXT = "Егор и Анатолий поехали закупаться для следующей порции напитков"
SOLO_TEXT = "всё выпил весь бар, следующая порция без тебя"


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _name(user) -> str:
    """«Фамилия Имя»."""
    fio = f"{user.last_name or ''} {user.first_name or ''}".strip()
    return html.escape(fio or user.full_name)


@bar_router.message(Exact("!налить"))
async def bar_cmd(message: Message):
    now = msk_now()
    uid = message.from_user.id
    name = _name(message.from_user)
    async with async_session_maker() as session:
        dao = BarDAO(session)
        banned = await dao.ban_until(message.chat.id, uid)
        if banned is not None and banned > now:
            await message.reply(f"🍺 {name} {SOLO_TEXT} 🥃")
            return
        until = await dao.cooldown_until(message.chat.id)
        if until is not None:
            if until > now:
                await message.reply(f"🍺 {EMPTY_TEXT}")
                return
            await dao.reset(message.chat.id)
        shot = random.randint(1, 10)
        personal, total = await dao.pour(
            message.chat.id, uid,
            (message.from_user.username or "").lstrip("@"),
            message.from_user.full_name, shot,
        )
        if total >= EMPTY_AT:
            solo = await dao.contributors(message.chat.id) == [uid]
            await dao.reset(message.chat.id)
            await dao.set_cooldown(message.chat.id, now + COOLDOWN)
            if solo:
                await dao.set_ban(message.chat.id, uid, now + SOLO_BAN)
                await message.reply(f"🍺 {name} {SOLO_TEXT} 🥃")
            else:
                await message.reply(f"🍺 {EMPTY_TEXT} 🥃")
            return
    left = EMPTY_AT - total
    await message.reply(
        f"🍺 {name} выпил {personal} "
        f"{plural(personal, 'бокал', 'бокала', 'бокалов')} 🥃\n"
        f"До пустого бара: {left} {plural(left, 'бокал', 'бокала', 'бокалов')}",
        parse_mode="HTML",
    )


@bar_router.message(Exact("!топ бара"))
async def bartop_cmd(message: Message):
    async with async_session_maker() as session:
        top = await BarDAO(session).top()
    if not top:
        await message.reply("Бар полон и нетронут. Начни с !налить 🍺")
        return
    lines = ["🍺 <b>Топ бара</b>", DIVIDER]
    for i, row in enumerate(top, 1):
        name = html.escape(row.display or "без имени")
        lines.append(
            f"{i}. {name} — {row.total} "
            f"{plural(row.total, 'бокал', 'бокала', 'бокалов')} 🥃")
    await message.answer("\n".join(lines), parse_mode="HTML")

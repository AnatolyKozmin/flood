"""!мефчик и !топ порошка — в личке и во флуде, зеркало бара.

Каждый вызов — случайные 1–10. Как только сумма чата добивает 50 — всё
пересыпано: счётчики в ноль и час тишины, а вызов отвечает «Пересыпаем
порошок». Все 50 в одно лицо — соло-бан 2 часа. Топ — общий по всем чатам.
"""
import html
import random
from datetime import timedelta

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.mef_dao import MefDAO
from utils.format import DIVIDER
from utils.helpers import msk_now
from utils.stats import plural

mef_router = Router()

EMPTY_AT = 50
COOLDOWN = timedelta(hours=1)
SOLO_BAN = timedelta(hours=2)
EMPTY_TEXT = "Пересыпаем порошок"
SOLO_TEXT = "всё рассыпал весь порошок, следующая порция без тебя"


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _name(user) -> str:
    """«Фамилия Имя»."""
    fio = f"{user.last_name or ''} {user.first_name or ''}".strip()
    return html.escape(fio or user.full_name)


@mef_router.message(Exact("!мефчик"))
async def mef_cmd(message: Message):
    now = msk_now()
    uid = message.from_user.id
    name = _name(message.from_user)
    async with async_session_maker() as session:
        dao = MefDAO(session)
        banned = await dao.ban_until(message.chat.id, uid)
        if banned is not None and banned > now:
            await message.reply(f"❄️ {name} {SOLO_TEXT}")
            return
        until = await dao.cooldown_until(message.chat.id)
        if until is not None:
            if until > now:
                await message.reply(f"❄️ {EMPTY_TEXT}")
                return
            await dao.reset(message.chat.id)
        hit = random.randint(1, 10)
        personal, total = await dao.puff(
            message.chat.id, uid,
            (message.from_user.username or "").lstrip("@"),
            message.from_user.full_name, hit,
        )
        if total >= EMPTY_AT:
            solo = await dao.contributors(message.chat.id) == [uid]
            await dao.reset(message.chat.id)
            await dao.set_cooldown(message.chat.id, now + COOLDOWN)
            if solo:
                await dao.set_ban(message.chat.id, uid, now + SOLO_BAN)
                await message.reply(f"❄️ {name} {SOLO_TEXT}")
            else:
                await message.reply(f"❄️ {EMPTY_TEXT}")
            return
    left = EMPTY_AT - total
    await message.reply(
        f"❄️ {name} разложил {personal} "
        f"{plural(personal, 'дорожку', 'дорожки', 'дорожек')}\n"
        f"До пересыпки: {left} {plural(left, 'дорожка', 'дорожки', 'дорожек')}",
        parse_mode="HTML",
    )


@mef_router.message(Exact("!топ порошка"))
async def meftop_cmd(message: Message):
    async with async_session_maker() as session:
        top = await MefDAO(session).top()
    if not top:
        await message.reply("Порошок нетронут. Начни с !мефчик ❄️")
        return
    lines = ["❄️ <b>Топ порошка</b>", DIVIDER]
    for i, row in enumerate(top, 1):
        name = html.escape(row.display or "без имени")
        lines.append(
            f"{i}. {name} — {row.total} "
            f"{plural(row.total, 'дорожка', 'дорожки', 'дорожек')}")
    await message.answer("\n".join(lines), parse_mode="HTML")

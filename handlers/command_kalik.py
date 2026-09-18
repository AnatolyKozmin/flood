"""!калик и !топ курильщиков — пока только в личке.

Каждый вызов !калик — +1 затяжка. Текст: «Фамилия Имя сделал N затяжек».
Как только сумма чата добивает 50 — перезарядка: счётчики в ноль и час
тишины, а вызов отвечает «Михаил забивает кальян и греет угли, таки
подождите». Через час можно снова. Топ — общий по всем чатам.
"""
import html
from datetime import timedelta

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.kalik_dao import KalikDAO
from utils.format import DIVIDER
from utils.helpers import msk_now
from utils.stats import plural

kalik_router = Router()

RELOAD_AT = 50
COOLDOWN = timedelta(hours=1)
RELOAD_TEXT = "Михаил забивает кальян и греет угли, таки подождите"
LS_ONLY = "Калик пока живёт в личке — напиши мне 🙂"


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _name(user) -> str:
    """«Фамилия Имя»."""
    fio = f"{user.last_name or ''} {user.first_name or ''}".strip()
    return html.escape(fio or user.full_name)


@kalik_router.message(Exact("!калик"))
async def kalik_cmd(message: Message):
    if message.chat.type != "private":
        await message.reply(LS_ONLY)
        return
    now = msk_now()
    async with async_session_maker() as session:
        dao = KalikDAO(session)
        until = await dao.cooldown_until(message.chat.id)
        if until is not None:
            if until > now:
                await message.reply(f"🚬 {RELOAD_TEXT}")
                return
            await dao.reset(message.chat.id)
        personal, total = await dao.puff(
            message.chat.id, message.from_user.id,
            (message.from_user.username or "").lstrip("@"),
            message.from_user.full_name,
        )
        if total >= RELOAD_AT:
            await dao.reset(message.chat.id)
            await dao.set_cooldown(message.chat.id, now + COOLDOWN)
            await message.reply(f"🚬 {RELOAD_TEXT} 💨")
            return
    left = RELOAD_AT - total
    await message.reply(
        f"🚬 {_name(message.from_user)} сделал {personal} "
        f"{plural(personal, 'затяжку', 'затяжки', 'затяжек')} 💨\n"
        f"До перезарядки: {left} {plural(left, 'затяжка', 'затяжки', 'затяжек')}",
        parse_mode="HTML",
    )


@kalik_router.message(Exact("!топ курильщиков"))
async def kaliktop_cmd(message: Message):
    if message.chat.type != "private":
        await message.reply(LS_ONLY)
        return
    async with async_session_maker() as session:
        top = await KalikDAO(session).top()
    if not top:
        await message.reply("Пока никто не затягивался. Начни с !калик 💨")
        return
    lines = ["🚬 <b>Топ курильщиков</b>", DIVIDER]
    for i, row in enumerate(top, 1):
        name = html.escape(row.display or "без имени")
        lines.append(
            f"{i}. {name} — {row.total} "
            f"{plural(row.total, 'затяжка', 'затяжки', 'затяжек')} 💨")
    await message.answer("\n".join(lines), parse_mode="HTML")

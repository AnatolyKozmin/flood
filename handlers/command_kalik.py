"""!калик и !топ курильщиков — в личке и во флуде.

Каждый вызов !калик — случайные 1–10 затяжек. Текст: «Фамилия Имя сделал
N затяжек». Как только сумма чата добивает 50 — перезарядка: счётчики
в ноль и час тишины, а вызов отвечает «Михаил забивает кальян и греет
угли, таки подождите». Все 50 в одно лицо — соло-бан: тот ждёт 2 часа,
«Фамилия Имя всё выкурил весь кальян, следующая порция без тебя».
Через час (два для соло) можно снова. Топ — общий по всем чатам.
"""
import html
import random
from datetime import timedelta

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.kalik_dao import KalikDAO
from utils.format import DIVIDER
from utils.helpers import msk_now
from utils.names import display_name
from utils.stats import plural

kalik_router = Router()

RELOAD_AT = 50
COOLDOWN = timedelta(hours=1)
SOLO_BAN = timedelta(hours=2)
RELOAD_TEXT = "Михаил забивает кальян и греет угли, таки подождите"
SOLO_TEXT = "всё выкурил весь кальян, следующая порция без тебя"


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
    now = msk_now()
    uid = message.from_user.id
    name = _name(message.from_user)
    async with async_session_maker() as session:
        dao = KalikDAO(session)
        banned = await dao.ban_until(message.chat.id, uid)
        if banned is not None and banned > now:
            await message.reply(f"🚬 {name} {SOLO_TEXT} 💨")
            return
        until = await dao.cooldown_until(message.chat.id)
        if until is not None:
            if until > now:
                await message.reply(f"🚬 {RELOAD_TEXT}")
                return
            await dao.reset(message.chat.id)
        hit = random.randint(1, 10)
        username = (message.from_user.username or "").lstrip("@")
        display = await display_name(session, uid, username,
                                     message.from_user.full_name)
        personal, total = await dao.puff(
            message.chat.id, uid, username, display, hit,
        )
        if total >= RELOAD_AT:
            solo = await dao.contributors(message.chat.id) == [uid]
            await dao.reset(message.chat.id)
            await dao.set_cooldown(message.chat.id, now + COOLDOWN)
            if solo:
                await dao.set_ban(message.chat.id, uid, now + SOLO_BAN)
                await message.reply(f"🚬 {name} {SOLO_TEXT} 💨")
            else:
                await message.reply(f"🚬 {RELOAD_TEXT} 💨")
            return
    left = RELOAD_AT - total
    await message.reply(
        f"🚬 {name} сделал {personal} "
        f"{plural(personal, 'затяжку', 'затяжки', 'затяжек')} 💨\n"
        f"До перезарядки: {left} {plural(left, 'затяжка', 'затяжки', 'затяжек')}",
        parse_mode="HTML",
    )


@kalik_router.message(Exact("!топ курильщиков"))
async def kaliktop_cmd(message: Message):
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

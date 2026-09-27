"""!напомни — личка-напоминалка.

Создать: !напомни через 2 часа позвонить / !напомни в 18:30 созвон.
Список: !напомни список. Удалить: !напомни удали 3 (номер из списка).
Лимиты: от минуты до 30 дней, не больше 10 активных на человека.
Шлёт всегда в личку; если человек боту ни разу не писал — просим
написаться (иначе телега не даст отправить первой).
"""
import html
import re
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.remind_dao import MAX_ACTIVE, RemindDAO
from utils.format import DIVIDER
from utils.helpers import msk_now

remind_router = Router()

MIN_WAIT = timedelta(minutes=1)
MAX_WAIT = timedelta(days=30)

UNITS = {
    "минут": 60, "минуту": 60, "минуты": 60,
    "час": 3600, "часа": 3600, "часов": 3600,
    "день": 86400, "дня": 86400, "дней": 86400,
    "неделю": 604800, "недели": 604800, "недель": 604800,
}

_REL_RE = re.compile(r"^через\s+(\d+)\s+(\S+)\s+(.+)$", re.IGNORECASE)
_AT_RE = re.compile(r"^в\s+(\d{1,2}):(\d{2})\s+(.+)$")


def _parse_when(arg: str, now):
    """(fire_at, ошибка-подсказка)."""
    rel = _REL_RE.match(arg.strip())
    if rel is not None:
        amount, unit, text = int(rel.group(1)), rel.group(2).casefold(), rel.group(3).strip()
        mult = UNITS.get(unit)
        if mult is None or not text:
            return None, True
        fire_at = now + timedelta(seconds=amount * mult)
        if fire_at - now < MIN_WAIT or fire_at - now > MAX_WAIT:
            return None, True
        return fire_at, text
    at = _AT_RE.match(arg.strip())
    if at is not None:
        hour, minute, text = int(at.group(1)), int(at.group(2)), at.group(3).strip()
        if not (0 <= hour <= 23 and 0 <= minute <= 59) or not text:
            return None, True
        fire_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if fire_at <= now:
            fire_at += timedelta(days=1)
        if fire_at - now > MAX_WAIT:
            return None, True
        return fire_at, text
    return None, True


def _usage() -> str:
    return ("Так можно: <code>!напомни через 2 часа позвонить</code> · "
            "<code>!напомни в 18:30 созвон</code> · "
            "<code>!напомни список</code> · <code>!напомни удали 3</code>")


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


@remind_router.message(Exact("!напомни"))
async def remind_help_cmd(message: Message):
    await message.reply(_usage(), parse_mode="HTML")


@remind_router.message(Exact("!напомни список"))
async def remind_list_cmd(message: Message):
    async with async_session_maker() as session:
        rows = await RemindDAO(session).list_active(message.from_user.id)
    if not rows:
        await message.reply("Активных напоминалок нет. " + _usage(), parse_mode="HTML")
        return
    lines = ["⏰ <b>Напоминалки</b>", DIVIDER]
    for row in rows:
        when = row.fire_at.strftime("%d.%m %H:%M")
        lines.append(f"{row.id}. {when} — {html.escape(row.text)}")
    lines.append(f"\nУдалить: <code>!напомни удали N</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@remind_router.message(F.text.startswith("!напомни удали"))
async def remind_del_cmd(message: Message):
    try:
        remind_id = int(message.text.strip().split()[-1])
    except (ValueError, AttributeError):
        await message.reply(_usage(), parse_mode="HTML")
        return
    async with async_session_maker() as session:
        removed = await RemindDAO(session).delete(remind_id, message.from_user.id)
    await message.reply("Удалил." if removed else "Такой своей напоминалки нет — глянь <code>!напомни список</code>.",
                        parse_mode="HTML")


@remind_router.message(F.text.startswith("!напомни "))
async def remind_add_cmd(message: Message):
    arg = message.text.strip()[len("!напомни"):].strip()
    now = msk_now()
    parsed = _parse_when(arg, now)
    if parsed[0] is None:
        await message.reply(_usage(), parse_mode="HTML")
        return
    fire_at, text = parsed
    async with async_session_maker() as session:
        row = await RemindDAO(session).create(message.from_user.id, text, fire_at)
    if row is None:
        await message.reply(f"Уже {MAX_ACTIVE} активных — удали лишние (<code>!напомни список</code>).",
                            parse_mode="HTML")
        return
    # Проверка, что в личку вообще долетит: пробуем тихо? Нет — просто верим,
    # воркер отметит блокировку. Но если человек ни разу не писал боту,
    # телега запретит первое сообщение — предупреждаем заранее.
    try:
        await message.bot.send_chat_action(message.from_user.id, "typing")
        reachable = True
    except TelegramAPIError:
        reachable = False
    if not reachable:
        async with async_session_maker() as session:
            await RemindDAO(session).delete(row.id, message.from_user.id)
        await message.reply("Сначала напиши мне в личку хоть что-нибудь — иначе телега не даст мне написать первой.")
        return
    await message.reply(
        f"⏰ Запомнил: {fire_at.strftime('%d.%m %H:%M')} — {html.escape(text)}")

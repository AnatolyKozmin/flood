import html
import random
from datetime import date

from aiogram import Router, F
from aiogram.filters import BaseFilter
from aiogram.types import Message
from sqlalchemy import select

from database.engine import async_session_maker
from database.dao import ActivistsDAO
from database.models import Activists
from handlers.command_duel import DUEL_TOP_WORDS
from handlers.command_karma import KARMA_WORDS
from handlers.command_quotes_top import BATTLE_WORDS, QUOTE_WORDS
from handlers.command_top import FILLER, PERIODS
from utils.helpers import format_activist, moscow_today


people_router = Router()

_plumber_last: dict[int, date] = {}

# Слова, которыми заведуют другие топы: их не трогаем, пусть разбираются сами.
_TOP_TAKEN = (QUOTE_WORDS | BATTLE_WORDS | DUEL_TOP_WORDS | KARMA_WORDS
              | set(PERIODS) | FILLER)


class TopFive(BaseFilter):
    """«!топ кто выигрывает»: !топ + свои слова, первое — никому
    не известное. Голый «!топ» и чужие топы/периоды уходят как раньше."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().split()
        if not parts or parts[0].casefold() != "!топ":
            return False
        meaningful = [w for w in parts[1:]
                      if w.casefold() not in _TOP_TAKEN]
        return len(meaningful) >= 1 and meaningful[0] not in _TOP_TAKEN


@people_router.message(TopFive())
async def top_five_cmd(message: Message):
    parts = message.text.strip().split()
    meaningful = [w for w in parts[1:] if w.casefold() not in _TOP_TAKEN]
    title = " ".join(meaningful)
    async with async_session_maker() as session:
        pool = (await session.execute(
            select(Activists).where(Activists.is_active.is_(True))
        )).scalars().all()
    if len(pool) < 5:
        await message.reply("В активе меньше пяти человек — зови людей.")
        return
    five = random.sample(list(pool), 5)
    lines = [f"🏆 <b>Топ 5 {html.escape(title)}</b>"]
    for i, activist in enumerate(five, start=1):
        lines.append(f"{i}. {format_activist(activist)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@people_router.message(F.text.startswith('!кто'))
async def who_cmd(message: Message):
    async with async_session_maker() as session:
        activist = await ActivistsDAO(session).get_random_activist()

    text_message = message.text[4:].strip()
    await message.answer(f'{format_activist(activist)} {text_message}')


@people_router.message(F.text.startswith('!сантехник дня'))
async def random_plumber_cmd(message: Message):
    today = moscow_today()
    if _plumber_last.get(message.chat.id) == today:
        await message.answer("Сегодня уже выбирали сантехника дня")
        return

    async with async_session_maker() as session:
        activist = await ActivistsDAO(session).get_random_activist()

    _plumber_last[message.chat.id] = today
    await message.answer(f'Сантехником дня становится {format_activist(activist)}!')


@people_router.message(F.text.startswith('!ебанат дня'))
async def random_ebanat_cmd(message: Message):
    async with async_session_maker() as session:
        activist = await ActivistsDAO(session).get_random_activist()

    await message.answer(f'Ебанатом дня становится {format_activist(activist)}!')

from datetime import date

from aiogram import Router, F
from aiogram.types import Message

from database.engine import async_session_maker
from database.dao import ActivistsDAO
from utils.helpers import format_activist


people_router = Router()

_plumber_last: dict[int, date] = {}


@people_router.message(F.text.startswith('!кто'))
async def who_cmd(message: Message):
    async with async_session_maker() as session:
        activist = await ActivistsDAO(session).get_random_activist()

    text_message = message.text[4:].strip()
    await message.answer(f'{format_activist(activist)} {text_message}')


@people_router.message(F.text.startswith('!сантехник дня'))
async def random_plumber_cmd(message: Message):
    today = date.today()
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

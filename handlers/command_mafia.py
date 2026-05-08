from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from database.engine import async_session_maker
from database.dao import ActivistsDAO


mafia_router = Router()


@mafia_router.message(F.text.starswith('!мафия'))
async def start_mafia(message: Message):
    ...



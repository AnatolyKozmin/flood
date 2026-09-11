import html
import random
from datetime import datetime
from pathlib import Path
from aiogram import Router, F
from aiogram.types import FSInputFile, Message
from aiogram.filters import Command
from sqlalchemy import select

from database.dao import ActivistsDAO
from database.engine import async_session_maker
from database.models import Activists
from database.profile_dao import ProfileDAO
from utils.helpers import first_last, moscow_today
from utils.stats import find_activists, plural


random_router = Router()


@random_router.message(F.text.startswith('!вероятность'))
async def probability_cmd(message: Message):
    # Получаем текст после команды '!вероятность'
    text_after = message.text[13:].strip()  # '!вероятность' = 12 символов + пробел
    
    random_percent = random.randint(1, 100)

    await message.reply(f'{text_after} с вероятностью {random_percent}%')


@random_router.message(F.text.startswith('!подскажи'))
async def get_advice(message: Message):
    lst_advice = ['Да', 'Нет', 'Забей да по братски']

    res_advise = random.choice(lst_advice)

    await message.reply(res_advise)


def _lived_days(birthday, today) -> int:
    """Сколько дней человек живёт. Дату рождения считаем московской,
    как и всё остальное в проекте."""
    born = birthday.date() if isinstance(birthday, datetime) else birthday
    return max(0, (today - born).days)


@random_router.message(F.text.startswith('!жызуха'))
async def lifespan_cmd(message: Message):
    arg = message.text.strip()[len('!жызуха'):].strip()
    reply = message.reply_to_message

    async with async_session_maker() as session:
        activist = None
        if reply and reply.from_user and not reply.from_user.is_bot:
            user = reply.from_user
            activist = await ProfileDAO(session).by_tg_id(user.id)
            if activist is None:
                activist = await ActivistsDAO(session).get_by_username(
                    user.username or ""
                )
            name = (
                first_last(activist.fio)
                if activist and activist.fio
                else user.full_name
            )
        elif arg:
            found = await find_activists(session, arg)
            if not found:
                clean = html.escape(arg.lstrip("@"))
                await message.reply(
                    f"Не нашёл «{clean}» в базе актива — дату рождения взять негде."
                )
                return
            if len(found) > 1:
                await message.reply(
                    "Нашёл нескольких — уточни по тегу: "
                    + ", ".join(
                        f"<code>!жызуха {html.escape(a.tg_username)}</code>"
                        for a in found[:5] if a.tg_username
                    ),
                    parse_mode="HTML",
                )
                return
            activist = found[0]
            name = first_last(activist.fio) if activist.fio else "Активист"
        else:
            user = message.from_user
            activist = await ProfileDAO(session).by_tg_id(user.id)
            if activist is None:
                activist = await ActivistsDAO(session).get_by_username(
                    user.username or ""
                )
            name = (
                first_last(activist.fio)
                if activist and activist.fio
                else user.full_name
            )

        if activist is None or not activist.birthday:
            await message.reply(
                f"Не знаю день рождения {html.escape(name)} — пусть заполнит "
                "анкету через <code>!обо мне</code>.",
                parse_mode="HTML",
            )
            return

        days = _lived_days(activist.birthday, moscow_today())
        born = activist.birthday.strftime("%d.%m.%Y")

    await message.answer(
        f"🎂 {html.escape(name)} живёт уже {days} "
        f"{plural(days, 'день', 'дня', 'дней')} (с {born})!",
        parse_mode="HTML",
    )


async def _anatoly_tag(session) -> str:
    """Тег Анатолия Козьмина из базы актива. Хардкод — запасной."""
    rows = (await session.execute(select(Activists))).scalars().all()
    for activist in rows:
        words = set((activist.fio or "").casefold().split())
        if {"анатолий", "козьмин"} <= words:
            tag = (activist.tg_username or "").strip().lstrip("@")
            if tag:
                return tag
    return "yanejettt"


@random_router.message(F.text.startswith('!тагил'))
async def tagil_cmd(message: Message):
    # Секретная команда: в !помощь её нет — и не добавляй.
    async with async_session_maker() as session:
        tag = await _anatoly_tag(session)
    await message.answer(f"@{tag}, ПОШЛИ РАБОТАТЬ В ПОНЕДЕЛЬНИК! ТАГИИИИЛ!")


_MAKAN_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_MAKAN_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAKAN_CAPTION = "Я говорю Macan, вы говорите ..."


def _makan_photo() -> Path | None:
    for ext in _MAKAN_EXTS:
        candidate = _MAKAN_ASSETS / f"macan{ext}"
        if candidate.is_file():
            return candidate
    return None


@random_router.message(F.text.startswith('!ботбрат'))
async def makan_cmd(message: Message):
    photo = _makan_photo()
    if photo is None:
        await message.reply("Фотка Макана потерялась — позовите Егора.")
        return
    await message.answer_photo(
        FSInputFile(photo), caption=MAKAN_CAPTION
    )





from aiogram import Router, F
from aiogram.types import Message

help_router = Router()

HELP_TEXT = """<b>Команды бота</b>

<b>Случайный активист</b>
<code>!кто</code> [текст] — случайный человек делает что-то
<code>!сантехник дня</code> — сантехник дня (раз в сутки)
<code>!ебанат дня</code> — ебанат дня

<b>Цитаты</b>
<code>!цитата</code> — ответом на сообщение, чтобы сохранить цитату
<code>!мудрость</code> — случайная цитата из архива

<b>Разное</b>
<code>!вероятность</code> [событие] — вероятность события
<code>!подскажи</code> — да, нет или забей

<code>!помощь</code> — этот список"""


@help_router.message(F.text == "!помощь")
async def help_cmd(message: Message):
    await message.answer(HELP_TEXT, parse_mode="HTML")

import random
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command


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





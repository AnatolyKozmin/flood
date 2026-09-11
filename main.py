import os
import asyncio
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from handlers.command_help import help_router
from handlers.command_mafia import mafia_router
from handlers.command_people import people_router
from handlers.command_quotes import quotes_router
from handlers.command_with_random import random_router
from handlers.command_info import info_router
from handlers.command_tarot import tarot_router
from handlers.command_top import top_router
from handlers.command_profile import profile_router
from handlers.command_all import all_router
from handlers.command_quotes_top import quotes_top_router
from handlers.command_battle import battle_router
from handlers.command_duel import duel_router
from handlers.command_karma import karma_router
from handlers.command_admin import admin_router
from middlewares.message_counter import MessageCounterMiddleware, flush_stats
from middlewares.dead_mute import DeadMuteMiddleware
from middlewares.admin_promote import AdminPromoteMiddleware
from database.engine import init_db, close_db
from utils.duel_scheduler import duel_worker
# ДР: from utils.birthday import birthday_worker

load_dotenv()

bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher()

# Анкета в личке идёт первой: пока человек отвечает на вопросы, её
# FSM-хендлер должен ловить сообщения раньше остальных команд.
dp.include_router(profile_router)
dp.include_router(admin_router)
dp.include_router(help_router)
dp.include_router(people_router)
dp.include_router(random_router)
dp.include_router(quotes_router)
dp.include_router(mafia_router)
dp.include_router(info_router)
dp.include_router(tarot_router)
# Раньше top_router: «!топ цитат» и «!топ батл» должны разбираться
# до обычного !топ, иначе он посчитает «цитат» неизвестным периодом.
dp.include_router(quotes_top_router)
dp.include_router(battle_router)
dp.include_router(duel_router)
dp.include_router(karma_router)
dp.include_router(top_router)
dp.include_router(all_router)

# Мёртвые молчат: идёт первой, чтобы сообщения погибших не считались
# в статистику и не срабатывали командами, а просто удалялись.
dp.message.outer_middleware(DeadMuteMiddleware())

# Считает сообщения для !топ и !стата. outer — значит срабатывает раньше
# фильтров: считаются все сообщения, а не только те, что попали в команды.
dp.message.outer_middleware(MessageCounterMiddleware())

# Включает админку тем, кому её выдали по @тегу заранее (см.
# middlewares/admin_promote.py). Срабатывает на первом сообщении в личку.
dp.message.outer_middleware(AdminPromoteMiddleware())

async def main():
    await init_db()
    duel_task = asyncio.create_task(duel_worker(bot))
    # ДР: поздравления с днём рождения. Как включить — см. utils/birthday.py
    # ДР: birthday_task = asyncio.create_task(birthday_worker(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        duel_task.cancel()
        # ДР: birthday_task.cancel()
        await flush_stats()  # дописать счётчики, что не успели уйти в базу
        await close_db()
    

if __name__ == "__main__":
    asyncio.run(main())
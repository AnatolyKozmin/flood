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
from handlers.command_checklist import checklist_router
from handlers.command_all import all_router
from handlers.command_quotes_top import quotes_top_router
from handlers.command_battle import battle_router
from handlers.command_duel import duel_router
from handlers.command_karma import karma_router
from handlers.command_durak import durak_router
from handlers.command_candle import candle_router
from handlers.command_seabattle import seabattle_router
from handlers.command_kalik import kalik_router
from handlers.command_bar import bar_router
from handlers.command_meter import meter_router
from handlers.command_remind import remind_router
from handlers.command_mod import mod_router
from handlers.command_admin import admin_router
from handlers.command_tracker import setup_menu_button, tracker_router
from middlewares.message_counter import MessageCounterMiddleware, flush_stats
from middlewares.dead_mute import DeadMuteMiddleware
from middlewares.admin_promote import AdminPromoteMiddleware
from middlewares.dm_contacts import DMContactsMiddleware
from middlewares.quote_history import QuoteHistoryMiddleware, record_quote_message
from handlers.command_quote_chain import chain_router
from utils.quote_worker import quote_worker
from database.engine import init_db, close_db
from database.feature_dao import ensure_defaults
from utils.duel_scheduler import duel_worker
from utils.remind_worker import remind_worker
# ДР: from utils.birthday import birthday_worker

load_dotenv()

bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher()

# Сборка цитат срабатывает только вне FSM. Во время анкеты её хендлеры
# по-прежнему получают ответы раньше прочих команд/чек-листов.
dp.include_router(chain_router)
dp.include_router(profile_router)
# Фото в личке — к чек-листу «Найди того, кто…», если он у человека есть.
dp.include_router(checklist_router)
dp.include_router(admin_router)
dp.include_router(mod_router)
dp.include_router(help_router)
dp.include_router(tracker_router)
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
dp.include_router(durak_router)
dp.include_router(candle_router)
dp.include_router(seabattle_router)
dp.include_router(kalik_router)
dp.include_router(bar_router)
dp.include_router(meter_router)
dp.include_router(remind_router)
dp.include_router(top_router)
dp.include_router(all_router)

# Мёртвые молчат: идёт первой, чтобы сообщения погибших не считались
# в статистику и не срабатывали командами, а просто удалялись.
dp.message.outer_middleware(DeadMuteMiddleware())
dp.message.outer_middleware(QuoteHistoryMiddleware())


@dp.edited_message()
async def remember_edited_quote_message(message, raw_state=None):
    await record_quote_message(message, raw_state)

# Считает сообщения для !топ и !стата. outer — значит срабатывает раньше
# фильтров: считаются все сообщения, а не только те, что попали в команды.
dp.message.outer_middleware(MessageCounterMiddleware())

# Кто писал боту в личку — трекер найдёт его по тегу (чек-листы, рассылка).
dp.message.outer_middleware(DMContactsMiddleware())

# Включает админку тем, кому её выдали по @тегу заранее (см.
# middlewares/admin_promote.py). Срабатывает на первом сообщении в личку.
dp.message.outer_middleware(AdminPromoteMiddleware())

async def main():
    await init_db()
    await ensure_defaults()
    duel_task = asyncio.create_task(duel_worker(bot))
    remind_task = asyncio.create_task(remind_worker(bot))
    quotes_task = asyncio.create_task(quote_worker(bot))
    # ДР: поздравления с днём рождения. Как включить — см. utils/birthday.py
    # ДР: birthday_task = asyncio.create_task(birthday_worker(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await setup_menu_button(bot)
        await dp.start_polling(bot)
    finally:
        duel_task.cancel()
        remind_task.cancel()
        quotes_task.cancel()
        await asyncio.gather(duel_task, remind_task, quotes_task, return_exceptions=True)
        # ДР: birthday_task.cancel()
        await flush_stats()  # дописать счётчики, что не успели уйти в базу
        await close_db()
    

if __name__ == "__main__":
    asyncio.run(main())

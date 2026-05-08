import os
import asyncio
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from handlers.command_help import help_router
from handlers.command_mafia import mafia_router
from handlers.command_people import people_router
from handlers.command_quotes import quotes_router
from handlers.command_with_random import random_router
from database.engine import init_db, close_db

load_dotenv()

bot = Bot(token=os.getenv('TOKEN'))
dp = Dispatcher()

dp.include_router(help_router)
dp.include_router(people_router)
dp.include_router(random_router)
dp.include_router(quotes_router)
dp.include_router(mafia_router)

async def main():
    await init_db()    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await close_db()
    

if __name__ == "__main__":
    asyncio.run(main())
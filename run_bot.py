import asyncio
import subprocess 
from watchfiles import awatch 
import sys


async def run_bot():

    process = None 

    try: 
        if process: 
            process.terminate()
            await asyncio.get_event_loop().run_in_executor(None, process.wait)

        process = subprocess.Popen([sys.executable, 'main.py'])
        await asyncio.sleep(1)
        async for changes in awatch('.'):
            print('Изменения были обнраужены, идёт перезапуск')
            break 

    except Exception as E: 
        print('Error in bot.py')

    finally: 
        if process: 
            process.terminate()
            await asyncio.get_event_loop().run_in_executor(None, process.wait)


if __name__ == "__main__":
    asyncio.run(run_bot())
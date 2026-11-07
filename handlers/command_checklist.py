"""Чек-лист «Найди того, кто…»: приём фото в личке.

Лист с тремя фактами рассылает трекер (раздел «Чек-листы»), он же кладёт
строку в таблицу checklists. Человек присылает сюда фото: без подписи —
к первому пункту без фото, с подписью «1»/«2»/«3» — к этому пункту (замена).
Каждое фото уходит в группу проверки (CHECKLIST_CHAT_ID в .env) с ФИО,
номером пункта и самим фактом.

В группу Telegram пускает не больше ~20 сообщений в минуту: если фото шлют
все разом, пересылка идёт через очередь — при «подожди N секунд» (RetryAfter)
ждём и досылаем, фото не теряются (а в базе и в трекере они сразу).

Роутер стоит сразу после анкеты: пока человек заполняет анкету, его фото —
ответы на её вопросы, а не чек-лист (фильтр StateFilter(None)).
"""
import asyncio
import html
import json
import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import StateFilter
from aiogram.types import Message

from database.checklist_dao import SLOTS, ChecklistDAO
from database.engine import async_session_maker

logger = logging.getLogger(__name__)

checklist_router = Router()

# Альбом приходит пачкой сообщений одновременно — раскладываем по пунктам по очереди.
_locks: dict[int, asyncio.Lock] = {}

# Пересылка в группу проверки: (chat_id, file_id, подпись).
_outbox: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
MAX_TRIES = 5


async def _forward_worker(bot: Bot) -> None:
    while True:
        chat_id, file_id, caption = await _outbox.get()
        for _ in range(MAX_TRIES):
            try:
                await bot.send_photo(chat_id, file_id, caption=caption, parse_mode="HTML")
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramAPIError as exc:
                logger.warning("Чек-лист: не переслали фото в группу: %s", exc)
                break
            except Exception as exc:  # сеть моргнула — пробуем ещё раз
                logger.warning("Чек-лист: ошибка пересылки: %s", exc)
                await asyncio.sleep(3)
        _outbox.task_done()


def _enqueue(bot: Bot, chat_id: int, file_id: str, caption: str) -> None:
    global _outbox, _worker
    if _outbox is None:
        _outbox = asyncio.Queue()
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_forward_worker(bot))
    _outbox.put_nowait((chat_id, file_id, caption))


def _review_chat_id() -> int | None:
    raw = os.getenv("CHECKLIST_CHAT_ID", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        logger.error("CHECKLIST_CHAT_ID должен быть числом, а не %r", raw)
        return None


def _slot_from_caption(caption: str | None) -> int | None:
    match = re.fullmatch(r"\s*(?:№|#|пункт)?\s*([123])\s*\.?\s*", caption or "", flags=re.I)
    return int(match.group(1)) if match else None


@checklist_router.message(F.chat.type == "private", F.photo, StateFilter(None))
async def on_checklist_photo(message: Message, bot: Bot):
    user_id = message.from_user.id
    lock = _locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        async with async_session_maker() as session:
            dao = ChecklistDAO(session)
            row = await dao.get(user_id)
            if row is None:
                raise SkipHandler()  # чек-листа нет — фото не наше
            facts = json.loads(row.facts)
            photos = dao.photos(row)
            slot = _slot_from_caption(message.caption)
            if slot is None:
                slot = next((s for s in SLOTS if not photos[s - 1]), None)
            if slot is None:
                await message.reply(
                    "Все три фото уже у нас ✅\n"
                    "Чтобы заменить одно, пришли новое фото с подписью «1», «2» или «3»."
                )
                return
            replaced = bool(photos[slot - 1])
            file_id = message.photo[-1].file_id  # последнее — самое крупное
            await dao.set_photo(row, slot, file_id)
            photos[slot - 1] = file_id

    fact = facts[slot - 1]
    chat_id = _review_chat_id()
    if chat_id is not None:
        tag = f" (@{html.escape(row.username)})" if row.username else ""
        caption = (
            f"<b>{html.escape(row.full_name)}</b>{tag}\n"
            f"Чек-лист №{row.number} · фото {slot}/3{' · замена' if replaced else ''}\n"
            f"Сфоткайся с {html.escape(fact)}"
        )
        _enqueue(bot, chat_id, file_id, caption)

    done = sum(1 for p in photos if p)
    if done == len(SLOTS):
        text = f"Фото к пункту {slot} {'заменено' if replaced else 'принято'} ✅\nВсе три есть — чек-лист выполнен! 🎉"
    else:
        nxt = next(s for s in SLOTS if not photos[s - 1])
        text = (
            f"Фото к пункту {slot} {'заменено' if replaced else 'принято'} ✅ ({done}/3)\n"
            f"Дальше пункт {nxt}: сфоткайся с {html.escape(facts[nxt - 1])}"
        )
    await message.reply(text, parse_mode="HTML")

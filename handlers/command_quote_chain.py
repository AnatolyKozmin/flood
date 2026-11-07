"""/цитата: цепочка ответов; в личке — ручная сборка из пересланных сообщений."""
import asyncio
import time

from aiogram import F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.engine import async_session_maker
from database.quote_media import (
    QuoteDraft, QuotePending, collect_chain, message_part, remember_message, validate_parts,
)
from database.feature_dao import feature_on, feature_off_text
from utils.quote_worker import enqueue, show_content

chain_router = Router()
chain_router.message.filter(StateFilter(None))
_draft_lock = asyncio.Lock()
_views = {}


class ChainCommand(BaseFilter):
    async def __call__(self, message: Message):
        word = (message.text or "").strip().split(maxsplit=1)
        if not word:
            return False
        first = word[0].casefold()
        command, _, target = first.partition("@")
        if command not in {"/цитата", "/quote"}:
            return False
        return not target or target == (await message.bot.me()).username.casefold()


def draft_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Собрать цитату", callback_data="qm:done"),
        InlineKeyboardButton(text="Отмена", callback_data="qm:cancel"),
    ]])


async def queue_message(message, parts, user_id=None, source_key=None):
    try:
        qid = await enqueue(parts, message.chat.id, user_id or message.from_user.id,
                            source_key or f"{message.chat.id}:{message.message_id}", message.message_thread_id)
    except ValueError as error:
        await message.answer(str(error))
        return False
    await message.answer(f"Цитата #{qid} в очереди. Отправлю результат сюда.")
    return True


@chain_router.message(ChainCommand())
async def chain_quote(message: Message):
    if not await feature_on("quotes"):
        await message.answer(await feature_off_text("quotes"))
        return
    if message.reply_to_message:
        async with async_session_maker() as session:
            await remember_message(session, message)
            await session.commit()
            parts, complete = await collect_chain(session, message.chat.id, message.reply_to_message.message_id)
            try:
                validate_parts(parts)
            except ValueError as error:
                await message.answer(str(error))
                return
            if not complete:
                pending = QuotePending(user_id=message.from_user.id, chat_id=message.chat.id,
                                       message_id=message.message_id, parts=parts, created_at=int(time.time()))
                session.add(pending)
                await session.commit()
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=f"Сохранить доступные сообщения ({len(parts)})",
                                         callback_data=f"qm:partial:{pending.id}")]])
                await message.answer("Полная цепочка недоступна: бот не сохранил одно из предыдущих звеньев. "
                                     "Можно сохранить доступный фрагмент или переслать всю цепочку в личку боту.", reply_markup=keyboard)
                return
        await queue_message(message, parts)
        return
    if message.chat.type != "private":
        await message.answer("Ответь /цитата на последнее сообщение цепочки. Для ручной сборки напиши /цитата в личке боту.")
        return
    async with _draft_lock, async_session_maker() as session:
        draft = await session.get(QuoteDraft, message.from_user.id)
        if draft and draft.updated_at > time.time() - 86400:
            await message.answer(f"Уже собираем цитату: сообщений {len(draft.parts)}. Пришли ещё или нажми «Собрать».", reply_markup=draft_keyboard())
            return
        if draft:
            await session.delete(draft)
            await session.flush()
        session.add(QuoteDraft(user_id=message.from_user.id, parts=[], updated_at=int(time.time())))
        await session.commit()
    await message.answer("Перешли сообщения в нужном порядке — от первого до последнего. "
                         "Можно прислать текст, фото, видео, GIF и кружки. Затем нажми «Собрать цитату». "
                         "Сохранённая цитата попадёт в общую библиотеку мудростей. "
                         "Автором цитаты будет автор первого сообщения. Если Telegram скроет автора пересылки, "
                         "в подписи останется имя, которое передал Telegram.", reply_markup=draft_keyboard())


class DraftActive(BaseFilter):
    async def __call__(self, message: Message):
        if message.chat.type != "private" or not message.from_user:
            return False
        # Команды должны работать и во время ручной сборки.
        if (message.text or "").lstrip().startswith(("/", "!")):
            return False
        async with async_session_maker() as session:
            draft = await session.get(QuoteDraft, message.from_user.id)
            return bool(draft and draft.updated_at > time.time() - 86400)


@chain_router.message(DraftActive())
async def add_to_draft(message: Message):
    if not await feature_on("quotes"):
        await message.answer(await feature_off_text("quotes"))
        return
    part = message_part(message)
    async with _draft_lock, async_session_maker() as session:
        draft = await session.get(QuoteDraft, message.from_user.id)
        if not draft:
            return
        parts = list(draft.parts)
        if any(p["chat_id"] == part["chat_id"] and p["message_id"] == part["message_id"] for p in parts):
            return
        parts.append(part)
        try:
            validate_parts(parts)
        except ValueError as error:
            await message.answer(str(error))
            return
        # Updates альбома могут обрабатываться параллельно: порядок определяется ID.
        draft.parts = sorted(parts, key=lambda p: p["message_id"])
        draft.updated_at = int(time.time())
        await session.commit()
    await message.answer(f"Добавлено сообщений: {len(parts)}.", reply_markup=draft_keyboard())


@chain_router.callback_query(F.data.in_({"qm:done", "qm:cancel"}))
async def finish_draft(call: CallbackQuery):
    if not isinstance(call.message, Message) or call.message.chat.type != "private" or call.message.chat.id != call.from_user.id:
        await call.answer("Сборка доступна только в своей личке с ботом.", show_alert=True)
        return
    await call.answer()
    async with _draft_lock, async_session_maker() as session:
        draft = await session.get(QuoteDraft, call.from_user.id)
        if not draft or draft.updated_at < time.time() - 86400:
            await call.message.answer("Нет активной сборки. Напиши /цитата.")
            return
        if call.data == "qm:done":
            if not await feature_on("quotes"):
                await call.message.answer(await feature_off_text("quotes"))
                return
            ok = await queue_message(call.message, draft.parts, call.from_user.id,
                                     f"draft:{call.from_user.id}:{draft.parts[0]['message_id']}" if draft.parts else None)
            if not ok:
                return
        else:
            await call.message.answer("Сборка отменена.")
        await session.delete(draft)
        await session.commit()


@chain_router.callback_query(F.data.startswith("qm:partial:"))
async def confirm_partial(call: CallbackQuery):
    if not isinstance(call.message, Message):
        await call.answer()
        return
    try:
        pid = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        await call.answer()
        return
    async with async_session_maker() as session:
        pending = await session.get(QuotePending, pid)
    if not pending or pending.user_id != call.from_user.id or pending.chat_id != call.message.chat.id or pending.created_at < time.time() - 86400:
        await call.answer("Кнопка доступна автору команды в течение суток.", show_alert=True)
        return
    await call.answer()
    if not await feature_on("quotes"):
        await call.message.answer(await feature_off_text("quotes"))
        return
    await queue_message(call.message, pending.parts, call.from_user.id,
                        f"{pending.chat_id}:{pending.message_id}")


@chain_router.callback_query(F.data.startswith("qm:view:"))
async def view_quote(call: CallbackQuery):
    if not isinstance(call.message, Message):
        await call.answer()
        return
    try:
        qid = int(call.data.rsplit(":", 1)[1])
    except ValueError:
        await call.answer()
        return
    if not await feature_on("quotes"):
        await call.answer("Цитаты сейчас отключены.", show_alert=True)
        return
    if _views.get(call.from_user.id, 0) > time.monotonic() - 10:
        await call.answer("Подожди несколько секунд.")
        return
    _views[call.from_user.id] = time.monotonic()
    if len(_views) > 2000:
        cutoff = time.monotonic() - 10
        for uid in list(_views):
            if _views[uid] < cutoff:
                del _views[uid]
    await call.answer()
    if not await show_content(call.bot, call.message.chat.id, qid, call.message.message_thread_id):
        await call.message.answer("Цитата не найдена.")

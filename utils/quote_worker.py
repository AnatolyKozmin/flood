"""Один последовательный worker, задания переживают перезапуск бота."""
import asyncio
import logging
import hashlib
import secrets
import tempfile
import time
from pathlib import Path

from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import func, select, update

from database.engine import async_session_maker
from database.models import Quotes
from database.quote_media import QuoteContent, QuoteJob, prune_history, validate_parts
from database.quotes_extra_dao import VotesDAO
from utils.helpers import msk_now
from utils.names import quote_name, quote_user_id
from utils.telegram_avatar import snapshot_avatar, avatar_at, _profile_avatar
from utils.media_quote import prepare_media, text_cards

logger = logging.getLogger(__name__)
wakeup = asyncio.Event()
_enqueue_lock = asyncio.Lock()


def content_summary(parts):
    labels = {"photo": "[Фото]", "video": "[Видео]", "video_note": "[Кружок]", "animation": "[GIF]"}
    return " ".join((labels.get(p["kind"], "") + " " + p["text"]).strip() for p in parts)


async def enqueue(parts, chat_id, user_id, source_key, thread_id=None):
    validate_parts(parts)
    async with _enqueue_lock, async_session_maker() as session:
        existing = (await session.execute(select(QuoteJob).where(QuoteJob.source_key == source_key))).scalars().first()
        if existing:
            return existing.quote_id
        pending = (await session.execute(select(func.count()).select_from(QuoteJob).where(
            QuoteJob.state.in_(["pending", "running"]), QuoteJob.user_id == user_id))).scalar_one()
        if pending >= 2:
            raise ValueError("У тебя уже две цитаты в очереди. Дождись их обработки.")
        total = (await session.execute(select(func.count()).select_from(QuoteJob).where(
            QuoteJob.state.in_(["pending", "running"])))).scalar_one()
        if total >= 20:
            raise ValueError("Очередь заполнена. Попробуй чуть позже.")
        root = parts[0]
        quote = Quotes(tg_id=root["tg_id"], tg_username=root["tg_username"],
                       text_of_quotes=content_summary(parts), created_at=msk_now())
        session.add(quote)
        await session.flush()
        session.add(QuoteContent(quote_id=quote.id, parts=parts, outputs=[]))
        session.add(QuoteJob(quote_id=quote.id, chat_id=chat_id, user_id=user_id,
                             thread_id=thread_id, source_key=source_key, state="pending"))
        await session.commit()
        wakeup.set()
        return quote.id


def quote_buttons(quote_id: int, votes: tuple[int, int] = (0, 0), *, display_id: str | None = None):
    display_id = display_id or secrets.token_hex(8)
    likes, dislikes = votes
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"❤️ {likes}", callback_data=f"qr:{quote_id}:{display_id}:like"),
        InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"qr:{quote_id}:{display_id}:dislike"),
    ]])


def job_display_id(job, key: str) -> str:
    # Возобновление очереди сохраняет тот же показ для всех частей.
    return f"j{job.id}-{key[:10]}" if job else "view"


def render_key(author, avatar):
    digest = hashlib.sha256(author.encode("utf-8"))
    if avatar is not None:
        digest.update(avatar.convert("RGB").tobytes())
    return digest.hexdigest()


async def send_file(bot, chat_id, kind, file, thread_id=None, reply_markup=None):
    kwargs = dict(chat_id=chat_id, message_thread_id=thread_id)
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if kind == "photo":
        message = await bot.send_photo(photo=file, **kwargs)
        return message.photo[-1].file_id
    if kind == "animation":
        message = await bot.send_animation(animation=file, **kwargs)
        return message.animation.file_id
    message = await bot.send_video(video=file, supports_streaming=True, **kwargs)
    return message.video.file_id


async def send_with_retry(bot, chat_id, kind, file, thread_id=None, reply_markup=None):
    for attempt in range(3):
        try:
            return await send_file(bot, chat_id, kind, file, thread_id, reply_markup)
        except TelegramRetryAfter as error:
            if attempt == 2:
                raise
            await asyncio.sleep(error.retry_after + 1)


async def show_content(bot, chat_id, quote_id, thread_id=None, *, fresh_vote=True):
    async with async_session_maker() as session:
        content = await session.get(QuoteContent, quote_id)
        quote = await session.get(Quotes, quote_id)
        job = (await session.execute(select(QuoteJob).where(QuoteJob.quote_id == quote_id))).scalars().first()
        votes = await VotesDAO(session).rating(quote_id)
    if content is None or quote is None:
        return False
    if job and job.state != "done":
        await bot.send_message(chat_id, "Цитата ещё обрабатывается или не была обработана. Сохрани её повторно, если бот сообщил об ошибке.", message_thread_id=thread_id)
        return True
    # Готовое медиа переиспользуется, пока не изменились имя/историческое фото.
    # Исправление ФИО должно отражаться и в уже сохранённых медиа-цитатах.
    from utils.quote_render import quote_author, quote_avatar
    author, avatar = await asyncio.gather(quote_author(quote), quote_avatar(bot, quote))
    if job and content.render_key != render_key(author, avatar):
        async with _enqueue_lock, async_session_maker() as session:
            pending = (await session.execute(select(func.count()).select_from(QuoteJob).where(
                QuoteJob.state.in_(["pending", "running"])
            ))).scalar_one()
            if pending >= 20:
                await bot.send_message(chat_id, "Очередь обновления цитат заполнена. Повтори просмотр позже.", message_thread_id=thread_id)
                return True
            current = await session.get(QuoteJob, job.id)
            if current.state == "done":
                current.state = "pending"
                current.chat_id, current.thread_id = chat_id, thread_id
                row = await session.get(QuoteContent, quote_id)
                row.outputs = []
                await session.commit()
        wakeup.set()
        await bot.send_message(chat_id, "Обновляю оформление цитаты. Повтори просмотр после обработки.", message_thread_id=thread_id)
        return True
    display_id = None if fresh_vote else job_display_id(job, content.render_key)
    keyboard = quote_buttons(quote_id, votes, display_id=display_id)
    for output in content.outputs:
        await send_with_retry(bot, chat_id, output["kind"], output["file_id"], thread_id,
                              keyboard)
    return True


async def process_job(bot, job):
    async with async_session_maker() as session:
        quote = await session.get(Quotes, job.quote_id)
        content = await session.get(QuoteContent, job.quote_id)
        parts, outputs = content.parts, list(content.outputs)
        votes = await VotesDAO(session).rating(job.quote_id)
        uid = await quote_user_id(session, quote.tg_id, quote.tg_username)
        author = await quote_name(session, uid, quote.tg_username)
    # Запись создания немного раньше снятия фото: для новой цитаты используем
    # сам снимок, для возобновлённого задания — исторический подбор.
    saved_at = quote.created_at
    if uid and not content.render_key:
        await snapshot_avatar(bot, uid)
        saved_at = msk_now()
    avatar = await avatar_at(bot, uid, saved_at) if uid else await _profile_avatar(bot, None, quote.tg_username)
    key = render_key(author, avatar)
    async with async_session_maker() as session:
        row = await session.get(QuoteContent, job.quote_id)
        if not row.render_key:
            saved_quote = await session.get(Quotes, job.quote_id)
            saved_quote.created_at = saved_at or msk_now()
        row.render_key = key
        await session.commit()
    output_index = 0
    keyboard = quote_buttons(job.quote_id, votes, display_id=job_display_id(job, key))

    async def emit(kind, file):
        nonlocal output_index, outputs
        if output_index >= len(outputs):
            fid = await send_with_retry(bot, job.chat_id, kind, file, job.thread_id,
                                       keyboard)
            outputs.append(dict(kind=kind, file_id=fid))
            async with async_session_maker() as session:
                row = await session.get(QuoteContent, job.quote_id)
                row.outputs = list(outputs)
                await session.commit()
        output_index += 1

    pending_text = []

    async def emit_text():
        if not pending_text:
            return
        cards = await asyncio.to_thread(text_cards, " ".join(pending_text), author, avatar)
        pending_text.clear()
        for card in cards:
            await emit("photo", BufferedInputFile(card.getvalue(), filename="quote.png"))

    for part in parts:
        if part["kind"] == "text":
            pending_text.append(part["text"])
            continue
        await emit_text()
        if output_index >= len(outputs):
            with tempfile.TemporaryDirectory(prefix="ik-media-quote-") as temporary:
                kind, path = await prepare_media(bot, part, Path(temporary), author, avatar)
                await emit(kind, FSInputFile(path))
        else:
            output_index += 1
        # Длинные подписи также сохраняем полностью, отдельными карточками.
        if part["text"]:
            pending_text.append(part["text"])
    await emit_text()
    async with async_session_maker() as session:
        row = await session.get(QuoteJob, job.id)
        row.state = "done"
        await session.commit()


async def quote_worker(bot):
    async with async_session_maker() as session:
        await session.execute(update(QuoteJob).where(QuoteJob.state == "running").values(state="pending"))
        await session.commit()
    last_prune = 0
    while True:
        try:
            async with async_session_maker() as session:
                if time.time() - last_prune > 3600:
                    await prune_history(session)
                    last_prune = time.time()
                job = (await session.execute(select(QuoteJob).where(QuoteJob.state == "pending").order_by(QuoteJob.id).limit(1))).scalars().first()
                if job:
                    job.state = "running"
                await session.commit()
            if not job:
                wakeup.clear()
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await process_job(bot, job)
            except Exception as error:
                logger.exception("Ошибка обработки цитаты %s", job.quote_id)
                async with async_session_maker() as session:
                    row = await session.get(QuoteJob, job.id)
                    row.state, row.error = "error", type(error).__name__
                    await session.commit()
                reason = str(error) if isinstance(error, ValueError) else "Не удалось загрузить или обработать медиа."
                try:
                    await bot.send_message(job.chat_id, f"Цитата #{job.quote_id}: {reason} Попробуй сохранить заново.", message_thread_id=job.thread_id)
                except Exception:
                    logger.exception("Не отправил сообщение об ошибке цитаты")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка очереди цитат")
            await asyncio.sleep(5)

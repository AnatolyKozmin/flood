import asyncio
import logging
import tempfile
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from database.dao import ActivistsDAO, QuotesDAO
from database.quotes_extra_dao import VotesDAO
from database.engine import async_session_maker
from utils.create_quote import render_quote_pages
from utils.helpers import first_last
from handlers.command_quotes_top import vote_kb
from utils.telegram_avatar import load_user_profile_avatar
from utils import voice_transcribe

logger = logging.getLogger(__name__)

VOICE_DISABLED_TEXT = (
    "В данный момент сервер загружен брифами к ЦТ, во избежании перегруза, "
    "расшифровка гс временно отключена. Проще говоря - хуярьте текстом"
)


class FirstWord(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().split(maxsplit=1)
        return bool(parts) and parts[0].casefold() == self.cmd


quotes_router = Router()
QUOTE_ALBUM_MAX = 10


async def _send_quote_pngs(message: Message, pngs: list, caption_html: str | None = None,
                          quote_id: int | None = None, votes: int = 0) -> None:
    if not pngs:
        return
    parse_mode = "HTML" if caption_html else None
    # Кнопка голосования (см. handlers/command_quotes_top.py). У альбомов
    # инлайн-клавиатур не бывает — для них кнопка уходит отдельным сообщением.
    kb = vote_kb(quote_id, votes) if quote_id is not None else None
    if len(pngs) == 1:
        pngs[0].seek(0)
        await message.answer_photo(
            BufferedInputFile(pngs[0].read(), filename="quote.png"),
            caption=caption_html,
            parse_mode=parse_mode,
            reply_markup=kb,
        )
        return
    for chunk_start in range(0, len(pngs), QUOTE_ALBUM_MAX):
        chunk = pngs[chunk_start : chunk_start + QUOTE_ALBUM_MAX]
        media: list[InputMediaPhoto] = []
        for local_i, bf in enumerate(chunk):
            global_i = chunk_start + local_i
            bf.seek(0)
            cap = caption_html if global_i == 0 else None
            media.append(InputMediaPhoto(
                media=BufferedInputFile(bf.read(), filename=f"quote_{global_i + 1:03d}.png"),
                caption=cap,
                parse_mode=parse_mode if cap else None,
            ))
        await message.answer_media_group(media)
    if kb is not None:
        await message.answer("Понравилась цитата?", reply_markup=kb)


def _quoted_text(reply: Message) -> str | None:
    parts = [t for src in (reply.text, reply.caption) if src and (t := src.strip())]
    return "\n\n".join(parts) if parts else None


def _display_author(user) -> str:
    return user.username or user.full_name or str(user.id)


def _forwarded_author(replied: Message) -> tuple[str, str] | None:
    """Автор пересланного сообщения: (tg_id, tg_username).

    У пересланного сообщения from_user — это пересылальщик (человек №1),
    а настоящий автор (человек №2) лежит в forward_origin (Bot API 7+)
    или в legacy-полях forward_from / forward_sender_name / forward_from_chat.
    Скрытого автора (без id) и канал возвращаем как есть: id пустой,
    вместо тега — имя/название, аватарки тогда не будет.
    """
    origin = getattr(replied, "forward_origin", None)
    if origin is not None:
        otype = getattr(origin, "type", None)
        if otype == "user":
            user = getattr(origin, "sender_user", None)
            if user is not None:
                return str(user.id), _display_author(user)
        elif otype == "hidden_user":
            name = getattr(origin, "sender_user_name", None)
            if name:
                return "", name
        elif otype in ("chat", "channel"):
            chat = getattr(origin, "chat", None)
            if chat is not None:
                uname = getattr(chat, "username", None) or getattr(chat, "title", "") or ""
                return str(getattr(chat, "id", "") or ""), uname

    user = getattr(replied, "forward_from", None)
    if user is not None:
        return str(user.id), _display_author(user)
    name = getattr(replied, "forward_sender_name", None)
    if name:
        return "", name
    chat = getattr(replied, "forward_from_chat", None)
    if chat is not None:
        uname = getattr(chat, "username", None) or getattr(chat, "title", "") or ""
        return str(getattr(chat, "id", "") or ""), uname
    return None


def _voice_source(msg: Message) -> tuple[str, int, str] | None:
    """Файл голосового/кружка: (file_id, длительность, подпись).
    Ничего голосового — None."""
    if msg.voice is not None:
        return msg.voice.file_id, msg.voice.duration or 0, "голосовое"
    if msg.video_note is not None:
        return msg.video_note.file_id, msg.video_note.duration or 0, "кружок"
    return None


async def _transcribe_file_id(bot, file_id: str, label: str) -> tuple[str | None, str | None]:
    """Скачать файл телеграма и распознать. Возвращает (текст, ошибка):
    одно всегда None. Лимит длительности проверяют вызывающие до вызова."""
    tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp.close()
    try:
        tg_file = await bot.get_file(file_id)
        await bot.download_file(tg_file.file_path, Path(tmp.name))
        text = await voice_transcribe.transcribe(tmp.name)
    except Exception:
        logger.exception("Транскрибация %s не удалась", label)
        return None, "Не вышло распознать — попробуй ещё раз."
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
    if not text:
        return None, "Ничего не расслышал — там точно есть речь?"
    return text, None


async def _build_and_send(message: Message, tg_id: str, tg_username: str,
                          text_body: str, caption_html: str | None = None) -> None:
    try:
        avatar_uid = int(tg_id)
    except (ValueError, TypeError):
        avatar_uid = None

    async def _avatar():
        if avatar_uid is None:
            return None
        return await load_user_profile_avatar(message.bot, avatar_uid)

    async def _db_lookup():
        async with async_session_maker() as session:
            return await ActivistsDAO(session).get_by_username(tg_username)

    activist, avatar = await asyncio.gather(_db_lookup(), _avatar())

    image_author = first_last(activist.fio) if activist else tg_username
    loop = asyncio.get_event_loop()
    try:
        pngs = await loop.run_in_executor(
            None, lambda: render_quote_pages(text_body, image_author, avatar=avatar)
        )
    except ValueError:
        # Сначала рисуем, потом сохраняем: слишком длинное не пишем в базу.
        await message.reply(
            "Цитата слишком длинная — не влезает на одну картинку даже "
            "мелким шрифтом. Ужми текст.")
        return

    async with async_session_maker() as session:
        quote = await QuotesDAO(session).create(
            tg_id=tg_id, tg_username=tg_username, text_of_quotes=text_body
        )
    await _send_quote_pngs(message, pngs, caption_html=caption_html, quote_id=quote.id)


@quotes_router.message(FirstWord("!цитата"))
async def save_quote(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь этой командой на сообщение, которое нужно сохранить как цитату.")
        return

    replied = message.reply_to_message
    text_body = _quoted_text(replied)
    caption_html: str | None = None

    if not text_body:
        # Текста нет — может, это голосовое или кружок? Распознаём.
        source = _voice_source(replied)
        if source is None:
            await message.reply("В этом сообщении нет текста (ни подписи). Ответь !цитата на сообщение с текстом.")
            return
        if not voice_transcribe.ENABLED:
            await message.reply(VOICE_DISABLED_TEXT)
            return
        file_id, duration, label = source
        if duration > voice_transcribe.MAX_SEC:
            await message.reply(
                f"{label.capitalize()} длинное ({duration} сек, "
                f"максимум {voice_transcribe.MAX_SEC}) — "
                "пришли кусок покороче."
            )
            return
        status = await message.reply(f"🎙 Распознаю {label} ({duration} сек)…")
        try:
            text_body, error = await _transcribe_file_id(
                message.bot, file_id, label)
        finally:
            try:
                await status.delete()
            except Exception:
                pass
        if error is not None or not text_body:
            await message.reply(error or "Ничего не расслышал.")
            return
        # Метка прямо в тексте: цитата из расшифровки видна везде —
        # в !мудрость, топе и батле, а не только в подписи под картинкой.
        text_body = f"🎙 {text_body}"
        caption_html = f"🎙 <i>{label}, {duration} сек</i>"

    author = replied.from_user
    if not author and _forwarded_author(replied) is None:
        await message.reply("Не могу определить автора цитаты.")
        return

    forwarded = _forwarded_author(replied)
    if forwarded is not None:
        # Пересланное: цитата человека №2, а не пересылальщика.
        tg_id, tg_username = forwarded
    else:
        tg_id = str(author.id)
        tg_username = _display_author(author)

    await _build_and_send(message, tg_id, tg_username, text_body, caption_html)


@quotes_router.message(F.voice | F.video_note)
async def save_voice_quote(message: Message):
    """Голосовое или кружок с подписью «!цитата»: распознать и в цитату.
    Чужие голосовые без подписи молча пропускаем."""
    caption = (message.caption or "").strip()
    if not caption or caption.split(maxsplit=1)[0].casefold() != "!цитата":
        return
    if not voice_transcribe.ENABLED:
        await message.reply(VOICE_DISABLED_TEXT)
        return

    source = _voice_source(message)
    if source is None:  # перестраховка, фильтр уже отобрал
        return
    file_id, duration, label = source
    if duration > voice_transcribe.MAX_SEC:
        await message.reply(
            f"{label.capitalize()} длинное ({duration} сек, "
            f"максимум {voice_transcribe.MAX_SEC}) — пришли кусок покороче."
        )
        return

    author = message.from_user
    if author is None:
        await message.reply("Не могу определить автора цитаты.")
        return

    status = await message.reply(f"🎙 Распознаю {label} ({duration} сек)…")
    try:
        text_body, error = await _transcribe_file_id(
            message.bot, file_id, label)
    finally:
        try:
            await status.delete()
        except Exception:
            pass
    if error is not None or not text_body:
        await message.reply(error or "Ничего не расслышал.")
        return

    await _build_and_send(
        message, str(author.id), _display_author(author), f"🎙 {text_body}",
        f"🎙 <i>{label}, {duration} сек</i>",
    )


@quotes_router.message(FirstWord("!мудрость"))
async def random_wisdom(message: Message):
    async with async_session_maker() as session:
        q = await QuotesDAO(session).get_random_quote()
        votes = await VotesDAO(session).count(q.id) if q else 0

    if not q:
        await message.reply("Пока нет ни одной цитаты. Сначала кто-нибудь использует !цитата 🙂")
        return

    tg_username_clean = q.tg_username.lstrip("@") if q.tg_username else None
    try:
        uid = int(q.tg_id)
    except (ValueError, TypeError):
        uid = None

    async def _get_activist():
        if not tg_username_clean:
            return None
        async with async_session_maker() as session:
            return await ActivistsDAO(session).get_by_username(tg_username_clean)

    async def _get_avatar():
        if uid is None:
            return None
        return await load_user_profile_avatar(message.bot, uid)

    activist, avatar = await asyncio.gather(_get_activist(), _get_avatar())

    image_author = first_last(activist.fio) if activist else (tg_username_clean or "чат")
    loop = asyncio.get_event_loop()
    try:
        pngs = await loop.run_in_executor(
            None, lambda: render_quote_pages(q.text_of_quotes, image_author, avatar=avatar)
        )
    except ValueError:
        await message.reply(
            "Эта цитата слишком длинная — не влезает на одну картинку даже "
            "мелким шрифтом.")
        return
    await _send_quote_pngs(message, pngs, quote_id=q.id, votes=votes)

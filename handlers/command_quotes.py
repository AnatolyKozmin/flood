import asyncio

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from database.dao import ActivistsDAO, QuotesDAO
from database.quotes_extra_dao import VotesDAO
from database.engine import async_session_maker
from utils.create_quote import render_quote_pages
from utils.helpers import first_last
from handlers.command_quotes_top import vote_kb
from utils.telegram_avatar import load_user_profile_avatar


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


@quotes_router.message(FirstWord("!цитата"))
async def save_quote(message: Message):
    if not message.reply_to_message:
        await message.reply("Ответь этой командой на сообщение, которое нужно сохранить как цитату.")
        return

    replied = message.reply_to_message
    text_body = _quoted_text(replied)
    if not text_body:
        await message.reply("В этом сообщении нет текста (ни подписи). Ответь !цитата на сообщение с текстом.")
        return

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

    try:
        avatar_uid = int(tg_id)
    except (ValueError, TypeError):
        avatar_uid = None

    async def _avatar():
        if avatar_uid is None:
            return None
        return await load_user_profile_avatar(message.bot, avatar_uid)

    async def _db_ops():
        async with async_session_maker() as session:
            activist = await ActivistsDAO(session).get_by_username(tg_username)
            quote = await QuotesDAO(session).create(
                tg_id=tg_id, tg_username=tg_username, text_of_quotes=text_body
            )
        return activist, quote

    (activist, quote), avatar = await asyncio.gather(_db_ops(), _avatar())

    image_author = first_last(activist.fio) if activist else tg_username
    loop = asyncio.get_event_loop()
    pngs = await loop.run_in_executor(
        None, lambda: render_quote_pages(text_body, image_author, avatar=avatar)
    )
    await _send_quote_pngs(message, pngs, quote_id=quote.id)


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
    pngs = await loop.run_in_executor(
        None, lambda: render_quote_pages(q.text_of_quotes, image_author, avatar=avatar)
    )
    await _send_quote_pngs(message, pngs, quote_id=q.id, votes=votes)

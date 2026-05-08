import asyncio

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from database.dao import ActivistsDAO, QuotesDAO
from database.engine import async_session_maker
from utils.create_quote import render_quote_pages
from utils.helpers import first_last
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


async def _send_quote_pngs(message: Message, pngs: list, caption_html: str | None = None) -> None:
    if not pngs:
        return
    parse_mode = "HTML" if caption_html else None
    if len(pngs) == 1:
        pngs[0].seek(0)
        await message.answer_photo(
            BufferedInputFile(pngs[0].read(), filename="quote.png"),
            caption=caption_html,
            parse_mode=parse_mode,
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


def _quoted_text(reply: Message) -> str | None:
    parts = [t for src in (reply.text, reply.caption) if src and (t := src.strip())]
    return "\n\n".join(parts) if parts else None


def _display_author(user) -> str:
    return user.username or user.full_name or str(user.id)


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
    if not author:
        await message.reply("Не могу определить автора цитаты.")
        return

    tg_id = str(author.id)
    tg_username = _display_author(author)

    async def _db_ops():
        async with async_session_maker() as session:
            activist = await ActivistsDAO(session).get_by_username(tg_username)
            quote = await QuotesDAO(session).create(
                tg_id=tg_id, tg_username=tg_username, text_of_quotes=text_body
            )
        return activist, quote

    (activist, quote), avatar = await asyncio.gather(
        _db_ops(),
        load_user_profile_avatar(message.bot, author.id),
    )

    image_author = first_last(activist.fio) if activist else tg_username
    loop = asyncio.get_event_loop()
    pngs = await loop.run_in_executor(
        None, lambda: render_quote_pages(text_body, image_author, avatar=avatar)
    )
    await _send_quote_pngs(message, pngs)


@quotes_router.message(FirstWord("!мудрость"))
async def random_wisdom(message: Message):
    async with async_session_maker() as session:
        q = await QuotesDAO(session).get_random_quote()

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
    await _send_quote_pngs(message, pngs)

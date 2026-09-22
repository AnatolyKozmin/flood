"""Отрисовка цитат для топа и батла.

Логика «кто автор + какая аватарка» повторяет то, что делает !мудрость в
handlers/command_quotes.py, — вынесена сюда, чтобы топ и батл не дублировали
её ещё дважды.
"""
import io

from PIL import Image
from aiogram import Bot

from database.engine import async_session_maker
from utils.create_quote import render_quote_pages
from utils.names import fio_name
from utils.telegram_avatar import load_user_profile_avatar

# Отступ между двумя цитатами на картинке батла.
_GAP = 18


async def quote_author(quote) -> str:
    """ФИО из базы актива (сначала по tg_id — скрытые профили тоже),
    иначе @тег, иначе «чат»."""
    try:
        uid = int(quote.tg_id)
    except (TypeError, ValueError):
        uid = None
    async with async_session_maker() as session:
        name = await fio_name(session, uid, quote.tg_username)
    if name:
        return name
    tag = (quote.tg_username or "").lstrip("@")
    return tag or "чат"


async def quote_avatar(bot: Bot, quote) -> Image.Image | None:
    try:
        uid = int(quote.tg_id)
    except (TypeError, ValueError):
        return None
    return await load_user_profile_avatar(bot, uid)


async def render_one(bot: Bot, quote, loop) -> io.BytesIO:
    """Одна карточка PNG. Длинную цитату ужимаем мелким кеглем в одну
    страницу: в топе и батле многостраничность только мешает. Совсем
    невпихуемое — обрезаем с многоточием, топ падать не должен."""
    author = await quote_author(quote)
    avatar = await quote_avatar(bot, quote)

    def _render(text: str) -> list:
        return render_quote_pages(
            text, author, avatar=avatar, max_pages_hint=1)

    try:
        pages = await loop.run_in_executor(None, lambda: _render(quote.text_of_quotes))
    except ValueError:
        short = " ".join((quote.text_of_quotes or "").split())[:500].rstrip() + "…"
        pages = await loop.run_in_executor(None, lambda: _render(short))
    return pages[0]


def stack(top: io.BytesIO, bottom: io.BytesIO) -> io.BytesIO:
    """Две карточки в одну картинку, одна над другой.

    Именно одной картинкой, а не альбомом: к альбому нельзя прицепить
    инлайн-кнопки, а без кнопок батла не будет. Вертикально, а не рядом —
    бок о бок каждая карточка ужалась бы вдвое и текст стал нечитаемым.
    """
    top.seek(0)
    bottom.seek(0)
    img_top = Image.open(top).convert("RGBA")
    img_bottom = Image.open(bottom).convert("RGBA")

    width = max(img_top.width, img_bottom.width)
    height = img_top.height + _GAP + img_bottom.height
    # Цвет полосы берём из угла карточки — так шов не выглядит инородным.
    divider = img_top.getpixel((0, 0))

    canvas = Image.new("RGBA", (width, height), divider)
    canvas.paste(img_top, ((width - img_top.width) // 2, 0), img_top)
    canvas.paste(img_bottom, ((width - img_bottom.width) // 2, img_top.height + _GAP), img_bottom)

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return out

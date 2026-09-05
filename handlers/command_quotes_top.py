"""Голосование за цитаты и топ: !топ цитат, !топ батл.

Сердечко под цитатой ставится кнопкой, один человек — один голос на цитату
(за этим следит уникальный индекс в quote_votes, см. quotes_extra_models).

Топ выводится альбомом: одним сообщением с несколькими картинками телеграм
не умеет, а альбом выглядит единым блоком и вмещает все 10 карточек в полном
разрешении. Подпись у альбома одна — в неё и складываем рейтинг с голосами.
"""
import asyncio
import html
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, Message,
)

from database.engine import async_session_maker
from database.quotes_extra_dao import BattleDAO, VotesDAO
from utils.format import DIVIDER
from utils.helpers import moscow_today
from utils.quote_render import quote_author, render_one
from utils.stats import plural

quotes_top_router = Router()

VOTE_CB = "qv"
TOP_LIMIT = 3           # три карточки — ровно столько, сколько читается с ходу
CAPTION_LIMIT = 1024    # жёсткий лимит телеграма на подпись к медиа
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

QUOTE_WORDS = {"цитат", "цитаты", "цитату", "цитат."}
BATTLE_WORDS = {"батл", "батла", "батлов", "батлы"}

PERIODS = {
    "": (None, "за всё время"),
    "всё": (None, "за всё время"),
    "все": (None, "за всё время"),
    "сегодня": (0, "за сегодня"),
    "день": (0, "за сегодня"),
    "неделя": (6, "за неделю"),
    "неделю": (6, "за неделю"),
    "месяц": (29, "за месяц"),
    "мес": (29, "за месяц"),
}


# ─────────────────────────── кнопка голосования ───────────────────────────

def vote_kb(quote_id: int, votes: int = 0) -> InlineKeyboardMarkup:
    label = f"❤️ {votes}" if votes else "❤️ Голосовать"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"{VOTE_CB}:{quote_id}")
    ]])


@quotes_top_router.callback_query(F.data.startswith(f"{VOTE_CB}:"))
async def cb_vote(call: CallbackQuery):
    try:
        quote_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        await call.answer()
        return

    async with async_session_maker() as session:
        added, total = await VotesDAO(session).toggle(quote_id, call.from_user.id)

    await call.answer("Голос учтён ❤️" if added else "Голос снял")
    try:
        await call.message.edit_reply_markup(reply_markup=vote_kb(quote_id, total))
    except TelegramBadRequest:
        pass  # разметка не изменилась — не страшно


# ─────────────────────────── команда ───────────────────────────

class TopOf(BaseFilter):
    """«!топ цитат ...» и «!топ батл ...» — ловим раньше обычного !топ."""

    def __init__(self, words: set[str]) -> None:
        self.words = words

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().casefold().split()
        return len(parts) >= 2 and parts[0] == "!топ" and parts[1] in self.words


def _period(words: list[str]) -> tuple[int | None, str] | None:
    tail = [w for w in words[2:] if w not in ("по", "за")]
    if not tail:
        return PERIODS[""]
    return PERIODS.get(tail[0])


async def _render_all(bot, quotes: list) -> list:
    """Карточки для топа. Рендерим параллельно — иначе десять штук
    с загрузкой аватарок ощутимо тормозят."""
    loop = asyncio.get_event_loop()
    return await asyncio.gather(*(render_one(bot, q, loop) for q in quotes))


def _trim(caption: str) -> str:
    if len(caption) <= CAPTION_LIMIT:
        return caption
    return caption[: CAPTION_LIMIT - 1].rsplit("\n", 1)[0] + "…"


async def _drop_note(note: Message) -> None:
    """Убрать служебное «секунду…». Альбом уже ушёл, так что любая
    осечка на уборке — не повод ронять команду целиком."""
    try:
        await note.delete()
    except TelegramAPIError:
        pass


async def _send_album(message: Message, pngs: list, caption: str) -> None:
    if len(pngs) == 1:
        pngs[0].seek(0)
        await message.answer_photo(
            BufferedInputFile(pngs[0].read(), filename="top_1.png"),
            caption=caption, parse_mode="HTML",
        )
        return
    media = []
    for i, buf in enumerate(pngs):
        buf.seek(0)
        media.append(InputMediaPhoto(
            media=BufferedInputFile(buf.read(), filename=f"top_{i + 1:02d}.png"),
            caption=caption if i == 0 else None,
            parse_mode="HTML" if i == 0 else None,
        ))
    await message.answer_media_group(media)


@quotes_top_router.message(TopOf(QUOTE_WORDS))
async def top_quotes(message: Message):
    period = _period(message.text.strip().casefold().split())
    if period is None:
        await message.reply(
            "Не понял период. Так можно:\n"
            "<code>!топ цитат</code> · <code>!топ цитат неделя</code> · "
            "<code>!топ цитат месяц</code>",
            parse_mode="HTML",
        )
        return

    days_back, title = period
    since = None if days_back is None else moscow_today() - timedelta(days=days_back)

    async with async_session_maker() as session:
        board = await VotesDAO(session).top(TOP_LIMIT, since)
        if not board:
            await message.reply(
                "За цитаты ещё никто не голосовал. Жми ❤️ под цитатой — "
                "и она попадёт в топ."
            )
            return
        quotes = await BattleDAO(session).quotes_by_ids([qid for qid, _ in board])

    board = [(qid, n) for qid, n in board if qid in quotes]
    note = await message.answer("Собираю топ, секунду…")

    pngs = await _render_all(message.bot, [quotes[qid] for qid, _ in board])
    authors = await asyncio.gather(*(quote_author(quotes[qid]) for qid, _ in board))

    lines = [f"🏆 <b>Топ цитат</b> — {title}", DIVIDER]
    for place, ((_, votes), author) in enumerate(zip(board, authors), start=1):
        medal = MEDALS.get(place, f"{place}.")
        lines.append(f"{medal} {html.escape(author)} — {votes} ❤️")

    await _send_album(message, pngs, _trim("\n".join(lines)))
    await _drop_note(note)


@quotes_top_router.message(TopOf(BATTLE_WORDS))
async def top_battle(message: Message):
    async with async_session_maker() as session:
        dao = BattleDAO(session)
        board = await dao.top_by_points(TOP_LIMIT)
        played = await dao.battles_played()
        if not board:
            await message.reply(
                "Батлов ещё не было. Запусти командой <code>!батл</code>.",
                parse_mode="HTML",
            )
            return
        quotes = await dao.quotes_by_ids([qid for qid, *_ in board])

    board = [row for row in board if row[0] in quotes]
    note = await message.answer("Считаю рейтинг, секунду…")

    pngs = await _render_all(message.bot, [quotes[row[0]] for row in board])
    authors = await asyncio.gather(*(quote_author(quotes[row[0]]) for row in board))

    lines = ["⚔️ <b>Топ цитат по батлам</b>", DIVIDER]
    for place, ((_, points, _shown), author) in enumerate(zip(board, authors), start=1):
        medal = MEDALS.get(place, f"{place}.")
        lines.append(
            f"{medal} {html.escape(author)} — {points} "
            f"{plural(points, 'балл', 'балла', 'баллов')}"
        )
    lines += ["", "<i>Балл за каждую выигранную пару. Поэтому цитата, которая "
                  "раз за разом остаётся второй, обгоняет ту, что один раз "
                  f"взяла первое место.</i>",
              f"<i>Сыграно {played} {plural(played, 'батл', 'батла', 'батлов')}.</i>"]

    await _send_album(message, pngs, _trim("\n".join(lines)))
    await _drop_note(note)

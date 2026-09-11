"""Карма: ответ «спасибо» — +1 автору сообщения.

Спасибо засчитывается только за сообщение-ответ из ровно одного слова:
спасибо, пасиб, пасиба, спс — в любом регистре. В ответ бот пишет,
кому капнуло.
Себе и ботам карму не капаем — иначе накрутка и бессмертные лидеры.
"""
from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.karma_dao import KarmaDAO
from handlers.command_duel import _plain_name
from utils.format import DIVIDER
from utils.stats import plural

karma_router = Router()

TOP_CMD = "!топ"
KARMA_WORDS = {"карма", "кармы", "карме", "карму"}

TOP_LIMIT = 10
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

GROUP_ONLY = "Эта команда для группового чата — карма живёт во флуде 🙂"


THANKS_WORDS = {"спасибо", "пасиб", "пасиба", "спс"}


def is_thanks(text: str | None) -> bool:
    """Ровно одно слово из списка в любом регистре. «Спасибо!» и «спасибо
    брат» — уже не считаются: только точное слово."""
    if not text:
        return False
    return text.strip().casefold() in THANKS_WORDS


class TopKarma(BaseFilter):
    """«!топ кармы» — ловим раньше обычного !топ (наш роутер идёт раньше)."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().casefold().split()
        return (
            len(parts) >= 2 and parts[0] == TOP_CMD and parts[1] in KARMA_WORDS
        )


def _is_group(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


@karma_router.message(F.reply_to_message, F.text)
async def thanks_cmd(message: Message):
    if not _is_group(message) or not is_thanks(message.text):
        return

    author = message.from_user
    target = message.reply_to_message.from_user
    if author is None or author.is_bot:
        return
    if target is None or target.is_bot:
        return
    if target.id == author.id:
        return  # сам себе спасибо — не считается

    async with async_session_maker() as session:
        await KarmaDAO(session).thank(
            message.chat.id, target.id,
            target.username or "", target.full_name or "",
        )
        name = await _plain_name(
            session, target.id, target.username or "", target.full_name or ""
        )
    await message.reply(f"{name} получает +1 к карме")


@karma_router.message(TopKarma())
async def top_karma_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    async with async_session_maker() as session:
        dao = KarmaDAO(session)
        board = await dao.top(message.chat.id, TOP_LIMIT)
        if not board:
            await message.reply(
                "Спасибо ещё никто никому не говорил. Ответь на сообщение "
                "словом <code>спасибо</code> (или пасиб / спс) — и карма капнет.",
                parse_mode="HTML",
            )
            return
        total = await dao.total_thanks(message.chat.id)
        lines = ["🏅 <b>Топ кармы</b>", DIVIDER]
        for place, row in enumerate(board, start=1):
            medal = MEDALS.get(place, f"{place}.")
            name = await _plain_name(session, row.user_id, row.username, row.display)
            lines.append(
                f"{medal} {name} — {row.points} "
                f"{plural(row.points, 'спасибо', 'спасибо', 'спасибо')}"
            )
        lines += ["", f"Всего сказано спасибо: {total}"]
    await message.answer("\n".join(lines), parse_mode="HTML")

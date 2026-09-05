"""!батл — попарное сравнение цитат.

Показываем две цитаты одной картинкой (одна над другой) и две кнопки:
⬆️ верхняя / ⬇️ нижняя. Выбрал — картинка и кнопки перерисовываются на
следующую пару, и так 10 раундов, потом итог. Весь батл живёт в одном
сообщении: правим у него медиа, подпись и клавиатуру.

Картинка склеенная, а не альбом из двух фото, по простой причине: к альбому
телеграм не даёт прицепить инлайн-кнопки, а без них выбирать нечем.
"""
import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, Message,
)

from database.engine import async_session_maker
from database.quotes_extra_dao import BATTLE_ROUNDS, BattleDAO
from utils.format import DIVIDER
from utils.quote_render import quote_author, render_one, stack
from utils.stats import plural

battle_router = Router()

CB = "bt"
MIN_QUOTES = 4  # меньше — и пары начнут повторяться уже на втором раунде


class StartsWith(BaseFilter):
    def __init__(self, *commands: str) -> None:
        self.commands = tuple(c.casefold() for c in commands)

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        text = message.text.strip().casefold()
        return any(text == cmd or text.startswith(cmd + " ") for cmd in self.commands)


def _kb(session_id: int, round_no: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬆️ Верхняя", callback_data=f"{CB}:{session_id}:{round_no}:u")],
        [InlineKeyboardButton(text="⬇️ Нижняя", callback_data=f"{CB}:{session_id}:{round_no}:d")],
    ])


async def _compose(bot: Bot, top_quote, bottom_quote):
    loop = asyncio.get_event_loop()
    top_png, bottom_png = await asyncio.gather(
        render_one(bot, top_quote, loop),
        render_one(bot, bottom_quote, loop),
    )
    return await loop.run_in_executor(None, lambda: stack(top_png, bottom_png))


def _caption(round_no: int) -> str:
    return (f"⚔️ <b>Батл цитат</b> · раунд {round_no} из {BATTLE_ROUNDS}\n"
            f"{DIVIDER}\nКакая лучше?")


async def _show_round(bot: Bot, battle, dao: BattleDAO, message: Message | None) -> bool:
    """Нарисовать очередную пару. False — если пары кончились."""
    pair = await dao.next_pair(battle)
    if pair is None:
        return False
    left, right = pair
    await dao.set_pair(battle, left, right)

    quotes = await dao.quotes_by_ids([left, right])
    img = await _compose(bot, quotes[left], quotes[right])
    img.seek(0)
    photo = BufferedInputFile(img.read(), filename=f"battle_{battle.round + 1}.png")
    caption, kb = _caption(battle.round + 1), _kb(battle.id, battle.round)

    if battle.message_id is None and message is not None:
        sent = await message.answer_photo(
            photo, caption=caption, parse_mode="HTML", reply_markup=kb
        )
        battle.message_id = sent.message_id
        await dao.session.commit()
    else:
        await bot.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=caption, parse_mode="HTML"),
            chat_id=battle.chat_id,
            message_id=battle.message_id,
            reply_markup=kb,
        )
    return True


async def _finish(bot: Bot, battle, dao: BattleDAO) -> None:
    scores = await dao.session_scores(battle.id)
    await dao.finish(battle)

    if not scores:
        await bot.edit_message_caption(
            chat_id=battle.chat_id, message_id=battle.message_id,
            caption="Батл закончился, но выбрать никто ничего не успел 🤷", reply_markup=None,
        )
        return

    winner_id, wins = scores[0]
    quotes = await dao.quotes_by_ids([winner_id])
    winner = quotes[winner_id]
    author = await quote_author(winner)

    loop = asyncio.get_event_loop()
    png = await render_one(bot, winner, loop)
    png.seek(0)

    rounds = sum(n for _, n in scores)
    caption = (
        f"👑 <b>Победитель батла</b>\n{DIVIDER}\n"
        f"Цитата <b>{html.escape(author)}</b> — {wins} "
        f"{plural(wins, 'победа', 'победы', 'побед')} "
f"из {rounds} {plural(rounds, 'сравнения', 'сравнений', 'сравнений')}\n\n"
        f"<i>Общий рейтинг: <code>!топ батл</code></i>"
    )
    await bot.edit_message_media(
        media=InputMediaPhoto(
            media=BufferedInputFile(png.read(), filename="battle_winner.png"),
            caption=caption, parse_mode="HTML",
        ),
        chat_id=battle.chat_id, message_id=battle.message_id, reply_markup=None,
    )


@battle_router.message(StartsWith("!батл", "!баттл"))
async def battle_cmd(message: Message):
    async with async_session_maker() as session:
        dao = BattleDAO(session)
        ids = await dao.quote_ids()
        if len(ids) < MIN_QUOTES:
            await message.reply(
                f"Для батла нужно хотя бы {MIN_QUOTES} цитаты, сейчас {len(ids)}. "
                "Сохраните ещё через <code>!цитата</code>.",
                parse_mode="HTML",
            )
            return

        battle = await dao.start(message.from_user.id, message.chat.id)
        if not await _show_round(message.bot, battle, dao, message):
            await message.reply("Не смог собрать пару цитат — странно, попробуй ещё раз.")


@battle_router.callback_query(F.data.startswith(f"{CB}:"))
async def cb_choice(call: CallbackQuery):
    try:
        _, raw_id, raw_round, side = call.data.split(":")
        session_id, round_no = int(raw_id), int(raw_round)
    except ValueError:
        await call.answer()
        return

    async with async_session_maker() as session:
        dao = BattleDAO(session)
        battle = await dao.get(session_id)

        if battle is None or battle.finished_at is not None:
            await call.answer("Этот батл уже закончился", show_alert=True)
            return
        if battle.user_id != call.from_user.id:
            await call.answer("Это чужой батл. Запусти свой: !батл", show_alert=True)
            return
        if battle.round != round_no:
            # Двойное нажатие по старой клавиатуре — раунд уже сыгран.
            await call.answer()
            return

        winner_id = battle.left_id if side == "u" else battle.right_id
        loser_id = battle.right_id if side == "u" else battle.left_id
        if winner_id is None or loser_id is None:
            await call.answer()
            return

        await call.answer("Принято")
        await dao.record(battle, winner_id, loser_id)

        if battle.round >= BATTLE_ROUNDS:
            await _finish(call.bot, battle, dao)
            return
        try:
            if not await _show_round(call.bot, battle, dao, None):
                await _finish(call.bot, battle, dao)
        except TelegramBadRequest:
            await _finish(call.bot, battle, dao)

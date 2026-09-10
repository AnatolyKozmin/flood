"""!батл — попарное сравнение цитат.

Показываем две цитаты одной картинкой (одна над другой) и две кнопки:
⬆️ верхняя / ⬇️ нижняя. Выбрал — старая картинка удаляется, генерится
следующая пара и приходит новым сообщением. И так 10 раундов, потом итог.
В чате всегда висит ровно одна картинка батла.

Почему не редактирование на месте: во флуде отредактированное сообщение
остаётся там, где его отправили, и через десяток чужих реплик до кнопок
надо доскроллить. Новое сообщение всегда приходит вниз. Сначала шлём
новое, потом удаляем старое — чтобы между раундами не было пустоты, пока
рисуется картинка.

Картинка склеенная, а не альбом из двух фото, по простой причине: к альбому
телеграм не даёт прицепить инлайн-кнопки, а без них выбирать нечем.
"""
import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database.engine import async_session_maker
from database.quotes_extra_dao import BATTLE_ROUNDS, BattleDAO
from database.stats_dao import StatsDAO
from utils.format import DIVIDER
from utils.quote_render import quote_author, render_one, stack
from utils.stats import plural

battle_router = Router()

CB = "bt"
MIN_QUOTES = 5  # круговой турнир — это пул из пяти и все 10 пар


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


async def _drop(bot: Bot, chat_id: int, message_id: int | None) -> None:
    """Убрать прошлую картинку батла. Свои сообщения бот удаляет и в группе,
    без прав администратора."""
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass


async def _swap(bot: Bot, battle, dao: BattleDAO, photo: BufferedInputFile,
                caption: str, kb: InlineKeyboardMarkup | None) -> None:
    """Прислать новую картинку и убрать предыдущую.

    Именно в таком порядке: пока рисуется новая, старая ещё висит, так что
    пустого места в чате не возникает. Если отправка почему-то не прошла —
    старая картинка остаётся на месте, и батл не превращается в тыкву.
    """
    previous = battle.message_id
    sent = await bot.send_photo(
        battle.chat_id, photo, caption=caption, parse_mode="HTML", reply_markup=kb
    )
    battle.message_id = sent.message_id
    await dao.session.commit()
    await _drop(bot, battle.chat_id, previous)


async def _show_round(bot: Bot, battle, dao: BattleDAO) -> bool:
    """Нарисовать очередную пару турнира. False — если пары кончились."""
    while True:
        pair = await dao.next_queued_pair(battle)
        if pair is None:
            return False
        left, right = pair
        await dao.set_pair(battle, left, right)

        quotes = await dao.quotes_by_ids([left, right])
        if left not in quotes or right not in quotes:
            continue  # цитату снесли посреди сессии — берём следующую пару
        img = await _compose(bot, quotes[left], quotes[right])
        img.seek(0)
        await _swap(
            bot, battle, dao,
            BufferedInputFile(img.read(), filename=f"battle_{battle.round + 1}.png"),
            _caption(battle.round + 1),
            _kb(battle.id, battle.round),
        )
        return True


async def _finish(bot: Bot, battle, dao: BattleDAO) -> None:
    scores = await dao.session_scores(battle.id)
    await dao.finish(battle)

    if not scores:
        await _drop(bot, battle.chat_id, battle.message_id)
        await bot.send_message(
            battle.chat_id, "Батл закончился, но выбрать никто ничего не успел 🤷"
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
        f"<i>Кто побеждает чаще всех: <code>!топ батл</code></i>"
    )
    await _swap(
        bot, battle, dao,
        BufferedInputFile(png.read(), filename="battle_winner.png"),
        caption, None,
    )


@battle_router.message(StartsWith("!батл", "!баттл"))
async def battle_cmd(message: Message):
    async with async_session_maker() as session:
        dao = BattleDAO(session)
        await dao.ensure_pool_columns()
        ids = await dao.quote_ids()
        if len(ids) < MIN_QUOTES:
            await message.reply(
                f"Для батла нужно хотя бы {MIN_QUOTES} цитат, сейчас {len(ids)}. "
                "Сохраните ещё через <code>!цитата</code>.",
                parse_mode="HTML",
            )
            return

        # Батл делает только один человек за раз — иначе картинки и кнопки
        # двух сессий перемешаются во флуде.
        active = await dao.active_in_chat(message.chat.id)
        if active is not None and not active.queue:
            # Остаток старого формата (пары брались случайно, без очереди):
            # доиграть его нельзя, молча закрываем и не блокируем новый.
            await dao.finish(active)
            active = None
        if active is not None:
            users = await StatsDAO(session).users([active.user_id])
            found = users.get(active.user_id)
            if found is not None and found.username:
                who = f"@{found.username.lstrip('@')}"
            elif found is not None and found.full_name:
                who = html.escape(found.full_name)
            else:
                who = "Кто-то"
            if active.user_id == message.from_user.id:
                await message.reply(
                    "Ты уже делаешь батл — жми кнопки на картинке выше 👆"
                )
            else:
                await message.reply(
                    f"{who} уже делает батл — дождись, пока закончит."
                )
            return

        battle = await dao.start(message.from_user.id, message.chat.id)
        if not await dao.setup_round_robin(battle, ids):
            await message.reply("Не смог собрать пятёрку цитат — странно, попробуй ещё раз.")
            return
        if not await _show_round(message.bot, battle, dao):
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
        if winner_id is None:
            await call.answer()
            return

        await call.answer("Принято")
        await dao.record(battle, winner_id)

        if battle.round >= BATTLE_ROUNDS:
            await _finish(call.bot, battle, dao)
            return
        try:
            if not await _show_round(call.bot, battle, dao):
                await _finish(call.bot, battle, dao)
        except TelegramAPIError:
            await _finish(call.bot, battle, dao)

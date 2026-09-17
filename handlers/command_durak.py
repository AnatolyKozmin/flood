"""Подкидной дурак с ботом — пока только в личке.

Человек (игрок 0) против бота (игрок 1). Свои карты — кнопками, чужие не
видны. Ход игры пошаговый: после каждого действия человека бот мгновенно
отвечает (бьёт, подкидывает или ведёт новый заход), так что отдельного
состояния «ход бота» нет — доска всегда ждёт решения человека или финал.

Сессии живут в памяти (SESSIONS по telegram id): рестарт бота партию
обнуляет — для v1 приемлемо, новая начинается по !дурак.
"""
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from utils import durak as D

logger = logging.getLogger(__name__)

durak_router = Router()
CB = "dk"

SESSIONS: dict[int, D.Game] = {}
BOARDS: dict[int, int] = {}  # tg_id -> message_id текущей доски (старую трём)


class Exact(BaseFilter):
    """Сообщение — ровно команда (как FirstWord у !цитата, но без хвоста)."""

    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


def _sorted_hand(game: D.Game, player: int) -> list[int]:
    return sorted(game.hands[player], key=lambda c: (D.suit_of(c), D.rank_of(c)))


def _board_text(game: D.Game) -> str:
    lines = [
        f"🃏 <b>Дурак</b> · козырь {D.SUIT_EMOJI[game.trump]} "
        f"· колода: {len(game.talon)}",
        f"Бот: {len(game.hands[1])} карт · Ты: {len(game.hands[0])} карт.",
    ]
    if game.table:
        lines.append("")
        for att, dfn in game.table:
            right = D.card_label(dfn) if dfn is not None else "<i>?</i>"
            lines.append(f"{D.card_label(att)} → {right}")
    if game.over:
        lines += ["", _final_line(game)]
    elif game.attacker == 0:
        if game.table:
            lines += ["", "Твой ход: подкинь карту или жми «Бито»."]
        else:
            lines += ["", "Твой ход — клади карту."]
    else:
        lines += ["", "Отбивайся картой или жми «Беру»."]
    return "\n".join(lines)


def _final_line(game: D.Game) -> str:
    if game.winner == 0:
        return "🎉 <b>Ты выиграл!</b>"
    if game.winner == 1:
        return "🤖 <b>Бот выиграл.</b> В следующий раз повезёт."
    return "🤝 <b>Ничья!</b>"


def _board_kb(game: D.Game) -> InlineKeyboardMarkup:
    if game.over:
        return _kb([_btn("🔄 Ещё партию", "new")])
    rows: list[list[InlineKeyboardButton]] = []
    hand = _sorted_hand(game, 0)
    for i in range(0, len(hand), 3):
        rows.append([_btn(D.card_label(c), f"c:{c}") for c in hand[i:i + 3]])
    if game.attacker == 0:
        actions = []
        if game.table:
            actions.append(_btn("✅ Бито", "done"))
        actions.append(_btn("🏳️ Сдаться", "giveup"))
        rows.append(actions)
    else:
        rows.append([_btn("🫳 Беру", "take"), _btn("🏳️ Сдаться", "giveup")])
    return _kb(*rows)


async def _paint_board(target: Message | CallbackQuery, game: D.Game) -> None:
    text, kb = _board_text(game), _board_kb(game)
    message = target if isinstance(target, Message) else target.message
    if isinstance(target, CallbackQuery):
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _new_session(user_id: int) -> D.Game:
    game = D.new_game()
    SESSIONS[user_id] = game
    logger.info("Дурак: новая партия для %s, первый ходит %s",
                user_id, "человек" if game.attacker == 0 else "бот")
    if game.attacker == 1:
        _bot_lead(game)
    return game


def _bot_lead(game: D.Game) -> None:
    """Бот начинает заход (старт партии или человек взял)."""
    lead = D.ai_lead(game, 1)
    D.apply_attack(game, 1, lead)


def _bot_answer_attack(game: D.Game) -> str | None:
    """Бот кроет только что подкинутую карту. 'take' — не смог и берёт всё
    (человек ходит снова), None — побил, бой продолжается."""
    att = game.table[-1][0]
    beater = D.ai_min_beater(game, 1, att)
    if beater is None:
        D.resolve_take(game)
        return "take"
    D.apply_defense(game, 1, att, beater)
    return None


def _bot_toss_or_done(game: D.Game) -> str | None:
    """После того как человек всё побил: бот подкидывает или заканчивает
    заход. Возвращает итог партии ('user'/'bot'/'draw') или None."""
    toss = D.ai_toss(game, 1)
    if toss is not None:
        D.apply_attack(game, 1, toss)
        return None
    return D.resolve_done(game)


@durak_router.message(F.chat.type == "private", Exact("!дурак"))
async def durak_cmd(message: Message):
    old_id = BOARDS.get(message.from_user.id)
    if old_id is not None:
        try:
            await message.bot.delete_message(message.chat.id, old_id)
        except TelegramAPIError:
            pass
    game = _new_session(message.from_user.id)
    first = ("Первым ходишь ты (младший козырь у тебя)."
             if game.attacker == 0 else "Первым ходит бот.")
    await message.answer(f"Новая партия! {first}")
    board = await message.answer(
        _board_text(game), reply_markup=_board_kb(game), parse_mode="HTML")
    BOARDS[message.from_user.id] = board.message_id


@durak_router.message(F.chat.type.in_({"group", "supergroup"}), Exact("!дурак"))
async def durak_group_hint(message: Message):
    await message.reply("В дурака играем в личке: напиши мне !дурак.")


def _session(call: CallbackQuery) -> D.Game | None:
    return SESSIONS.get(call.from_user.id)


@durak_router.callback_query(F.data == f"{CB}:new")
async def cb_new(call: CallbackQuery):
    await call.answer()
    game = _new_session(call.from_user.id)
    BOARDS[call.from_user.id] = call.message.message_id
    await _paint_board(call, game)


@durak_router.callback_query(F.data == f"{CB}:giveup")
async def cb_giveup(call: CallbackQuery):
    game = _session(call)
    if game is None:
        await call.answer("Партии нет — начни с !дурак.", show_alert=True)
        return
    if game.over:
        await call.answer()
        await _paint_board(call, game)
        return
    game.over, game.winner = True, 1
    logger.info("Дурак: %s сдался", call.from_user.id)
    await call.answer("Сдался — бот выиграл.")
    await _paint_board(call, game)


@durak_router.callback_query(F.data.startswith(f"{CB}:c:"))
async def cb_card(call: CallbackQuery):
    game = _session(call)
    if game is None:
        await call.answer("Партии нет — начни с !дурак.", show_alert=True)
        return
    if game.over:
        await call.answer()
        await _paint_board(call, game)
        return
    try:
        card = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if card not in game.hands[0]:
        await call.answer("Этой карты у тебя уже нет.", show_alert=True)
        await _paint_board(call, game)
        return

    if game.attacker == 0:
        # Человек нападает/подкидывает — бот тут же кроет.
        if card not in D.legal_attacks(game, 0):
            await call.answer("Так подкинуть нельзя: нужен ранг со стола.",
                              show_alert=True)
            return
        D.apply_attack(game, 0, card)
        took = _bot_answer_attack(game)
        if took:
            await call.answer(_final_line(game) if game.over else "Бот берёт.")
        else:
            await call.answer("Побито.")
        await _paint_board(call, game)
        return

    # Человек отбивается: карта должна бить непокрытую.
    target = next(
        (att for att in D.uncovered(game) if D.beats(att, card, game.trump)),
        None,
    )
    if target is None:
        await call.answer(
            f"{D.card_label(card)} ничего со стола не бьёт.", show_alert=True)
        return
    D.apply_defense(game, 0, target, card)
    if D.uncovered(game):
        await call.answer()
        await _paint_board(call, game)
        return
    # Всё покрыто — бот подкидывает или заканчивает заход.
    result = _bot_toss_or_done(game)
    if result is None:
        await call.answer("Бот подкидывает.")
    else:
        await call.answer(_final_line(game))
    await _paint_board(call, game)


@durak_router.callback_query(F.data == f"{CB}:done")
async def cb_done(call: CallbackQuery):
    game = _session(call)
    if game is None:
        await call.answer("Партии нет — начни с !дурак.", show_alert=True)
        return
    if game.over or game.attacker != 0 or not game.table:
        await call.answer()
        return
    if D.uncovered(game):
        # Недостижимо: бот кроет каждый подкид сразу. Страховка от assert.
        await call.answer("Подожди, бот ещё отвечает.", show_alert=True)
        await _paint_board(call, game)
        return
    result = D.resolve_done(game)
    if result is None:
        _bot_lead(game)
        await call.answer("Бито! Бот ходит.")
    else:
        await call.answer(_final_line(game))
    await _paint_board(call, game)


@durak_router.callback_query(F.data == f"{CB}:take")
async def cb_take(call: CallbackQuery):
    game = _session(call)
    if game is None:
        await call.answer("Партии нет — начни с !дурак.", show_alert=True)
        return
    if game.over or game.attacker == 0:
        await call.answer()
        return
    result = D.resolve_take(game)
    if result is None:
        _bot_lead(game)
        await call.answer("Взял. Бот ходит снова.")
    else:
        await call.answer(_final_line(game))
    await _paint_board(call, game)

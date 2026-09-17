"""Подкидной дурак с ботом — в личке и во флуде.

Человек (игрок 0) против бота (игрок 1). Свои карты — кнопками, чужие не
видны. Ход игры пошаговый: после каждого действия человека бот мгновенно
отвечает (бьёт, подкидывает или ведёт новый заход), так что отдельного
состояния «ход бота» нет — доска всегда ждёт решения человека или финал.

Столы: во флуде стол один на всех (пока один не доиграл, второй ждёт),
в личке у каждого свой — до 50 одновременных. Зависший стол (тишина дольше
IDLE_TIMEOUT) отдаём новому игроку, протухшие лички чистим по-тихому.
Жмут кнопки только занявшие стол. Выбор колоды 24/36/52 перед партией.
Итоги пишутся в durak_stats, топ — !топовый дурак.

Сессии живут в памяти: рестарт бота партии обнуляет.
"""
import html
import logging
import time
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database.durak_dao import DurakDAO
from database.engine import async_session_maker
from utils import durak as D
from utils.format import DIVIDER

logger = logging.getLogger(__name__)

durak_router = Router()
CB = "dk"

DECKS = (24, 36, 52)
IDLE_TIMEOUT = 1800  # зависший стол отдаём через полчаса тишины
MAX_PRIVATE = 50  # столько личек играют одновременно
THROTTLE_SEC = 1.0  # чаще — игнор: защита от пулемёта по кнопкам и двойных тапов


@dataclass
class Seat:
    """Занятый стол: чья партия, где доска, когда последний ход."""

    game: D.Game
    uid: int
    name: str
    display: str
    deck: int
    board: tuple[int, int] | None = None  # (chat_id, msg_id) доски
    last_active: float = field(default_factory=time.monotonic)


PRIVATE_SEATS: dict[int, Seat] = {}  # tg_id -> стол в личке
GROUP_SEAT: Seat | None = None  # один стол на все флуды
BOARDS: dict[int, tuple[int, int]] = {}  # tg_id -> (chat_id, msg_id) доски
LAST_DECK: dict[int, int] = {}  # tg_id -> размер колоды для кнопки «Ещё»
LAST_TAP: dict[int, float] = {}  # tg_id -> время последнего нажатия (троттлинг)
_RECORDED: set[int] = set()  # id игр, уже записанных в топ


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


def _is_group_chat(chat) -> bool:
    return chat.type in ("group", "supergroup")


def _sorted_hand(game: D.Game, player: int) -> list[int]:
    return sorted(game.hands[player], key=lambda c: (D.suit_of(c), D.rank_of(c)))


def _deck_text() -> tuple[str, InlineKeyboardMarkup]:
    text = "🃏 <b>Дурак</b>\n" + DIVIDER + "\nСколько карт в колоде?"
    kb = _kb([_btn(str(n), f"deck:{n}") for n in DECKS])
    return text, kb


def _board_text(game: D.Game, display: str, name: str) -> str:
    lines = [
        f"🃏 <b>ИГРА ДУРАК-{game.deck_size}</b>",
        f"Игрок: {html.escape(display or name)}",
        f"Козырь: {D.SUIT_EMOJI[game.trump]}",
        f"Колода: {len(game.talon)}",
        f"Бот: {len(game.hands[1])} карт",
        f"Ты: {len(game.hands[0])} карт.",
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


async def _paint_board(target: Message | CallbackQuery, seat: Seat) -> None:
    text = _board_text(seat.game, seat.display, seat.name)
    kb = _board_kb(seat.game)
    message = target if isinstance(target, Message) else target.message
    if isinstance(target, CallbackQuery):
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _player_display(user) -> str:
    """«Фамилия Имя» для шапки доски."""
    fio = f"{user.last_name or ''} {user.first_name or ''}".strip()
    return fio or user.full_name


def _purge_idle_private() -> None:
    """Тихая чистка заброшенных личек, чтобы не забивать лимит."""
    now = time.monotonic()
    dead = [uid for uid, seat in PRIVATE_SEATS.items()
            if not seat.game.over and now - seat.last_active > IDLE_TIMEOUT]
    for uid in dead:
        del PRIVATE_SEATS[uid]


def _group_busy_for(user_id: int) -> str | None:
    """Имя занявшего общий стол — или None, если садиться можно."""
    if GROUP_SEAT is None or GROUP_SEAT.game.over or GROUP_SEAT.uid == user_id:
        return None
    if time.monotonic() - GROUP_SEAT.last_active > IDLE_TIMEOUT:
        return None
    return GROUP_SEAT.name


def _start_game(user_id: int, user, deck_size: int, group: bool) -> Seat:
    """Занять стол и раздать. Проверки — до вызова."""
    global GROUP_SEAT
    game = D.new_game(deck_size=deck_size)
    seat = Seat(game=game, uid=user_id, name=user.full_name,
                display=_player_display(user), deck=game.deck_size)
    if group:
        GROUP_SEAT = seat
    else:
        PRIVATE_SEATS[user_id] = seat
    LAST_DECK[user_id] = game.deck_size
    logger.info("Дурак-%s (%s): партия для %s, первый ходит %s",
                game.deck_size, "флуд" if group else "личка", user_id,
                "человек" if game.attacker == 0 else "бот")
    if game.attacker == 1:
        _bot_lead(game)
    return seat


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


async def _owned(call: CallbackQuery) -> Seat | None:
    """Стол звонящего: чужой — «стол занят», без партии — «начни с !дурак».
    Пулемёт по кнопкам режем молча: чаще THROTTLE_SEC — игнор."""
    uid = call.from_user.id
    if _is_group_chat(call.message.chat):
        seat = GROUP_SEAT
        if seat is None or seat.game.over or seat.uid != uid:
            if seat is not None and not seat.game.over and seat.uid != uid:
                await call.answer(f"Стол занят — играет {seat.name}.",
                                  show_alert=True)
            else:
                await call.answer("Партии нет — начни с !дурак.",
                                  show_alert=True)
            return None
    else:
        seat = PRIVATE_SEATS.get(uid)
        if seat is None or seat.game.over:
            await call.answer("Партии нет — начни с !дурак.", show_alert=True)
            return None
    now = time.monotonic()
    if now - LAST_TAP.get(uid, 0.0) < THROTTLE_SEC:
        await call.answer()
        return None
    LAST_TAP[uid] = now
    seat.last_active = now
    return seat


async def _finish_if_over(seat: Seat, user: Message | CallbackQuery) -> bool:
    """Партия кончилась: раз в топ, стол свободен. True — конец."""
    global GROUP_SEAT
    game = seat.game
    if not game.over:
        return False
    if id(game) not in _RECORDED:
        _RECORDED.add(id(game))
        uid = user.from_user.id
        tag = (user.from_user.username or "").lstrip("@")
        if game.winner is None:
            outcome = "draw"
        else:
            outcome = "win" if game.winner == 0 else "loss"
        try:
            async with async_session_maker() as session:
                await DurakDAO(session).record(
                    uid, tag, user.from_user.full_name, outcome)
        except Exception:
            logger.exception("Дурак: не записал итог %s", uid)
    if _is_group_chat(user.chat if isinstance(user, Message) else user.message.chat):
        if GROUP_SEAT is seat:
            GROUP_SEAT = None
    else:
        PRIVATE_SEATS.pop(seat.uid, None)
    return True


@durak_router.message(Exact("!дурак"))
async def durak_cmd(message: Message):
    uid = message.from_user.id
    if _is_group_chat(message.chat):
        busy = _group_busy_for(uid)
        if busy:
            await message.answer(
                f"Стол занят — сейчас играет {busy}. Дождись конца партии.")
            return
    else:
        _purge_idle_private()
        if uid not in PRIVATE_SEATS and len(PRIVATE_SEATS) >= MAX_PRIVATE:
            await message.answer(
                "Все столы заняты — подожди, кто-нибудь доиграет.")
            return
    text, kb = _deck_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@durak_router.message(Exact("!топовый дурак"))
async def pokertop_cmd(message: Message):
    async with async_session_maker() as session:
        top = await DurakDAO(session).top()
    if not top:
        await message.answer(
            "Пока никто не доиграл ни одной партии. Начни с !дурак.")
        return

    lines = ["🏆 <b>Топовый дурак</b> — победы над ботом в дурака", DIVIDER]
    for i, row in enumerate(top, 1):
        name = html.escape(row.display or "без имени")
        games = row.wins + row.losses + row.draws
        lines.append(
            f"{i}. {name} — {row.wins} поб. · {row.losses} пораж. · "
            f"{row.draws} нич. ({games} игр)")
    await message.answer("\n".join(lines), parse_mode="HTML")


@durak_router.callback_query(F.data.startswith(f"{CB}:deck:"))
async def cb_deck(call: CallbackQuery):
    try:
        deck_size = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if deck_size not in DECKS:
        await call.answer()
        return
    group = _is_group_chat(call.message.chat)
    if group:
        busy = _group_busy_for(call.from_user.id)
        if busy:
            await call.answer(f"Стол занят — играет {busy}.", show_alert=True)
            return
        took_over = (GROUP_SEAT is not None
                     and GROUP_SEAT.uid != call.from_user.id)
    else:
        _purge_idle_private()
        uid = call.from_user.id
        if uid not in PRIVATE_SEATS and len(PRIVATE_SEATS) >= MAX_PRIVATE:
            await call.answer("Все столы заняты — подожди.", show_alert=True)
            return
        took_over = False
    old = BOARDS.get(call.from_user.id)
    if old is not None and (old[0], old[1]) != (
            call.message.chat.id, call.message.message_id):
        try:
            await call.bot.delete_message(old[0], old[1])
        except TelegramAPIError:
            pass
    seat = _start_game(call.from_user.id, call.from_user, deck_size, group)
    BOARDS[call.from_user.id] = (call.message.chat.id,
                                 call.message.message_id)
    seat.board = BOARDS[call.from_user.id]
    game = seat.game
    first = ("Первым ходишь ты (младший козырь у тебя)."
             if game.attacker == 0 else "Первым ходит бот.")
    await call.answer()
    if took_over:
        await call.message.answer("Прошлый стол завис — забираю его себе.")
    await call.message.answer(
        f"{call.from_user.full_name}, новая партия на {game.deck_size}! "
        f"{first}")
    await _paint_board(call, seat)


@durak_router.callback_query(F.data == f"{CB}:new")
async def cb_new(call: CallbackQuery):
    group = _is_group_chat(call.message.chat)
    if group:
        busy = _group_busy_for(call.from_user.id)
        if busy:
            await call.answer(f"Стол занят — играет {busy}.", show_alert=True)
            return
    else:
        _purge_idle_private()
        uid = call.from_user.id
        if uid not in PRIVATE_SEATS and len(PRIVATE_SEATS) >= MAX_PRIVATE:
            await call.answer("Все столы заняты — подожди.", show_alert=True)
            return
    deck_size = LAST_DECK.get(call.from_user.id)
    if deck_size is None:
        text, kb = _deck_text()
        await call.answer()
        try:
            await call.message.edit_text(text, reply_markup=kb,
                                         parse_mode="HTML")
        except TelegramAPIError:
            pass
        return
    await call.answer()
    seat = _start_game(call.from_user.id, call.from_user, deck_size, group)
    BOARDS[call.from_user.id] = (call.message.chat.id,
                                 call.message.message_id)
    seat.board = BOARDS[call.from_user.id]
    await _paint_board(call, seat)


@durak_router.callback_query(F.data == f"{CB}:giveup")
async def cb_giveup(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None:
        return
    game = seat.game
    game.over, game.winner = True, 1
    logger.info("Дурак: %s сдался", call.from_user.id)
    await call.answer("Сдался — бот выиграл.")
    await _finish_if_over(seat, call)
    await _paint_board(call, seat)


@durak_router.callback_query(F.data.startswith(f"{CB}:c:"))
async def cb_card(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None:
        return
    game = seat.game
    try:
        card = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if card not in game.hands[0]:
        await call.answer("Этой карты у тебя уже нет.", show_alert=True)
        await _paint_board(call, seat)
        return

    if game.attacker == 0:
        # Человек нападает/подкидывает — бот тут же кроет.
        if card not in D.legal_attacks(game, 0):
            await call.answer("Так подкинуть нельзя: нужен ранг со стола.",
                              show_alert=True)
            return
        D.apply_attack(game, 0, card)
        took = _bot_answer_attack(game)
        if await _finish_if_over(seat, call):
            await call.answer(_final_line(game))
        else:
            await call.answer("Бот берёт." if took else "Побито.")
        await _paint_board(call, seat)
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
        await _paint_board(call, seat)
        return
    # Всё покрыто — бот подкидывает или заканчивает заход.
    result = _bot_toss_or_done(game)
    if result is None:
        await call.answer("Бот подкидывает.")
    else:
        await call.answer(_final_line(game))
    await _finish_if_over(seat, call)
    await _paint_board(call, seat)


@durak_router.callback_query(F.data == f"{CB}:done")
async def cb_done(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None:
        return
    game = seat.game
    if game.attacker != 0 or not game.table:
        await call.answer()
        return
    if D.uncovered(game):
        # Недостижимо: бот кроет каждый подкид сразу. Страховка от assert.
        await call.answer("Подожди, бот ещё отвечает.", show_alert=True)
        await _paint_board(call, seat)
        return
    result = D.resolve_done(game)
    if await _finish_if_over(seat, call):
        await call.answer(_final_line(game))
    else:
        _bot_lead(game)
        await call.answer("Бито! Бот ходит.")
    await _paint_board(call, seat)


@durak_router.callback_query(F.data == f"{CB}:take")
async def cb_take(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None:
        return
    game = seat.game
    if game.attacker == 0:
        await call.answer()
        return
    result = D.resolve_take(game)
    if await _finish_if_over(seat, call):
        await call.answer(_final_line(game))
    else:
        _bot_lead(game)
        await call.answer("Взял. Бот ходит снова.")
    await _paint_board(call, seat)

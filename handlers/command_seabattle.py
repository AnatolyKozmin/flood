"""Морской бой с ботом 10 на 10 — в личке и во флуде.

Расстановка на выбор: сам (нос → конец, с проверкой касаний) или авто.
В личке у каждого свой стол (до 50), во флуде стол один на всех. Кнопки
жмёт только занявший стол. Бот ищет по шахматке и добивает раненые.

Два сообщения на партию: главное (чужое поле / расстановка) и своё поле
(корабли + кнопка сдаться — она там, чтобы не превышать лимит 100 кнопок
на сообщение в разгар боя). Ход всегда возвращается человеку: бот отвечает
мгновенно, отдельного «хода бота» нет.

Сессии в памяти: рестарт бота партии обнуляет. Топов пока нет.
"""
import html
import logging
import random
import time
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from utils import seabattle as S

logger = logging.getLogger(__name__)

seabattle_router = Router()
CB = "sb"

IDLE_TIMEOUT = 1800
MAX_PRIVATE = 50
THROTTLE_SEC = 1.0


@dataclass
class Seat:
    uid: int
    name: str
    player: S.Board
    enemy: S.Board  # флот бота
    hunter: S.Hunter
    phase: str = "placing"  # placing | battle | over
    to_place: list[int] = field(default_factory=list)
    history: list[set[int]] = field(default_factory=list)  # встал — для отмены
    start: int | None = None  # выбранный нос при ручной расстановке
    main_msg: tuple[int, int] | None = None  # (chat_id, msg_id) главного
    own_msg: tuple[int, int] | None = None  # (chat_id, msg_id) своего поля
    winner: int | None = None
    last_active: float = field(default_factory=time.monotonic)


PRIVATE_SEATS: dict[int, Seat] = {}
GROUP_SEAT: Seat | None = None
LAST_TAP: dict[int, float] = {}


class Exact(BaseFilter):
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


def _grid(board: S.Board, *, own: bool) -> str:
    """Поле текстом. Своё: корабли видны; чужое: только попадания."""
    rows = []
    for r in range(S.N):
        line = []
        for c in range(S.N):
            cell = r * S.N + c
            if cell in board.sunk:
                line.append("🔥")
            elif board.shots.get(cell) == "hit":
                line.append("💥")
            elif board.shots.get(cell) == "miss":
                line.append("🌊" if not own else "⚪")
            elif own and any(cell in ship for ship in board.ships):
                line.append("🚢")
            else:
                line.append("🟦")
        rows.append("".join(line))
    return "\n".join(rows)


def _ship_word(size: int) -> str:
    return {4: "четырёхпалубный", 3: "трёхпалубный",
            2: "двухпалубный", 1: "однопалубный"}[size]


def _main_text(seat: Seat) -> str:
    head = (f"⚓ <b>МОРСКОЙ БОЙ</b>\nИгрок: {html.escape(seat.name)}\n")
    if seat.phase == "placing":
        size = seat.to_place[0]
        left = len(seat.to_place)
        body = _grid(seat.player, own=True)
        if seat.start is None:
            tail = (f"\nСтавь {_ship_word(size)} (осталось кораблей: {left}): "
                    f"выбери нос кнопками.")
        else:
            tail = (f"\nНос — <b>{S.cell_label(seat.start)}</b>, "
                    f"{_ship_word(size)}: выбери конец.")
        return head + body + tail
    body = _grid(seat.enemy, own=False)
    if seat.phase == "over":
        tail = ("\n🎉 <b>Ты потопил весь флот!</b>" if seat.winner == 0
                else "\n🤖 <b>Бот потопил твой флот.</b>")
        return head + body + tail
    return head + body + "\nТвой выстрел — жми по клетке."


def _main_kb(seat: Seat) -> InlineKeyboardMarkup | None:
    if seat.phase == "over":
        return _kb([_btn("🔄 Новая", "new")])
    if seat.phase == "placing":
        if seat.start is not None:
            ends = S.valid_ends(seat.player, seat.start, seat.to_place[0])
            rows = [[_btn(S.cell_label(c), f"end:{c}") for c in ends]]
            rows.append([_btn("↩️ Назад", "back")])
            return _kb(*rows)
        size = seat.to_place[0]
        starts = S.valid_starts(seat.player, size)
        rows = []
        for i in range(0, len(starts), 5):
            rows.append([_btn(S.cell_label(c), f"cell:{c}")
                         for c in starts[i:i + 5]])
        extra = []
        if seat.history:
            extra.append(_btn("↩️ Отменить последний", "undo"))
        if len(starts) < 98:
            extra.append(_btn("🔀 Авто: доставить остальные", "auto"))
        if extra:
            rows.append(extra)
        return _kb(*rows) if rows else None
    rows = []
    free = [c for c in range(S.N * S.N) if c not in seat.enemy.shots]
    for i in range(0, len(free), 5):
        rows.append([_btn(S.cell_label(c), f"shot:{c}") for c in free[i:i + 5]])
    return _kb(*rows) if rows else None


def _own_text(seat: Seat) -> str:
    body = _grid(seat.player, own=True)
    if seat.phase == "over":
        return f"Твой флот:\n{body}\nПартия окончена."
    if seat.phase == "placing":
        placed = len(S.FLEET) - len(seat.to_place)
        return f"Твой флот (стоит: {placed} из {len(S.FLEET)}):\n{body}"
    return f"Твой флот:\n{body}"


def _own_kb(seat: Seat) -> InlineKeyboardMarkup | None:
    if seat.phase == "over":
        return None
    return _kb([_btn("🏳️ Сдаться", "giveup")])


async def _edit(bot, where: tuple[int, int] | None, text: str,
                kb: InlineKeyboardMarkup | None) -> None:
    if where is None:
        return
    try:
        await bot.edit_message_text(text, where[0], where[1],
                                    reply_markup=kb, parse_mode="HTML")
    except TelegramAPIError:
        pass


async def _paint(seat: Seat, bot) -> None:
    await _edit(bot, seat.main_msg, _main_text(seat), _main_kb(seat))
    await _edit(bot, seat.own_msg, _own_text(seat), _own_kb(seat))


def _purge_idle_private() -> None:
    now = time.monotonic()
    dead = [uid for uid, seat in PRIVATE_SEATS.items()
            if seat.phase != "over" and now - seat.last_active > IDLE_TIMEOUT]
    for uid in dead:
        del PRIVATE_SEATS[uid]


def _group_busy_for(user_id: int) -> str | None:
    if GROUP_SEAT is None or GROUP_SEAT.phase == "over" \
            or GROUP_SEAT.uid == user_id:
        return None
    if time.monotonic() - GROUP_SEAT.last_active > IDLE_TIMEOUT:
        return None
    return GROUP_SEAT.name


async def _drop_messages(bot, seat: Seat) -> None:
    """Старые доски — удалить, чтобы не жили кнопки прошлой партии."""
    for where in (seat.main_msg, seat.own_msg):
        if where is None:
            continue
        try:
            await bot.delete_message(where[0], where[1])
        except TelegramAPIError:
            pass


async def _new_seat(user, group: bool, auto: bool) -> Seat:
    """Создать стол. Проверки занятости — до вызова."""
    global GROUP_SEAT
    seat = Seat(
        uid=user.id, name=user.full_name,
        player=S.Board(), enemy=S.Board(), hunter=S.Hunter(),
    )
    if auto:
        seat.player = S.random_fleet(random.Random())
        seat.to_place = []
        _begin_battle(seat)
    else:
        seat.to_place = list(S.FLEET)
    if group:
        GROUP_SEAT = seat
    else:
        PRIVATE_SEATS[user.id] = seat
    logger.info("Морбой (%s): %s, режим %s", "флуд" if group else "личка",
                user.id, "авто" if auto else "руки")
    return seat


def _begin_battle(seat: Seat) -> None:
    seat.enemy = S.random_fleet(random.Random())
    seat.phase = "battle"
    seat.start = None


def _release(seat: Seat) -> None:
    global GROUP_SEAT
    if GROUP_SEAT is seat:
        GROUP_SEAT = None
    else:
        PRIVATE_SEATS.pop(seat.uid, None)


async def _owned(call: CallbackQuery) -> Seat | None:
    uid = call.from_user.id
    if _is_group_chat(call.message.chat):
        seat = GROUP_SEAT
        if seat is None or seat.phase == "over" or seat.uid != uid:
            if seat is not None and seat.phase != "over" and seat.uid != uid:
                await call.answer(f"Стол занят — играет {seat.name}.",
                                  show_alert=True)
            else:
                await call.answer("Партии нет — начни с !морскойбой.",
                                  show_alert=True)
            return None
    else:
        seat = PRIVATE_SEATS.get(uid)
        if seat is None or seat.phase == "over":
            await call.answer("Партии нет — начни с !морскойбой.",
                              show_alert=True)
            return None
    now = time.monotonic()
    if now - LAST_TAP.get(uid, 0.0) < THROTTLE_SEC:
        await call.answer()
        return None
    LAST_TAP[uid] = now
    seat.last_active = now
    return seat


@seabattle_router.message(Exact("!морскойбой"))
async def seabattle_cmd(message: Message):
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
            await message.answer("Все столы заняты — подожди, кто-нибудь доиграет.")
            return
    await message.answer(
        "⚓ <b>Морской бой</b> — как расставляем корабли?",
        reply_markup=_kb([_btn("🚢 Сам", "mode:manual"),
                          _btn("🔀 Авто", "mode:auto")]),
        parse_mode="HTML",
    )


@seabattle_router.callback_query(F.data.startswith(f"{CB}:mode:"))
async def cb_mode(call: CallbackQuery):
    auto = call.data.split(":")[-1] == "auto"
    group = _is_group_chat(call.message.chat)
    uid = call.from_user.id
    if group:
        busy = _group_busy_for(uid)
        if busy:
            await call.answer(f"Стол занят — играет {busy}.", show_alert=True)
            return
        took_over = GROUP_SEAT is not None and GROUP_SEAT.uid != uid
        if took_over and GROUP_SEAT is not None:
            await _drop_messages(call.bot, GROUP_SEAT)
    else:
        _purge_idle_private()
        if uid not in PRIVATE_SEATS and len(PRIVATE_SEATS) >= MAX_PRIVATE:
            await call.answer("Все столы заняты — подожди.", show_alert=True)
            return
        took_over = False
        old = PRIVATE_SEATS.get(uid)
        if old is not None:
            await _drop_messages(call.bot, old)
    seat = await _new_seat(call.from_user, group, auto)
    seat.main_msg = (call.message.chat.id, call.message.message_id)
    own = await call.message.answer(
        _own_text(seat), reply_markup=_own_kb(seat), parse_mode="HTML")
    seat.own_msg = (own.chat.id, own.message_id)
    await call.answer()
    if took_over:
        await call.message.answer("Прошлый стол завис — забираю его себе.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data.startswith(f"{CB}:cell:"))
async def cb_cell(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing" or seat.start is not None:
        await call.answer()
        return
    try:
        start = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    size = seat.to_place[0]
    if start not in S.valid_starts(seat.player, size):
        await call.answer("Сюда не встанет.", show_alert=True)
        await _paint(seat, call.bot)
        return
    ends = S.valid_ends(seat.player, start, size)
    if not ends:
        await call.answer("Сюда не встанет.", show_alert=True)
        return
    if len(ends) == 1 and ends[0] == start:
        # Однопалубный — ставить сразу, без экрана конца.
        S.place(seat.player, {start})
        seat.history.append({start})
        seat.to_place.pop(0)
        await call.answer(f"{S.cell_label(start)} — встал.")
        if not seat.to_place:
            _begin_battle(seat)
            await call.message.answer("Флот готов! Ты стреляешь первым.")
        await _paint(seat, call.bot)
        return
    seat.start = start
    await call.answer()
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data.startswith(f"{CB}:end:"))
async def cb_end(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing" or seat.start is None:
        await call.answer()
        return
    try:
        end = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    size = seat.to_place[0]
    start = seat.start
    if end not in S.valid_ends(seat.player, start, size):
        seat.start = None
        await call.answer("Уже не встанет — выбери нос заново.",
                          show_alert=True)
        await _paint(seat, call.bot)
        return
    if S.row_of(end) == S.row_of(start):
        cells = {r for r in range(min(start, end), max(start, end) + 1)}
    else:
        lo, hi = sorted((start, end))
        cells = {c for c in range(lo, hi + 1, S.N)}
    if len(cells) != size or not S.can_place(seat.player, cells):
        seat.start = None
        await call.answer("Уже не встанет — выбери нос заново.",
                          show_alert=True)
        await _paint(seat, call.bot)
        return
    S.place(seat.player, cells)
    seat.history.append(set(cells))
    seat.to_place.pop(0)
    seat.start = None
    await call.answer("Встал.")
    if not seat.to_place:
        _begin_battle(seat)
        await call.message.answer("Флот готов! Ты стреляешь первым.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:back")
async def cb_back(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
        await call.answer()
        return
    seat.start = None
    await call.answer()
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:undo")
async def cb_undo(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing" or seat.start is not None:
        await call.answer()
        return
    if not seat.history:
        await call.answer()
        await _paint(seat, call.bot)
        return
    cells = seat.history.pop()
    seat.player.ships = [s for s in seat.player.ships if s != cells]
    seat.to_place.insert(0, len(cells))
    await call.answer("Убрал последний.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:auto")
async def cb_auto(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing" or seat.start is not None:
        await call.answer()
        return
    rng = random.Random()
    rest = S.Board()
    rest.ships = [set(s) for s in seat.player.ships]
    for size in seat.to_place:
        options = []
        for start in range(S.N * S.N):
            for vertical in (False, True):
                cells = S.ship_cells(start, size, vertical)
                if cells is not None and S.can_place(rest, cells):
                    options.append(cells)
        if not options:  # не влезло — такого быть не должно, но не виснем
            await call.answer("Не влезло, расставь руками.", show_alert=True)
            return
        S.place(rest, set(rng.choice(options)))
    seat.player = rest
    seat.to_place = []
    _begin_battle(seat)
    await call.answer("Доставил автоматом.")
    await call.message.answer("Флот готов! Ты стреляешь первым.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data.startswith(f"{CB}:shot:"))
async def cb_shot(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "battle":
        await call.answer()
        return
    try:
        cell = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if cell in seat.enemy.shots:
        await call.answer("Сюда уже стрелял.", show_alert=True)
        return
    result, _ = S.shoot(seat.enemy, cell)
    if S.all_sunk(seat.enemy):
        seat.phase, seat.winner = "over", 0
        await call.answer("🎉 Победа!")
        _release(seat)
        await _paint(seat, call.bot)
        return
    # Ответ бота: мимо — ход тебе, попал — бьёт ещё? Нет: по очереди, как в
    # дворовом бое без «дополнительного выстрела».
    bot_cell = seat.hunter.next_shot()
    bot_result, bot_sunk = S.shoot(seat.player, bot_cell)
    seat.hunter.report(bot_cell, bot_result, bot_sunk)
    if S.all_sunk(seat.player):
        seat.phase, seat.winner = "over", 1
        await call.answer("🤖 Бот потопил твой флот.")
        _release(seat)
        await _paint(seat, call.bot)
        return
    await call.answer({"miss": "Мимо.", "hit": "Попал!",
                       "sunk": "Потопил!"}[result])
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:giveup")
async def cb_giveup(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None:
        return
    if seat.phase == "placing":
        _release(seat)
        logger.info("Морбой: %s отменил расстановку", call.from_user.id)
        await call.answer("Расстановка отменена.")
        await _edit(call.bot, seat.main_msg, "Партия отменена.", None)
        await _edit(call.bot, seat.own_msg, "Партия отменена.", None)
        return
    seat.phase, seat.winner = "over", 1
    logger.info("Морбой: %s сдался", call.from_user.id)
    await call.answer("Сдался — бот выиграл.")
    _release(seat)
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:new")
async def cb_new(call: CallbackQuery):
    uid = call.from_user.id
    group = _is_group_chat(call.message.chat)
    if group:
        busy = _group_busy_for(uid)
        if busy:
            await call.answer(f"Стол занят — играет {busy}.", show_alert=True)
            return
    else:
        _purge_idle_private()
        if uid not in PRIVATE_SEATS and len(PRIVATE_SEATS) >= MAX_PRIVATE:
            await call.answer("Все столы заняты — подожди.", show_alert=True)
            return
    await call.answer()
    try:
        await call.message.edit_text(
            "⚓ <b>Морской бой</b> — как расставляем корабли?",
            reply_markup=_kb([_btn("🚢 Сам", "mode:manual"),
                              _btn("🔀 Авто", "mode:auto")]),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        pass

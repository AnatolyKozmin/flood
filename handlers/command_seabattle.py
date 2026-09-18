"""Морской бой с ботом 10 на 10 — в личке и во флуде.

Расстановка на выбор: сам стрелками (корабль-призрак двигается ◀️▶️⬆️⬇️,
↪️ поворачивает вокруг носа, ✅ ставит) или авто. Порядок: сначала
четырёхпалубный, потом 3, 2 и 1. В личке у каждого свой стол (до 50),
во флуде стол один на всех. Кнопки жмёт только занявший стол. Бот ищет
по шахматке и добивает раненые.

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
    cursor: int = 0  # нос корабля-призрака при ручной расстановке
    vertical: bool = False  # призрак стоит вертикально
    shot_row: int | None = None  # выбранный ряд обстрела
    main_msg: tuple[int, int] | None = None  # (chat_id, msg_id) главного
    own_msg: tuple[int, int] | None = None  # (chat_id, msg_id) своего поля
    winner: int | None = None
    last_active: float = field(default_factory=time.monotonic)


PRIVATE_SEATS: dict[int, Seat] = {}
GROUP_SEAT: Seat | None = None
LAST_TAP: dict[int, float] = {}
CHOICE: dict[int, tuple[int, int]] = {}  # tg_id -> (chat, msg) выбора режима


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


def _grid(board: S.Board, *, own: bool,
            ghost: set[int] = frozenset(), ghost_ok: bool = True) -> str:
    """Поле текстом. Своё: корабли видны; чужое: только попадания.
    Призрак — двигающийся корабль: ⛴️ влезает, 🟥 нет."""
    rows = []
    for r in range(S.N):
        line = []
        for c in range(S.N):
            cell = r * S.N + c
            if cell in ghost:
                line.append("⛴️" if ghost_ok else "🟥")
            elif cell in board.sunk:
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


def _ghost(seat: Seat) -> tuple[set[int], bool]:
    """Клетки призрака и влезает ли он."""
    cells = S.ship_cells(seat.cursor, seat.to_place[0], seat.vertical)
    if cells is None:
        return set(), False
    return cells, S.can_place(seat.player, cells)


def _default_cursor(board: S.Board, size: int) -> tuple[int, bool]:
    """Первый влезающий нос построчно, сначала горизонтально."""
    for start in range(S.N * S.N):
        cells = S.ship_cells(start, size, False)
        if cells is not None and S.can_place(board, cells):
            return start, False
    for start in range(S.N * S.N):
        cells = S.ship_cells(start, size, True)
        if cells is not None and S.can_place(board, cells):
            return start, True
    return 0, False


def _ship_word(size: int) -> str:
    return {4: "четырёхпалубный", 3: "трёхпалубный",
            2: "двухпалубный", 1: "однопалубный"}[size]


def _main_text(seat: Seat) -> str:
    head = (f"⚓ <b>МОРСКОЙ БОЙ</b>\nИгрок: {html.escape(seat.name)}\n")
    if seat.phase == "placing":
        size = seat.to_place[0]
        left = len(seat.to_place)
        ghost, ok = _ghost(seat)
        body = _grid(seat.player, own=True, ghost=ghost, ghost_ok=ok)
        tail = (f"\nСтавь {_ship_word(size)} (осталось кораблей: {left}): "
                f"двигай стрелками, ↪️ повернуть, ✅ поставить.")
        return head + body + tail
    body = _grid(seat.enemy, own=False)
    if seat.phase == "over":
        tail = ("\n🎉 <b>Ты потопил весь флот!</b>" if seat.winner == 0
                else "\n🤖 <b>Бот потопил твой флот.</b>")
        return head + body + tail
    if seat.shot_row is None:
        return head + body + "\nТвой выстрел — выбери ряд."
    return head + body + "\nТвой выстрел — жми по клетке."


def _rows_with(cells: set[int] | list[int]) -> list[int]:
    """Ряды, где есть что выбирать, по порядку."""
    return sorted({c // S.N for c in cells})


def _main_kb(seat: Seat) -> InlineKeyboardMarkup | None:
    if seat.phase == "over":
        return _kb([_btn("🔄 Новая", "new")])
    if seat.phase == "placing":
        rows = [[_btn("◀️", "mv:l"), _btn("▶️", "mv:r"),
                 _btn("⬆️", "mv:u"), _btn("⬇️", "mv:d")],
                [_btn("↪️ Повернуть", "rot"), _btn("✅ Поставить", "ok")],
                [_btn("↩️ Отменить", "undo"),
                 _btn("🔀 Авто", "auto")]]
        return _kb(*rows)
    if seat.shot_row is None:
        unknown = [c for c in range(S.N * S.N) if c not in seat.enemy.shots]
        rows = [[_btn(f"Ряд {r + 1}", f"brow:{r}")
                 for r in _rows_with(unknown)[i:i + 5]]
                for i in range(0, len(_rows_with(unknown)), 5)]
        return _kb(*rows) if rows else None
    in_row = [c for c in range(seat.shot_row * S.N, (seat.shot_row + 1) * S.N)
              if c not in seat.enemy.shots]
    rows = [[_btn(S.cell_label(c), f"shot:{c}") for c in in_row[i:i + 5]]
            for i in range(0, len(in_row), 5)]
    rows.append([_btn("↩️ Ряды", "brows")])
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
        # Только именованные: в aiogram 3.27 вторым позиционным идёт
        # business_connection_id и порядок легко перепутать (уже было).
        await bot.edit_message_text(text, chat_id=where[0],
                                    message_id=where[1],
                                    reply_markup=kb, parse_mode="HTML")
    except TelegramAPIError:
        logger.warning("Морбой: не правил сообщение %s", where, exc_info=True)


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
        seat.cursor, seat.vertical = _default_cursor(seat.player,
                                                    seat.to_place[0])
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
    # Старый выбор режима затираем, иначе висят мёртвые «Сам/Авто».
    old_choice = CHOICE.pop(uid, None)
    if old_choice is not None:
        try:
            await message.bot.delete_message(old_choice[0], old_choice[1])
        except TelegramAPIError:
            pass
    sent = await message.answer(
        "⚓ <b>Морской бой</b> — как расставляем корабли?",
        reply_markup=_kb([_btn("🚢 Сам", "mode:manual"),
                          _btn("🔀 Авто", "mode:auto")]),
        parse_mode="HTML",
    )
    CHOICE[uid] = (sent.chat.id, sent.message_id)


@seabattle_router.callback_query(F.data.startswith(f"{CB}:mode:"))
async def cb_mode(call: CallbackQuery):
    uid = call.from_user.id
    now = time.monotonic()
    if now - LAST_TAP.get(uid, 0.0) < THROTTLE_SEC:
        await call.answer()  # двойной тап по «Сам/Авто» — игнор
        return
    LAST_TAP[uid] = now
    auto = call.data.split(":")[-1] == "auto"
    group = _is_group_chat(call.message.chat)
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
    # Выбор стал доской — из списка на удаление убираем.
    CHOICE.pop(call.from_user.id, None)
    seat.main_msg = (call.message.chat.id, call.message.message_id)
    own = await call.message.answer(
        _own_text(seat), reply_markup=_own_kb(seat), parse_mode="HTML")
    seat.own_msg = (own.chat.id, own.message_id)
    await call.answer()
    if took_over:
        await call.message.answer("Прошлый стол завис — забираю его себе.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data.startswith(f"{CB}:mv:"))
async def cb_move(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
        await call.answer()
        return
    direction = call.data.split(":")[-1]
    r, c = seat.cursor // S.N, seat.cursor % S.N
    delta = {"l": (0, -1), "r": (0, 1), "u": (-1, 0), "d": (1, 0)}.get(direction)
    if delta is None:
        await call.answer()
        return
    nr, nc = r + delta[0], c + delta[1]
    if 0 <= nr < S.N and 0 <= nc < S.N:
        seat.cursor = nr * S.N + nc
    await call.answer()
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:rot")
async def cb_rot(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
        await call.answer()
        return
    seat.vertical = not seat.vertical  # поворот вокруг носа; не влез — покраснеет
    await call.answer()
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:ok")
async def cb_ok(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
        await call.answer()
        return
    ghost, ok = _ghost(seat)
    if not ok:
        await call.answer("Здесь не встанет — подвинь или поверни.",
                          show_alert=True)
        return
    S.place(seat.player, ghost)
    seat.history.append(set(ghost))
    seat.to_place.pop(0)
    await call.answer("Встал.")
    if not seat.to_place:
        _begin_battle(seat)
        await call.message.answer("Флот готов! Ты стреляешь первым.")
    else:
        seat.cursor, seat.vertical = _default_cursor(
            seat.player, seat.to_place[0])
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:undo")
async def cb_undo(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
        await call.answer()
        return
    if not seat.history:
        await call.answer()
        await _paint(seat, call.bot)
        return
    cells = seat.history.pop()
    seat.player.ships = [s for s in seat.player.ships if s != cells]
    seat.to_place.insert(0, len(cells))
    seat.cursor, seat.vertical = _default_cursor(seat.player,
                                                seat.to_place[0])
    await call.answer("Убрал последний.")
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:auto")
async def cb_auto(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "placing":
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


@seabattle_router.callback_query(F.data.startswith(f"{CB}:prow:"))
async def cb_prow(call: CallbackQuery):
    await call.answer("Выбор рядами убран — двигай корабль стрелками.")


@seabattle_router.callback_query(F.data.startswith(f"{CB}:brow:"))
async def cb_brow(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "battle":
        await call.answer()
        return
    try:
        row = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    unknown = [c for c in range(S.N * S.N) if c not in seat.enemy.shots]
    if row not in _rows_with(unknown):
        await call.answer("Ряд уже прострелян.", show_alert=True)
        await _paint(seat, call.bot)
        return
    seat.shot_row = row
    await call.answer()
    await _paint(seat, call.bot)


@seabattle_router.callback_query(F.data == f"{CB}:brows")
async def cb_brows(call: CallbackQuery):
    seat = await _owned(call)
    if seat is None or seat.phase != "battle":
        await call.answer()
        return
    seat.shot_row = None
    await call.answer()
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
        seat.shot_row = None
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
        seat.shot_row = None
        await call.answer("🤖 Бот потопил твой флот.")
        _release(seat)
        await _paint(seat, call.bot)
        return
    if seat.shot_row is not None and not any(
            c not in seat.enemy.shots
            for c in range(seat.shot_row * S.N, (seat.shot_row + 1) * S.N)):
        seat.shot_row = None  # ряд прострелян — вернуться к выбору рядов
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

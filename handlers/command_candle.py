"""Онлайн-свечка !свечка: общая часовня 4 на 4, суточная.

С утра сетка чистая, каждый зажигает по одной свечке в день (день
московский). Занятые слоты подписаны именами. Место можно менять сколько
угодно до сохранения: выбрал слот — предпросмотр — «Сохранить» или
«Другое место». После сохранения переставить нельзя.

Доска у каждого своя (и во флуде тоже): чужие кнопки отвечают «это чужая
доска». Данные общие — сетка одна на всех.
"""
import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database.candle_dao import CandleDAO
from database.engine import async_session_maker
from utils.format import DIVIDER
from utils.helpers import moscow_today

logger = logging.getLogger(__name__)

candle_router = Router()
CB = "sv"

PENDING: dict[int, int] = {}  # tg_id -> выбранный, но ещё не saved слот
MSG_OWNER: dict[tuple[int, int], int] = {}  # (chat_id, msg_id) -> tg_id


class Exact(BaseFilter):
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd.strip().casefold()

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() == self.cmd


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


def _today() -> str:
    return moscow_today().isoformat()


async def _day_state(day: str, user_id: int):
    """Свежая сетка дня: (слоты {slot: row}, мой слот | None)."""
    async with async_session_maker() as session:
        dao = CandleDAO(session)
        lights = await dao.day_lights(day)
        mine = await dao.user_light(day, user_id)
    return {r.slot: r for r in lights}, (mine.slot if mine else None)


def _board(day: str, by_slot: dict, pending: int | None,
           viewer_id: int, mine: int | None,
           ) -> tuple[str, InlineKeyboardMarkup | None]:
    label = moscow_today().strftime("%d.%m")
    cells = []
    for i in range(16):
        if i == pending:
            cells.append("✨")
        elif i in by_slot:
            cells.append("🕯️")
        else:
            cells.append("⬜")
    grid = "\n".join("".join(cells[r * 4:(r + 1) * 4]) for r in range(4))

    lines = [f"🕯️ <b>Свечки на {label}</b>", grid, ""]
    for i in range(16):
        row = by_slot.get(i)
        if row is None:
            continue
        name = html.escape(row.display or "без имени")
        mark = " (твоя)" if row.user_id == viewer_id else ""
        lines.append(f"• {i + 1} — {name}{mark}")
    if pending is not None:
        lines += ["", f"Твоё место: <b>{pending + 1}</b> — сохранить?"]
        kb = _kb([_btn("✅ Сохранить", "save"), _btn("↩️ Другое место", "back")])
    elif mine is not None:
        lines += ["", "Твоя свечка уже горит. Приходи завтра."]
        kb = None
    elif len(by_slot) >= 16:
        lines += ["", "Все места заняты. Приходи завтра."]
        kb = None
    else:
        lines += ["", "Выбери свободное место кнопками."]
        rows = []
        free = [i for i in range(16) if i not in by_slot]
        for j in range(0, len(free), 4):
            rows.append([_btn(str(i + 1), f"p:{i}") for i in free[j:j + 4]])
        kb = _kb(*rows)
    return "\n".join(lines), kb


async def _show(target: Message | CallbackQuery, user_id: int) -> None:
    day = _today()
    by_slot, mine = await _day_state(day, user_id)
    pending = PENDING.get(user_id)
    if pending is not None and (pending in by_slot or mine is not None):
        pending = PENDING.pop(user_id, None)
    text, kb = _board(day, by_slot, pending, user_id, mine)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb,
                                           parse_mode="HTML")
            return
        except TelegramAPIError:
            pass
        await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        sent = await target.answer(text, reply_markup=kb, parse_mode="HTML")
        MSG_OWNER[(sent.chat.id, sent.message_id)] = user_id


def _owner(call: CallbackQuery) -> int | None:
    """Владелец доски. Чужая — алерт и None."""
    owner = MSG_OWNER.get((call.message.chat.id, call.message.message_id))
    if owner is None:
        owner = call.from_user.id
        MSG_OWNER[(call.message.chat.id, call.message.message_id)] = owner
    if owner != call.from_user.id:
        return None
    return owner


@candle_router.message(Exact("!свечка"))
async def candle_cmd(message: Message):
    await _show(message, message.from_user.id)


@candle_router.callback_query(F.data.startswith(f"{CB}:p:"))
async def cb_pick(call: CallbackQuery):
    if _owner(call) is None:
        await call.answer("Это чужая доска — вызови !свечка.", show_alert=True)
        return
    try:
        slot = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    if slot not in range(16):
        await call.answer()
        return
    day = _today()
    by_slot, mine = await _day_state(day, call.from_user.id)
    if mine is not None:
        await call.answer("Твоя свечка уже горит.", show_alert=True)
        await _show(call, call.from_user.id)
        return
    if slot in by_slot:
        await call.answer("Это место уже заняли.", show_alert=True)
        await _show(call, call.from_user.id)
        return
    PENDING[call.from_user.id] = slot
    await call.answer()
    await _show(call, call.from_user.id)


@candle_router.callback_query(F.data == f"{CB}:back")
async def cb_back(call: CallbackQuery):
    if _owner(call) is None:
        await call.answer("Это чужая доска — вызови !свечка.", show_alert=True)
        return
    PENDING.pop(call.from_user.id, None)
    await call.answer()
    await _show(call, call.from_user.id)


@candle_router.callback_query(F.data == f"{CB}:save")
async def cb_save(call: CallbackQuery):
    if _owner(call) is None:
        await call.answer("Это чужая доска — вызови !свечка.", show_alert=True)
        return
    uid = call.from_user.id
    pending = PENDING.get(uid)
    if pending is None:
        await call.answer()
        await _show(call, uid)
        return
    day = _today()
    async with async_session_maker() as session:
        result = await CandleDAO(session).light(
            day, pending, uid,
            (call.from_user.username or "").lstrip("@"),
            call.from_user.full_name,
        )
    if result == "already":
        PENDING.pop(uid, None)
        await call.answer("Твоя свечка уже горит.", show_alert=True)
        await _show(call, uid)
        return
    if result == "taken":
        PENDING.pop(uid, None)
        await call.answer("Это место только что заняли.", show_alert=True)
        await _show(call, uid)
        return
    PENDING.pop(uid, None)
    logger.info("Свечка: %s зажёг слот %s", uid, pending)
    await call.answer("🕯️ Свечка поставлена!")
    await call.message.answer("🕯️ Свечка поставлена!")
    await _show(call, uid)

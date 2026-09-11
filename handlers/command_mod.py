"""Теневая модерация в личке: !модерация.

Отдельная панель, обычная админка её не видит. Доступ — только владелец
(OWNER_ID из .env) плюс максимум один заместитель, которого владелец
назначает и снимает здесь же.

Что умеет: замутить и размутить трёх конкретных людей из базы. Замученный
молчит во всех чатах (сообщения трёт DeadMuteMiddleware, как у мёртвых),
но во флуде об этом ни слова — статус виден только тут. Если человек
и так мёртв после дуэли, показываем это и размутить не даём: сначала
пусть воскреснет.
"""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select

from database.duel_dao import DuelDAO
from database.engine import async_session_maker
from database.mod_dao import DeputyDAO, MuteDAO
from database.models import Activists
from database.profile_models import ActivistLink
from database.stats_dao import StatsDAO
from handlers.command_admin import owner_id
from utils.format import DIVIDER
from utils.helpers import first_last

logger = logging.getLogger(__name__)

mod_router = Router()

CB = "md"

# Те, кем управляет панель. Полные ФИО — ищем точным совпадением.
WATCHED = (
    "Пакина Злата Сергеевна",
    "Никитин Олег Олегович",
    "Магомедов Руслан Магомедович",
    "Брагинец Николай Валентинович",
)


async def is_mod(tg_id: int) -> bool:
    """Свой для теневой модерации: владелец или назначенный заместитель."""
    if tg_id == owner_id():
        return True
    async with async_session_maker() as session:
        deputy = await DeputyDAO(session).get()
    return deputy is not None and deputy.tg_id == tg_id


class ModAccess(BaseFilter):
    """Личка + владелец или заместитель. Чужим — молчание."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        chat = event.chat if isinstance(event, Message) else event.message.chat
        if chat.type != "private" or user is None:
            return False
        return await is_mod(user.id)


class Mod(StatesGroup):
    wait_deputy = State()


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


async def _paint(target: Message | CallbackQuery, text: str,
                 kb: InlineKeyboardMarkup | None) -> None:
    message = target if isinstance(target, Message) else target.message
    if isinstance(target, CallbackQuery):
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


async def _resolve_tg(session, activist: Activists) -> int | None:
    """Telegram id человека: привязка анкеты надёжнее, статистика писавших
    — запасной. Нет ни того ни другого — замутить технически некого."""
    link = (await session.execute(
        select(ActivistLink).where(ActivistLink.activist_id == activist.id)
    )).scalars().first()
    if link is not None:
        return int(link.tg_id)
    tag = (activist.tg_username or "").strip().lstrip("@")
    if tag:
        found = await StatsDAO(session).user_by_username(tag)
        if found is not None:
            return int(found.user_id)
    return None


def _name(activist: Activists) -> str:
    fio = (activist.fio or "").strip()
    return html.escape(first_last(fio) if fio else "без имени")


async def _screen_text(is_owner: bool) -> tuple[str, InlineKeyboardMarkup]:
    """Текст панели и кнопки. Всё считается заново при каждой отрисовке."""
    async with async_session_maker() as session:
        dao, deputies = MuteDAO(session), DeputyDAO(session)
        deputy = await deputies.get()
        duel = DuelDAO(session)

        lines = ["🔇 <b>Модерация</b>", DIVIDER]
        rows: list[list[InlineKeyboardButton]] = []
        for fio in WATCHED:
            activist = (await session.execute(
                select(Activists).where(Activists.fio == fio)
            )).scalars().first()
            if activist is None:
                lines.append(f"• {html.escape(fio)} — <i>нет в базе</i>")
                continue

            tg_id = await _resolve_tg(session, activist)
            name = _name(activist)
            tag = (activist.tg_username or "").strip().lstrip("@")
            if tag:
                name += f" (@{html.escape(tag)})"

            if tg_id is None:
                lines.append(f"• {name} — <i>id неизвестен, мут невозможен</i>")
                continue

            muted = await dao.is_muted(tg_id) is not None
            dead = await duel.is_dead_anywhere(tg_id) is not None
            if dead and muted:
                lines.append(f"• {name} — 💀 мёртв, 🔇 замучен")
            elif dead:
                lines.append(f"• {name} — 💀 мёртв")
            elif muted:
                lines.append(f"• {name} — 🔇 замучен")
                rows.append([_btn(f"🔊 Размутить {first_last(activist.fio or '')}",
                                  f"unmute:{tg_id}")])
            else:
                lines.append(f"• {name} — ✅ свободен")
                rows.append([_btn(f"🔇 Замутить {first_last(activist.fio or '')}",
                                  f"mute:{activist.id}")])
            if muted and dead:
                lines.append("<i>Размут — только после воскрешения.</i>")

        if is_owner:
            lines += ["", "<b>Заместитель:</b>"]
            if deputy is None:
                lines.append("<i>Никого нет.</i>")
            else:
                who = (f"@{html.escape(deputy.username.lstrip('@'))}"
                       if deputy.username else f"<code>{deputy.tg_id}</code>")
                lines.append(f"• {who}")
            rows.append([_btn("👤 Заместитель", "deputy")])

        rows.append([_btn("❌ Закрыть", "close")])

    return "\n".join(lines), _kb(*rows) if rows else None


async def _show(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    is_owner = target.from_user.id == owner_id()
    text, kb = await _screen_text(is_owner)
    await _paint(target, text, kb)


@mod_router.message(F.chat.type == "private", F.text.lower() == "!модерация", ModAccess())
async def mod_cmd(message: Message, state: FSMContext):
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    await _show(message, state)


@mod_router.callback_query(F.data == f"{CB}:panel", ModAccess())
async def cb_panel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show(call, state)


@mod_router.callback_query(F.data == f"{CB}:close", ModAccess())
async def cb_close(call: CallbackQuery, state: FSMContext):
    """Выйти из модерации: закрыть панель, чтобы не висела в личке."""
    await state.clear()
    await call.answer()
    try:
        await call.message.delete()
    except TelegramAPIError:
        pass


@mod_router.callback_query(F.data.startswith(f"{CB}:mute:"), ModAccess())
async def cb_mute(call: CallbackQuery, state: FSMContext):
    try:
        activist_id = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    async with async_session_maker() as session:
        activist = await session.get(Activists, activist_id)
        if activist is None:
            await call.answer("Его уже нет в базе", show_alert=True)
        else:
            tg_id = await _resolve_tg(session, activist)
            if tg_id is None:
                await call.answer("id неизвестен — мут невозможен", show_alert=True)
            else:
                ok = await MuteDAO(session).mute(
                    tg_id,
                    (activist.tg_username or "").strip().lstrip("@"),
                    first_last(activist.fio or ""),
                    activist.id, call.from_user.id,
                )
                await call.answer("Замучен" if ok else "Он уже замучен")
                logger.info("Модератор %s замутил %s", call.from_user.id, tg_id)
    await _show(call, state)


@mod_router.callback_query(F.data.startswith(f"{CB}:unmute:"), ModAccess())
async def cb_unmute(call: CallbackQuery, state: FSMContext):
    try:
        user_id = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    async with async_session_maker() as session:
        dao = MuteDAO(session)
        if await DuelDAO(session).is_dead_anywhere(user_id) is not None:
            await call.answer("Он мёртв — сначала пусть воскреснет", show_alert=True)
        else:
            removed = await dao.unmute(user_id)
            await call.answer("Размучен" if removed else "Он уже свободен")
            logger.info("Модератор %s размутил %s", call.from_user.id, user_id)
    await _show(call, state)


class OwnerOnly(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        chat = event.chat if isinstance(event, Message) else event.message.chat
        if chat.type != "private":
            return False
        return event.from_user is not None and event.from_user.id == owner_id()


@mod_router.callback_query(F.data == f"{CB}:dep_add", OwnerOnly())
async def cb_dep_add(call: CallbackQuery, state: FSMContext):
    await call.answer()
    async with async_session_maker() as session:
        if await DeputyDAO(session).get() is not None:
            await call.answer("Место занято — сначала удали", show_alert=True)
            await _deputy_screen(call)
            return
    await state.set_state(Mod.wait_deputy)
    await _paint(
        call,
        "➕ <b>Кого назначить?</b>\n" + DIVIDER + "\n"
        "Пришли @тег, id числом или перешли сюда его сообщение.\n\n"
        "<i>Место одно: пока доступ у кого-то есть, нового не назначить.</i>",
        _kb([_btn("↩️ Назад", "deputy")]),
    )


async def _deputy_screen(target: Message | CallbackQuery) -> None:
    """Отдельная страница заместителя: есть он или нет + добавить/удалить."""
    async with async_session_maker() as session:
        deputy = await DeputyDAO(session).get()
    if deputy is None:
        text = (
            "👤 <b>Заместитель</b>\n" + DIVIDER + "\n"
            "Никого нет — модерацию видишь только ты.\n\n"
            "<i>Доступ включает только эту панель, "
            "обычной админки зам не видит.</i>"
        )
        kb = _kb(
            [_btn("➕ Добавить", "dep_add")],
            [_btn("↩️ В панель", "panel")],
            [_btn("❌ Закрыть", "close")],
        )
    else:
        who = (f"@{html.escape(deputy.username.lstrip('@'))}"
               if deputy.username else f"<code>{deputy.tg_id}</code>")
        text = (
            "👤 <b>Заместитель</b>\n" + DIVIDER + "\n"
            f"• {who}\n\n"
            "<i>Место одно — нового назначить нельзя, пока есть этот.</i>"
        )
        kb = _kb(
            [_btn("🗑 Удалить", "dep_del")],
            [_btn("↩️ В панель", "panel")],
            [_btn("❌ Закрыть", "close")],
        )
    await _paint(target, text, kb)


@mod_router.callback_query(F.data == f"{CB}:deputy", OwnerOnly())
async def cb_deputy(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await _deputy_screen(call)


async def _resolve_person(bot: Bot, message: Message) -> tuple[int | None, str | None]:
    """(tg_id, тег) из пересланного, id или @тега. Порядок — от надёжного."""
    forwarded = getattr(message, "forward_from", None)
    if forwarded is not None:
        return forwarded.id, forwarded.username

    # Новое API пересылок (aiogram 3.x): forward_from часто пуст из-за
    # приватности, а данные лежат в forward_origin.
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        sender = getattr(origin, "sender_user", None)
        if sender is not None:
            return sender.id, getattr(sender, "username", None)
        # HiddenUser / Chat / Channel без user id — назначить некого.
        if getattr(origin, "type", "") in ("hidden_user", "chat", "channel"):
            return None, None

    if message.text:
        text = message.text.strip().split()[0]
        if text.lstrip("-").isdigit():
            return int(text), None
        clean = text.lstrip("@").strip()
        if clean:
            needle = clean.casefold()
            async with async_session_maker() as session:
                link = (await session.execute(
                    select(ActivistLink)
                    .where(ActivistLink.username.is_not(None))
                )).scalars().all()
                for row in link:
                    if (row.username or "").lstrip("@").casefold() == needle:
                        return int(row.tg_id), row.username
                found = await StatsDAO(session).user_by_username(clean)
                if found is not None:
                    return int(found.user_id), found.username or clean
            try:
                chat = await bot.get_chat(f"@{clean}")
                if chat.type == "private":
                    return int(chat.id), chat.username or clean
            except Exception:
                logger.debug("get_chat по тегу @%s не сработал", clean, exc_info=True)
    return None, None


@mod_router.message(Mod.wait_deputy, OwnerOnly())
async def on_deputy(message: Message, state: FSMContext):
    tg_id, username = await _resolve_person(message.bot, message)
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    if tg_id is None:
        await message.answer(
            "Не понял, кто это. Нужен @тег, id числом или пересланное сообщение."
        )
        return
    if tg_id == owner_id():
        await message.answer("Это ты — у тебя и так полный доступ.")
        return
    async with async_session_maker() as session:
        granted = await DeputyDAO(session).grant(tg_id, username or "", message.from_user.id)
    if not granted:
        await message.answer("Пока доступ у кого-то есть — сначала удали.")
        await _deputy_screen(message)
        return
    logger.info("Владелец %s назначил заместителя %s", message.from_user.id, tg_id)
    await _show(message, state)


@mod_router.callback_query(F.data == f"{CB}:dep_del", OwnerOnly())
async def cb_dep_del(call: CallbackQuery, state: FSMContext):
    async with async_session_maker() as session:
        removed = await DeputyDAO(session).revoke()
    await call.answer("Доступ забран" if removed else "И так никого")
    logger.info("Владелец %s забрал заместителя", call.from_user.id)
    await _deputy_screen(call)

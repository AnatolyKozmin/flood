"""Админ-панель: только в личке и только для своих.

Доступ устроен в два уровня, чтобы не было курицы с яйцом:
  • владелец — OWNER_ID в .env, его нельзя разжаловать;
  • админы — таблица bot_admins, владелец добавляет и убирает их командой,
    без правки .env и без перезапуска бота.

Чужим бот на команду не отвечает вообще: молчание не подсказывает, что
панель вообще существует.

Что умеет: выгрузить состав в Excel, принять поправленный файл обратно и
показать, сколько актива уже зарегистрировалось в боте.
"""
import html
import io
import logging
import os
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database.admin_dao import AdminDAO, PendingDAO, RosterDAO
from database.duel_dao import DuelDAO
from database.models import Activists
from database.profile_models import ActivistLink
from sqlalchemy import func, select
from database.engine import async_session_maker
from database.stats_dao import StatsDAO
from utils.format import DIVIDER
from utils.helpers import first_last, moscow_today
from utils.roster_excel import build_workbook, parse_workbook
from utils.stats import plural

logger = logging.getLogger(__name__)

admin_router = Router()

CB = "ad"
MAX_FILE_BYTES = 5 * 1024 * 1024     # состав актива — это десятки килобайт
PREVIEW_LIMIT = 12                   # сколько строк показывать в сводке


def owner_id() -> int | None:
    raw = os.getenv("OWNER_ID", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        logger.error("OWNER_ID должен быть числом, а не %r", raw)
        return None


async def is_admin(tg_id: int) -> bool:
    if tg_id == owner_id():
        return True
    async with async_session_maker() as session:
        return await AdminDAO(session).exists(tg_id)


class AdminPrivate(BaseFilter):
    """Личка + свой. Всё вместе, чтобы не забыть половину в каком-нибудь хендлере."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        chat = event.chat if isinstance(event, Message) else event.message.chat
        if chat.type != "private":
            return False
        return await is_admin(event.from_user.id)


class Admin(StatesGroup):
    wait_file = State()
    wait_admin = State()
    confirm = State()


# ─────────────────────────── экраны ───────────────────────────

def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


async def _panel_kb(is_owner: bool) -> InlineKeyboardMarkup:
    async with async_session_maker() as session:
        dead = len(await DuelDAO(session).list_all())
    rows = [
        [_btn("📊 Кто привязал телеграм", "stats")],
        [_btn("📥 Выгрузить Excel", "export")],
        [_btn("📤 Загрузить Excel", "import")],
        [_btn(f"🪦 Воскресить{f' ({dead})' if dead else ''}", "revive")],
    ]
    if is_owner:
        rows.append([_btn("👥 Админы", "admins")])
    return _kb(*rows)


def _back_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("↩️ В панель", "panel")])


PANEL_TEXT = (
    "🛠 <b>Админ-панель</b>\n" + DIVIDER + "\n"
    "Выгрузить состав, поправить в Excel и залить обратно — база обновится.\n"
    "Строки находятся по столбцу <code>id</code>, так что правки не плодят дублей."
)


async def _paint(target: Message | CallbackQuery, text: str,
                 kb: InlineKeyboardMarkup | None) -> None:
    """Панель живёт в одном сообщении: перерисовываем его, а не плодим новые."""
    message = target if isinstance(target, Message) else target.message
    if isinstance(target, CallbackQuery):
        try:
            await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except TelegramAPIError:
            pass
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


def _is_panel_cmd(message: Message) -> bool:
    if not message.text:
        return False
    return message.text.strip().casefold() in ("!админка", "/admin", "!админ", "!панель")


# ─────────────────────────── вход ───────────────────────────

@admin_router.message(F.chat.type == "private", F.text.lower().in_({"!id", "/id", "!мойid"}))
async def my_id(message: Message):
    """Свой telegram id. Нужен, чтобы прописать OWNER_ID и добавлять админов.

    Доступно всем: это не секрет, человек и так может узнать его десятком
    способов, зато без этой команды не с чего начать настройку.
    """
    await message.reply(
        f"Твой telegram id: <code>{message.from_user.id}</code>\n\n"
        "<i>Первая настройка: положи его в .env как "
        "<code>OWNER_ID=…</code> и перезапусти бота.</i>",
        parse_mode="HTML",
    )


@admin_router.message(_is_panel_cmd, AdminPrivate())
async def panel_cmd(message: Message, state: FSMContext):
    await state.clear()
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    await _paint(message, PANEL_TEXT, await _panel_kb(message.from_user.id == owner_id()))


@admin_router.callback_query(F.data == f"{CB}:panel", AdminPrivate())
async def cb_panel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await _paint(call, PANEL_TEXT, await _panel_kb(call.from_user.id == owner_id()))


# ─────────────────────────── статистика регистраций ───────────────────────────

@admin_router.callback_query(F.data == f"{CB}:stats", AdminPrivate())
async def cb_stats(call: CallbackQuery):
    await call.answer()
    async with async_session_maker() as session:
        data = await RosterDAO(session).stats()

    total, linked = data["total"], data["registered"]
    share = round(linked / total * 100) if total else 0
    lines = [
        "📊 <b>Актив в боте</b>", DIVIDER,
        f"👥 <b>Всего в базе:</b> {total}",
        f"🔗 <b>Привязали телеграм:</b> {linked} · {share}%",
        f"📋 <b>Только из таблицы:</b> {len(data['missing'])}",
    ]
    if data["active"] != total:
        lines.append(f"🚫 <b>Помечены как не в активе:</b> {total - data['active']}")

    lines += ["", "<i>В базе есть все — состав загружен из Excel. «Привязали "
                  "телеграм» значит, что человек зашёл в бота и подтвердил себя: "
                  "только у таких известен tg_id, поэтому их получается надёжно "
                  "тегать, и они сами правят свои данные.</i>"]

    if data["missing"]:
        lines += ["", "<b>Кто ещё не заходил в бота:</b>"]
        for activist in data["missing"][:PREVIEW_LIMIT * 2]:
            name = html.escape(first_last(activist.fio) if activist.fio else "без имени")
            tag = (activist.tg_username or "").strip().lstrip("@")
            lines.append(f"• {name}" + (f" — @{html.escape(tag)}" if tag else " — <i>без тега</i>"))
        left = len(data["missing"]) - PREVIEW_LIMIT * 2
        if left > 0:
            lines.append(f"<i>…и ещё {left} — полный список в выгрузке Excel</i>")
        lines += ["", f"<i>Из них с @тегом: {data['missing_with_tag']} — этих можно позвать "
                      "лично, остальных по тегу не найти.</i>"]

    await _paint(call, "\n".join(lines), _back_kb())


# ─────────────────────────── выгрузка ───────────────────────────

@admin_router.callback_query(F.data == f"{CB}:export", AdminPrivate())
async def cb_export(call: CallbackQuery):
    await call.answer("Собираю файл…")
    async with async_session_maker() as session:
        dao = RosterDAO(session)
        activists = await dao.activists()
        linked = await dao.registered_ids()

    if not activists:
        await _paint(call, "В базе пусто — выгружать нечего.", _back_kb())
        return

    buf = build_workbook(activists, linked)
    name = f"актив_{moscow_today().strftime('%Y-%m-%d')}.xlsx"
    await call.message.answer_document(
        BufferedInputFile(buf.read(), filename=name),
        caption=(f"📥 Состав актива — {len(activists)} "
                 f"{plural(len(activists), 'человек', 'человека', 'человек')}.\n\n"
                 "Правь что нужно и присылай обратно кнопкой «Загрузить Excel».\n"
                 "<b>Столбец id не трогай</b> — по нему строки находят себя в базе."),
        parse_mode="HTML",
    )


# ─────────────────────────── загрузка ───────────────────────────

@admin_router.callback_query(F.data == f"{CB}:import", AdminPrivate())
async def cb_import(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Admin.wait_file)
    await _paint(
        call,
        "📤 <b>Пришли файл .xlsx</b>\n" + DIVIDER + "\n"
        "Лучше всего — тот, что выгрузил кнопкой выше, с правками.\n\n"
        "Как это сработает:\n"
        "• строка с заполненным <code>id</code> обновит своего человека;\n"
        "• строка с пустым <code>id</code> добавит нового;\n"
        "• кто есть в базе, но пропал из файла, <b>сам не удалится</b> — "
        "покажу отдельно и спрошу.\n\n"
        "<i>Сначала покажу, что изменится, и только потом применю.</i>",
        _back_kb(),
    )


@admin_router.message(Admin.wait_file, F.document, AdminPrivate())
async def on_file(message: Message, state: FSMContext):
    document = message.document
    if not (document.file_name or "").casefold().endswith((".xlsx", ".xlsm")):
        await message.reply("Нужен файл .xlsx — этот не подойдёт.")
        return
    if document.file_size and document.file_size > MAX_FILE_BYTES:
        await message.reply("Файл слишком большой. Состав актива весит десятки килобайт — "
                            "похоже, это что-то другое.")
        return

    note = await message.answer("Читаю файл…")
    buf = io.BytesIO()
    try:
        await message.bot.download(document, destination=buf)
    except TelegramAPIError as err:
        await note.edit_text(f"Не смог скачать файл: {err}")
        return

    buf.seek(0)
    rows, errors = parse_workbook(buf)
    if not rows:
        await note.edit_text(
            "❌ <b>Не смог разобрать файл</b>\n" + DIVIDER + "\n" +
            "\n".join(f"• {html.escape(e)}" for e in errors[:10]),
            reply_markup=_back_kb(), parse_mode="HTML",
        )
        return

    async with async_session_maker() as session:
        plan = await RosterDAO(session).plan(rows)
        summary = _summary(plan, errors)

    # В состоянии храним разобранные строки, а не план: в плане лежат объекты
    # закрытой сессии. Перед применением план пересчитается на свежих данных.
    await state.set_state(Admin.confirm)
    await state.update_data(rows=rows)

    buttons = [[_btn("✅ Применить", "apply")]]
    if plan["missing"]:
        buttons.append([_btn(f"🗑 Применить и удалить {len(plan['missing'])}", "apply_drop")])
    buttons.append([_btn("❌ Отмена", "panel")])

    await note.edit_text(summary, reply_markup=_kb(*buttons), parse_mode="HTML")
    try:
        await message.delete()
    except TelegramAPIError:
        pass


def _names(items, attr: bool = True) -> list[str]:
    out = []
    for item in items:
        fio = (item.fio if attr else item.get("fio")) or "без имени"
        out.append(html.escape(first_last(fio)))
    return out


def _summary(plan: dict, errors: list[str]) -> str:
    creates = len(plan["creates"]) + len(plan["unknown"])
    lines = [
        "📋 <b>Что изменится</b>", DIVIDER,
        f"➕ <b>Добавится:</b> {creates}",
        f"✏️ <b>Обновится:</b> {len(plan['updates'])}",
        f"➖ <b>Есть в базе, нет в файле:</b> {len(plan['missing'])}",
    ]
    if plan["unknown"]:
        lines.append(f"<i>⚠️ {len(plan['unknown'])} строк с неизвестным id — "
                     "добавлю как новых (файл от старой базы?)</i>")
    if errors:
        lines += ["", f"⚠️ <b>Пропущу строк с ошибками: {len(errors)}</b>"]
        lines += [f"• {html.escape(e)}" for e in errors[:5]]
        if len(errors) > 5:
            lines.append(f"<i>…и ещё {len(errors) - 5}</i>")

    if plan["updates"]:
        lines += ["", "<b>Кого поправлю:</b>"]
        for item in plan["updates"][:PREVIEW_LIMIT]:
            name = html.escape(first_last(item["activist"].fio or "без имени"))
            fields = html.escape(", ".join(item["changed"][:4]))
            lines.append(f"• {name} — {fields}")
        if len(plan["updates"]) > PREVIEW_LIMIT:
            lines.append(f"<i>…и ещё {len(plan['updates']) - PREVIEW_LIMIT}</i>")

    if plan["creates"]:
        lines += ["", "<b>Новые:</b>"]
        lines += [f"• {name}" for name in _names(plan["creates"], attr=False)[:PREVIEW_LIMIT]]

    if plan["missing"]:
        lines += ["", "<b>Пропали из файла</b> <i>(останутся, если не нажать «удалить»)</i>:"]
        lines += [f"• {name}" for name in _names(plan["missing"])[:PREVIEW_LIMIT]]
        if len(plan["missing"]) > PREVIEW_LIMIT:
            lines.append(f"<i>…и ещё {len(plan['missing']) - PREVIEW_LIMIT}</i>")

    text = "\n".join(lines)
    # Лимит телеграма на сообщение — 4096 символов.
    return text if len(text) <= 4000 else text[:3990].rsplit("\n", 1)[0] + "\n<i>…</i>"


async def _apply(call: CallbackQuery, state: FSMContext, drop_missing: bool) -> None:
    rows = (await state.get_data()).get("rows")
    if not rows:
        await call.answer("Файл потерялся, пришли заново", show_alert=True)
        await state.clear()
        return

    await call.answer("Применяю…")
    async with async_session_maker() as session:
        dao = RosterDAO(session)
        # Пересчитываем на свежих данных: между показом и нажатием кто-то мог
        # заполнить анкету или измениться.
        plan = await dao.plan(rows)
        result = await dao.apply(plan, drop_missing=drop_missing)

    await state.clear()
    lines = ["✅ <b>Готово</b>", DIVIDER,
             f"➕ Добавлено: {result['created']}",
             f"✏️ Обновлено: {result['updated']}"]
    if result["removed"]:
        lines.append(f"🗑 Удалено: {result['removed']}")
    lines += ["", "<i>Изменения уже в базе — !инфо покажет их сразу.</i>"]
    await _paint(call, "\n".join(lines), _back_kb())
    logger.info("Состав обновлён админом %s: %s", call.from_user.id, result)


@admin_router.callback_query(Admin.confirm, F.data == f"{CB}:apply", AdminPrivate())
async def cb_apply(call: CallbackQuery, state: FSMContext):
    await _apply(call, state, drop_missing=False)


@admin_router.callback_query(Admin.confirm, F.data == f"{CB}:apply_drop", AdminPrivate())
async def cb_apply_drop(call: CallbackQuery, state: FSMContext):
    await _apply(call, state, drop_missing=True)


# ─────────────────────────── воскрешение ───────────────────────────

REVIVE_LIMIT = 15  # сколько мёртвых показать кнопками (остальные — через «всех»)


def _dead_who(soul) -> tuple[str, str]:
    """Коротко для кнопки и полно для строки: (кнопка, строка)."""
    tag = (soul.username or "").strip().lstrip("@")
    if tag:
        return f"@{tag}", f"@{html.escape(tag)}"
    name = html.escape((soul.display or "").strip() or "боец")
    full = f'<a href="tg://user?id={soul.user_id}">{name}</a>'
    return (soul.display or "боец")[:24], full


async def _chat_name(bot: Bot, chat_id: int) -> str:
    try:
        chat = await bot.get_chat(chat_id)
        if chat.title:
            return f"«{html.escape(chat.title)}»"
    except Exception:
        logger.debug("get_chat %s не сработал", chat_id, exc_info=True)
    return f"<code>{chat_id}</code>"


async def _announce_revive(bot: Bot, soul) -> None:
    """Сказать во флуд, что человек воскрес. Не вышло (бота выгнали) —
    не страшно: запись всё равно удаляем, человек и так ожил."""
    try:
        await bot.send_message(
            soul.chat_id,
            f"Возрадуемся! {_dead_who(soul)[1]} воскрес!\nУдачи в следующий раз",
            parse_mode="HTML",
        )
    except Exception:
        logger.warning("Не смог объявить воскрешение %s в %s",
                       soul.user_id, soul.chat_id, exc_info=True)


async def _revive_screen(target: CallbackQuery) -> None:
    async with async_session_maker() as session:
        dead = await DuelDAO(session).list_all()

    if not dead:
        await _paint(target, "🪦 <b>Воскрешение</b>\n" + DIVIDER + "\n"
                     "Мёртвых нет — все живы 🎉", _back_kb())
        return

    lines = ["🪦 <b>Кого воскресить</b>", DIVIDER]
    rows = []
    for soul in dead[:REVIVE_LIMIT]:
        short, full = _dead_who(soul)
        chat = await _chat_name(target.bot, soul.chat_id)
        lines.append(f"• {full} — {chat}")
        rows.append([_btn(f"✅ {short}", f"rz:{soul.chat_id}:{soul.user_id}")])
    if len(dead) > REVIVE_LIMIT:
        lines.append(f"<i>…и ещё {len(dead) - REVIVE_LIMIT} — их воскресит кнопка ниже</i>")
    rows.append([_btn(f"✅ Воскресить всех ({len(dead)})", "rzall")])
    rows.append([_btn("↩️ В панель", "panel")])
    await _paint(target, "\n".join(lines), _kb(*rows))


@admin_router.callback_query(F.data == f"{CB}:revive", AdminPrivate())
async def cb_revive(call: CallbackQuery):
    await call.answer()
    await _revive_screen(call)


@admin_router.callback_query(F.data == f"{CB}:rzall", AdminPrivate())
async def cb_revive_all(call: CallbackQuery):
    async with async_session_maker() as session:
        dao = DuelDAO(session)
        souls = await dao.list_all()
        await dao.revive_all()
    for soul in souls:
        await _announce_revive(call.bot, soul)
    await call.answer(f"Воскресил: {len(souls)}")
    logger.info("Админ %s воскресил всех (%s)", call.from_user.id, len(souls))
    await _revive_screen(call)


@admin_router.callback_query(F.data.startswith(f"{CB}:rz:"), AdminPrivate())
async def cb_revive_one(call: CallbackQuery):
    try:
        _, _, raw_chat, raw_user = call.data.split(":")
        chat_id, user_id = int(raw_chat), int(raw_user)
    except ValueError:
        await call.answer()
        return
    async with async_session_maker() as session:
        dao = DuelDAO(session)
        soul = await dao.is_dead(chat_id, user_id)
        if soul is None:
            await call.answer("Он уже воскрес сам")
        else:
            await dao.revive(soul)
            await _announce_revive(call.bot, soul)
            await call.answer("Воскресил")
            logger.info("Админ %s воскресил %s", call.from_user.id, user_id)
    await _revive_screen(call)


# ─────────────────────────── управление админами ───────────────────────────

class OwnerOnly(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        chat = event.chat if isinstance(event, Message) else event.message.chat
        return chat.type == "private" and event.from_user.id == owner_id()


async def _who(bot: Bot, tg_id: int, known: str | None = None) -> str:
    """Как показать человека: @тег, если он вообще известен, иначе id.

    Тег спрашиваем у телеграма: он мог смениться с момента добавления, а
    у добавленных по числовому id его изначально нет. Не вышло —
    показываем id, это всегда честно.
    """
    try:
        chat = await bot.get_chat(tg_id)
        if chat.username:
            return f"@{html.escape(chat.username)}"
    except Exception:
        # Запрос чисто косметический — ниже есть чем показать человека.
        logger.debug("get_chat по id %s не сработал", tg_id, exc_info=True)
    if known:
        return f"@{html.escape(known.lstrip('@'))}"
    return f"<code>{tg_id}</code>"


async def _admins_screen(target: CallbackQuery) -> None:
    async with async_session_maker() as session:
        admins = await AdminDAO(session).all()
        pending = await PendingDAO(session).all()

    owner = await _who(target.bot, owner_id())
    lines = ["👥 <b>Кто имеет доступ</b>", DIVIDER,
             f"👑 Владелец — {owner} <i>(из .env, снять нельзя)</i>"]
    rows = []
    for admin in admins:
        who = await _who(target.bot, admin.tg_id, admin.username)
        title = f" · {html.escape(admin.title)}" if admin.title else ""
        lines.append(f"• {who}{title}")
        rows.append([_btn(f"🗑 Убрать {admin.username or admin.tg_id}", f"del:{admin.tg_id}")])
    if not admins and not pending:
        lines.append("<i>Больше никого. Добавь кнопкой ниже.</i>")

    if pending:
        lines += ["", "<b>Ждут первого захода в бота:</b>"]
        for item in pending:
            lines.append(f"⏳ @{html.escape(item.username)}")
            rows.append([_btn(f"🗑 Отменить @{item.username}", f"delp:{item.username}")])
        lines.append("<i>Доступ включится сам, когда человек напишет боту.</i>")

    rows.append([_btn("➕ Добавить", "add")])
    rows.append([_btn("↩️ В панель", "panel")])
    await _paint(target, "\n".join(lines), _kb(*rows))


@admin_router.callback_query(F.data == f"{CB}:admins", OwnerOnly())
async def cb_admins(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await _admins_screen(call)


@admin_router.callback_query(F.data == f"{CB}:add", OwnerOnly())
async def cb_add(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Admin.wait_admin)
    await _paint(
        call,
        "➕ <b>Кого добавить?</b>\n" + DIVIDER + "\n"
        "Пришли одно из:\n"
        "• <code>@тег</code> — годится любой из базы актива;\n"
        "• telegram id числом — <code>123456789</code>;\n"
        "• перешли сюда любое его сообщение.\n\n"
        "<i>Если человек ещё ни разу не заходил в бота, доступ включится "
        "сам при первом его сообщении боту в личку — телеграм не даёт "
        "узнать id по одному тегу.</i>",
        _kb([_btn("↩️ Назад", "admins")]),
    )


async def _by_tag(bot: Bot, tag: str) -> tuple[int | None, str | None, bool]:
    """Найти id по @тегу. Возвращает (id, тег, есть ли такой в базе актива).

    Порядок от надёжного к слабому: кто заполнил анкету (у него точно есть
    tg_id), кто писал во флуде, потом попытка спросить сам телеграм. Если id
    так и не нашёлся, но тег есть в активе — вернём его, чтобы выдать доступ
    отложенно.
    """
    clean = tag.strip().lstrip("@")
    if not clean:
        return None, None, False
    needle = clean.casefold()

    async with async_session_maker() as session:
        # 1. Заполнил анкету в личке — id известен наверняка.
        row = (await session.execute(
            select(ActivistLink.tg_id)
            .join(Activists, Activists.id == ActivistLink.activist_id)
            .where(func.lower(func.replace(Activists.tg_username, "@", "")) == needle)
        )).scalars().first()
        if row:
            return int(row), clean, True

        # 2. Писал во флуде при работающем счётчике.
        found = await StatsDAO(session).user_by_username(clean)
        if found is not None:
            return found.user_id, found.username or clean, True

        in_roster = (await session.execute(
            select(func.count()).select_from(Activists).where(
                func.lower(func.replace(Activists.tg_username, "@", "")) == needle
            )
        )).scalar_one() > 0

    # 3. Вдруг телеграм ответит сам. Для пользователей это не гарантировано,
    #    поэтому проверяем тип: под тем же тегом может оказаться канал.
    try:
        chat = await bot.get_chat(f"@{clean}")
        if chat.type == "private":
            return chat.id, chat.username or clean, in_roster
    except Exception:
        # Попытка необязательная: телеграм не обещает отвечать на тег
        # пользователя. Что бы тут ни случилось, ниже есть отложенная
        # выдача — ронять из-за этого добавление админа нельзя.
        logger.debug("get_chat по тегу @%s не сработал", clean, exc_info=True)

    return None, clean, in_roster


@admin_router.message(Admin.wait_admin, OwnerOnly())
async def on_add_admin(message: Message, state: FSMContext):
    tg_id, username, in_roster = None, None, False

    forwarded = getattr(message, "forward_from", None)
    if forwarded is not None:
        tg_id, username = forwarded.id, forwarded.username
    elif message.text:
        text = message.text.strip()
        if text.lstrip("-").isdigit():
            tg_id = int(text)
        else:
            tg_id, username, in_roster = await _by_tag(message.bot, text)

    try:
        await message.delete()
    except TelegramAPIError:
        pass

    if tg_id is None and not in_roster:
        await message.answer(
            "Не понял, кто это. Нужен @тег из базы актива, id числом или "
            "пересланное сообщение.\n\n"
            "<i>Если тега в базе нет — попроси человека прислать "
            "<code>!id</code> из лички с ботом.</i>",
            parse_mode="HTML",
        )
        return

    if tg_id == owner_id():
        await message.answer("Это ты, у тебя и так полный доступ.")
        return

    await state.clear()

    # Тег в активе есть, а id неизвестен: выдаём отложенно.
    if tg_id is None:
        async with async_session_maker() as session:
            added = await PendingDAO(session).add(username, message.from_user.id)
        who = html.escape(f"@{username}")
        await message.answer(
            f"⏳ Доступ для {who} записан.\n\n"
            "Он ещё ни разу не заходил в бота, а телеграм не даёт узнать id по "
            "одному тегу. Админка включится сама, как только он напишет боту в "
            "личку — попроси его отправить <code>/start</code>."
            if added else f"{who} уже в очереди на доступ.",
            parse_mode="HTML",
        )
        logger.info("Отложенный доступ для @%s выдал %s", username, message.from_user.id)
        return

    async with async_session_maker() as session:
        added = await AdminDAO(session).add(tg_id, username, "", message.from_user.id)

    who = html.escape(f"@{username}" if username else str(tg_id))
    await message.answer(
        f"✅ {who} теперь админ." if added else f"{who} и так уже в списке.",
        parse_mode="HTML",
    )
    logger.info("Админ %s добавил %s", message.from_user.id, tg_id)


@admin_router.callback_query(F.data.startswith(f"{CB}:delp:"), OwnerOnly())
async def cb_del_pending(call: CallbackQuery):
    username = call.data.split(":", 2)[-1]
    async with async_session_maker() as session:
        removed = await PendingDAO(session).remove(username)
    await call.answer("Убрал из очереди" if removed else "Его и так нет")
    await _admins_screen(call)


@admin_router.callback_query(F.data.startswith(f"{CB}:del:"), OwnerOnly())
async def cb_del(call: CallbackQuery):
    try:
        tg_id = int(call.data.split(":")[-1])
    except ValueError:
        await call.answer()
        return
    async with async_session_maker() as session:
        removed = await AdminDAO(session).remove(tg_id)
    await call.answer("Убрал" if removed else "Его и так нет")
    await _admins_screen(call)
    logger.info("Админ %s убрал %s", call.from_user.id, tg_id)

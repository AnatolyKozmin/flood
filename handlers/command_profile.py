"""Анкета активиста в личке с ботом: заполнение и правка данных в базе.

Раньше путь был один: гугл-форма → Excel → import_excel.py → база. Здесь
человек отвечает боту в личных сообщениях, и запись сразу попадает в ту же
таблицу activists — то есть !инфо и !топ видят его без ручной выгрузки.

Вся анкета живёт в ОДНОМ сообщении: бот редактирует своё сообщение под
очередной вопрос, а ответ человека удаляет. Telegram это разрешает — в
личных чатах бот может удалять входящие сообщения (Bot API, deleteMessage).
Если удалить не вышло (сообщение старше 48 часов и т.п.) — просто идём
дальше, диалог от этого не ломается.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.engine import async_session_maker
from database.profile_dao import ProfileDAO
from utils.format import DIVIDER, field, format_phone
from utils.helpers import mention

profile_router = Router()

CB = "pf"  # префикс callback_data, чтобы не пересечься с другими командами


# ─────────────────────────── разбор ответов ───────────────────────────
# Каждый парсер возвращает (значение, текст ошибки). Ошибка — None, если всё ок.

def _text(raw: str) -> tuple[Any, str | None]:
    value = " ".join(raw.split())
    if len(value) > 200:
        return None, "Слишком длинно — уложись в 200 символов."
    return value, None


def _fio(raw: str) -> tuple[Any, str | None]:
    value = " ".join(raw.split())
    if len(value.split()) < 2:
        return None, "Нужно хотя бы фамилия и имя. Например: <code>Иванов Иван Иванович</code>"
    if len(value) > 120:
        return None, "Слишком длинное ФИО."
    if not re.fullmatch(r"[А-Яа-яЁёA-Za-z\s\-']+", value):
        return None, "В ФИО закрались лишние символы — только буквы, дефис и пробелы."
    return value, None


def _birthday(raw: str) -> tuple[Any, str | None]:
    value = raw.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if not 1950 <= parsed.year <= datetime.now().year:
            return None, "Год какой-то подозрительный. Проверь дату."
        return parsed, None
    return None, "Не понял дату. Формат: <code>ДД.ММ.ГГГГ</code> — например <code>14.03.2005</code>"


def _phone(raw: str) -> tuple[Any, str | None]:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) != 11 or digits[0] != "7":
        return None, "Не похоже на номер. Формат: <code>+7 999 123-45-67</code>"
    # Храним 11 цифр — как приходило из Excel; format_phone() красиво покажет.
    return digits, None


def _email(raw: str) -> tuple[Any, str | None]:
    value = raw.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", value):
        return None, "Не похоже на почту. Например: <code>ivanov@urfu.me</code>"
    if len(value) > 120:
        return None, "Слишком длинная почта."
    return value, None


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    emoji: str
    question: str
    hint: str
    required: bool
    parse: Callable[[str], tuple[Any, str | None]]


# Порядок и состав — как в карточке !инфо.
FIELDS: tuple[Field, ...] = (
    Field("fio", "ФИО", "👤",
          "Как тебя зовут?",
          "Фамилия Имя Отчество — например <code>Иванов Иван Иванович</code>",
          True, _fio),
    Field("birthday", "День рождения", "🎂",
          "Когда у тебя день рождения?",
          "Формат <code>ДД.ММ.ГГГГ</code> — например <code>14.03.2005</code>",
          False, _birthday),
    Field("ik_div", "Направление", "🧭",
          "В каком направлении ты состоишь?",
          "ЦТ · СМИ · Фото · Дизайн · ПЗ · F&amp;U prod. Можно несколько через запятую.",
          False, _text),
    Field("group", "Группа", "🎓",
          "Твоя учебная группа?",
          "Например <code>РИ-410015</code>",
          False, _text),
    Field("phone", "Номер телефона", "📞",
          "Номер телефона?",
          "Например <code>+7 999 123-45-67</code>",
          False, _phone),
    Field("email", "Почта", "✉️",
          "Твоя почта?",
          "Например <code>ivanov@urfu.me</code>",
          False, _email),
    Field("clothes_size", "Размер одежды", "👕",
          "Размер одежды?",
          "S · M · L · XL — можно и <code>S/M</code>",
          False, _text),
    Field("someone_div", "Другие подразделения", "🏢",
          "Состоишь ещё где-то, кроме актива ИК?",
          "Профбюро, штаб отрядов, что угодно. Если нигде — жми «Пропустить».",
          False, _text),
)

BY_KEY = {f.key: f for f in FIELDS}


class Form(StatesGroup):
    filling = State()


# ─────────────────────────── клавиатуры ───────────────────────────

def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


def _question_kb(fld: Field, editing_one: bool) -> InlineKeyboardMarkup:
    row = []
    if not fld.required:
        row.append(_btn("⏭ Пропустить", "skip"))
    row.append(_btn("❌ Отмена", "cancel"))
    if editing_one:
        return _kb([_btn("↩️ Назад к анкете", "preview")], row)
    return _kb(row)


def _preview_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Сохранить", "save")],
        [_btn("✏️ Исправить", "edit")],
        [_btn("❌ Отмена", "cancel")],
    )


def _edit_kb() -> InlineKeyboardMarkup:
    rows = [[_btn(f"{f.emoji} {f.label}", f"one:{f.key}")] for f in FIELDS]
    rows.append([_btn("↩️ Назад", "preview")])
    return _kb(*rows)


def _start_kb(has_profile: bool) -> InlineKeyboardMarkup:
    title = "✏️ Изменить данные" if has_profile else "📝 Заполнить анкету"
    return _kb([_btn(title, "fill")])


# ─────────────────────────── отрисовка ───────────────────────────

def _shown(key: str, value) -> str:
    if key == "birthday" and isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if key == "phone" and value:
        return format_phone(value)
    return str(value) if value else ""


def _question_text(idx: int, fld: Field, error: str | None, editing_one: bool) -> str:
    head = "✏️ <b>Правим поле</b>" if editing_one else f"📝 <b>Анкета</b> · вопрос {idx + 1} из {len(FIELDS)}"
    lines = [head, DIVIDER, f"{fld.emoji} <b>{fld.question}</b>", f"<i>{fld.hint}</i>"]
    if error:
        lines += ["", f"⚠️ {error}"]
    lines += ["", "<i>Просто отправь ответ сообщением — я его подчищу.</i>"]
    return "\n".join(lines)


def _preview_text(values: dict, is_edit: bool) -> str:
    head = "✏️ <b>Проверь изменения</b>" if is_edit else "📝 <b>Проверь анкету</b>"
    lines = [head, DIVIDER]
    for fld in FIELDS:
        lines.append(field(fld.label, _shown(fld.key, values.get(fld.key)), fld.emoji, placeholder="—"))
    lines += ["", "Всё верно? Жми «Сохранить»."]
    return "\n".join(lines)


def _card(activist) -> str:
    """Короткая карточка того, что уже лежит в базе — для /start."""
    lines = ["👤 <b>Твоя анкета</b>", DIVIDER]
    for fld in FIELDS:
        lines.append(field(fld.label, _shown(fld.key, getattr(activist, fld.key, None)),
                           fld.emoji, placeholder="—"))
    tag = mention(activist.tg_username)
    lines.append(field("Телеграм", tag, "✈️", placeholder="—"))
    return "\n".join(lines)


# ─────────────────────────── механика одного сообщения ───────────────────────────

async def _drop(message: Message) -> None:
    """Убрать ответ человека, чтобы в личке остался только один диалог-экран."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass  # нет прав / сообщение слишком старое — не критично


async def _paint(bot: Bot, chat_id: int, state: FSMContext, text: str,
                 kb: InlineKeyboardMarkup | None) -> None:
    """Перерисовать сообщение-анкету. Если его удалили — отправить новое."""
    data = await state.get_data()
    mid = data.get("mid")
    if mid:
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=mid,
                reply_markup=kb, parse_mode="HTML",
            )
            return
        except TelegramBadRequest as err:
            # "message is not modified" — значит и так всё нарисовано.
            if "message is not modified" in str(err):
                return
    sent = await bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    await state.update_data(mid=sent.message_id)


async def _ask(bot: Bot, chat_id: int, state: FSMContext, error: str | None = None) -> None:
    data = await state.get_data()
    one = data.get("one")
    if one:
        fld = BY_KEY[one]
        idx = FIELDS.index(fld)
    else:
        idx = data.get("idx", 0)
        fld = FIELDS[idx]
    await _paint(bot, chat_id, state, _question_text(idx, fld, error, bool(one)),
                 _question_kb(fld, bool(one)))


async def _show_preview(bot: Bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(one=None)
    await _paint(bot, chat_id, state,
                 _preview_text(data.get("values", {}), bool(data.get("aid"))),
                 _preview_kb())


async def _advance(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Следующий вопрос — или превью, если вопросы кончились."""
    data = await state.get_data()
    if data.get("one"):
        await _show_preview(bot, chat_id, state)
        return
    idx = data.get("idx", 0) + 1
    await state.update_data(idx=idx)
    if idx >= len(FIELDS):
        await _show_preview(bot, chat_id, state)
    else:
        await _ask(bot, chat_id, state)


async def _begin(bot: Bot, chat_id: int, user, state: FSMContext, mid: int | None) -> None:
    """Старт анкеты. Если человек уже есть в базе — подставляем его данные."""
    async with async_session_maker() as session:
        activist = await ProfileDAO(session).find(user.id, user.username)
        values, aid = {}, None
        if activist is not None:
            aid = activist.id
            for fld in FIELDS:
                current = getattr(activist, fld.key, None)
                if current not in (None, "", 0):
                    values[fld.key] = current

    await state.set_state(Form.filling)
    await state.set_data({"values": values, "idx": 0, "mid": mid, "one": None, "aid": aid})
    await _ask(bot, chat_id, state)


# ─────────────────────────── входные точки ───────────────────────────

def _is_anketa_cmd(message: Message) -> bool:
    if not message.text:
        return False
    first = message.text.strip().split(maxsplit=1)[0].casefold()
    return first in ("!анкета", "!я", "/анкета")


@profile_router.message(CommandStart(), F.chat.type == "private")
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    async with async_session_maker() as session:
        activist = await ProfileDAO(session).find(user.id, user.username)

    # Диплинк из чата: t.me/<bot>?start=anketa — сразу открываем анкету.
    payload = (message.text or "").partition(" ")[2].strip()
    if payload == "anketa":
        sent = await message.answer("Секунду…")
        await _begin(message.bot, message.chat.id, user, state, sent.message_id)
        return

    if activist is not None:
        text = (f"Привет! Ты уже есть в базе актива ИК.\n\n{_card(activist)}\n\n"
                "Что-то поменялось — жми кнопку ниже.")
    else:
        text = ("Привет! Я <b>БотИК</b>.\n\n"
                "Здесь можно заполнить анкету активиста — она попадёт прямо в базу, "
                "и тебя будет видно по команде <code>!инфо</code> во флуде. "
                "Гугл-форму заполнять больше не надо.\n\n"
                "Займёт минуту: 8 вопросов, почти все можно пропустить.")
    await message.answer(text, reply_markup=_start_kb(activist is not None), parse_mode="HTML")


@profile_router.message(F.chat.type == "private", _is_anketa_cmd)
async def anketa_private(message: Message, state: FSMContext):
    await state.clear()
    sent = await message.answer("Секунду…")
    await _begin(message.bot, message.chat.id, message.from_user, state, sent.message_id)


@profile_router.message(F.chat.type.in_({"group", "supergroup"}), _is_anketa_cmd)
async def anketa_group(message: Message):
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=anketa"
    await message.reply(
        f'Анкета заполняется в личке — <a href="{link}">жми сюда</a>, '
        "и я задам восемь вопросов.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@profile_router.message(Form.filling, F.chat.type == "private")
async def on_answer(message: Message, state: FSMContext):
    bot, chat_id = message.bot, message.chat.id

    if not message.text:
        await _drop(message)
        await _ask(bot, chat_id, state, "Мне нужен текст — картинки и стикеры не подойдут.")
        return

    data = await state.get_data()
    one = data.get("one")
    fld = BY_KEY[one] if one else FIELDS[data.get("idx", 0)]

    value, error = fld.parse(message.text)
    await _drop(message)
    if error:
        await _ask(bot, chat_id, state, error)
        return

    values = dict(data.get("values", {}))
    values[fld.key] = value
    await state.update_data(values=values)
    await _advance(bot, chat_id, state)


# ─────────────────────────── кнопки ───────────────────────────

@profile_router.callback_query(F.data == f"{CB}:fill")
async def cb_fill(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await _begin(call.bot, call.message.chat.id, call.from_user, state, call.message.message_id)


@profile_router.callback_query(Form.filling, F.data == f"{CB}:skip")
async def cb_skip(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    one = data.get("one")
    fld = BY_KEY[one] if one else FIELDS[data.get("idx", 0)]
    if fld.required:
        await call.answer("Это поле обязательное", show_alert=True)
        return
    await call.answer()
    values = dict(data.get("values", {}))
    values.pop(fld.key, None)
    await state.update_data(values=values)
    await _advance(call.bot, call.message.chat.id, state)


@profile_router.callback_query(Form.filling, F.data == f"{CB}:preview")
async def cb_preview(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _show_preview(call.bot, call.message.chat.id, state)


@profile_router.callback_query(Form.filling, F.data == f"{CB}:edit")
async def cb_edit(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await _paint(call.bot, call.message.chat.id, state,
                 "✏️ <b>Что поправить?</b>", _edit_kb())


@profile_router.callback_query(Form.filling, F.data.startswith(f"{CB}:one:"))
async def cb_edit_one(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[-1]
    if key not in BY_KEY:
        await call.answer()
        return
    await call.answer()
    await state.update_data(one=key)
    await _ask(call.bot, call.message.chat.id, state)


@profile_router.callback_query(Form.filling, F.data == f"{CB}:save")
async def cb_save(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    values = data.get("values", {})
    if not values.get("fio"):
        await call.answer("Без ФИО не сохранить", show_alert=True)
        return

    await call.answer("Сохраняю…")
    user = call.from_user
    async with async_session_maker() as session:
        dao = ProfileDAO(session)
        activist = await dao.find(user.id, user.username)
        # Пустые поля означают «стереть»: человек мог нажать «Пропустить» осознанно.
        payload = {fld.key: values.get(fld.key, None if fld.key == "birthday" else "")
                   for fld in FIELDS}
        activist = await dao.save(user.id, user.username, payload, activist)

    tail = ("\n\n<i>У тебя не заполнен @тег в телеграме — по нему тебя не найти "
            "через !инфо. Поставь юзернейм в настройках и загляни сюда снова.</i>"
            if not user.username else "")
    await _paint(call.bot, call.message.chat.id, state,
                 f"✅ <b>Готово, записал!</b>\n\n{_card(activist)}{tail}",
                 _kb([_btn("✏️ Изменить данные", "fill")]))
    await state.clear()


@profile_router.callback_query(F.data == f"{CB}:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await call.answer("Отменил")
    await state.clear()
    try:
        await call.message.edit_text(
            "Окей, ничего не сохранил. Захочешь вернуться — команда <code>!анкета</code>.",
            reply_markup=_kb([_btn("📝 Заполнить анкету", "fill")]),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass

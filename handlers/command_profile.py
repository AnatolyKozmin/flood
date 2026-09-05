"""Личный кабинет активиста в личке с ботом.

Как это устроено. В личном чате бот держит ровно ОДИН свой «экран»: показывая
следующий, он удаляет предыдущий, а ответы человека удаляет сразу после
разбора. Поэтому переписка не растёт — всегда видно только текущий шаг.
Telegram это разрешает: в личных чатах бот может удалять и свои сообщения,
и входящие (Bot API, deleteMessage). Если удалить не вышло (сообщение старше
48 часов, сеть моргнула) — просто идём дальше, поток от этого не ломается.

Экраны:
  помощь   — привет + что бот умеет (для тех, кто уже в базе)
  вопрос   — один вопрос анкеты
  карточка — анкета целиком и три кнопки: сохранить / изменить / удалить

Новый человек: /start → сразу первый вопрос → ... → карточка.
Уже знакомый:  /start → помощь, а по !обо мне → карточка.
Сохранил      → карточка удаляется, снова помощь.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

from database.engine import async_session_maker
from database.profile_dao import ProfileDAO
from handlers.command_help import HELP_TEXT
from utils.format import DIVIDER, field, format_phone
from utils.helpers import first_last, mention

profile_router = Router()

CB = "pf"

# Список умений берём из общей справки, чтобы не расходился с !помощь.
_ABILITIES = HELP_TEXT.split("\n", 1)[1].strip()

# Подписи кнопок нижней клавиатуры на вопросе про телефон. Они приходят
# обычным текстом, поэтому и разбираются в on_answer как текст.
CONTACT_BTN = "📱 Отправить мой номер"
SKIP_BTN = "⏭ Пропустить"
CANCEL_BTN = "❌ Отмена"
BACK_BTN = "↩️ Назад к анкете"

# У кого сейчас висит нижняя клавиатура — чтобы вовремя её убрать.
_reply_kb: set[int] = set()

# Последний экран бота у каждого человека: user_id → message_id.
# В памяти: потерять его не страшно, в худшем случае одно старое сообщение
# останется висеть в личке.
_screens: dict[int, int] = {}


# ─────────────────────────── разбор ответов ───────────────────────────

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
    Field("fio", "ФИО", "👤", "Как тебя зовут?",
          "Фамилия Имя Отчество — например <code>Иванов Иван Иванович</code>", True, _fio),
    Field("birthday", "День рождения", "🎂", "Когда у тебя день рождения?",
          "Формат <code>ДД.ММ.ГГГГ</code> — например <code>14.03.2005</code>", False, _birthday),
    Field("ik_div", "Направление", "🧭", "В каком направлении ты состоишь?",
          "ЦТ · СМИ · Фото · Дизайн · ПЗ · F&amp;U prod. Можно несколько через запятую.",
          False, _text),
    Field("group", "Группа", "🎓", "Твоя учебная группа?",
          "Например <code>РИ-410015</code>", False, _text),
    Field("phone", "Номер телефона", "📞", "Номер телефона?",
          "Жми <b>«Отправить мой номер»</b> внизу — подставлю сам. "
          "Или впиши руками: <code>+7 999 123-45-67</code>", False, _phone),
    Field("email", "Почта", "✉️", "Твоя почта?",
          "Например <code>ivanov@urfu.me</code>", False, _email),
    Field("clothes_size", "Размер одежды", "👕", "Размер одежды?",
          "S · M · L · XL — можно и <code>S/M</code>", False, _text),
    Field("someone_div", "Другие подразделения", "🏢",
          "Состоишь ещё где-то, кроме актива ИК?",
          "Профбюро, штаб отрядов, что угодно. Если нигде — жми «Пропустить».", False, _text),
)

BY_KEY = {f.key: f for f in FIELDS}


class Form(StatesGroup):
    question = State()   # ждём текстовый ответ
    card = State()       # на экране карточка, ждём кнопку


# ─────────────────────────── клавиатуры ───────────────────────────

def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=f"{CB}:{action}")


def _phone_kb(editing_one: bool) -> ReplyKeyboardMarkup:
    """Нижняя клавиатура с запросом контакта.

    Кнопка «поделиться контактом» бывает только на reply-клавиатуре, а у
    сообщения разметка одна — поэтому на этом шаге «Пропустить» и «Отмена»
    тоже становятся текстовыми кнопками, а не инлайновыми.
    """
    second = KeyboardButton(text=BACK_BTN if editing_one else CANCEL_BTN)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=CONTACT_BTN, request_contact=True)],
            [KeyboardButton(text=SKIP_BTN), second],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="или впиши номер руками",
    )


def _question_kb(fld: Field, editing_one: bool) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
    if fld.key == "phone":
        return _phone_kb(editing_one)
    row = []
    if not fld.required:
        row.append(_btn("⏭ Пропустить", "skip"))
    if editing_one:
        return _kb(row or [], [_btn("↩️ Назад к анкете", "card")])
    row.append(_btn("❌ Отмена", "cancel"))
    return _kb(row)


def _card_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("💾 Сохранить", "save")],
        [_btn("✏️ Изменить", "edit")],
        [_btn("🗑 Удалить", "drop")],
    )


def _edit_kb() -> InlineKeyboardMarkup:
    rows = [[_btn(f"{f.emoji} {f.label}", f"one:{f.key}")] for f in FIELDS]
    rows.append([_btn("↩️ Назад", "card")])
    return _kb(*rows)


def _confirm_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("🗑 Да, удалить", "drop_yes")], [_btn("↩️ Отмена", "card")])


# ─────────────────────────── тексты ───────────────────────────

def _shown(key: str, value) -> str:
    if key == "birthday" and isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if key == "phone" and value:
        return format_phone(value)
    return str(value) if value else ""


def _help_text(name: str) -> str:
    return "\n".join([
        f"👋 <b>Привет, {name}!</b>",
        "Ты есть в базе актива ИК.",
        DIVIDER,
        "📇 <b>Твоя анкета:</b>",
        "<code>!обо мне</code> — посмотреть, изменить или удалить свои данные",
        "",
        _ABILITIES,
        "",
        "<i>Команды про чат — топы, теги, цитаты — работают во флуде, не здесь.</i>",
    ])


def _question_text(idx: int, fld: Field, error: str | None,
                   editing_one: bool, greeting: bool) -> str:
    if editing_one:
        head = ["✏️ <b>Правим поле</b>"]
    elif greeting:
        head = [
            "👋 <b>Привет! Я БотИК.</b>",
            "",
            "Тебя ещё нет в базе актива ИК — давай зарегистрируемся. "
            "Восемь вопросов, почти все можно пропустить. "
            "Гугл-форму заполнять больше не надо.",
            "",
            f"<b>Вопрос 1 из {len(FIELDS)}</b>",
        ]
    else:
        head = [f"📝 <b>Регистрация</b> · вопрос {idx + 1} из {len(FIELDS)}"]

    lines = head + [DIVIDER, f"{fld.emoji} <b>{fld.question}</b>", f"<i>{fld.hint}</i>"]
    if error:
        lines += ["", f"⚠️ {error}"]
    lines += ["", "<i>Ответь сообщением — я его подчищу.</i>"]
    return "\n".join(lines)


def _card_text(values: dict, saved: bool) -> str:
    head = "📇 <b>Твоя анкета</b>" if saved else "📝 <b>Проверь, что получилось</b>"
    lines = [head, DIVIDER]
    for fld in FIELDS:
        lines.append(field(fld.label, _shown(fld.key, values.get(fld.key)),
                           fld.emoji, placeholder="—"))
    lines += ["", "💾 сохранить · ✏️ изменить поле · 🗑 удалить анкету"]
    return "\n".join(lines)


# ─────────────────────────── экраны ───────────────────────────

async def _drop_msg(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramAPIError:
        pass  # нет прав, слишком старое, уже удалено — не критично


async def _screen(bot: Bot, chat_id: int, user_id: int, text: str, kb=None) -> None:
    """Показать новый экран вместо предыдущего."""
    await _drop_msg(bot, chat_id, _screens.pop(user_id, None))

    # Уходим с шага телефона — убираем нижнюю клавиатуру. Снять её можно
    # только сообщением; само сообщение тут же удаляем, на снятие это
    # никак не влияет.
    if user_id in _reply_kb and not isinstance(kb, ReplyKeyboardMarkup):
        carrier = await bot.send_message(chat_id, "⌛", reply_markup=ReplyKeyboardRemove())
        await _drop_msg(bot, chat_id, carrier.message_id)
        _reply_kb.discard(user_id)
    sent = await bot.send_message(
        chat_id, text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True
    )
    _screens[user_id] = sent.message_id
    if isinstance(kb, ReplyKeyboardMarkup):
        _reply_kb.add(user_id)


async def _show_help(bot: Bot, chat_id: int, user_id: int, state: FSMContext,
                     activist=None) -> None:
    await state.clear()
    if activist is None:
        async with async_session_maker() as session:
            activist = await ProfileDAO(session).by_tg_id(user_id)
    name = first_last(activist.fio) if activist and activist.fio else "друг"
    await _screen(bot, chat_id, user_id, _help_text(name), None)


async def _show_question(bot: Bot, chat_id: int, user_id: int, state: FSMContext,
                         error: str | None = None, greeting: bool = False) -> None:
    data = await state.get_data()
    one = data.get("one")
    if one:
        fld, idx = BY_KEY[one], FIELDS.index(BY_KEY[one])
    else:
        idx = data.get("idx", 0)
        fld = FIELDS[idx]
    await state.set_state(Form.question)
    await _screen(bot, chat_id, user_id,
                  _question_text(idx, fld, error, bool(one), greeting),
                  _question_kb(fld, bool(one)))


async def _show_card(bot: Bot, chat_id: int, user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(one=None)
    await state.set_state(Form.card)
    await _screen(bot, chat_id, user_id,
                  _card_text(data.get("values", {}), bool(data.get("aid"))),
                  _card_kb())


async def _advance(bot: Bot, chat_id: int, user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("one"):
        await _show_card(bot, chat_id, user_id, state)
        return
    idx = data.get("idx", 0) + 1
    await state.update_data(idx=idx)
    if idx >= len(FIELDS):
        await _show_card(bot, chat_id, user_id, state)
    else:
        await _show_question(bot, chat_id, user_id, state)


async def _load(user, state: FSMContext) -> bool:
    """Забрать анкету из базы в состояние. True — человек уже зарегистрирован."""
    async with async_session_maker() as session:
        activist = await ProfileDAO(session).find(user.id, user.username)
    values, aid = {}, None
    if activist is not None:
        aid = activist.id
        for fld in FIELDS:
            current = getattr(activist, fld.key, None)
            if current not in (None, "", 0):
                values[fld.key] = current
    await state.set_data({"values": values, "idx": 0, "one": None, "aid": aid})
    return aid is not None


# ─────────────────────────── входные точки ───────────────────────────

def _is_about_cmd(message: Message) -> bool:
    if not message.text:
        return False
    text = message.text.strip().casefold()
    return text in ("!обо мне", "!обомне", "!анкета", "!я", "/анкета")


@profile_router.message(CommandStart(), F.chat.type == "private")
async def start_cmd(message: Message, state: FSMContext):
    bot, chat_id, user = message.bot, message.chat.id, message.from_user
    await _drop_msg(bot, chat_id, message.message_id)
    await state.clear()

    registered = await _load(user, state)
    payload = (message.text or "").partition(" ")[2].strip()

    if registered and payload != "anketa":
        await _show_help(bot, chat_id, user.id, state)
        return
    if registered:
        await _show_card(bot, chat_id, user.id, state)
        return
    # Новый человек: сразу первый вопрос, без лишнего клика.
    await _show_question(bot, chat_id, user.id, state, greeting=True)


@profile_router.message(F.chat.type == "private", _is_about_cmd)
async def about_cmd(message: Message, state: FSMContext):
    bot, chat_id, user = message.bot, message.chat.id, message.from_user
    await _drop_msg(bot, chat_id, message.message_id)
    await state.clear()

    if await _load(user, state):
        await _show_card(bot, chat_id, user.id, state)
    else:
        await _show_question(bot, chat_id, user.id, state, greeting=True)


@profile_router.message(F.chat.type.in_({"group", "supergroup"}), _is_about_cmd)
async def about_group(message: Message):
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=anketa"
    await message.reply(
        f'Анкета — в личке, <a href="{link}">жми сюда</a>.',
        parse_mode="HTML", disable_web_page_preview=True,
    )


def _from_contact(message: Message, fld: Field) -> tuple:
    """Номер из присланного контакта.

    Обязательно сверяем, что контакт его собственный: телеграм разрешает
    поделиться любым человеком из адресной книги, и без этой проверки в
    анкету уехал бы номер постороннего.
    """
    if fld.key != "phone":
        return None, "Сейчас я спрашиваю не про номер — ответь текстом."
    contact = message.contact
    if contact.user_id != message.from_user.id:
        return None, "Это чужой контакт. Нужен твой — жми «Отправить мой номер»."
    value, error = _phone(contact.phone_number)
    if error:
        # Не российский формат, но номер настоящий — пришёл из телеграма,
        # а не набран руками. Сохраняем как есть, чтобы не потерять.
        return contact.phone_number.strip(), None
    return value, None


async def _skip_field(bot: Bot, chat_id: int, user_id: int,
                      state: FSMContext, fld: Field) -> None:
    """Пропуск поля — общий путь для инлайн-кнопки и текстовой."""
    data = await state.get_data()
    values = dict(data.get("values", {}))
    values.pop(fld.key, None)
    await state.update_data(values=values)
    await _advance(bot, chat_id, user_id, state)


async def _cancel(bot: Bot, chat_id: int, user, state: FSMContext) -> None:
    async with async_session_maker() as session:
        activist = await ProfileDAO(session).by_tg_id(user.id)
    if activist is not None:
        await _show_help(bot, chat_id, user.id, state, activist)
        return
    await state.clear()
    await _screen(bot, chat_id, user.id,
                  "Окей, регистрацию отложили.\n\n"
                  "Захочешь вернуться — <code>/start</code>.")


@profile_router.message(Form.question, F.chat.type == "private")
async def on_answer(message: Message, state: FSMContext):
    bot, chat_id, user = message.bot, message.chat.id, message.from_user
    await _drop_msg(bot, chat_id, message.message_id)

    data = await state.get_data()
    one = data.get("one")
    fld = BY_KEY[one] if one else FIELDS[data.get("idx", 0)]

    # На шаге с телефоном «Пропустить», «Отмена» и «Назад» — кнопки нижней
    # клавиатуры, то есть приходят обычным текстом.
    text = (message.text or "").strip()
    if text == SKIP_BTN:
        if fld.required:
            await _show_question(bot, chat_id, user.id, state, "Это поле обязательное.")
        else:
            await _skip_field(bot, chat_id, user.id, state, fld)
        return
    if text == BACK_BTN:
        await _show_card(bot, chat_id, user.id, state)
        return
    if text == CANCEL_BTN:
        await _cancel(bot, chat_id, user, state)
        return

    if message.contact is not None:
        value, error = _from_contact(message, fld)
    elif not message.text:
        value, error = None, "Мне нужен текст — картинки и стикеры не подойдут."
    else:
        value, error = fld.parse(message.text)

    if error:
        await _show_question(bot, chat_id, user.id, state, error)
        return

    values = dict(data.get("values", {}))
    values[fld.key] = value
    await state.update_data(values=values)
    await _advance(bot, chat_id, user.id, state)


# ─────────────────────────── кнопки ───────────────────────────

def _remember(call: CallbackQuery) -> None:
    """Экран, на котором нажали кнопку, — он же и будет удалён следующим."""
    _screens[call.from_user.id] = call.message.message_id


@profile_router.callback_query(Form.question, F.data == f"{CB}:skip")
async def cb_skip(call: CallbackQuery, state: FSMContext):
    _remember(call)
    data = await state.get_data()
    one = data.get("one")
    fld = BY_KEY[one] if one else FIELDS[data.get("idx", 0)]
    if fld.required:
        await call.answer("Это поле обязательное", show_alert=True)
        return
    await call.answer()
    await _skip_field(call.bot, call.message.chat.id, call.from_user.id, state, fld)


@profile_router.callback_query(F.data == f"{CB}:card")
async def cb_card(call: CallbackQuery, state: FSMContext):
    _remember(call)
    await call.answer()
    if not (await state.get_data()).get("values"):
        await _load(call.from_user, state)
    await _show_card(call.bot, call.message.chat.id, call.from_user.id, state)


@profile_router.callback_query(Form.card, F.data == f"{CB}:edit")
async def cb_edit(call: CallbackQuery, state: FSMContext):
    _remember(call)
    await call.answer()
    await _screen(call.bot, call.message.chat.id, call.from_user.id,
                  "✏️ <b>Что поправить?</b>", _edit_kb())


@profile_router.callback_query(Form.card, F.data.startswith(f"{CB}:one:"))
async def cb_edit_one(call: CallbackQuery, state: FSMContext):
    _remember(call)
    key = call.data.split(":")[-1]
    if key not in BY_KEY:
        await call.answer()
        return
    await call.answer()
    await state.update_data(one=key)
    await _show_question(call.bot, call.message.chat.id, call.from_user.id, state)


@profile_router.callback_query(Form.card, F.data == f"{CB}:save")
async def cb_save(call: CallbackQuery, state: FSMContext):
    _remember(call)
    data = await state.get_data()
    values = data.get("values", {})
    if not values.get("fio"):
        await call.answer("Без ФИО не сохранить", show_alert=True)
        return

    await call.answer("Сохранил")
    user = call.from_user
    async with async_session_maker() as session:
        dao = ProfileDAO(session)
        existing = await dao.find(user.id, user.username)
        # Пропущенное поле — это осознанное «пусто», поэтому пишем и пустые.
        payload = {fld.key: values.get(fld.key, None if fld.key == "birthday" else "")
                   for fld in FIELDS}
        activist = await dao.save(user.id, user.username, payload, existing)

    await _show_help(call.bot, call.message.chat.id, user.id, state, activist)


@profile_router.callback_query(Form.card, F.data == f"{CB}:drop")
async def cb_drop(call: CallbackQuery, state: FSMContext):
    _remember(call)
    await call.answer()
    data = await state.get_data()
    if not data.get("aid"):
        # Черновик, в базе его ещё нет — просто выбрасываем, спрашивать не о чем.
        await state.clear()
        await _screen(call.bot, call.message.chat.id, call.from_user.id,
                      "Окей, ничего не сохранил.\n\nЗахочешь вернуться — "
                      "команда <code>!обо мне</code>.", None)
        return
    await _screen(
        call.bot, call.message.chat.id, call.from_user.id,
        "🗑 <b>Удалить анкету?</b>\n" + DIVIDER +
        "\nТебя не станет в базе актива: <code>!инфо</code> перестанет находить, "
        "из поздравлений и рассылок тоже выпадешь.\n\n"
        "<i>Отменить это я не смогу — только заполнить заново.</i>",
        _confirm_kb(),
    )


@profile_router.callback_query(Form.card, F.data == f"{CB}:drop_yes")
async def cb_drop_yes(call: CallbackQuery, state: FSMContext):
    _remember(call)
    data = await state.get_data()
    aid = data.get("aid")
    if not aid:
        await call.answer()
        return
    await call.answer("Удалил")
    async with async_session_maker() as session:
        await ProfileDAO(session).delete(aid, call.from_user.id)
    await state.clear()
    await _screen(call.bot, call.message.chat.id, call.from_user.id,
                  "🗑 <b>Анкета удалена.</b>\n\nПередумаешь — <code>/start</code>, "
                  "заполним заново.", None)


@profile_router.callback_query(F.data == f"{CB}:cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    _remember(call)
    await call.answer()
    await _cancel(call.bot, call.message.chat.id, call.from_user, state)

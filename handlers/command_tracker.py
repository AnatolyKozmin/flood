"""Вход в таск-трекер ИК (мини-апп на rezonans26.ru).

Трекер — отдельный сервис, но бот у них общий: по этому же токену трекер
шлёт уведомления о задачах и проверяет подпись входа из мини-аппа. Поэтому
здесь только кнопки, которые открывают трекер:

  кнопка меню  — слева от поля ввода в личке, ставится при старте бота
  !трекер      — в личке кнопка мини-аппа, в группе ссылка в личку
  /start web   — диплинк из веб-версии трекера («Подключить Telegram»)
  /start durak_<код>, sea_<код>, bunker_<код> — приглашение в партию из раздела «Игры» трекера
  /start play_durak_bot|human — новая партия в дурака (кнопки !дурак в группе)
  /start games — раздел «Игры»

Мини-апп открывается кнопкой web_app только в личке: в группах Telegram
такие кнопки не принимает, поэтому там ведём в личку обычной ссылкой.
"""
import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message, WebAppInfo,
)

from utils.format import DIVIDER

logger = logging.getLogger(__name__)

tracker_router = Router()

TRACKER_URL = os.getenv("TRACKER_URL", "https://rezonans26.ru").strip()

# Пейлоады /start, по которым показываем трекер, а не анкету.
START_PAYLOADS = {"web", "tracker"}

TRACKER_TEXT = "\n".join([
    "📋 <b>Трекер ИК</b>",
    DIVIDER,
    "Задачи, поручения, «Мой день» и календарь — прямо в Telegram.",
    "",
    "В первый раз трекер попросит заполнить анкету — после одобрения админа "
    "он будет открываться сразу, а уведомления о задачах придут сюда. "
    "Уже есть аккаунт? Войди на сайте и нажми «Подключить Telegram».",
    "",
    "<i>Ещё трекер открывается кнопкой «Трекер» слева от поля ввода.</i>",
])


# Приглашение в партию: код из трекера (app/services/durak_service.py,
# sea_battle_service.py, bunker_service.py). Пейлоад → (путь в трекере, текст, кнопка, о партии).
GAME_PAYLOAD = re.compile(r"^(durak|sea|bunker)_([A-Za-z0-9]{4,16})$")

GAME_INVITES = {
    "durak": ("durak", "🃏 <b>Тебя зовут в дурака</b>", "🃏 Сесть за стол", None),
    "sea": ("sea", "🚢 <b>Тебя зовут в морской бой</b>", "🚢 Сесть за стол", None),
    "bunker": (
        "bunker", "☢️ <b>Тебя зовут в бункер</b>", "☢️ Войти в лобби",
        "Партия в трекере ИК, от 4 до 16 игроков. Жми кнопку — попадёшь в лобби.",
    ),
}

GAMES_PAYLOAD = "games"

GAMES_TEXT = "\n".join([
    "🎮 <b>Игры в трекере ИК</b>",
    DIVIDER,
    "Дурак и морской бой — с ботом или один на один, бункер — всем активом.",
    "Позвать соперника можно прямо из игры: приглашение придёт во флуд.",
])


# Новая партия: play_<игра>_<bot|human> → /games/<игра>/new?vs=...
PLAY_PAYLOAD = re.compile(r"^play_(durak|sea)_(bot|human)$")

DURAK_PLAY_TEXT = "\n".join([
    "🃏 <b>Дурак</b> · подкидной один на один",
    DIVIDER,
    "Играем в трекере ИК: с ботом или позови кого-нибудь из актива — "
    "приглашение с кнопкой «Присоединиться» бот опубликует во флуде.",
])


def games_url(path: str = "") -> str:
    return f"{TRACKER_URL.rstrip('/')}/games{path}"


def game_invite(payload: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Пейлоад /start с приглашением в партию → текст и кнопка мини-аппа."""
    match = GAME_PAYLOAD.match(payload)
    if not match:
        return None
    path, title, button, about = GAME_INVITES[match.group(1)]
    url = games_url(f"/{path}/{match.group(2).upper()}")
    about = about or "Партия в трекере ИК, один на один. Жми кнопку — сядешь за стол."
    text = "\n".join([title, DIVIDER, about])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button, web_app=WebAppInfo(url=url))]
    ])
    return text, kb


def play_start(payload: str) -> tuple[str, InlineKeyboardMarkup] | None:
    """Пейлоад /start новой партии (из группы) → кнопка мини-аппа в личке."""
    match = PLAY_PAYLOAD.match(payload)
    if not match:
        return None
    kind, vs = match.groups()
    url = games_url(f"/{kind}/new?vs={vs}")
    title = "🃏 <b>Дурак</b>" if kind == "durak" else "🚢 <b>Морской бой</b>"
    who = "с ботом" if vs == "bot" else "с человеком"
    text = "\n".join([f"{title} {who}", DIVIDER, "Жми кнопку — партия откроется в трекере."])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать", web_app=WebAppInfo(url=url))]
    ])
    return text, kb


def durak_play_kb_private() -> InlineKeyboardMarkup:
    """!дурак в личке: мини-апп открывается прямо кнопками."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤖 С ботом", web_app=WebAppInfo(url=games_url("/durak/new?vs=bot"))),
        InlineKeyboardButton(text="👥 С человеком", web_app=WebAppInfo(url=games_url("/durak/new?vs=human"))),
    ]])


async def durak_play_kb_group(bot: Bot) -> InlineKeyboardMarkup:
    """!дурак в группе: web_app-кнопки там нельзя — ведём ссылками
    (сразу в мини-апп, если он основной у бота, иначе через личку)."""
    me = await bot.me()
    key = "startapp" if getattr(me, "has_main_web_app", False) else "start"
    link = f"https://t.me/{me.username}?{key}=play_durak_"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🤖 С ботом", url=link + "bot"),
        InlineKeyboardButton(text="👥 С человеком", url=link + "human"),
    ]])


def games_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Открыть игры", web_app=WebAppInfo(url=games_url()))]
    ])


def tracker_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="📋 Открыть трекер", web_app=WebAppInfo(url=TRACKER_URL))


def tracker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[tracker_button()]])


async def setup_menu_button(bot: Bot) -> None:
    """Кнопка меню в личке открывает трекер. Не вышло — бот работает и без неё."""
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Трекер", web_app=WebAppInfo(url=TRACKER_URL))
        )
    except Exception as exc:
        logger.warning("Не удалось поставить кнопку трекера в меню: %s", exc)


class Exact(BaseFilter):
    def __init__(self, *cmds: str) -> None:
        self.cmds = {cmd.strip().casefold() for cmd in cmds}

    async def __call__(self, message: Message) -> bool:
        return bool(message.text) and message.text.strip().casefold() in self.cmds


TRACKER_CMDS = ("!трекер", "/tracker", "/трекер")


@tracker_router.message(F.chat.type == "private", Exact(*TRACKER_CMDS))
async def tracker_private(message: Message):
    await message.answer(TRACKER_TEXT, reply_markup=tracker_kb(), parse_mode="HTML")


@tracker_router.message(F.chat.type.in_({"group", "supergroup"}), Exact(*TRACKER_CMDS))
async def tracker_group(message: Message):
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=tracker"
    await message.reply(
        f'Трекер открывается в личке, <a href="{link}">жми сюда</a>.',
        parse_mode="HTML", disable_web_page_preview=True,
    )

"""!все — тегнуть всех во флуде.

Важное ограничение телеграма: бот НЕ может получить список участников группы
(getChatAdministrators отдаёт только админов, а полного списка в Bot API нет).
Поэтому тегаем тех, кого бот реально видел в этом чате — их собирает
middlewares/message_counter.py в таблицу message_stats. То есть в списке
окажется каждый, кто написал хоть одно сообщение с момента добавления бота.

Тегаем ссылкой tg://user?id=... — она пингует и тех, у кого нет @юзернейма,
а имя подставляем из базы актива (ФИО), если человек там есть.
"""
import html
from time import monotonic

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.stats_dao import StatsDAO
from middlewares.message_counter import flush_stats
from utils.stats import build_names, plural

all_router = Router()

ALIASES = ("!все", "!всё", "!all", "!общий сбор")

# Массовый пинг — штука шумная, поэтому не чаще раза в 5 минут на чат.
COOLDOWN_SECONDS = 5 * 60

# Сколько упоминаний в одно сообщение. Ограничение не по длине, а по
# количеству сущностей: телеграм режет сообщения с сотнями ссылок.
CHUNK = 40

GROUP_ONLY = "Эта команда для группового чата — в личке тегать некого 🙂"

_last_call: dict[int, float] = {}


class FirstWords(BaseFilter):
    """Команда — начало сообщения (учитываем «!общий сбор» из двух слов)."""

    def __init__(self, *commands: str) -> None:
        self.commands = tuple(c.strip().casefold() for c in commands)

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        text = message.text.strip().casefold()
        return any(
            text == cmd or text.startswith(cmd + " ")
            for cmd in self.commands
        )


def _tail(text: str) -> str:
    """Хвост после команды: «!все го обедать» → «го обедать»."""
    low = text.strip().casefold()
    for cmd in ALIASES:
        if low.startswith(cmd):
            return text.strip()[len(cmd):].strip()
    return ""


def _link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


@all_router.message(FirstWords(*ALIASES))
async def all_cmd(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.reply(GROUP_ONLY)
        return

    now = monotonic()
    last = _last_call.get(message.chat.id)
    if last is not None and now - last < COOLDOWN_SECONDS:
        left = int(COOLDOWN_SECONDS - (now - last))
        await message.reply(
            f"Только что уже звали. Следующий сбор через {left // 60} мин {left % 60} сек ⏳"
        )
        return

    await flush_stats()  # вдруг кто-то написал первый раз только что
    async with async_session_maker() as session:
        board = await StatsDAO(session).leaderboard(message.chat.id)
        user_ids = [user_id for user_id, _ in board]
        if not user_ids:
            await message.reply(
                "Я пока никого здесь не видел — тегать некого. "
                "Дай людям написать хоть по сообщению."
            )
            return
        names = await build_names(session, user_ids)

    _last_call[message.chat.id] = now

    tail = _tail(message.text)
    header = html.escape(tail) if tail else "Общий сбор!"
    total = len(user_ids)
    lines = [f"📣 <b>{header}</b>",
             f"<i>Зову всех, кого видел в чате — {total} "
             f"{plural(total, 'человек', 'человека', 'человек')}</i>"]
    await message.answer("\n".join(lines), parse_mode="HTML")

    for start in range(0, total, CHUNK):
        chunk = user_ids[start:start + CHUNK]
        await message.answer(
            " ".join(_link(user_id, names[user_id]) for user_id in chunk),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

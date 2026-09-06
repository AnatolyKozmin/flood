"""!все — тегнуть всех во флуде.

Важное ограничение телеграма: бот НЕ может получить список участников группы
(getChatAdministrators отдаёт только админов, полного списка в Bot API нет).
Поэтому зовём из двух источников сразу:

  1. Кого бот видел пишущим в этом чате — их собирает message_counter в
     message_stats. Таких зовём ссылкой tg://user?id=: она не протухает при
     смене @тега и работает даже у тех, у кого тега нет вовсе.
  2. Весь актив из базы, у кого есть @тег. Нужно потому, что счётчик считает
     только с момента своего появления: сразу после обновления бота первый
     источник пуст, и без второго !все не позвал бы вообще никого.

Дубли убираем по @тегу, иначеавший активист попал бы в список дважды.

Оговорка, которую не обойти: упоминание пингует только того, кто реально
состоит в чате. Кто в базе есть, а во флуде его нет, просто увидит свой тег
текстом — узнать состав чата бот не может.
"""
import html
from time import monotonic

from aiogram import F, Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.engine import async_session_maker
from database.models import Activists
from database.stats_dao import StatsDAO
from sqlalchemy import select
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
        dao = StatsDAO(session)
        board = await dao.leaderboard(message.chat.id)
        user_ids = [user_id for user_id, _ in board]
        names = await build_names(session, user_ids) if user_ids else {}

        # Чьи теги уже покрыты первым источником — чтобы не звать дважды.
        seen = await dao.users(user_ids)
        covered = {
            (user.username or "").strip().lstrip("@").casefold()
            for user in seen.values() if user.username
        }

        rows = (await session.execute(
            select(Activists).where(Activists.is_active.is_(True))
        )).scalars().all()
        extra = []
        for activist in rows:
            tag = (activist.tg_username or "").strip().lstrip("@")
            if tag and tag.casefold() not in covered:
                extra.append(tag)

    mentions = [_link(user_id, names[user_id]) for user_id in user_ids]
    mentions += [f"@{tag}" for tag in sorted(extra, key=str.casefold)]

    if not mentions:
        await message.reply(
            "Звать некого: в базе актива нет ни одного @тега, и писавших я "
            "пока не видел."
        )
        return

    _last_call[message.chat.id] = now

    tail = _tail(message.text)
    header = html.escape(tail) if tail else "Общий сбор!"
    total = len(mentions)
    lines = [f"📣 <b>{header}</b>",
             f"<i>Зову {total} {plural(total, 'человека', 'человек', 'человек')} — "
             f"весь актив из базы плюс всех, кто писал в чате</i>"]
    await message.answer("\n".join(lines), parse_mode="HTML")

    for start in range(0, total, CHUNK):
        await message.answer(
            " ".join(mentions[start:start + CHUNK]),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

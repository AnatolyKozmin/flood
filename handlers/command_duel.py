"""Дуэли, рулетка, белый флаг.

!дуэль @тег (или ответом на сообщение) — победитель случаен: может пасть
как вызванный, так и вызвавший. Проигравший умирает на час: все его
сообщения во флуде трёт мидлварь (middlewares/dead_mute.py), через час
планировщик воскрешает.

!поднять флаг — защита от дуэлей на 3 дня, !опустить флаг — снять.
!рулетка — шанс 1 к 6 умереть на час.
"""
import html
import random

from aiogram import Router
from aiogram.filters import BaseFilter
from aiogram.types import Message

from database.duel_dao import DuelDAO
from database.engine import async_session_maker
from database.stats_dao import StatsDAO

duel_router = Router()

DUEL_CMD = "!дуэль"
ROULETTE_CMD = "!рулетка"
FLAG_UP_CMDS = ("!поднять флаг", "!подними флаг")
FLAG_DOWN_CMDS = ("!опустить флаг", "!опусти флаг")

ROULETTE_DEATH_CHANCE = 1 / 6

GROUP_ONLY = "Эта команда для группового чата — дуэли во флуде 🙂"


class FirstWord(BaseFilter):
    """Команда — ровно первое слово сообщения (как в !цитата)."""

    def __init__(self, *commands: str) -> None:
        self.commands = {c.strip().casefold() for c in commands}

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().split(maxsplit=1)
        return bool(parts) and parts[0].casefold() in self.commands


class FirstWords(BaseFilter):
    """Команда — начало сообщения (для «!поднять флаг» из двух слов)."""

    def __init__(self, *commands: str) -> None:
        self.commands = tuple(c.strip().casefold() for c in commands)

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        text = message.text.strip().casefold()
        return any(text == cmd or text.startswith(cmd + " ") for cmd in self.commands)


def _who(user_id: int, username: str | None, display: str | None) -> str:
    """Как показать человека: @тег, а если тега нет — кликабельная ссылка
    (работает и пингует даже без username)."""
    tag = (username or "").strip().lstrip("@")
    if tag:
        return f"@{tag}"
    name = html.escape((display or "").strip() or "боец")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def _me(user) -> tuple[int, str, str]:
    return user.id, user.username or "", user.full_name or ""


async def _resolve_target(message: Message) -> tuple[int, str, str] | None:
    """Кого вызывают: автор сообщения-ответа → @тег из счётчика писавших.
    None — никого не нашли."""
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        return _me(reply.from_user)

    raw = message.text.strip().split(maxsplit=1)
    if len(raw) > 1 and raw[1].strip():
        async with async_session_maker() as session:
            found = await StatsDAO(session).user_by_username(raw[1].strip())
        if found is not None:
            return found.user_id, found.username or "", found.full_name or ""
        clean = html.escape(raw[1].strip().lstrip("@"))
        await message.reply(
            f"Не нашёл «{clean}» среди тех, кто писал в чат при мне. "
            "Пусть напишет что-нибудь — или ответь <code>!дуэль</code> "
            "на его сообщение.",
            parse_mode="HTML",
        )
        return None

    await message.reply(
        "Кого вызываешь? Укажи @тег или ответь <code>!дуэль</code> "
        "на его сообщение.",
        parse_mode="HTML",
    )
    return None


def _is_group(message: Message) -> bool:
    return message.chat.type in ("group", "supergroup")


@duel_router.message(FirstWord(DUEL_CMD))
async def duel_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    target = await _resolve_target(message)
    if target is None:
        return
    target_id, target_tag, target_name = target
    me_id, me_tag, me_name = _me(message.from_user)

    if target_id == me_id:
        await message.reply("С самим собой драться не получится.")
        return

    async with async_session_maker() as session:
        dao = DuelDAO(session)
        if await dao.is_dead(message.chat.id, target_id) is not None:
            await message.reply("То, что мертво, умереть не может")
            return
        if await dao.get_flag(message.chat.id, target_id) is not None:
            await message.reply("Белый флаг даёт неприкосновенность")
            return

        # Победитель случаен: пасть может и вызвавший.
        if random.random() < 0.5:
            loser, winner = (target_id, target_tag, target_name), (me_id, me_tag, me_name)
        else:
            loser, winner = (me_id, me_tag, me_name), (target_id, target_tag, target_name)
        await dao.kill(message.chat.id, loser[0], loser[1], loser[2])

    await message.answer(
        f"{_who(*loser)} умер! Воскрешение запланировано через час. "
        f"Наслаждайся победой {_who(*winner)}",
        parse_mode="HTML",
    )


@duel_router.message(FirstWord(ROULETTE_CMD))
async def roulette_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    me_id, me_tag, me_name = _me(message.from_user)
    who = _who(me_id, me_tag, me_name)

    if random.random() < ROULETTE_DEATH_CHANCE:
        async with async_session_maker() as session:
            await DuelDAO(session).kill(message.chat.id, me_id, me_tag, me_name)
        await message.answer(
            f"{who} умер(( Воскрешение запланировано через 1 час, "
            "зря ты игрался",
            parse_mode="HTML",
        )
        return
    await message.answer(
        f"{who} остался жив! Лучше не играй с такими вещами...",
        parse_mode="HTML",
    )


@duel_router.message(FirstWords(*FLAG_UP_CMDS))
async def flag_up_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    me_id, me_tag, me_name = _me(message.from_user)
    async with async_session_maker() as session:
        raised = await DuelDAO(session).raise_flag(
            message.chat.id, me_id, me_tag, me_name
        )
    if not raised:
        await message.reply("Ты уже под белым флагом!")
        return
    await message.answer(
        f"{_who(me_id, me_tag, me_name)} теперь под белым флагом!",
        parse_mode="HTML",
    )


@duel_router.message(FirstWords(*FLAG_DOWN_CMDS))
async def flag_down_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    me_id, me_tag, me_name = _me(message.from_user)
    async with async_session_maker() as session:
        lowered = await DuelDAO(session).lower_flag(message.chat.id, me_id)
    if not lowered:
        await message.reply("Ты и так без белого флага.")
        return
    await message.answer(
        f"{_who(me_id, me_tag, me_name)} больше не под белым флагом! "
        "Можно атаковать",
        parse_mode="HTML",
    )

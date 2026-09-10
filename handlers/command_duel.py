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

from database.dao import ActivistsDAO
from database.duel_dao import DuelDAO, FLAG_TTL
from database.engine import async_session_maker
from database.stats_dao import StatsDAO
from utils.format import DIVIDER
from utils.helpers import first_last, msk_now
from utils.stats import plural

duel_router = Router()

DUEL_CMD = "!дуэль"
ROULETTE_CMD = "!рулетка"
FLAG_UP_CMDS = ("!поднять флаг", "!подними флаг")
FLAG_DOWN_CMDS = ("!опустить флаг", "!опусти флаг")
PEACEFUL_CMD = "!мирные"
GRAVEYARD_CMD = "!кладбище"
DUEL_TOP_WORDS = {"дуэлей", "дуэлянтов", "дуэли", "дуэлянты"}

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

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


async def _plain_name(
    session, user_id: int, username: str | None, display: str | None
) -> str:
    """Имя без тега и без ссылки — «Имя Фамилия» из базы актива,
    иначе имя из телеграма. Для списков (!мирные, !кладбище, флаги),
    где пинговать никого не надо."""
    tag = (username or "").strip().lstrip("@")
    if tag:
        activist = await ActivistsDAO(session).get_by_username(tag)
        if activist and activist.fio:
            return html.escape(first_last(activist.fio))
    base = (display or "").strip() or tag
    return html.escape(base or "боец")


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


def _left(delta) -> str:
    """Остаток времени по-человечески: «5 мин», «1 ч 10 мин»."""
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"
    hours, rest = divmod(minutes, 60)
    tail = f" {rest} {plural(rest, 'минуту', 'минуты', 'минут')}" if rest else ""
    return f"{hours} {plural(hours, 'час', 'часа', 'часов')}{tail}"


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
        await dao.record_duel(message.chat.id, winner, loser)

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
    me = (me_id, me_tag, me_name)

    if random.random() < ROULETTE_DEATH_CHANCE:
        async with async_session_maker() as session:
            dao = DuelDAO(session)
            await dao.kill(message.chat.id, me_id, me_tag, me_name)
            await dao.record_roulette(message.chat.id, me, survived=False)
        await message.answer(
            f"{who} умер(( Воскрешение запланировано через 1 час, "
            "зря ты игрался",
            parse_mode="HTML",
        )
        return
    async with async_session_maker() as session:
        await DuelDAO(session).record_roulette(message.chat.id, me, survived=True)
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
        dao = DuelDAO(session)
        raised = await dao.raise_flag(message.chat.id, me_id, me_tag, me_name)
        name = await _plain_name(session, me_id, me_tag, me_name)
    if not raised:
        await message.reply("Ты уже под белым флагом!")
        return
    await message.answer(
        f"{name} теперь под белым флагом!",
        parse_mode="HTML",
    )


@duel_router.message(FirstWords(*FLAG_DOWN_CMDS))
async def flag_down_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    me_id, me_tag, me_name = _me(message.from_user)
    async with async_session_maker() as session:
        dao = DuelDAO(session)
        lowered = await dao.lower_flag(message.chat.id, me_id)
        name = await _plain_name(session, me_id, me_tag, me_name)
    if not lowered:
        await message.reply("Ты и так без белого флага.")
        return
    await message.answer(
        f"{name} больше не под белым флагом! Можно атаковать",
        parse_mode="HTML",
    )


@duel_router.message(FirstWord(PEACEFUL_CMD))
async def peaceful_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    async with async_session_maker() as session:
        flags = await DuelDAO(session).list_flags(message.chat.id)
        if not flags:
            await message.answer("🕊️ Белых флагов нет — все уязвимы ⚔️")
            return
        lines = ["🕊️ <b>Мирные — под белым флагом</b>", DIVIDER]
        for flag in flags:
            until = (flag.raised_at + FLAG_TTL).strftime("%d.%m")
            name = await _plain_name(session, flag.user_id, flag.username, flag.display)
            lines.append(f"• {name} — до {until}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@duel_router.message(FirstWord(GRAVEYARD_CMD))
async def graveyard_cmd(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    async with async_session_maker() as session:
        dead = await DuelDAO(session).list_dead(message.chat.id)
        if not dead:
            await message.answer("🪦 Кладбище пусто — все живы 🎉")
            return
        now = msk_now()
        lines = ["🪦 <b>Кладбище</b>", DIVIDER]
        for soul in dead:
            left = soul.resurrect_at - now
            when = "вот-вот" if left.total_seconds() <= 0 else f"через {_left(left)}"
            name = await _plain_name(
                session, soul.user_id, soul.username, soul.display
            )
            lines.append(f"• {name} — воскреснет {when}")
    await message.answer("\n".join(lines), parse_mode="HTML")


class TopDuel(BaseFilter):
    """«!топ дуэлей» — ловим раньше обычного !топ (наш роутер идёт раньше)."""

    async def __call__(self, message: Message) -> bool:
        if not message.text:
            return False
        parts = message.text.strip().casefold().split()
        return (
            len(parts) >= 2 and parts[0] == "!топ" and parts[1] in DUEL_TOP_WORDS
        )


@duel_router.message(TopDuel())
async def top_duelists(message: Message):
    if not _is_group(message):
        await message.reply(GROUP_ONLY)
        return

    async with async_session_maker() as session:
        dao = DuelDAO(session)
        winners, losers = await dao.top_fighters(message.chat.id)
        if not winners and not losers:
            await message.reply(
                "Дуэлей и рулеток ещё не было — начни с "
                "<code>!дуэль</code> или <code>!рулетка</code>.",
                parse_mode="HTML",
            )
            return
        lines = ["⚔️ <b>Топ дуэлянтов</b>", DIVIDER]
        if winners:
            lines.append("👑 <b>Чаще выигрывают:</b>")
            for place, row in enumerate(winners, start=1):
                total = row.duel_wins + row.roulette_wins
                name = await _plain_name(
                    session, row.user_id, row.username, row.display
                )
                medal = MEDALS.get(place, f"{place}.")
                lines.append(
                    f"{medal} {name} — {total} "
                    f"{plural(total, 'победа', 'победы', 'побед')}"
                )
        if losers:
            lines.append("💀 <b>Чаще проигрывают:</b>")
            for place, row in enumerate(losers, start=1):
                total = row.duel_losses + row.roulette_losses
                name = await _plain_name(
                    session, row.user_id, row.username, row.display
                )
                medal = MEDALS.get(place, f"{place}.")
                lines.append(
                    f"{medal} {name} — {total} "
                    f"{plural(total, 'поражение', 'поражения', 'поражений')}"
                )
    await message.answer("\n".join(lines), parse_mode="HTML")

import asyncio
import html
import io
import random
import re
from datetime import date, datetime
from pathlib import Path
from PIL import Image
from aiogram import Router, F
from aiogram.types import BufferedInputFile, FSInputFile, Message
from aiogram.filters import Command
from sqlalchemy import select

from database.dao import ActivistsDAO
from database.engine import async_session_maker
from database.models import Activists
from database.profile_dao import ProfileDAO
from utils.helpers import first_last, moscow_today
from utils.stats import find_activists, plural


random_router = Router()


@random_router.message(F.text.startswith('!вероятность'))
async def probability_cmd(message: Message):
    # Получаем текст после команды '!вероятность'
    text_after = message.text[13:].strip()  # '!вероятность' = 12 символов + пробел
    
    random_percent = random.randint(1, 100)

    await message.reply(f'{text_after} с вероятностью {random_percent}%')


@random_router.message(F.text.startswith('!подскажи'))
async def get_advice(message: Message):
    lst_advice = ['Да', 'Нет', 'Забей да по братски']

    res_advise = random.choice(lst_advice)

    await message.reply(res_advise)


def _lived_days(birthday, today) -> int:
    """Сколько дней человек живёт. Дату рождения считаем московской,
    как и всё остальное в проекте."""
    born = birthday.date() if isinstance(birthday, datetime) else birthday
    return max(0, (today - born).days)


@random_router.message(F.text.startswith('!жызуха') | F.text.startswith('!жизуха'))
async def lifespan_cmd(message: Message):
    arg = message.text.strip()[len('!жызуха'):].strip()
    reply = message.reply_to_message

    async with async_session_maker() as session:
        activist = None
        if reply and reply.from_user and not reply.from_user.is_bot:
            user = reply.from_user
            activist = await ProfileDAO(session).by_tg_id(user.id)
            if activist is None:
                activist = await ActivistsDAO(session).get_by_username(
                    user.username or ""
                )
            name = (
                first_last(activist.fio)
                if activist and activist.fio
                else user.full_name
            )
        elif arg:
            found = await find_activists(session, arg)
            if not found:
                clean = html.escape(arg.lstrip("@"))
                await message.reply(
                    f"Не нашёл «{clean}» в базе актива — дату рождения взять негде."
                )
                return
            if len(found) > 1:
                await message.reply(
                    "Нашёл нескольких — уточни по тегу: "
                    + ", ".join(
                        f"<code>!жызуха {html.escape(a.tg_username)}</code>"
                        for a in found[:5] if a.tg_username
                    ),
                    parse_mode="HTML",
                )
                return
            activist = found[0]
            name = first_last(activist.fio) if activist.fio else "Активист"
        else:
            user = message.from_user
            activist = await ProfileDAO(session).by_tg_id(user.id)
            if activist is None:
                activist = await ActivistsDAO(session).get_by_username(
                    user.username or ""
                )
            name = (
                first_last(activist.fio)
                if activist and activist.fio
                else user.full_name
            )

        if activist is None or not activist.birthday:
            await message.reply(
                f"Не знаю день рождения {html.escape(name)} — пусть заполнит "
                "анкету через <code>!обо мне</code>.",
                parse_mode="HTML",
            )
            return

        days = _lived_days(activist.birthday, moscow_today())
        born = activist.birthday.strftime("%d.%m.%Y")

    await message.answer(
        f"🎂 {html.escape(name)} живёт уже {days} "
        f"{plural(days, 'день', 'дня', 'дней')} (с {born})!",
        parse_mode="HTML",
    )


async def _anatoly_tag(session) -> str:
    """Тег Анатолия Козьмина из базы актива. Хардкод — запасной."""
    rows = (await session.execute(select(Activists))).scalars().all()
    for activist in rows:
        words = set((activist.fio or "").casefold().split())
        if {"анатолий", "козьмин"} <= words:
            tag = (activist.tg_username or "").strip().lstrip("@")
            if tag:
                return tag
    return "yanejettt"


@random_router.message(F.text.startswith('!тагил'))
async def tagil_cmd(message: Message):
    # Секретная команда: в !помощь её нет — и не добавляй.
    async with async_session_maker() as session:
        tag = await _anatoly_tag(session)
    await message.answer(f"@{tag}, ПОШЛИ РАБОТАТЬ В ПОНЕДЕЛЬНИК! ТАГИИИИЛ!")


VOTE_QUOTES = (
    "Если драка неизбежна, бить надо первым",
    "Нам скучно, застой! Хочется движухи!",
    "Чем меньше зубов, тем больше любишь кашу",
    "В известном итальянском фильме есть такая фраза - "
    "\"настоящий мужчина должен всегда пытаться, "
    "а настоящая женщина должна сопротивляться\"",
    "Везёт дуракам, а мы работаем с утра до ночи",
    "Мы будем преследовать террористов всюду. Если в туалете поймаем, "
    "то и в сортире их замочим",
    "Я не понимаю, как 200 наших болельщиков отметелили несколько тысяч "
    "англичан, но подход должен быть одинаковый ко всем",
)


@random_router.message(F.text.startswith('!голосовать'))
async def vote_cmd(message: Message):
    await message.answer(f"🗳️ {random.choice(VOTE_QUOTES)}")


_MAKAN_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_MAKAN_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MAKAN_CAPTION = "Я говорю Macan, вы говорите ..."


def _makan_photo() -> Path | None:
    for ext in _MAKAN_EXTS:
        candidate = _MAKAN_ASSETS / f"macan{ext}"
        if candidate.is_file():
            return candidate
    return None


DICE_COOLDOWN = 15.0  # один кубик на человека: чаще — жди
_last_dice: dict[int, float] = {}

EVEN = {"чет", "чёт", "четное", "чётное"}
ODD = {"нечет", "нечёт", "нечетное", "нечётное"}


_CHOICE_RE = re.compile(
    r"^(.+?)\(([^)]+)\)\s+или\s+(.+?)\(([^)]+)\)$", re.IGNORECASE)
_BARE_RE = re.compile(
    r"^(.*\S)\s+(чёт|чет|чётное|четное|нечёт|нечет|нечётное|нечетное|1-3|4-6)$",
    re.IGNORECASE)


def _parse_choice(arg: str) -> tuple[tuple[str, set[int]], tuple[str, set[int]]] | None:
    """Выбор из двух: 'домой(1-3) или в клуб(4-6)' или без скобок
    'спать 1-3 или не спать 4-6' (условие — последним словом половины).
    Условия те же строгие. None — не похоже на выбор."""
    text = arg.strip().replace("—", "-").replace("–", "-")
    match = _CHOICE_RE.match(text)
    if match is not None:
        first, cond_first, second, cond_second = (
            part.strip() for part in match.groups())
        parsed_first = _parse_dice(cond_first)
        parsed_second = _parse_dice(cond_second)
        if (parsed_first is None or parsed_second is None
                or not first or not second
                or len(first) > 100 or len(second) > 100):
            return None
        return (first, parsed_first[0]), (second, parsed_second[0])
    halves = re.split(r"\s+или\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(halves) != 2:
        return None
    out = []
    for half in halves:
        bare = _BARE_RE.match(half.strip())
        if bare is None:
            return None
        opt, cond = bare.group(1).strip(), bare.group(2)
        parsed = _parse_dice(cond)
        if parsed is None or not opt or len(opt) > 100:
            return None
        out.append((opt, parsed[0]))
    return out[0], out[1]


def _parse_dice(arg: str) -> tuple[set[int], str] | None:
    """Условие -> (выигрышные значения, название). None — не распознали."""
    clean = arg.strip().casefold().replace("—", "-").replace("–", "-")
    clean = "".join(clean.split())
    if clean in EVEN:
        return {2, 4, 6}, "чётное"
    if clean in ODD:
        return {1, 3, 5}, "нечётное"
    if clean in ("1-3", "1–3"):
        return {1, 2, 3}, "1–3"
    if clean in ("4-6", "4–6"):
        return {4, 5, 6}, "4–6"
    return None


@random_router.message(F.text.startswith('!кубик'))
async def dice_cmd(message: Message):
    from time import monotonic

    arg = message.text.strip()[len('!кубик'):].strip()
    if not arg:
        await message.reply(
            "Условие не понял. Так можно: <code>!кубик чёт</code> · "
            "<code>!кубик нечет</code> · <code>!кубик 1-3</code> · "
            "<code>!кубик 4-6</code> · <code>!кубик выиграю ли</code> · "
            "<code>!кубик домой(1-3) или в клуб(4-6)</code>",
            parse_mode="HTML",
        )
        return
    choice = _parse_choice(arg)
    parsed = None if choice is not None else _parse_dice(arg)
    now = monotonic()
    last = _last_dice.get(message.from_user.id, 0.0)
    if now - last < DICE_COOLDOWN:
        left = int(DICE_COOLDOWN - (now - last))
        await message.reply(f"Кубик отдыхает. Ещё {left} сек ⏳")
        return
    _last_dice[message.from_user.id] = now
    rolled = await message.answer_dice(emoji="🎲")
    value = rolled.dice.value if rolled.dice else 0
    # Вердикт только после анимации (~3 сек): иначе спойлерим итог.
    await asyncio.sleep(4.0)
    if choice is not None:
        (first, win_first), (second, win_second) = choice
        if value in win_first:
            await message.reply(f'🎲 Кубик сказал: "{first}"')
        elif value in win_second:
            await message.reply(f'🎲 Кубик сказал: "{second}"')
        else:
            await message.reply(f"🎲 Выпало {value} — мимо обоих ❌")
        return
    if parsed is not None:
        win, label = parsed
        if value in win:
            await message.reply(f"🎲 Выпало {value} — {label}, сошлось ✅")
        else:
            await message.reply(f"🎲 Выпало {value} — мимо ❌")
        return
    # Ставка на событие: 1–3 — да, 4–6 — нет.
    half = "1–3" if value <= 3 else "4–6"
    verdict = "да ✅" if value <= 3 else "нет ❌"
    await message.reply(
        f"🎲 {html.escape(arg)} — выпало {value} ({half}): {verdict}")


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря")


@random_router.message(F.text.startswith('!когда'))
async def when_cmd(message: Message):
    from datetime import timedelta

    event = message.text.strip()[len('!когда'):].strip()
    if not event:
        await message.reply("А что именно? Например: <code>!когда зарплата</code>",
                            parse_mode="HTML")
        return
    day = moscow_today() + timedelta(days=random.randint(1, 365))
    await message.reply(
        f"📅 {html.escape(event)} — {day.day} {_MONTHS[day.month - 1]} {day.year}")


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря")

_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")


def _parse_day_month(arg: str) -> tuple[int, int, int | None] | None:
    """ДД.ММ[.ГГГГ] или None."""
    match = _DATE_RE.match(arg.strip())
    if match is None:
        return None
    day, month, year = (int(match.group(1)), int(match.group(2)),
                        int(match.group(3)) if match.group(3) else None)
    try:
        # Без года проверяем по високосному (29.02 пропускаем — дальше
        # вызывающие разберутся с конкретным годом).
        date(year if year is not None else 2000, month, day)
    except ValueError:
        return None
    return day, month, year


def _date_hint(cmd: str) -> str:
    return (f"Когда? Например: <code>{cmd} 01.09</code> "
            f"или <code>{cmd} 01.09.2026</code>")


@random_router.message(F.text.startswith('!до'))
async def until_cmd(message: Message):
    arg = message.text.strip()[len('!до'):].strip()
    parsed = _parse_day_month(arg)
    if parsed is None:
        await message.reply(_date_hint("!до"), parse_mode="HTML")
        return
    day, month, year = parsed
    today = moscow_today()
    if year is not None:
        target = date(year, month, day)
    else:
        target = date(today.year, month, day)
        try:
            candidate = target
        except ValueError:
            await message.reply(_date_hint("!до"), parse_mode="HTML")
            return
        if candidate <= today:
            try:
                target = date(today.year + 1, month, day)
            except ValueError:
                await message.reply(_date_hint("!до"), parse_mode="HTML")
                return
    delta = (target - today).days
    if delta <= 0:
        await message.reply("Это уже прошло или сегодня 🙂")
        return
    await message.reply(
        f"📅 До {target.day} {_MONTHS[target.month - 1]} {target.year} "
        f"осталось: {delta} {plural(delta, 'день', 'дня', 'дней')}")


@random_router.message(F.text.startswith('!от'))
async def since_cmd(message: Message):
    arg = message.text.strip()[len('!от'):].strip()
    parsed = _parse_day_month(arg)
    if parsed is None:
        await message.reply(_date_hint("!от"), parse_mode="HTML")
        return
    day, month, year = parsed
    today = moscow_today()
    if year is not None:
        target = date(year, month, day)
    else:
        try:
            target = date(today.year, month, day)
        except ValueError:
            await message.reply(_date_hint("!от"), parse_mode="HTML")
            return
        if target > today:
            try:
                target = date(today.year - 1, month, day)
            except ValueError:
                await message.reply(_date_hint("!от"), parse_mode="HTML")
                return
    delta = (today - target).days
    if delta < 0:
        await message.reply("Это ещё не наступило 🙂")
        return
    await message.reply(
        f"📅 С {target.day} {_MONTHS[target.month - 1]} {target.year} "
        f"прошло: {delta} {plural(delta, 'день', 'дня', 'дней')}")


@random_router.message(F.text.startswith('!редбулл'))
async def redbull_cmd(message: Message):
    await message.reply("Сердце посадишь, не надо оно тебе")


@random_router.message(F.text.startswith('!ботбрат'))
async def makan_cmd(message: Message):
    photo = _makan_photo()
    if photo is None:
        await message.reply("Фотка Макана потерялась — позовите Егора.")
        return
    if photo.suffix.casefold() == ".webp":
        # sendPhoto не любит webp — отдаём JPEG из памяти.
        buf = io.BytesIO()
        Image.open(photo).convert("RGB").save(buf, format="JPEG", quality=90)
        buf.seek(0)
        await message.answer_photo(
            BufferedInputFile(buf.read(), filename="macan.jpg"),
            caption=MAKAN_CAPTION,
        )
        return
    await message.answer_photo(
        FSInputFile(photo), caption=MAKAN_CAPTION
    )





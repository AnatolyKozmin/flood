import hashlib
import html
import json
import random
from datetime import date
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message

from utils.format import DIVIDER, rating_short


tarot_router = Router()

CMD = "!расклад"

# Колода из 78 карт Таро Уэйта, выгруженная из Таро_Уэйта_78_карт.xlsx.
# Лежит рядом с этим модулем, чтобы во время работы бота не зависеть от Excel.
_CARDS_PATH = Path(__file__).with_name("tarot_cards.json")
with _CARDS_PATH.open(encoding="utf-8") as _f:
    CARDS = json.load(_f)


def _pick_card_for(user_id: int, day: date) -> dict:
    """Карта дня для конкретного человека.

    Выбор детерминированный: один и тот же человек в течение одного дня всегда
    получает одну и ту же карту (можно перепроверять сколько угодно раз), а на
    следующий день сид меняется — и карта выпадает заново. Хранить ничего не нужно.
    """
    seed_src = f"{user_id}:{day.isoformat()}"
    seed = int(hashlib.sha256(seed_src.encode("utf-8")).hexdigest(), 16)
    return random.Random(seed).choice(CARDS)


def _format_card(card: dict, day: date) -> str:
    e = html.escape
    lines = ["🔮 <b>Карта дня</b>", ""]

    # Название + «девиз» карты (Основное значение идёт капсом — делаем курсивом).
    lines.append(f"{card['emoji']} <b>{e(card['name'])}</b>")
    if card["meaning"]:
        lines.append(f"<i>{e(card['meaning'])}</i>")
    lines.append(DIVIDER)

    for label, value in (
        ("✅ В плюсе", card["plus"]),
        ("❌ В минусе", card["minus"]),
        ("💡 Совет", card["advice"]),
    ):
        if value:
            lines.append(f"<b>{label}:</b> {e(value)}")

    # Сферы жизни — компактно, только оценка N/10, без длинных полосок звёзд.
    spheres = [
        ("❤️ Любовь", rating_short(card["love"])),
        ("💰 Финансы", rating_short(card["finance"])),
        ("🌿 Здоровье", rating_short(card["health"])),
        ("🍀 Удача", rating_short(card["luck"])),
    ]
    spheres = [(lbl, val) for lbl, val in spheres if val]
    if spheres:
        lines.append("")
        lines.append("<b>Сферы жизни:</b>")
        lines += [f"{lbl} — {e(val)}" for lbl, val in spheres]

    lines.append("")
    lines.append(f"<i>🗓 Карта на {day.strftime('%d.%m')} — завтра выпадет новая</i>")
    return "\n".join(lines)


@tarot_router.message(F.text == CMD)
async def tarot_cmd(message: Message):
    user = message.from_user
    if user is None:
        return
    today = date.today()
    card = _pick_card_for(user.id, today)
    await message.answer(_format_card(card, today), parse_mode="HTML")

import html
import random

from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Activists
from utils.helpers import mention, format_activist
from utils.format import DIVIDER, field, format_phone


info_router = Router()

CMD = "!инфо"


def _val(value) -> str:
    """Пустые / заглушечные значения ('', '-', 0) показываем прочерком."""
    if value is None:
        return "—"
    text = str(value).strip()
    if text in ("", "-", "0"):
        return "—"
    return text


def _birthday(value) -> str:
    if not value:
        return "—"
    try:
        return value.strftime("%d.%m.%Y")
    except (AttributeError, ValueError):
        return _val(value)


def _matches(activist, needle: str) -> bool:
    """Совпадением считаем:
      • точный @username (в БД он хранится как с '@', так и без);
      • фамилию — первое слово в ФИО ('Фамилия Имя Отчество');
      • полностью введённое ФИО.
    Сравниваем через casefold() в Python: SQLite LOWER() не понимает кириллицу.
    """
    username = (activist.tg_username or "").lstrip("@").strip().casefold()
    if username and username == needle:
        return True
    fio = (activist.fio or "").strip().casefold()
    if not fio:
        return False
    if fio == needle:
        return True
    return fio.split()[0] == needle


async def _find_activists(query: str) -> list:
    """Поиск активистов по тегу ИЛИ по фамилии.

    По фамилии людей может быть несколько — поэтому возвращаем список.
    """
    needle = query.strip().lstrip("@").strip().casefold()
    if not needle:
        return []
    async with async_session_maker() as session:
        result = await session.execute(select(Activists))
        return [a for a in result.scalars().all() if _matches(a, needle)]


def _render_activist(activist) -> str:
    e = html.escape
    fio = e(activist.fio.strip()) if activist.fio else "Активист"
    header = f"👤 <b>{fio}</b>"

    # Пустые поля показываем прочерком — чтобы структура анкеты была видна целиком.
    body = [
        field("День рождения", _birthday(activist.birthday), "🎂", placeholder="—"),
        field("Направление", activist.ik_div, "🧭", placeholder="—"),
        field("Группа", activist.group, "🎓", placeholder="—"),
        field("Номер телефона", format_phone(activist.phone), "📞", code=True, placeholder="—"),
        field("Почта", activist.email, "✉️", placeholder="—"),
        field("Телеграм", mention(activist.tg_username), "✈️", placeholder="—"),
        field("Размер одежды", activist.clothes_size, "👕", placeholder="—"),
        field("Другие подразделения", activist.someone_div, "🏢", placeholder="—"),
    ]
    status = "✅ состоит в активе" if activist.is_active else "🚫 не состоит в активе"

    lines = [header, DIVIDER]
    lines += [b for b in body if b]
    lines += ["", status]
    return "\n".join(lines)


@info_router.message(F.text.startswith(CMD))
async def info_cmd(message: Message):
    query = message.text[len(CMD):].strip()

    # !инфо в ответ на сообщение (без явного тега/фамилии) — берём его автора.
    reply = message.reply_to_message
    if not query and reply and reply.from_user:
        author = reply.from_user
        if author.username:
            query = author.username
        else:
            name = html.escape(author.full_name)
            await message.reply(
                f"У {name} нет @username — по нему не найти. "
                "Попробуй по фамилии: <code>!инфо Фамилия</code>",
                parse_mode="HTML",
            )
            return

    if not query:
        await message.reply(
            "Укажи тег или фамилию активиста, либо ответь <code>!инфо</code> на его сообщение.\n"
            "Например: <code>!инфо @username</code> или <code>!инфо Иванов</code>",
            parse_mode="HTML",
        )
        return

    activists = await _find_activists(query)
    if not activists:
        clean = html.escape(query.lstrip("@"))
        await message.reply(f"Активист «{clean}» не найден в базе (проверь тег или фамилию).")
        return

    if len(activists) > 1:
        # По фамилии нашлось несколько — просим уточнить по тегу.
        e = html.escape
        header = f"🔎 Нашёл несколько активистов ({len(activists)}). Уточни по тегу:"
        listing = "\n".join(f"• {e(format_activist(a))}" for a in activists)
        example = next((a.tg_username for a in activists if a.tg_username), None)
        hint = f"\n\nНапример: <code>!инфо {mention(example)}</code>" if example else ""
        await message.answer(f"{header}\n{listing}{hint}", parse_mode="HTML")
        return

    await message.answer(_render_activist(activists[0]), parse_mode="HTML")

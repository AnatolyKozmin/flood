"""Имена людей: всегда из базы актива, телеграм — только запасной.

Проблема: половина мест брала ФИО только по @тегу, а tg_id игнорировала.
У кого профиль скрыт (тега нет) — такие места показывали голый ник из
телеграма, хотя человек есть в базе. Правило теперь одно:

  1. анкета по tg_id (привязка, id не меняется) → «Фамилия Имя»;
  2. анкета по @тегу (нормализуем обе стороны) → «Фамилия Имя»;
  3. иначе — как звать в телеграме (имя → @тег → id).

Возвращаем сырое имя, экранируют вызывающие (у них parse_mode разный).
"""
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.dao import ActivistsDAO
from database.profile_dao import ProfileDAO
from database.stats_models import StatsUser
from database.profile_models import ActivistLink
from database.contact_models import BotContact
from utils.helpers import first_last

# Подтверждённые старые ники: для импортированных цитат без Telegram-id.
# Значения уже в порядке «Имя Фамилия», без догадок по похожим никам.
_QUOTE_ALIASES = json.loads(
    (Path(__file__).resolve().parent.parent / "assets" / "quote_author_aliases.json").read_text(encoding="utf-8")
)


def _fio(activist) -> str | None:
    if activist is not None and activist.fio:
        name = first_last(activist.fio)
        return name or None
    return None


async def activist_by_tg_id(session: AsyncSession, tg_id: int | None):
    """Анкета по привязке. Самый надёжный путь."""
    if not tg_id:
        return None
    try:
        return await ProfileDAO(session).by_tg_id(int(tg_id))
    except (TypeError, ValueError):
        return None


async def activist_by_username(session: AsyncSession, username: str | None):
    """Анкета по @тегу. Запасной путь."""
    tag = (username or "").strip().lstrip("@")
    if not tag:
        return None
    return await ActivistsDAO(session).get_by_username(tag)


async def fio_name(session: AsyncSession, user_id: int | None,
                   username: str | None) -> str | None:
    """«Фамилия Имя» из базы или None — дальше решает вызывающий."""
    activist = await activist_by_tg_id(session, user_id)
    name = _fio(activist)
    if name:
        return name
    activist = await activist_by_username(session, username)
    return _fio(activist)


async def display_name(session: AsyncSession, user_id: int | None,
                       username: str | None, tg_display: str | None) -> str:
    """Имя для показа: база → телеграм → тег → id. Пустым не бывает."""
    name = await fio_name(session, user_id, username)
    if name:
        return name
    if tg_display and tg_display.strip():
        return tg_display.strip()
    tag = (username or "").strip().lstrip("@")
    if tag:
        return f"@{tag}"
    return f"id{user_id}" if user_id else "боец"


async def quote_name(session: AsyncSession, user_id: int | None, username: str | None) -> str:
    """Живое ФИО и для старых цитат со старым тегом или без привязки анкеты."""
    name = await fio_name(session, user_id, username)
    if name:
        return name
    # У анкеты может не быть activist_links, но статистика знает актуальный
    # тег того же Telegram-id. Старый тег цитаты менять в базе не требуется.
    person = await session.get(StatsUser, user_id) if user_id else None
    tag = (username or "").strip().lstrip("@").lower()
    if person is None and not user_id and tag:
        person = (await session.execute(select(StatsUser).where(
            func.lower(func.trim(func.replace(StatsUser.username, "@", ""))) == tag
        ).limit(1))).scalars().first()
    if person:
        name = await fio_name(session, person.user_id, person.username)
        if name:
            return name
    return _QUOTE_ALIASES.get(tag) or (username or "").strip().lstrip("@") or "чат"


async def quote_user_id(session: AsyncSession, raw_id, username: str | None) -> int | None:
    """Восстановить отсутствующий id только по однозначным известным связкам."""
    try:
        uid = int(raw_id)
        if uid:
            return uid
    except (TypeError, ValueError):
        pass
    tag = (username or "").strip().lstrip("@").lower()
    if not tag:
        return None
    candidates = set()
    for column, tag_column in ((StatsUser.user_id, StatsUser.username),
                               (ActivistLink.tg_id, ActivistLink.username),
                               (BotContact.tg_id, BotContact.username)):
        rows = await session.execute(select(column).where(
            func.lower(func.trim(func.replace(tag_column, "@", ""))) == tag))
        candidates.update(uid for uid in rows.scalars() if uid and uid > 0)
    person = await activist_by_username(session, tag)
    if person:
        rows = await session.execute(select(ActivistLink.tg_id).where(ActivistLink.activist_id == person.id))
        candidates.update(uid for uid in rows.scalars() if uid and uid > 0)
    return next(iter(candidates)) if len(candidates) == 1 else None

"""Имена людей: всегда из базы актива, телеграм — только запасной.

Проблема: половина мест брала ФИО только по @тегу, а tg_id игнорировала.
У кого профиль скрыт (тега нет) — такие места показывали голый ник из
телеграма, хотя человек есть в базе. Правило теперь одно:

  1. анкета по tg_id (привязка, id не меняется) → «Фамилия Имя»;
  2. анкета по @тегу (нормализуем обе стороны) → «Фамилия Имя»;
  3. иначе — как звать в телеграме (имя → @тег → id).

Возвращаем сырое имя, экранируют вызывающие (у них parse_mode разный).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from database.dao import ActivistsDAO
from database.profile_dao import ProfileDAO
from utils.helpers import first_last


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

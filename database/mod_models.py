"""Теневая модерация: мут без шума и отдельный доступ.

Отдельный модуль, существующие таблицы не трогаем. Мут тут — бессрочный
и нигде во флуде не светится (ни сообщений, ни кладбища): действует,
пока не размутят. Таблицы создаются сами через init_db().
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class AdminMute(Base):
    """Замученный теневой модерацией. Ключ — tg_id: мут глобальный,
    действует во всех чатах, где есть бот."""

    __tablename__ = "admin_mutes"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    activist_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    muted_by: Mapped[int] = mapped_column(BigInteger, default=0)
    muted_at: Mapped[datetime] = mapped_column(DateTime)


class ModDeputy(Base):
    """Заместитель теневой модерации. Строка всегда максимум одна:
    владелец + один человек, больше никого."""

    __tablename__ = "mod_deputy"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str] = mapped_column(String, default="")
    granted_by: Mapped[int] = mapped_column(BigInteger, default=0)
    granted_at: Mapped[datetime] = mapped_column(DateTime)

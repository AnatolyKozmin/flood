"""Счётчик затяжек кальяна. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class KalikPuff(Base):
    """Затяжки по чатам: у каждого свой счётчик, сумма чата — перезарядка."""

    __tablename__ = "kalik_puffs"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class KalikCooldown(Base):
    """Перезарядка чата: кальян забит, угли греются до until."""

    __tablename__ = "kalik_cooldown"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)


class KalikBan(Base):
    """Личный бан соло-курильщика: все 50 в одно лицо — ждёт 2 часа."""

    __tablename__ = "kalik_bans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)

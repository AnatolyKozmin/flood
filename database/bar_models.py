"""Бар для !налить. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class BarPour(Base):
    """Выпитое по чатам: у каждого свой счётчик, сумма чата — пустой бар."""

    __tablename__ = "bar_pours"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class BarCooldown(Base):
    """Бар пуст: Егор и Анатолий закупаются до until."""

    __tablename__ = "bar_cooldown"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)


class BarBan(Base):
    """Личный бан соло-пьяницы: весь бар в одно лицо — ждёт 2 часа."""

    __tablename__ = "bar_bans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)

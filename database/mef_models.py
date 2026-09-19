"""Счётчик порошка для !мефчик. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class MefPuff(Base):
    __tablename__ = "mef_puffs"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class MefCooldown(Base):
    __tablename__ = "mef_cooldown"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)


class MefBan(Base):
    __tablename__ = "mef_bans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)

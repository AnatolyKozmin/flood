"""Счётчик косячков для !шмальнуть. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class ShmalPuff(Base):
    __tablename__ = "shmal_puffs"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class ShmalCooldown(Base):
    __tablename__ = "shmal_cooldown"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)


class ShmalBan(Base):
    __tablename__ = "shmal_bans"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime)

"""Метры для !метр. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import date

from sqlalchemy import BigInteger, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class Meter(Base):
    """Размер общий (не по чатам): один человек — один писюн."""

    __tablename__ = "meters"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    last_played: Mapped[date | None] = mapped_column(Date, nullable=True)

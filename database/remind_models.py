"""Напоминалки. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class Remind(Base):
    """Одна напоминалка: кому, что, когда. Срабатывание — воркером."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(String(500), default="")
    fire_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)

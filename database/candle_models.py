"""Онлайн-свечки 4 на 4 для !свечка. Отдельный модуль, чужие таблицы не трогаем."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class CandleLight(Base):
    """Зажжённая свечка. Сетка общая и суточная: день по Москве,
    слоты 0..15. Один человек — одна свечка в день."""

    __tablename__ = "candle_lights"
    __table_args__ = (UniqueConstraint("day", "user_id"),)

    day: Mapped[str] = mapped_column(String, primary_key=True)  # YYYY-MM-DD
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    lit_at: Mapped[datetime] = mapped_column(DateTime)

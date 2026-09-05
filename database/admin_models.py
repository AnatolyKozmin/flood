"""Кто имеет доступ к админ-панели.

Владелец задаётся в .env (OWNER_ID) и в таблице не хранится: иначе получилась
бы курица с яйцом — некому было бы добавить первого админа. Владельца нельзя
разжаловать, остальных он добавляет и убирает командой, без перезапуска бота.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class BotAdmin(Base):
    __tablename__ = "bot_admins"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    added_by: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime)

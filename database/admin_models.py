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


class PendingAdmin(Base):
    """Доступ, выданный по @тегу заранее.

    Bot API не умеет превращать @тег в числовой id — такого метода просто
    нет. А в базе актива лежат только теги. Поэтому человека, который ещё ни
    разу не заходил в бота, выдать админом сразу нельзя: id взять неоткуда.
    Записываем тег сюда, и при первом же его сообщении боту в личку
    middleware превращает запись в настоящего админа.
    """

    __tablename__ = "pending_admins"

    # Тег в нижнем регистре, без '@' — по нему и сверяемся.
    username: Mapped[str] = mapped_column(String, primary_key=True)
    added_by: Mapped[int] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime)

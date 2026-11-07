"""Кто писал боту в личку (/start и любые сообщения). Нужен трекеру: по тегу
найти Telegram id человека, которого нет ни в анкетах, ни в чатах, — чтобы
прислать ему чек-лист или рассылку (писать в личку можно только тем, кто
сам запускал бота)."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class BotContact(Base):
    __tablename__ = "bot_contacts"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

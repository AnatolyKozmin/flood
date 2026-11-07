"""Чек-листы «Найди того, кто…». Строки пишет трекер (checklist_service) при
рассылке листов, бот дописывает file_id присланных фото. Схема та же, что
CREATE TABLE в трекере, — таблицу создаёт тот, кто первым до неё дошёл."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class Checklist(Base):
    __tablename__ = "checklists"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[int] = mapped_column(Integer)
    full_name: Mapped[str] = mapped_column(String)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    facts: Mapped[str] = mapped_column(Text)  # JSON-список из трёх фактов
    photo1: Mapped[str | None] = mapped_column(String, nullable=True)
    photo2: Mapped[str | None] = mapped_column(String, nullable=True)
    photo3: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

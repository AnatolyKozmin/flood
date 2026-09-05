"""Связка «телеграм-аккаунт ↔ анкета активиста».

Отдельная таблица, а не новое поле в Activists: у существующей модели нет
tg_id, а искать человека только по @тегу ненадёжно — тег можно сменить, и
тогда анкета «потеряется». Здесь же держим, кто и когда заполнял.

Таблица создастся сама: init_db() зовёт Base.metadata.create_all(), а этот
модуль импортируется хендлером анкеты ещё до старта бота.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class ActivistLink(Base):
    __tablename__ = "activist_links"

    # tg_id — первичный ключ: один телеграм-аккаунт = одна анкета.
    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    activist_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

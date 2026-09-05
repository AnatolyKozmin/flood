"""Модели для подсчёта сообщений в чате.

Лежат отдельным файлом, а не в database/models.py, чтобы не трогать
существующие модели. Таблицы всё равно создадутся сами: init_db() зовёт
Base.metadata.create_all(), а metadata собирает все классы, которые успели
импортироваться до старта бота (модуль тянется через middleware и хендлер).
"""
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class MessageStat(Base):
    """Счётчик сообщений: одна строка на «чат + человек + день».

    Разбивка по дням нужна, чтобы считать топ не только за всё время, но и
    за сегодня / неделю / месяц. Строк получается мало: активных людей в чате
    десятки, дней в году 365 — база от этого не распухнет.
    """

    __tablename__ = "message_stats"
    __table_args__ = (
        # Уникальность нужна не только для порядка: по ней работает UPSERT
        # (ON CONFLICT DO UPDATE) — иначе счётчик пришлось бы читать перед записью.
        UniqueConstraint("chat_id", "user_id", "day", name="uq_message_stats_chat_user_day"),
        Index("ix_message_stats_chat_day", "chat_id", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    day: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(Integer, default=0)


class StatsUser(Base):
    """Кто есть кто: имя и тег на момент последнего сообщения.

    Считаем по user_id (он не меняется), а ник и имя держим отдельно и
    обновляем при каждом сообщении — люди меняют @тег, и без этого потом
    не сопоставить статистику с анкетой активиста.
    """

    __tablename__ = "stats_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, default="")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
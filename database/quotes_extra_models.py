"""Таблицы для голосования за цитаты и батла цитат.

Отдельный модуль, чтобы не трогать database/models.py. Создаются сами:
init_db() зовёт Base.metadata.create_all(), а сюда мы попадаем через импорт
хендлеров ещё до старта бота.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class QuoteVote(Base):
    """Сердечко под цитатой. Один человек — один голос за цитату.

    Защита от накрутки не в коде, а в схеме: уникальный индекс на пару
    «цитата + человек» физически не даёт вставить второй голос, сколько бы
    раз ни нажали кнопку и какие бы callback'и ни присылали руками.
    """

    __tablename__ = "quote_votes"
    __table_args__ = (
        UniqueConstraint("quote_id", "user_id", name="uq_quote_votes_quote_user"),
        Index("ix_quote_votes_quote", "quote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quote_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(BigInteger)
    voted_at: Mapped[datetime] = mapped_column(DateTime)


class QuoteRating(Base):
    """Рейтинг Эло по итогам батлов — отдельно от сердечек.

    Сердечко значит «нравится», батл — «нравится больше вот этой». Это разные
    метрики, поэтому и храним раздельно, в один рейтинг не мешаем.
    """

    __tablename__ = "quote_ratings"

    quote_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    rating: Mapped[float] = mapped_column(Float, default=1000.0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    battles: Mapped[int] = mapped_column(Integer, default=0)


class BattleSession(Base):
    """Один заход батла: 10 сравнений одного человека.

    Держим в базе, а не в памяти, чтобы батл пережил перезапуск бота —
    иначе на середине сессии кнопки превращаются в тыкву.
    """

    __tablename__ = "battle_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    round: Mapped[int] = mapped_column(Integer, default=0)
    left_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    right_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Пары, которые уже показывали в этой сессии: "3-7,1-9" — чтобы не повторяться.
    seen: Mapped[str] = mapped_column(String, default="")

    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BattleRound(Base):
    """Результат одного сравнения. Нужен для подсчёта победителя сессии."""

    __tablename__ = "battle_rounds"
    __table_args__ = (Index("ix_battle_rounds_session", "session_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer)
    left_id: Mapped[int] = mapped_column(Integer)
    right_id: Mapped[int] = mapped_column(Integer)
    winner_id: Mapped[int] = mapped_column(Integer)
    decided_at: Mapped[datetime] = mapped_column(DateTime)

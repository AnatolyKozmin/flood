"""Дуэли, рулетка, белый флаг: кто мёртв и кто под защитой.

Отдельный модуль, существующие таблицы не трогаем. Создаются сами:
init_db() зовёт Base.metadata.create_all(), а сюда мы попадаем через импорт
хендлера ещё до старта бота — на сервере с живой базой просто добавятся две
пустые таблицы, старые данные не пострадают.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class DeadSoul(Base):
    """Погибший в дуэли или рулетке. Пока запись жива — мидлварь
    (middlewares/dead_mute.py) трёт все его сообщения в этом чате.
    Воскрешение — удаление записи по расписанию (utils/duel_scheduler.py)."""

    __tablename__ = "duel_dead"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_duel_dead_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    dies_at: Mapped[datetime] = mapped_column(DateTime)
    resurrect_at: Mapped[datetime] = mapped_column(DateTime)


class WhiteFlag(Base):
    """Белый флаг: защита от дуэлей. Протухает сам через 3 дня."""

    __tablename__ = "duel_flags"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_duel_flag_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    raised_at: Mapped[datetime] = mapped_column(DateTime)


class DuelStat(Base):
    """Счёт дуэлянта: победы и поражения отдельно по дуэлям и рулетке.

    Победа в дуэли — противник умер, поражение — умер сам. В рулетке
    победа — остался жив, поражение — умер. Прошлое не восстановить:
    мёртвые удаляются при воскрешении, так что счёт идёт с момента
    появления таблицы."""

    __tablename__ = "duel_stats"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_duel_stats_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    duel_wins: Mapped[int] = mapped_column(Integer, default=0)
    duel_losses: Mapped[int] = mapped_column(Integer, default=0)
    roulette_wins: Mapped[int] = mapped_column(Integer, default=0)
    roulette_losses: Mapped[int] = mapped_column(Integer, default=0)

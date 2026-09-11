"""Карма: спасибо ответом — +1 автору сообщения.

Отдельный модуль, таблицы создаются сами через init_db().
Счёт по чатам: в каждом флуде своя карма.
"""
from sqlalchemy import BigInteger, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class Karma(Base):
    __tablename__ = "karma"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_karma_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    points: Mapped[int] = mapped_column(Integer, default=0)

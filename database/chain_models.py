"""Лог сообщений для склейки цепочек /цитата.

Телеграм отдаёт боту только один уровень ответа, глубже в API нет.
Поэтому каждый входящий текст складываем сюда (chat_id, message_id,
reply_to_id) — и цепочку любой глубины гуляем уже по своей базе.
Текст режем до 2000 знаков (для цитаты хватит), старье 30+ дней чистим.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base


class ChainMessage(Base):
    __tablename__ = "chain_messages"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                         autoincrement=False)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                            autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, default=0)
    username: Mapped[str] = mapped_column(String, default="")
    display: Mapped[str] = mapped_column(String, default="")
    text: Mapped[str] = mapped_column(String, default="")
    reply_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

"""Дополнительные таблицы цитат: старые quotes и голоса не меняются."""
import time

from sqlalchemy import JSON, BigInteger, Boolean, Integer, String, select, delete, and_, or_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base

MAX_PARTS = 100
MAX_TEXT = 32000


class QuoteMessage(Base):
    __tablename__ = "quote_messages"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ancestry_known: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSON)
    seen_at: Mapped[int] = mapped_column(BigInteger, index=True)


class QuoteContent(Base):
    __tablename__ = "quote_contents"
    quote_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parts: Mapped[list] = mapped_column(JSON)
    outputs: Mapped[list] = mapped_column(JSON, default=list)
    render_key: Mapped[str] = mapped_column(String, default="")


class QuoteJob(Base):
    __tablename__ = "quote_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quote_id: Mapped[int] = mapped_column(Integer, unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    source_key: Mapped[str] = mapped_column(String, unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(String, default="pending")
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class QuoteDraft(Base):
    __tablename__ = "quote_drafts"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parts: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class QuotePending(Base):
    __tablename__ = "quote_pending"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    parts: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[int] = mapped_column(BigInteger)


def message_part(message) -> dict:
    origin = message.forward_origin
    author = message.from_user
    uid, tag = str(author.id) if author else "", (author.username or author.full_name) if author else "чат"
    if origin:
        if origin.type == "user":
            author = origin.sender_user
            uid, tag = str(author.id), author.username or author.full_name
        elif origin.type == "hidden_user":
            uid, tag = "", origin.sender_user_name
        elif origin.type in {"chat", "channel"}:
            chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
            uid, tag = (str(chat.id), chat.title or chat.username or "канал") if chat else ("", "канал")
    elif message.sender_chat:
        uid, tag = str(message.sender_chat.id), message.sender_chat.title or "чат"
    kind, media = "text", None
    for key in ("animation", "video_note", "video", "photo", "voice"):
        value = getattr(message, key, None)
        if value:
            kind, media = key, value[-1] if key == "photo" else value
            break
    supported = bool(message.text or media)
    return dict(chat_id=message.chat.id, message_id=message.message_id,
                tg_id=uid, tg_username=tag, text=message.text or message.caption or "",
                kind=kind if supported else "unsupported", file_id=media.file_id if media else None,
                file_unique_id=media.file_unique_id if media else None,
                file_size=getattr(media, "file_size", None), duration=getattr(media, "duration", None),
                thumbnail_id=(message.photo[0].file_id if kind == "photo" else
                              getattr(getattr(media, "thumbnail", None), "file_id", None)),
                media_group_id=message.media_group_id,
                protected=bool(message.has_protected_content), date=message.date.isoformat())


async def remember_message(session, message, *, complete=True):
    """Вложенный reply не содержит своего родителя: не затирать известную связь."""
    if message.reply_to_message:
        await remember_message(session, message.reply_to_message, complete=False)
    values = dict(chat_id=message.chat.id, message_id=message.message_id,
                  parent_id=message.reply_to_message.message_id if message.reply_to_message else None,
                  ancestry_known=complete, payload=message_part(message), seen_at=int(time.time()))
    stmt = insert(QuoteMessage).values(**values)
    updates = {k: v for k, v in values.items() if k not in {"chat_id", "message_id"}}
    if not complete:
        # Существующий полный update важнее усечённого вложенного reply.
        await session.execute(stmt.on_conflict_do_nothing())
    else:
        await session.execute(stmt.on_conflict_do_update(index_elements=["chat_id", "message_id"], set_=updates))


async def collect_chain(session, chat_id, message_id):
    parts, seen = [], set()
    while message_id is not None:
        if message_id in seen or len(parts) >= MAX_PARTS:
            return list(reversed(parts)), False
        seen.add(message_id)
        row = await session.get(QuoteMessage, (chat_id, message_id))
        if row is None:
            return list(reversed(parts)), False
        parts.append(row.payload)
        if not row.ancestry_known:
            return list(reversed(parts)), False
        message_id = row.parent_id
    return list(reversed(parts)), True


def validate_parts(parts):
    if not parts or len(parts) > MAX_PARTS:
        raise ValueError(f"В цитате должно быть от 1 до {MAX_PARTS} сообщений.")
    if sum(len(p["text"]) for p in parts) > MAX_TEXT:
        raise ValueError("Слишком много текста: максимум 32 000 символов.")
    for part in parts:
        if part.get("protected"):
            raise ValueError("Это сообщение защищено от сохранения и пересылки.")
        if part["kind"] not in {"text", "photo", "video", "video_note", "animation"}:
            raise ValueError("В цепочке неподдерживаемое вложение. Поддерживаются текст, фото, видео, GIF и кружки.")
        if (part.get("file_size") or 0) > 20 * 1024 * 1024:
            raise ValueError("Вложение больше 20 МБ. Пришли файл поменьше.")
        if (part.get("duration") or 0) > 60:
            raise ValueError("Видео длиннее 60 секунд. Пришли фрагмент покороче.")


async def prune_history(session):
    # Сохранённые цитаты содержат собственный снимок частей и не зависят от журнала.
    cutoff = int(time.time()) - 90 * 86400
    await session.execute(delete(QuoteMessage).where(or_(
        QuoteMessage.seen_at < cutoff,
        and_(QuoteMessage.chat_id > 0, QuoteMessage.seen_at < int(time.time()) - 86400),
    )))
    await session.execute(delete(QuoteDraft).where(QuoteDraft.updated_at < int(time.time()) - 86400))
    await session.execute(delete(QuotePending).where(QuotePending.created_at < int(time.time()) - 86400))

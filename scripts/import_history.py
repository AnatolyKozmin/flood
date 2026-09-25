"""Импорт истории чата из выгрузки Telegram Desktop в статистику и лог цепочек.

Зачем: Bot API не отдаёт старые сообщения, поэтому счётчики (!топ) и
цепочки (/цитата) знают только то, что пришло при работающем боте.
Выгрузка закрывает прошлое разом.

Как выгрузить: Telegram Desktop → нужный чат → ⋮ → Export chat history →
только JSON (медиа не надо) → получится result.json.

Запуск на сервере из корня проекта:
    python3 scripts/import_history.py /tmp/result.json [--chat-id -100123]

chat_id подставится сам для супергрупп/каналов (-100 + id из выгрузки),
для обычных групп укажи вручную. Узнать текущий id чата бота:
    sqlite3 database/bot.db "SELECT DISTINCT chat_id FROM message_stats;"
"""
import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.chain_dao import TEXT_MAX, ChainDAO
from database.engine import async_session_maker, init_db
from database.stats_dao import StatsDAO

CHUNK = 2000


def _text_of(raw) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in raw)
    return ""


def _user_id(from_id) -> int | None:
    m = re.fullmatch(r"user(\d+)", str(from_id or ""))
    return int(m.group(1)) if m else None


def _parse_day(value: str):
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json", help="result.json из выгрузки Telegram Desktop")
    ap.add_argument("--chat-id", type=int, default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.export_json).read_text(encoding="utf-8"))
    chat_id = args.chat_id
    if chat_id is None:
        # У супергрупп/каналов в выгрузке голый id, в Bot API — с -100 впереди.
        raw_id = abs(int(data.get("id", 0)))
        chat_id = int(f"-100{raw_id}") if raw_id else 0
    print(f"chat_id={chat_id}, сообщений в выгрузке: {len(data.get('messages', []))}")

    counts: dict[tuple[int, int, object], int] = defaultdict(int)
    users: dict[int, tuple] = {}
    chain: list[dict] = []
    skipped = 0
    for msg in data.get("messages", []):
        if not isinstance(msg, dict) or msg.get("type") != "message":
            skipped += 1
            continue
        uid = _user_id(msg.get("from_id"))
        if uid is None:
            skipped += 1
            continue
        day = _parse_day(msg.get("date", ""))
        if day is None:
            skipped += 1
            continue
        text = _text_of(msg.get("text", "")).strip()[:TEXT_MAX]
        seen = datetime.now().replace(tzinfo=None)
        try:
            seen = datetime.fromisoformat(msg["date"]).replace(tzinfo=None)
        except (KeyError, TypeError, ValueError):
            pass
        counts[(chat_id, uid, day)] += 1
        users[uid] = (None, str(msg.get("from", "")) or f"id{uid}", seen)
        reply = msg.get("reply_to_message_id")
        chain.append({
            "chat_id": chat_id,
            "message_id": int(msg.get("id", 0)),
            "user_id": uid,
            "username": "",
            "display": str(msg.get("from", "")),
            "text": text,
            "reply_to_id": int(reply) if isinstance(reply, int) else None,
            "created_at": seen,
        })

    await init_db()
    total_counts = 0
    items = list(counts.items())
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        for i in range(0, len(items), CHUNK):
            chunk = dict(items[i:i + CHUNK])
            sub_users = {uid: users[uid] for (_, uid, _) in chunk if uid in users}
            await dao.bump(chunk, sub_users)
            total_counts += sum(chunk.values())
    async with async_session_maker() as session:
        dao = ChainDAO(session)
        for i in range(0, len(chain), CHUNK):
            await dao.bump(chain[i:i + CHUNK])
    print(f"готово: счётчиков {total_counts}, юзеров {len(users)}, "
          f"строк цепочек {len(chain)}, пропущено {skipped}")


if __name__ == "__main__":
    asyncio.run(main())

"""Пересборка message_stats строго по выгрузке Telegram Desktop.

Зачем: повторные прогоны импорта и наложение лайва на импорт дают
задвоение (upsert в bump прибавляет). Здесь без угадайки: всё, что
не новее выгрузки, заменяется её посчитанными данными один в один.
Живые строки новее выгрузки не трогаем.

Запуск в контейнере бота из корня проекта:
    docker compose exec bot python scripts/rebuild_stats.py /app/database/result.json
"""
import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select
from database.engine import async_session_maker, init_db
from database.stats_dao import StatsDAO
from database.stats_models import MessageStat


def _parse_day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(value).date()
    except (TypeError, ValueError):
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_json")
    ap.add_argument("--chat-id", type=int, default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.export_json).read_text(encoding="utf-8"))
    raw_id = abs(int(data.get("id", 0)))
    chat_id = args.chat_id if args.chat_id is not None else (
        int(f"-100{raw_id}") if raw_id else 0)

    counts: dict[tuple[int, int, date], int] = defaultdict(int)
    max_day: date | None = None
    total = 0
    for msg in data.get("messages", []):
        if not isinstance(msg, dict) or msg.get("type") != "message":
            continue
        import re
        m = re.fullmatch(r"user(\d+)", str(msg.get("from_id") or ""))
        if m is None:
            continue
        day = _parse_day(msg.get("date", ""))
        if day is None:
            continue
        counts[(chat_id, int(m.group(1)), day)] += 1
        total += 1
        if max_day is None or day > max_day:
            max_day = day
    print(f"в выгрузке: {total} сообщений, {len(counts)} строк, max_day={max_day}")
    if not counts or max_day is None:
        print("пусто — ничего не делаю")
        return

    await init_db()
    async with async_session_maker() as session:
        dao = StatsDAO(session)
        # Сносим всё не новее выгрузки (там и дубли, и наложения)…
        gone = (await session.execute(
            select(MessageStat.chat_id, MessageStat.user_id, MessageStat.day)
            .where(MessageStat.chat_id == chat_id, MessageStat.day <= max_day)
        )).all()
        await session.execute(
            delete(MessageStat).where(
                MessageStat.chat_id == chat_id, MessageStat.day <= max_day)
        )
        # …и кладём выгрузку один в один.
        await dao.bump(dict(counts), {})
        kept = (await session.execute(
            select(MessageStat.chat_id).where(
                MessageStat.chat_id == chat_id, MessageStat.day > max_day)
        )).all()
        print(f"удалено строк: {len(gone)}, живых новее выгрузки оставлено: {len(kept)}")
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())

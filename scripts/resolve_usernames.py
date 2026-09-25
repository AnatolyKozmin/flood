"""Добить @теги юзерам без них: Bot API отдаёт username по user_id.

Зачем: после импорта истории часть людей висит без тегов (только
контактные имена), и топы не маппятся на официальные ФИО. Живой трафик
чинит это сам, но скрипт закрывает всех разом.

Запуск в контейнере бота (там токен и зависимости):
    docker compose exec bot python scripts/resolve_usernames.py [--limit 200]

На каждого без тега — один getChat, ошибки (удалённые, скрытые, флудвейт)
пропускаем молча. Ничего не затираем: пишем только пустые поля.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from sqlalchemy import select

from database.engine import async_session_maker
from database.stats_models import StatsUser

logger = logging.getLogger(__name__)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    token = os.getenv("TOKEN", "").strip()
    if not token:
        print("Нет TOKEN в окружении — запускай через docker compose exec bot")
        return

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(StatsUser).where(StatsUser.username.is_(None))
            .limit(args.limit)
        )).scalars().all()
        users = [(r.user_id, r.full_name) for r in rows]
    print(f"без тега: {len(users)}")
    if not users:
        return

    bot = Bot(token=token)
    fixed, skipped = [], []
    try:
        for uid, old_name in users:
            try:
                chat = await bot.get_chat(uid)
            except Exception:
                # Удалённые, скрытые, сеть легла — пропускаем, не роняем прогон.
                skipped.append((uid, old_name))
                continue
            tag = (getattr(chat, "username", None) or "").lstrip("@")
            name = f"{getattr(chat, 'first_name', '') or ''} " \
                   f"{getattr(chat, 'last_name', '') or ''}".strip()
            if not tag:
                skipped.append((uid, old_name))
                continue
            async with async_session_maker() as session:
                row = await session.get(StatsUser, uid)
                if row is not None and not row.username:
                    row.username = tag
                    if name:
                        row.full_name = name
                    await session.commit()
                    fixed.append((uid, tag))
            await asyncio.sleep(0.2)
    finally:
        await bot.session.close()
    print(f"тегов поставлено: {len(fixed)}")
    for uid, tag in fixed:
        print(f"  {uid} -> @{tag}")
    print(f"пропущено: {len(skipped)}")
    for uid, name in skipped:
        print(f"  {uid} | {name}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

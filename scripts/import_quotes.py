"""Ручной импорт цитат на позицию 12 (сразу после «сантехника дня»).

Новые цитаты занимают id 12, 13, ... — старые (и все ссылки на них: голоса,
рейтинги, батлы) сдвигаются вниз в ТОЙ ЖЕ транзакции, так что ничего
не разъезжается. Повторный запуск безопасен: дубликаты по тексту пропускаются.

Формат батч-файла: TSV, одна цитата на строку — `автор<TAB>текст`.
Автор — @тег или ФИО (ФИО резолвится в тег через базу актива).

Запуск на сервере (там живая база):
    cd /root/flood && cp database/bot.db database/bot.db.bak_$(date +%F_%H%M) \\
        && docker compose exec bot python scripts/import_quotes.py scripts/quotes_batch_01.txt
"""
import asyncio
import sys

from sqlalchemy import func, select, text as sql

from database.engine import async_session_maker, init_db
from database.models import Activists, Quotes
from database.stats_dao import StatsDAO

AT = 12          # вставляем начиная с этого id
OFF = 100_000    # временный зазор для сдвига без коллизий UNIQUE/PK

REFS = [
    ("quote_votes", "quote_id"),
    ("quote_ratings", "quote_id"),
    ("battle_rounds", "left_id"),
    ("battle_rounds", "right_id"),
    ("battle_rounds", "winner_id"),
    ("battle_sessions", "left_id"),
    ("battle_sessions", "right_id"),
]


def clean_text(raw: str) -> str:
    """Текст как в !цитата: без типографских кавычек по краям, пробелы в норму."""
    text = " ".join(raw.split()).strip()
    while len(text) >= 2 and text[0] in "«‹\"'„" and text[-1] in "»›\"'“":
        text = text[1:-1].strip()
        text = " ".join(text.split()).strip()
    return text


async def resolve_author(session, author: str) -> tuple[str, str]:
    """(tg_username без @, tg_id). Тег — напрямую, ФИО — через базу актива,
    tg_id — через статистику писавших. Не нашлось — как есть и пустой id."""
    needle = author.strip().lstrip("@")
    if not needle:
        return "", ""

    stats = await StatsDAO(session).user_by_username(needle)
    if stats is not None:
        return (stats.username or needle).lstrip("@"), str(stats.user_id)

    key = needle.casefold()
    rows = (await session.execute(select(Activists))).scalars().all()
    words = key.split()
    for activist in rows:
        fio = (activist.fio or "").strip()
        if not fio:
            continue
        fio_words = set(fio.casefold().split())
        # «Анатолий Козьмин» vs «Козьмин Анатолий Владиславович»:
        # порядок слов разный, поэтому проверяем вхождением всех слов.
        if (len(words) >= 2 and set(words) <= fio_words) or fio.split()[0].casefold() == key:
            tag = (activist.tg_username or "").strip().lstrip("@")
            if not tag:
                continue
            found = await StatsDAO(session).user_by_username(tag)
            tg_id = str(found.user_id) if found is not None else ""
            return tag, tg_id

    return needle, ""


async def import_batch(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        lines = [ln.rstrip("\n") for ln in f if ln.strip()]

    await init_db()
    async with async_session_maker() as session:
        existing = {
            clean_text(row[0]).casefold()
            for row in (await session.execute(
                select(Quotes.text_of_quotes)
            )).all()
        }

        fresh: list[tuple[str, str, str]] = []
        for lineno, line in enumerate(lines, start=1):
            if "\t" not in line:
                print(f"⚠️ строка {lineno}: нет табуляции, пропускаю")
                continue
            author, _, raw_text = line.partition("\t")
            text = clean_text(raw_text)
            if not text:
                print(f"⚠️ строка {lineno}: пустой текст, пропускаю")
                continue
            if text.casefold() in existing:
                print(f"⏭ строка {lineno}: дубликат, пропускаю")
                continue
            username, tg_id = await resolve_author(session, author)
            fresh.append((username, tg_id, text))
            existing.add(text.casefold())

        if not fresh:
            print("Нового нет — ничего не делаю.")
            return

        n = len(fresh)
        params = {"at": AT, "off": OFF, "n": n}

        # 1. Всё, что от AT и дальше, — во временный диапазон.
        await session.execute(
            sql("UPDATE quotes SET id = id + :off WHERE id >= :at"), params
        )
        for table, column in REFS:
            await session.execute(
                sql(f"UPDATE {table} SET {column} = {column} + :off "
                    f"WHERE {column} >= :at"),
                params,
            )

        # 2. Новые цитаты на освободившиеся id.
        for i, (username, tg_id, text) in enumerate(fresh):
            session.add(Quotes(
                id=AT + i, tg_id=tg_id, tg_username=username,
                text_of_quotes=text,
            ))
        await session.flush()

        # 3. Временный диапазон — на место со сдвигом +n.
        await session.execute(
            sql("UPDATE quotes SET id = id - :off + :n "
                "WHERE id >= :at + :off"),
            params,
        )
        for table, column in REFS:
            await session.execute(
                sql(f"UPDATE {table} SET {column} = {column} - :off + :n "
                    f"WHERE {column} >= :at + :off"),
                params,
            )

        await session.commit()

        print(f"✅ Вставил {n} на id {AT}–{AT + n - 1}, "
              f"старые уехали на +{n}. Голоса и батлы пересчитаны.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python scripts/import_quotes.py scripts/quotes_batch_01.txt")
        raise SystemExit(1)
    asyncio.run(import_batch(sys.argv[1]))

from datetime import datetime, timezone, timedelta

# Москва — фиксированный UTC+3 (перехода на летнее время нет с 2014 г.).
# Через смещение, а не ZoneInfo, чтобы не зависеть от tzdata в slim-образе.
MSK = timezone(timedelta(hours=3))


def moscow_today():
    """Текущая дата по Москве. Нужна, чтобы «день» (сантехник, расклад)
    сбрасывался в 00:00 МСК, а не в 00:00 по времени сервера (UTC)."""
    return datetime.now(MSK).date()


def msk_now():
    """Текущее время по Москве, наивное — в таком виде лежат DateTime
    в базе (дуэли, дни рождения)."""
    return datetime.now(MSK).replace(tzinfo=None)


def first_last(fio: str) -> str:
    """'Фамилия Имя Отчество' → 'Имя Фамилия'"""
    parts = fio.strip().split()
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}"
    return fio


def mention(tg_username: str | None) -> str:
    if tg_username and tg_username.strip():
        return f"@{tg_username.strip().lstrip('@')}"
    return ""


def format_activist(activist) -> str:
    name = first_last(activist.fio)
    tag = mention(activist.tg_username)
    return f"{name} ({tag})" if tag else name

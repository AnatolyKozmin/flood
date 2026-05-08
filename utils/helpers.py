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

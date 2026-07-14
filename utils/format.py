"""Общие хелперы оформления текста для сообщений бота.

Чтобы все команды выглядели в одном стиле: единый разделитель, единая
логика «поле: значение» с пропуском пустых значений и т.п.
Разметка везде — HTML (parse_mode="HTML").
"""
import html
import re

DIVIDER = "━━━━━━━━━━━━━━"

_EMPTY = {"", "-", "—", "0", "none", "нет"}


def is_empty(value) -> bool:
    """Пустые / заглушечные значения, которые не стоит показывать."""
    if value is None:
        return True
    return str(value).strip().casefold() in _EMPTY


def field(label: str, value, emoji: str = "", code: bool = False,
          placeholder: str | None = None) -> str | None:
    """Строка вида '🎂 <b>Метка:</b> значение'.

    Если значения нет: вернём строку с прочерком (при заданном placeholder,
    например '—') либо None, чтобы поле можно было вовсе не показывать.
    """
    if is_empty(value):
        if placeholder is None:
            return None
        text = html.escape(placeholder)
    else:
        text = html.escape(str(value).strip())
        if code:
            text = f"<code>{text}</code>"
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}<b>{label}:</b> {text}"


def format_phone(value) -> str:
    """Российский номер к виду +7(953)-458-01-25.

    Понимает 8XXXXXXXXXX, 7XXXXXXXXXX, +7…, а также запись с пробелами,
    скобками и дефисами. Если номер не похож на российский (не 11 цифр после
    нормализации) — возвращаем как есть, чтобы ничего не терять.
    """
    if is_empty(value):
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 10:                 # без кода страны
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) != 11 or digits[0] != "7":
        return str(value).strip()
    d = digits
    return f"+7({d[1:4]})-{d[4:7]}-{d[7:9]}-{d[9:11]}"


def rating_short(value) -> str | None:
    """Из '★★★★★★★★☆☆  8/10' достаёт '8/10'. Если не вышло — исходная строка."""
    if is_empty(value):
        return None
    m = re.search(r"\d+\s*/\s*10", str(value))
    return m.group(0).replace(" ", "") if m else str(value).strip()

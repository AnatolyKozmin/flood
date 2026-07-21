import asyncio
import openpyxl
from datetime import datetime

from sqlalchemy import delete

from database.engine import async_session_maker, init_db
from database.dao import ActivistsDAO
from database.models import Activists


def _clean_str(value) -> str:
    """Строка без мусора. Числа из Excel приходят как float (89963479923.0) —
    убираем хвост '.0', чтобы телефон/студак не портились."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _to_int(value) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _to_birthday(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.strptime(value.strip(), "%d.%m.%Y")
        except ValueError:
            return None
    return None


async def import_from_excel(excel_path: str):
    """Заливает активистов из Excel в БД, ЗАМЕНЯЯ прежний состав.

    Структура файла active_summer_2026.xlsx (по индексам столбцов):
      0 Фио · 1 Почта · 2 Студенческий · 3 Группа · 4 Телефон · 5 Телеграм ·
      6 Размер · 7 Другие_подразделения · 8 День_рождения · 9 Ссылка_в_вк ·
      10 Направление
    Колонки «В_активе» в этом файле нет — считаем всех активными.
    """
    await init_db()

    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook.active

    async with async_session_maker() as session:
        dao = ActivistsDAO(session)

        # Замена состава: чистим старых активистов. Цитаты (таблица quotes) не трогаем.
        await session.execute(delete(Activists))
        await session.commit()

        imported = 0
        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            # Пропускаем пустые строки (в файле есть пустые хвосты) и строки без ФИО.
            if not row or not row[0]:
                continue
            try:
                activist_data = {
                    "fio": _clean_str(row[0]),
                    "email": _clean_str(row[1]),
                    "studak": _to_int(row[2]),
                    "group": _clean_str(row[3]),
                    "phone": _clean_str(row[4]),
                    "tg_username": _clean_str(row[5]),
                    "clothes_size": _clean_str(row[6]),   # 'S/M', 'M' и т.п. — как есть
                    "someone_div": _clean_str(row[7]),
                    "birthday": _to_birthday(row[8]),
                    "ik_div": _clean_str(row[10]),        # Направление
                    "is_active": True,                    # колонки «В_активе» в файле нет
                }
                await dao.create(**activist_data)
                imported += 1
                print(f"✅ Строка {row_idx}: {activist_data['fio']} импортирована")
            except Exception as e:
                print(f"❌ Ошибка в строке {row_idx}: {e}")

    print(f"\n🎉 Импорт завершён! Загружено активистов: {imported}")


if __name__ == "__main__":
    excel_file = "active_summer_2026.xlsx"
    asyncio.run(import_from_excel(excel_file))

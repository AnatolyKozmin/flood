import asyncio
import openpyxl
from datetime import datetime
from database.engine import async_session_maker, init_db
from database.dao import ActivistsDAO


async def import_from_excel(excel_path: str):
    """Импортирует данные из Excel в базу данных"""
    
    # Инициализируем БД
    await init_db()
    
    # Загружаем файл Excel
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook.active
    
    # Маппинг столбцов по твоему Excel (столбик A и дальше)
    # Заголовки: Фио, Почта, Студенческий билет, Группа, Телефон, Телеграм, Размер, Другие_подразделения, День_рождения, Ссылка_в_вк, В_активе, Направление
    columns = [
        'fio',              # A - Фио
        'email',            # B - Почта
        'studak',           # C - Студенческий билет
        'group',            # D - Группа
        'phone',            # E - Телефон
        'tg_username',      # F - Телеграм
        'clothes_size',     # G - Размер
        'someone_div',      # H - Другие_подразделения
        'birthday',         # I - День_рождения
        'ik_div',           # J - Ссылка_в_вк (переделаю в направление)
        'is_active',        # K - В_активе
        'ik_div'            # L - Направление (настоящее направление)
    ]
    
    async with async_session_maker() as session:
        dao = ActivistsDAO(session)
        
        # Начинаем со второй строки (первая - заголовок)
        for row_idx, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            try:
                # Проверяем, не пустая ли строка
                if not any(row):
                    continue
                
                # Создаём словарь для активиста
                activist_data = {}
                
                for col_idx, col_name in enumerate(columns):
                    value = row[col_idx]
                    
                    if value is None:
                        value = ""
                    
                    # Преобразуем типы данных
                    if col_name == 'studak':
                        try:
                            activist_data[col_name] = int(value) if value else 0
                        except (ValueError, TypeError):
                            activist_data[col_name] = 0
                    elif col_name == 'clothes_size':
                        try:
                            activist_data[col_name] = int(value) if value else 0
                        except (ValueError, TypeError):
                            activist_data[col_name] = 0
                    elif col_name == 'birthday':
                        if isinstance(value, str) and value.strip():
                            try:
                                activist_data[col_name] = datetime.strptime(value, '%d.%m.%Y').date()
                            except:
                                activist_data[col_name] = None
                        elif isinstance(value, datetime):
                            activist_data[col_name] = value.date()
                        else:
                            activist_data[col_name] = None
                    elif col_name == 'is_active':
                        activist_data[col_name] = bool(value) if value else False
                    else:
                        activist_data[col_name] = str(value).strip()
                
                # Создаём запись в БД
                await dao.create(**activist_data)
                print(f"✅ Строка {row_idx}: {activist_data.get('fio')} импортирована")
            
            except Exception as e:
                print(f"❌ Ошибка в строке {row_idx}: {e}")
    
    print(f"\n🎉 Импорт завершён!")


if __name__ == "__main__":
    # Укажи путь к файлу Excel
    excel_file = "activ.xlsx"  # Измени на свой путь
    
    asyncio.run(import_from_excel(excel_file))

# ══════════════════════════════════════════════════════════════════════════
# ПОЗДРАВЛЕНИЯ С ДНЁМ РОЖДЕНИЯ — код готов и проверен, но ВЫКЛЮЧЕН.
#
# Что делает: в 00:00 по Москве бот пишет во флуд и тегает именинников из
# базы актива. Если бот лежал в полночь — поздравит сразу после старта,
# чтобы человек не остался без поздравления.
#
# Что проверено до того, как это закомментировали:
#   • обычный день рождения ловится, чужой — нет;
#   • 29 февраля в невисокосный год поздравляем 28-го;
#   • возраст считается верно (в том числе накануне дня рождения);
#   • сон до полуночи считается по МСК, а не по времени сервера;
#   • повторный запуск в тот же день дублей НЕ шлёт;
#   • все шесть шаблонов склеиваются без лишних пробелов, с возрастом и без.
#
# КАК ВКЛЮЧИТЬ
#   1. Снять комментарии со всего, что ниже: выделить и Cmd+/ (PyCharm,
#      VS Code) — блок оформлен так, чтобы это сработало одним движением.
#   2. Узнать id чата флуда. Он уже есть в базе, если бот там поработал:
#         sqlite3 database/bot.db "SELECT DISTINCT chat_id FROM message_stats;"
#      У супергрупп число отрицательное — так и надо.
#   3. Положить его в .env:   FLOOD_CHAT_ID=-1001234567890
#   4. В main.py раскомментировать три строки, помеченные «ДР».
#   5. Перезапустить бота. Таблица birthday_greetings создастся сама.
#
# ВАЖНО ПРО ТЕГИ: надёжнее всего пингуется tg://user?id, а tg_id берётся из
# activist_links — он появляется у тех, кто заполнил анкету в личке. У кого
# анкеты не было, идёт @тег из Excel; у кого нет и его — просто имя без пинга.
# То есть чем больше народу пройдёт !обо мне, тем лучше работают поздравления.
# ══════════════════════════════════════════════════════════════════════════
#
# """Поздравления с днём рождения во флуде.
#
# Фоновая задача просыпается в 00:00 по Москве, забирает именинников из базы
# актива и пишет в чат с тегом. Ничего не ждёт от пользователя — работает сама.
# """
# import asyncio
# import html
# import logging
# import os
# import random
# from datetime import date, datetime, time, timedelta
#
# from aiogram import Bot
# from sqlalchemy import Integer, DateTime, UniqueConstraint, select
# from sqlalchemy.orm import Mapped, mapped_column
#
# from database.engine import async_session_maker
# from database.models import Activists, Base
# from database.profile_models import ActivistLink
# from utils.helpers import MSK, first_last
# from utils.stats import plural
#
# logger = logging.getLogger(__name__)
#
#
# class BirthdayGreeting(Base):
#     """Кого и в каком году уже поздравили.
#
#     Уникальность «активист + год» — защита от повторов: бот могли
#     перезапустить в 00:00:30, и без этой записи он поздравил бы второй раз.
#     """
#
#     __tablename__ = "birthday_greetings"
#     __table_args__ = (
#         UniqueConstraint("activist_id", "year", name="uq_birthday_activist_year"),
#     )
#
#     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
#     activist_id: Mapped[int] = mapped_column(Integer)
#     year: Mapped[int] = mapped_column(Integer)
#     sent_at: Mapped[datetime] = mapped_column(DateTime)
#
#
# GREETINGS = (
#     "🎉 {who}, с днём рождения!{age}",
#     "🥳 Сегодня праздник у {who} — с днём рождения!{age}",
#     "🎂 {who}, поздравляем!{age} Пусть год будет твоим.",
#     "✨ С днём рождения, {who}!{age}",
#     "🎈 {who}, с днюхой!{age} Всего самого доброго.",
#     "🍰 Поздравляем {who} с днём рождения!{age}",
# )
#
#
# def _leap(year: int) -> bool:
#     return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
#
#
# def is_birthday(birthday, today: date) -> bool:
#     """29 февраля в невисокосный год поздравляем 28-го — иначе человек
#     остался бы без поздравления на три года из четырёх."""
#     if not birthday:
#         return False
#     if birthday.month == 2 and birthday.day == 29 and not _leap(today.year):
#         return today.month == 2 and today.day == 28
#     return (birthday.month, birthday.day) == (today.month, today.day)
#
#
# def age_of(birthday, today: date) -> int | None:
#     if not birthday or birthday.year < 1900:
#         return None
#     years = today.year - birthday.year
#     if (today.month, today.day) < (birthday.month, birthday.day):
#         years -= 1
#     return years if 0 < years < 120 else None
#
#
# def seconds_to_midnight(now: datetime | None = None) -> float:
#     """Сколько спать до ближайшей полуночи по Москве.
#
#     Именно по МСК, а не по времени сервера: на UTC-машине «полночь»
#     наступила бы в три часа ночи по Москве.
#     """
#     now = now or datetime.now(MSK).replace(tzinfo=None)
#     midnight = datetime.combine(now.date() + timedelta(days=1), time.min)
#     return max(1.0, (midnight - now).total_seconds())
#
#
# async def _mention(session, activist) -> str:
#     """Тег для поздравления.
#
#     Сначала пробуем tg://user?id — он пингует даже тех, у кого нет @тега,
#     а tg_id появляется у всех, кто заполнил анкету в личке. Если анкеты не
#     было — падаем на @тег из Excel, а совсем без тега просто зовём по имени.
#     """
#     name = html.escape(first_last(activist.fio) if activist.fio else "Активист")
#     link = (await session.execute(
#         select(ActivistLink).where(ActivistLink.activist_id == activist.id)
#     )).scalars().first()
#     if link is not None:
#         return f'<a href="tg://user?id={link.tg_id}">{name}</a>'
#     tag = (activist.tg_username or "").strip().lstrip("@")
#     return f"@{tag}" if tag else name
#
#
# def _text(who: str, age: int | None) -> str:
#     age_part = f" Сегодня {age} {plural(age, 'год', 'года', 'лет')}!" if age else ""
#     return random.choice(GREETINGS).format(who=who, age=age_part)
#
#
# async def greet_pending(bot: Bot, chat_id: int) -> int:
#     """Поздравить всех сегодняшних именинников, кого ещё не поздравляли.
#
#     Зовётся и по расписанию, и при старте бота: если бот лежал в полночь,
#     именинник иначе остался бы без поздравления.
#     """
#     today = datetime.now(MSK).date()
#     sent = 0
#     async with async_session_maker() as session:
#         # Активистов десятки — проще отфильтровать в Python, чем городить
#         # SQL по дню и месяцу с оглядкой на 29 февраля.
#         everyone = (await session.execute(select(Activists))).scalars().all()
#         birthday_people = [a for a in everyone if is_birthday(a.birthday, today)]
#         if not birthday_people:
#             return 0
#
#         done = set((await session.execute(
#             select(BirthdayGreeting.activist_id).where(
#                 BirthdayGreeting.year == today.year,
#                 BirthdayGreeting.activist_id.in_([a.id for a in birthday_people]),
#             )
#         )).scalars().all())
#
#         for activist in birthday_people:
#             if activist.id in done:
#                 continue
#             who = await _mention(session, activist)
#             try:
#                 await bot.send_message(
#                     chat_id, _text(who, age_of(activist.birthday, today)),
#                     parse_mode="HTML",
#                 )
#             except Exception:
#                 logger.exception("Не смог поздравить активиста %s", activist.id)
#                 continue
#             # Отметку ставим только после удачной отправки: если телеграм
#             # не ответил, лучше попробовать снова, чем молча пропустить.
#             session.add(BirthdayGreeting(
#                 activist_id=activist.id, year=today.year,
#                 sent_at=datetime.now(MSK).replace(tzinfo=None),
#             ))
#             await session.commit()
#             sent += 1
#     return sent
#
#
# async def birthday_worker(bot: Bot) -> None:
#     """Вечный цикл: догнать пропущенное на старте, дальше — каждую полночь."""
#     raw = os.getenv("FLOOD_CHAT_ID")
#     if not raw:
#         logger.warning("FLOOD_CHAT_ID не задан — поздравления выключены")
#         return
#     try:
#         chat_id = int(raw)
#     except ValueError:
#         logger.error("FLOOD_CHAT_ID должен быть числом, а не %r", raw)
#         return
#
#     await greet_pending(bot, chat_id)
#     while True:
#         await asyncio.sleep(seconds_to_midnight())
#         try:
#             await greet_pending(bot, chat_id)
#         except Exception:
#             # Одна осечка не должна убивать задачу на всю жизнь бота.
#             logger.exception("Ошибка в поздравлениях")

"""Вкл/выкл функций. Выключенного в базе нет — значит включено."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import async_session_maker
from database.feature_models import FeatureFlag

# key -> (подпись, текст при выключении).
FLAGS: dict[str, tuple[str, str]] = {
    "quotes_top": ("Топ цитат", "Без мудростей нигде не ТОПаем"),
    "battle": ("Батл", "Без мудростей нигде не ТОПаем"),
    "battle_top": ("Топ батла", "Без мудростей нигде не ТОПаем"),
    "duel": ("Дуэль", "Поднять руки, никаких дуэлей!"),
    "roulette": ("Рулетка", "Сегодня без суицида"),
    "shalnaya": ("Шальная", "Патроны кончились, пока ждём"),
    "flags": ("Флаги", "Пока нет обстрелов, флаги убраны"),
    "wisdom": ("Мудрость", "Мудрые мысли пока не преследуют комитет"),
    "quotes": ("Цитаты", "Такое лучше просто запомнить, сегодня без записей"),
}

# Какие сеем выключенными при первом появлении. Остальные новые — включёнными.
DEFAULT_OFF = set(FLAGS)


class FeatureDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_on(self, name: str) -> bool:
        row = await self.session.get(FeatureFlag, name)
        return bool(row.enabled) if row is not None else True

    async def set(self, name: str, enabled: bool) -> None:
        row = await self.session.get(FeatureFlag, name)
        if row is None:
            row = FeatureFlag(name=name, enabled=enabled)
            self.session.add(row)
        else:
            row.enabled = enabled
        await self.session.commit()

    async def all_states(self) -> dict[str, bool]:
        rows = list((await self.session.execute(
            select(FeatureFlag)
        )).scalars().all())
        return {row.name: bool(row.enabled) for row in rows}


async def feature_on(name: str) -> bool:
    """Горит ли фича (своя сессия — удобно из хендлеров)."""
    async with async_session_maker() as session:
        return await FeatureDAO(session).is_on(name)


async def feature_off_text(name: str) -> str:
    """Что ответить на выключенную команду."""
    return FLAGS[name][1]


async def ensure_defaults() -> None:
    """Первый запуск и новые ключи: отсутствующие сеем (выключенными —
    только из DEFAULT_OFF, остальные включёнными). Существующие и ручные
    тумблеры не трогаем, рестарты не сбрасывают."""
    async with async_session_maker() as session:
        dao = FeatureDAO(session)
        states = await dao.all_states()
        for name in FLAGS:
            if name not in states:
                await dao.set(name, name not in DEFAULT_OFF)

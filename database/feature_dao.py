"""Вкл/выкл функций. Выключенного в базе нет — значит включено."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.engine import async_session_maker
from database.feature_models import FeatureFlag

# key -> (подпись, текст при выключении). Выключены сразу — первые три.
FLAGS: dict[str, tuple[str, str]] = {
    "quotes_top": ("Топ цитат", "Без мудростей нигде не ТОПаем"),
    "battle": ("Батл", "Без мудростей нигде не ТОПаем"),
    "battle_top": ("Топ батла", "Без мудростей нигде не ТОПаем"),
}


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
    """Первый запуск: сеем известные флаги выключенными — один раз.
    Дальше рулит только тумблер в админке, рестарты не сбрасывают."""
    async with async_session_maker() as session:
        states = await FeatureDAO(session).all_states()
        if states:
            return
        dao = FeatureDAO(session)
        for name in FLAGS:
            await dao.set(name, False)

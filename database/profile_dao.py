"""Чтение и запись анкеты активиста, которую человек заполняет в личке с ботом.

Отдельный DAO, чтобы не трогать database/dao.py. Работает с той же таблицей
activists — то есть анкета из лички и импорт из Excel наполняют одну базу,
и !инфо одинаково видит и тех, и других.
"""
from datetime import datetime

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Activists
from database.profile_models import ActivistLink
from utils.helpers import MSK

# Поля, которые человек заполняет сам. Остальные колонки Activists
# (studak, is_active) анкета не трогает или ставит по умолчанию.
EDITABLE = (
    "fio", "birthday", "ik_div", "group", "phone", "email", "clothes_size", "someone_div",
)


class ProfileDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def by_tg_id(self, tg_id: int) -> Activists | None:
        """Анкета по телеграм-id — самый надёжный путь (id не меняется)."""
        link = await self.session.get(ActivistLink, tg_id)
        if link is None:
            return None
        return await self.session.get(Activists, link.activist_id)

    async def by_username(self, username: str | None) -> Activists | None:
        """Запасной путь: по @тегу. В базе теги вразнобой (с '@' и без,
        разный регистр) — нормализуем обе стороны, как в ActivistsDAO."""
        needle = (username or "").strip().lstrip("@").lower()
        if not needle:
            return None
        query = select(Activists).where(
            func.lower(func.replace(Activists.tg_username, "@", "")) == needle
        )
        return (await self.session.execute(query)).scalars().first()

    async def find(self, tg_id: int, username: str | None) -> Activists | None:
        """Сначала по id, потом по тегу — чтобы человек, которого уже залили
        из Excel, при первом заходе в бота дополнял свою строку, а не плодил дубль."""
        return await self.by_tg_id(tg_id) or await self.by_username(username)

    async def save(
        self,
        tg_id: int,
        username: str | None,
        values: dict,
        activist: Activists | None = None,
    ) -> Activists:
        """Создать анкету или обновить существующую и привязать её к tg_id."""
        clean_tag = (username or "").strip().lstrip("@")

        if activist is None:
            activist = Activists(
                tg_username=clean_tag,
                fio="",
                email="",
                studak=0,
                group="",
                phone="",
                clothes_size="",
                someone_div="",
                birthday=None,
                is_active=True,
                ik_div="",
            )
            self.session.add(activist)

        for key in EDITABLE:
            if key in values:
                setattr(activist, key, values[key])
        # Тег мог смениться с прошлого раза — держим актуальный.
        activist.tg_username = clean_tag
        await self.session.flush()  # нужен activist.id для связки

        stmt = sqlite_insert(ActivistLink).values(
            tg_id=tg_id,
            activist_id=activist.id,
            username=clean_tag or None,
            filled_at=datetime.now(MSK).replace(tzinfo=None),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tg_id"],
            set_={
                "activist_id": stmt.excluded.activist_id,
                "username": stmt.excluded.username,
                "filled_at": stmt.excluded.filled_at,
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return activist

    async def delete(self, activist_id: int, tg_id: int) -> None:
        """Убрать анкету из базы вместе со связкой.

        Цитаты человека не трогаем: они принадлежат чату, а не анкете, и
        !мудрость по ним продолжит работать.
        """
        await self.session.execute(
            sa_delete(ActivistLink).where(ActivistLink.tg_id == tg_id)
        )
        await self.session.execute(
            sa_delete(Activists).where(Activists.id == activist_id)
        )
        await self.session.commit()

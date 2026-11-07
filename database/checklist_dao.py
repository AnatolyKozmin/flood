"""Чек-листы: найти лист человека и записать присланное фото."""
from sqlalchemy.ext.asyncio import AsyncSession

from database.checklist_models import Checklist
from utils.helpers import msk_now

SLOTS = (1, 2, 3)


class ChecklistDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, tg_id: int) -> Checklist | None:
        return await self.session.get(Checklist, tg_id)

    @staticmethod
    def photos(row: Checklist) -> list[str | None]:
        return [row.photo1, row.photo2, row.photo3]

    async def set_photo(self, row: Checklist, slot: int, file_id: str) -> None:
        setattr(row, f"photo{slot}", file_id)
        row.updated_at = msk_now()
        await self.session.commit()

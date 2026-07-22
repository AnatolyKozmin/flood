from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Activists, Quotes


class BaseDAO:
    """Базовый класс для всех DAO"""
    
    model = None  
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, **kwargs):
        """Создать запись"""
        obj = self.model(**kwargs)
        self.session.add(obj)
        await self.session.commit()
        return obj
    
    async def get_by_id(self, obj_id: int):
        """Получить по ID"""
        result = await self.session.execute(
            select(self.model).where(self.model.id == obj_id)
        )
        return result.scalar_one_or_none()
    
    async def update(self, obj_id: int, **kwargs):
        """Обновить запись"""
        obj = await self.get_by_id(obj_id)
        if obj:
            for key, value in kwargs.items():
                setattr(obj, key, value)
            await self.session.commit()
        return obj
    
    async def delete(self, obj_id: int) -> bool:
        """Удалить запись"""
        obj = await self.get_by_id(obj_id)
        if obj:
            await self.session.delete(obj)
            await self.session.commit()
            return True
        return False
    
    async def get_all(self) -> list:
        """Получить все записи"""
        result = await self.session.execute(select(self.model))
        return result.scalars().all()


class ActivistsDAO(BaseDAO):
 
    model = Activists

    async def get_random_activist(self):
        """Получить случайного активиста"""
        result = await self.session.execute(
            select(self.model).order_by(func.random()).limit(1)
        )
        return result.scalar_one_or_none()


    async def get_by_username(self, tg_username: str):
        # Ники в базе хранятся вразнобой: часть с '@', часть без, разный регистр.
        # Telegram отдаёт username без '@' — нормализуем обе стороны, иначе
        # автор не находится и в цитатах вместо ФИО показывается ник.
        normalized = (tg_username or "").strip().lstrip("@").lower()
        if not normalized:
            return None
        res = await self.session.execute(
            select(self.model).where(
                func.lower(func.replace(self.model.tg_username, "@", "")) == normalized
            )
        )
        return res.scalar_one_or_none()


class QuotesDAO(BaseDAO):
    model = Quotes

    async def get_random_quote(self):
        result = await self.session.execute(
            select(self.model).order_by(func.random()).limit(1)
        )
        return result.scalar_one_or_none()


    
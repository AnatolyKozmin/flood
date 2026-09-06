"""Админ-панель: доступы, статистика регистраций, обновление состава из Excel."""
import re
from datetime import datetime

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.admin_models import BotAdmin, PendingAdmin
from database.models import Activists
from database.profile_models import ActivistLink
from utils.helpers import MSK
from utils.roster_excel import COLUMNS, READ_ONLY

# Поля, которые загрузка вправе менять.
EDITABLE = tuple(key for key, _, _ in COLUMNS if key not in READ_ONLY and key != "id")


def _now() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None)


class AdminDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def all(self) -> list[BotAdmin]:
        return list((await self.session.execute(
            select(BotAdmin).order_by(BotAdmin.added_at)
        )).scalars().all())

    async def exists(self, tg_id: int) -> bool:
        return await self.session.get(BotAdmin, tg_id) is not None

    async def add(self, tg_id: int, username: str | None, title: str, added_by: int) -> bool:
        """False — если такой админ уже есть."""
        if await self.session.get(BotAdmin, tg_id) is not None:
            return False
        self.session.add(BotAdmin(
            tg_id=tg_id, username=(username or "").lstrip("@") or None,
            title=title or "", added_by=added_by, added_at=_now(),
        ))
        await self.session.commit()
        return True

    async def remove(self, tg_id: int) -> bool:
        admin = await self.session.get(BotAdmin, tg_id)
        if admin is None:
            return False
        await self.session.execute(sa_delete(BotAdmin).where(BotAdmin.tg_id == tg_id))
        await self.session.commit()
        return True


class PendingDAO:
    """Отложенные админы: выданы по тегу, ждут первого захода в бота."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip().lstrip("@").casefold()

    async def all(self) -> list[PendingAdmin]:
        return list((await self.session.execute(
            select(PendingAdmin).order_by(PendingAdmin.added_at)
        )).scalars().all())

    async def add(self, username: str, added_by: int) -> bool:
        key = self._key(username)
        if not key or await self.session.get(PendingAdmin, key) is not None:
            return False
        self.session.add(PendingAdmin(username=key, added_by=added_by, added_at=_now()))
        await self.session.commit()
        return True

    async def remove(self, username: str) -> bool:
        key = self._key(username)
        if await self.session.get(PendingAdmin, key) is None:
            return False
        await self.session.execute(
            sa_delete(PendingAdmin).where(PendingAdmin.username == key)
        )
        await self.session.commit()
        return True

    async def claim(self, tg_id: int, username: str) -> bool:
        """Превратить отложенную запись в настоящего админа. True — если сработало."""
        key = self._key(username)
        if not key:
            return False
        pending = await self.session.get(PendingAdmin, key)
        if pending is None:
            return False
        await self.session.execute(
            sa_delete(PendingAdmin).where(PendingAdmin.username == key)
        )
        if await self.session.get(BotAdmin, tg_id) is None:
            self.session.add(BotAdmin(
                tg_id=tg_id, username=key, title="",
                added_by=pending.added_by, added_at=_now(),
            ))
        await self.session.commit()
        return True


def _same(key: str, old, new) -> bool:
    """Сравнение «по смыслу»: пустая строка, None и 0 — одно и то же.

    Без этого почти каждая строка выглядела бы изменённой, и в сводке
    «обновится 51» ничего не значило бы.
    """
    if key == "birthday":
        old_d = old.date() if isinstance(old, datetime) else old
        new_d = new.date() if isinstance(new, datetime) else new
        return old_d == new_d
    if key == "is_active":
        return bool(old) == bool(new)
    if key == "studak":
        return (old or 0) == (new or 0)
    left = "" if old in (None, 0) else str(old).strip()
    right = "" if new in (None, 0) else str(new).strip()
    if key == "phone":
        # По цифрам: «+7 999 123-45-67» и «79991234567» — один номер,
        # переформатирование не должно выглядеть правкой.
        return re.sub(r"\D", "", left) == re.sub(r"\D", "", right)
    if key == "tg_username":
        return left.lstrip("@").casefold() == right.lstrip("@").casefold()
    return left == right


class RosterDAO:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def activists(self) -> list[Activists]:
        return list((await self.session.execute(
            select(Activists).order_by(Activists.fio)
        )).scalars().all())

    async def registered_ids(self) -> set[int]:
        """Кто привязал телеграм-аккаунт, то есть прошёл анкету в боте."""
        return set((await self.session.execute(
            select(ActivistLink.activist_id)
        )).scalars().all())

    async def stats(self) -> dict:
        activists = await self.activists()
        linked = await self.registered_ids()
        registered = [a for a in activists if a.id in linked]
        missing = [a for a in activists if a.id not in linked]
        with_tag = [a for a in missing if (a.tg_username or "").strip()]
        return {
            "total": len(activists),
            "registered": len(registered),
            "missing": missing,
            "missing_with_tag": len(with_tag),
            "active": sum(1 for a in activists if a.is_active),
        }

    async def plan(self, rows: list[dict]) -> dict:
        """Что произойдёт при загрузке — БЕЗ записи в базу.

        Строки находят свою запись по id, поэтому правка не плодит дублей.
        Пустой id — новый человек. Кто есть в базе, но пропал из файла,
        сам собой не удаляется: это решает админ отдельной кнопкой.
        """
        current = {a.id: a for a in await self.activists()}
        creates, updates, unknown = [], [], []
        touched = set()

        for row in rows:
            row_id = row.get("id")
            if row_id is None:
                creates.append(row)
                continue
            activist = current.get(row_id)
            if activist is None:
                # id есть, но такой записи нет: файл от старой базы.
                unknown.append(row)
                continue
            touched.add(row_id)
            changed = [key for key in EDITABLE
                       if not _same(key, getattr(activist, key, None), row.get(key))]
            if changed:
                updates.append({"row": row, "activist": activist, "changed": changed})

        missing = [a for a in current.values() if a.id not in touched]
        return {"creates": creates, "updates": updates,
                "unknown": unknown, "missing": missing}


    async def apply(self, plan: dict, drop_missing: bool = False) -> dict:
        """Применить план. Возвращает счётчики сделанного."""
        created = updated = removed = 0

        for row in plan["creates"] + plan["unknown"]:
            activist = Activists(
                fio=row.get("fio", ""), email=row.get("email", ""),
                studak=row.get("studak", 0), group=row.get("group", ""),
                phone=row.get("phone", ""), tg_username=row.get("tg_username", ""),
                clothes_size=row.get("clothes_size", ""),
                someone_div=row.get("someone_div", ""),
                birthday=row.get("birthday"), ik_div=row.get("ik_div", ""),
                is_active=row.get("is_active", True),
            )
            self.session.add(activist)
            created += 1

        for item in plan["updates"]:
            activist = item["activist"]
            for key in item["changed"]:
                setattr(activist, key, item["row"].get(key))
            updated += 1

        if drop_missing and plan["missing"]:
            ids = [a.id for a in plan["missing"]]
            # Привязки телеграм-аккаунтов уходят вместе с анкетами, иначе
            # остались бы ссылки в никуда.
            await self.session.execute(
                sa_delete(ActivistLink).where(ActivistLink.activist_id.in_(ids))
            )
            await self.session.execute(sa_delete(Activists).where(Activists.id.in_(ids)))
            removed = len(ids)

        await self.session.commit()
        return {"created": created, "updated": updated, "removed": removed}

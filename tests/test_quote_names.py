import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Activists, Base
from database.profile_models import ActivistLink
from database.stats_models import StatsUser
from utils.names import quote_name, quote_user_id
from utils.quote_render import quote_author, quote_avatar


class QuoteNamesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.maker() as session:
            session.add(Activists(id=7, tg_username=" @current_tag ",
                                 fio="Зарубина Диана", email="", studak=0,
                                 group="", phone="", clothes_size="", someone_div="",
                                 is_active=True, ik_div=""))
            session.add(StatsUser(user_id=42, username="current_tag", full_name="TG name"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_old_tag_with_id_resolves_current_profile_without_link(self):
        async with self.maker() as session:
            self.assertEqual(await quote_name(session, 42, "old_tag"), "Диана Зарубина")

    async def test_user_confirmed_historical_alias_without_id(self):
        async with self.maker() as session:
            self.assertEqual(await quote_name(session, None, " @Meowwwow66 "), "Диана Зарубина")

    async def test_current_profile_name_has_priority_over_alias(self):
        async with self.maker() as session:
            person = await session.get(Activists, 7)
            person.fio = "Новая Диана"
            session.add(ActivistLink(tg_id=42, activist_id=7))
            await session.commit()
            self.assertEqual(await quote_name(session, 42, "meowwwow66"), "Диана Новая")

    async def test_historical_quote_is_resolved_at_render_time(self):
        quote = SimpleNamespace(tg_id="", tg_username="meowwwow66")
        with patch("utils.quote_render.async_session_maker", self.maker):
            self.assertEqual(await quote_author(quote), "Диана Зарубина")

    async def test_unknown_author_keeps_tag(self):
        async with self.maker() as session:
            self.assertEqual(await quote_name(session, None, "@unknown_tag"), "unknown_tag")

    async def test_missing_id_is_resolved_by_normalized_known_tag(self):
        async with self.maker() as session:
            self.assertEqual(await quote_user_id(session, "", " @CURRENT_TAG "), 42)
            self.assertEqual(await quote_user_id(session, "777", "current_tag"), 777)
            self.assertIsNone(await quote_user_id(session, "", "unknown_tag"))

    async def test_conflicting_links_do_not_guess_identity(self):
        async with self.maker() as session:
            session.add(ActivistLink(tg_id=99, activist_id=7, username="current_tag"))
            await session.commit()
            self.assertIsNone(await quote_user_id(session, "", "current_tag"))

    async def test_missing_id_avatar_uses_resolved_account_without_database_update(self):
        quote = SimpleNamespace(tg_id="", tg_username="current_tag", created_at=None)
        with patch("utils.quote_render.async_session_maker", self.maker), patch(
            "utils.quote_render.avatar_at", return_value=None
        ) as lookup:
            await quote_avatar(None, quote)
            lookup.assert_awaited_once_with(None, 42, None)
        self.assertEqual(quote.tg_id, "")

    async def test_profile_photo_can_be_found_by_tag_without_telegram_id(self):
        quote = SimpleNamespace(tg_id="", tg_username="unknown_tag", created_at=None)
        with patch("utils.quote_render.async_session_maker", self.maker), patch(
            "utils.quote_render._profile_avatar", return_value=None
        ) as lookup:
            await quote_avatar(None, quote)
            lookup.assert_awaited_once_with(None, None, "unknown_tag")

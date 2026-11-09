import unittest
from io import BytesIO
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base, Quotes
from database.quotes_extra_dao import VotesDAO
from database.quotes_extra_models import QuoteVote
from handlers import command_quotes


class QuoteVotingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_repeat_show_allows_another_vote_but_repeat_click_does_not(self):
        async with self.maker() as session:
            dao = VotesDAO(session)
            await dao.react(1, "first", 100, 1)
            await dao.react(1, "first", 100, 1)
            self.assertEqual(await dao.rating(1), (1, 0))

            await dao.react(1, "first", 100, -1)
            self.assertEqual(await dao.rating(1), (0, 1))

            await dao.react(1, "second", 100, 1)
            await dao.react(1, "second", 101, 1)
            self.assertEqual(await dao.rating(1), (2, 1))

    async def test_rankings_use_balance_keep_legacy_likes_and_filter_period(self):
        async with self.maker() as session:
            session.add_all(Quotes(id=qid, tg_id="100", tg_username="author", text_of_quotes="Цитата")
                            for qid in (1, 2, 3))
            session.add(QuoteVote(quote_id=1, user_id=100, voted_at=datetime(2020, 1, 1)))
            await session.commit()

            dao = VotesDAO(session)
            await dao.react(1, "first", 101, -1)
            await dao.react(2, "first", 100, 1)
            await dao.react(2, "second", 100, 1)
            await dao.react(3, "first", 100, -1)
            await dao.react(3, "second", 100, -1)
            await dao.react(99, "deleted", 100, -1)

            self.assertEqual(await dao.rating(1), (1, 1))
            self.assertEqual(await dao.top(), [(2, 2, 0)])
            self.assertEqual(await dao.top(bottom=True), [(3, 0, 2)])
            self.assertEqual(await dao.top(since=date(2021, 1, 1), bottom=True), [(3, 0, 2), (1, 0, 1)])

    async def test_wisdom_repeats_same_quote_with_distinct_ballots(self):
        async with self.maker() as session:
            session.add(Quotes(id=1, tg_id="100", tg_username="author", text_of_quotes="Цитата"))
            await session.commit()

        message = SimpleNamespace(bot=None, chat=SimpleNamespace(id=100), message_thread_id=None,
                                  answer_photo=AsyncMock())
        with patch.object(command_quotes, "async_session_maker", self.maker), \
             patch.object(command_quotes, "feature_on", AsyncMock(return_value=True)), \
             patch.object(command_quotes, "show_content", AsyncMock(return_value=False)), \
             patch.object(command_quotes, "quote_author", AsyncMock(return_value="Автор")), \
             patch.object(command_quotes, "quote_avatar", AsyncMock(return_value=None)), \
             patch.object(command_quotes, "render_quote_pages", side_effect=lambda *a, **kw: [BytesIO(b"png")]):
            await command_quotes.random_wisdom(message)
            await command_quotes.random_wisdom(message)
        first, second = [call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data.split(":")
                         for call in message.answer_photo.await_args_list]
        self.assertEqual(first[:2], ["qr", "1"])
        self.assertEqual(second[:2], ["qr", "1"])
        self.assertNotEqual(first[2], second[2])

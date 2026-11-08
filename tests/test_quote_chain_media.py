import asyncio
import io
import json
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Message
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import Base, Quotes
from database.dao import QuotesDAO
from database.quotes_extra_dao import BattleDAO
from database.quote_media import (
    QuoteContent, QuoteDraft, QuoteJob, QuoteMessage, collect_chain,
    message_part, prune_history, remember_message, validate_parts,
)
from utils import quote_worker as worker
from utils.media_quote import prepare_media, run_process, text_cards
from handlers import command_quote_chain as chain


def message(mid, text="текст", parent=None, **extra):
    data = dict(message_id=mid, date=datetime.now(timezone.utc),
                chat={"id": -100123, "type": "supergroup"},
                from_user={"id": 42, "is_bot": False, "first_name": "Имя", "username": "tag"}, text=text)
    if parent is not None:
        data["reply_to_message"] = parent
    data.update(extra)
    return Message.model_validate(data)


class ChainDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patch = patch.object(worker, "async_session_maker", self.maker)
        self.patch.start()
        self.chain_patch = patch.object(chain, "async_session_maker", self.maker)
        self.chain_patch.start()
        self.flag_patch = patch.object(chain, "feature_on", AsyncMock(return_value=True))
        self.flag_patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.chain_patch.stop()
        self.flag_patch.stop()
        await self.engine.dispose()

    async def test_twenty_messages_reach_root_in_order_without_siblings(self):
        async with self.maker() as session:
            for index in range(1, 21):
                parent = message(index - 1, str(index - 1)) if index > 1 else None
                await remember_message(session, message(index, str(index), parent))
            await remember_message(session, message(99, "сосед", message(1, "1")))
            await session.commit()
            parts, complete = await collect_chain(session, -100123, 20)
            self.assertTrue(complete)
            self.assertEqual([p["text"] for p in parts], list(map(str, range(1, 21))))

    async def test_unseen_reply_is_not_mistaken_for_root(self):
        async with self.maker() as session:
            await remember_message(session, message(3, "/цитата", message(2, "старое")))
            await session.commit()
            parts, complete = await collect_chain(session, -100123, 2)
            self.assertFalse(complete)
            self.assertEqual(len(parts), 1)

    async def test_nested_reply_does_not_overwrite_known_parent_and_edits_update_text(self):
        async with self.maker() as session:
            await remember_message(session, message(1, "корень"))
            await remember_message(session, message(2, "ответ", message(1, "корень")))
            await remember_message(session, message(3, "команда", message(2, "ответ")))
            await remember_message(session, message(1, "исправленный корень"))
            await session.commit()
            parts, complete = await collect_chain(session, -100123, 2)
            self.assertTrue(complete)
            self.assertEqual(parts[0]["text"], "исправленный корень")

    async def test_cycle_and_limit_are_incomplete(self):
        async with self.maker() as session:
            await remember_message(session, message(1, "a", message(2, "b")))
            await remember_message(session, message(2, "b", message(1, "a")))
            await session.commit()
            self.assertFalse((await collect_chain(session, -100123, 2))[1])

    async def test_enqueue_is_idempotent_and_pending_not_in_wisdom_or_battle(self):
        parts = [message_part(message(1, "я обожаю")), message_part(message(2, "когда ем апельсины"))]
        qid = await worker.enqueue(parts, -100123, 42, "command:123")
        self.assertEqual(qid, await worker.enqueue(parts, -100123, 42, "command:123"))
        async with self.maker() as session:
            quote = await session.get(Quotes, qid)
            self.assertEqual(quote.text_of_quotes, "я обожаю когда ем апельсины")
            self.assertEqual(quote.tg_id, "42")
            self.assertIsNone(await QuotesDAO(session).get_random_quote())
            self.assertEqual(await BattleDAO(session).quote_ids(), [])
            job = (await session.execute(select(QuoteJob))).scalars().one()
            job.state = "done"
            await session.commit()
            self.assertEqual((await QuotesDAO(session).get_random_quote()).id, qid)
            self.assertEqual(await BattleDAO(session).quote_ids(), [qid])

    async def test_queue_limits_per_user(self):
        parts = [message_part(message(1))]
        for i in range(2):
            await worker.enqueue(parts, -100123, 42, str(i))
        with self.assertRaises(ValueError):
            await worker.enqueue(parts, -100123, 42, "third")

    async def test_pruning_does_not_destroy_saved_quote(self):
        parts = [message_part(message(1))]
        qid = await worker.enqueue(parts, -100123, 42, "saved")
        async with self.maker() as session:
            session.add(QuoteMessage(chat_id=-100123, message_id=1, payload=parts[0],
                                     parent_id=None, ancestry_known=True, seen_at=0))
            await session.commit()
            await prune_history(session)
            await session.commit()
            self.assertIsNone(await session.get(QuoteMessage, (-100123, 1)))
            self.assertEqual((await session.get(QuoteContent, qid)).parts, parts)

    async def test_worker_persists_outputs_and_reuses_them_on_recovery(self):
        parts = [message_part(message(1, "Я обожаю")), message_part(message(2, "апельсины"))]
        qid = await worker.enqueue(parts, -100123, 42, "render")
        bot = SimpleNamespace(send_photo=AsyncMock(return_value=SimpleNamespace(
            photo=[SimpleNamespace(file_id="rendered-card")])), send_message=AsyncMock())
        async with self.maker() as session:
            job = (await session.execute(select(QuoteJob))).scalars().one()
        with patch.object(worker, "quote_name", AsyncMock(return_value="Имя Фамилия")), patch.object(
            worker, "snapshot_avatar", AsyncMock(return_value=None)
        ), patch.object(worker, "avatar_at", AsyncMock(return_value=None)):
            await worker.process_job(bot, job)
            await worker.process_job(bot, job)
        self.assertEqual(bot.send_photo.await_count, 1)
        kwargs = bot.send_photo.call_args.kwargs
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].callback_data, f"qv:{qid}")
        self.assertNotIn("caption", kwargs)
        bot.send_message.assert_not_awaited()
        async with self.maker() as session:
            self.assertEqual((await session.get(QuoteJob, job.id)).state, "done")
            self.assertEqual((await session.get(QuoteContent, qid)).outputs[0]["file_id"], "rendered-card")

    async def test_enqueue_does_not_send_queue_message(self):
        with patch.object(Message, "answer", AsyncMock()) as answer:
            self.assertTrue(await chain.queue_message(message(1), [message_part(message(2))]))
            answer.assert_not_awaited()

    async def test_partial_chain_requires_explicit_confirmation(self):
        command = message(3, "/цитата", message(2, "старый ответ"))
        with patch.object(Message, "answer", AsyncMock()) as answer:
            await chain.chain_quote(command)
        self.assertIn("Полная цепочка недоступна", answer.call_args.args[0])
        async with self.maker() as session:
            self.assertEqual(list((await session.execute(select(QuoteJob))).scalars()), [])

    async def test_manual_draft_preserves_forward_order_and_author(self):
        private = dict(chat={"id": 42, "type": "private"})
        start = message(1, "/цитата", **private)
        first = message(2, "я обожаю", **private, forward_origin={"type": "user", "date": 1,
            "sender_user": {"id": 99, "first_name": "Корневой автор", "is_bot": False}})
        second = message(3, "апельсины", **private)
        with patch.object(Message, "answer", AsyncMock()):
            await chain.chain_quote(start)
            # Параллельная доставка updates альбома может переставить обработку.
            await chain.add_to_draft(second)
            await chain.add_to_draft(first)
            call = SimpleNamespace(message=message(4, "кнопки", **private), from_user=SimpleNamespace(id=42),
                                   data="qm:done", answer=AsyncMock())
            await chain.finish_draft(call)
        async with self.maker() as session:
            quote = (await session.execute(select(Quotes))).scalars().one()
            self.assertEqual(quote.tg_id, "99")
            self.assertEqual(quote.text_of_quotes, "я обожаю апельсины")
            self.assertIsNone(await session.get(QuoteDraft, 42))

    async def test_foreign_user_cannot_finish_manual_draft(self):
        call = SimpleNamespace(message=message(4, "кнопки", chat={"id": 42, "type": "private"}),
                               from_user=SimpleNamespace(id=99), data="qm:done", answer=AsyncMock())
        await chain.finish_draft(call)
        self.assertTrue(call.answer.call_args.kwargs["show_alert"])

    async def test_worker_recovers_running_job_on_startup(self):
        qid = await worker.enqueue([message_part(message(1))], -100123, 42, "recover")
        async with self.maker() as session:
            job = (await session.execute(select(QuoteJob))).scalars().one()
            job.state = "running"
            await session.commit()
        done = asyncio.Event()
        async def process(bot, job):
            self.assertEqual(job.quote_id, qid)
            self.assertEqual(job.state, "running")
            async with self.maker() as session:
                row = await session.get(QuoteJob, job.id)
                row.state = "done"
                await session.commit()
            done.set()
        with patch.object(worker, "process_job", process):
            task = asyncio.create_task(worker.quote_worker(None))
            try:
                await asyncio.wait_for(done.wait(), timeout=3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_mixed_text_video_caption_order_and_cached_replay(self):
        video = message_part(message(2, None, caption="подпись", video=dict(
            file_id="video", file_unique_id="v", width=100, height=100, duration=1)))
        parts = [message_part(message(1, "начало")), video, message_part(message(3, "конец"))]
        qid = await worker.enqueue(parts, -100123, 42, "mixed")
        sent = []
        async def photo(**kwargs):
            sent.append("photo")
            return SimpleNamespace(photo=[SimpleNamespace(file_id="photo" + str(len(sent)))])
        async def send_video(**kwargs):
            sent.append("video")
            return SimpleNamespace(video=SimpleNamespace(file_id="video-result"))
        bot = SimpleNamespace(send_photo=photo, send_video=send_video, send_message=AsyncMock())
        async with self.maker() as session:
            job = (await session.execute(select(QuoteJob))).scalars().one()
        with patch.object(worker, "quote_name", AsyncMock(return_value="Автор")), patch.object(
            worker, "snapshot_avatar", AsyncMock(return_value=None)
        ), patch.object(worker, "avatar_at", AsyncMock(return_value=None)), patch.object(
            worker, "prepare_media", AsyncMock(return_value=("video", Path("unused.mp4")))
        ):
            await worker.process_job(bot, job)
        self.assertEqual(sent, ["photo", "video", "photo"])
        async with self.maker() as session:
            quote = await session.get(Quotes, qid)
        with patch("utils.quote_render.quote_author", AsyncMock(return_value="Автор")), patch(
            "utils.quote_render.quote_avatar", AsyncMock(return_value=None)
        ):
            await worker.show_content(bot, -100123, qid)
        self.assertEqual(sent, ["photo", "video", "photo"] * 2)
        # Исправление имени аннулирует рендер; в новой очереди остаётся тот же номер.
        with patch("utils.quote_render.quote_author", AsyncMock(return_value="Исправленный автор")), patch(
            "utils.quote_render.quote_avatar", AsyncMock(return_value=None)
        ):
            await worker.show_content(bot, -100456, qid)
        async with self.maker() as session:
            job = await session.get(QuoteJob, job.id)
            self.assertEqual(job.state, "pending")
            self.assertEqual(job.chat_id, -100456)

    async def test_private_chain_reaches_root_through_middleware(self):
        from middlewares.quote_history import QuoteHistoryMiddleware
        private = dict(chat={"id": 42, "type": "private"})
        with patch("middlewares.quote_history.async_session_maker", self.maker):
            handler = AsyncMock(return_value="next")
            root = message(1, "проверка", **private)
            reply = message(2, "хуерка", root, **private)
            command = message(3, "/цитата", message(2, "хуерка", **private), **private)
            for item in (root, reply, command):
                self.assertEqual(await QuoteHistoryMiddleware()(handler, item, {"raw_state": None}), "next")
            with patch.object(Message, "answer", AsyncMock()) as answer:
                await chain.chain_quote(command)
                answer.assert_not_awaited()
        async with self.maker() as session:
            quote = (await session.execute(select(Quotes))).scalars().one()
            self.assertEqual(quote.text_of_quotes, "проверка хуерка")
            parts, complete = await collect_chain(session, 42, 2)
            self.assertTrue(complete)
            self.assertEqual([p["text"] for p in parts], ["проверка", "хуерка"])

    async def test_private_form_answers_are_not_archived(self):
        from middlewares.quote_history import QuoteHistoryMiddleware
        with patch("middlewares.quote_history.async_session_maker", self.maker):
            handler = AsyncMock()
            await QuoteHistoryMiddleware()(handler, message(1, "анкета", chat={"id": 42, "type": "private"}), {"raw_state": "Form:answer"})
            handler.assert_awaited_once()
        async with self.maker() as session:
            self.assertEqual(list((await session.execute(select(QuoteMessage))).scalars()), [])

    async def test_private_edits_preserve_parent_chain(self):
        from middlewares.quote_history import record_quote_message
        private = dict(chat={"id": 42, "type": "private"})
        with patch("middlewares.quote_history.async_session_maker", self.maker):
            await record_quote_message(message(1, "исходник", **private))
            await record_quote_message(message(2, "ответ", message(1, "исходник", **private), **private))
            await record_quote_message(message(1, "исправленный текст", **private))
        async with self.maker() as session:
            parts, complete = await collect_chain(session, 42, 2)
            self.assertTrue(complete)
            self.assertEqual(parts[0]["text"], "исправленный текст")

    async def test_private_history_expires_in_a_day_group_history_in_90_days(self):
        async with self.maker() as session:
            for chat in (42, -100123):
                session.add(QuoteMessage(chat_id=chat, message_id=1, parent_id=None,
                                         ancestry_known=True, payload={}, seen_at=int(time.time()) - 2 * 86400))
            await session.commit()
            await prune_history(session)
            await session.commit()
            self.assertIsNone(await session.get(QuoteMessage, (42, 1)))
            self.assertIsNotNone(await session.get(QuoteMessage, (-100123, 1)))

    async def test_cyrillic_command_and_english_alias_filter(self):
        command_filter = chain.ChainCommand()
        self.assertTrue(await command_filter(message(1, "/цитата")))
        self.assertTrue(await command_filter(message(1, "/quote")))
        self.assertFalse(await command_filter(message(1, "обычный текст")))


class PayloadTests(unittest.TestCase):
    def test_media_types_and_forwarded_author(self):
        media = dict(file_id="file", file_unique_id="unique", width=100, height=100, duration=1, length=100)
        for kind in ("photo", "video", "animation", "video_note"):
            value = [dict(file_id="file", file_unique_id="unique", width=100, height=100)] if kind == "photo" else media
            msg = message(1, None, **{kind: value}, forward_origin={"type": "user", "date": 1,
                          "sender_user": {"id": 77, "first_name": "Автор", "is_bot": False}})
            part = message_part(msg)
            self.assertEqual(part["kind"], kind)
            self.assertEqual(part["tg_id"], "77")
            validate_parts([part])

    def test_rejects_protected_large_long_and_unsupported(self):
        part = message_part(message(1))
        for change in ({"protected": True}, {"file_size": 21 * 1024 * 1024},
                       {"duration": 61}, {"kind": "unsupported"}):
            with self.assertRaises(ValueError):
                validate_parts([{**part, **change}])

    def test_long_text_paginates_without_empty_cards(self):
        cards = text_cards("длинная цитата " * 200, "Автор", None)
        self.assertGreater(len(cards), 1)
        for card in cards:
            self.assertEqual(Image.open(card).size, (1024, 576))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class MediaEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_note_is_square_video_with_audio_and_gif_is_silent(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            fixture = folder / "fixture.mp4"
            await run_process("ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                              "color=c=red:s=160x120:d=0.5", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                              "-c:v", "libx264", "-c:a", "aac", "-shortest", fixture)
            async def get_file(fid):
                return SimpleNamespace(file_size=fixture.stat().st_size, file_path="fixture")
            async def download_file(path, destination, timeout=60):
                shutil.copyfile(fixture, destination)
            bot = SimpleNamespace(get_file=get_file, download_file=download_file)
            mp4 = fixture
            gif = folder / "fixture.gif"
            await run_process("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", mp4, "-an", gif)
            for source_kind, expected_kind, audio in (("video_note", "video", True), ("video", "video", True), ("animation", "animation", False)):
                fixture = gif if source_kind == "animation" else mp4
                output_folder = folder / source_kind
                output_folder.mkdir()
                kind, path = await prepare_media(bot, {"kind": source_kind, "file_id": "source"}, output_folder, "Автор", None)
                self.assertEqual(kind, expected_kind)
                probe = json.loads(await run_process("ffprobe", "-v", "error", "-show_streams", "-of", "json", path))
                video = next(s for s in probe["streams"] if s["codec_type"] == "video")
                self.assertEqual((video["width"], video["height"]), (1024, 1024))
                self.assertEqual(any(s["codec_type"] == "audio" for s in probe["streams"]), audio)

    async def test_photo_keeps_whole_image_on_square_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "fixture.jpg"
            Image.new("RGB", (800, 200), "red").save(source)
            async def get_file(fid):
                return SimpleNamespace(file_size=source.stat().st_size, file_path="photo")
            async def download_file(path, destination, timeout=60):
                shutil.copyfile(source, destination)
            kind, path = await prepare_media(SimpleNamespace(get_file=get_file, download_file=download_file),
                                              {"kind": "photo", "file_id": "photo"}, folder, "Автор", None)
            self.assertEqual(kind, "photo")
            with Image.open(path) as result:
                self.assertEqual(result.size, (1024, 1024))
                self.assertGreater(result.getpixel((512, 500))[0], 200)

import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.meter_dao import MeterDAO
from database.meter_models import Meter
from database.profile_models import ActivistLink, ActivistPhoto
from utils import telegram_avatar as avatars


class AvatarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("_AVATAR_DIR", root), ("_SNAP_DIR", root / "history")):
            p = patch.object(avatars, name, value)
            p.start()
            self.addCleanup(p.stop)

    async def test_live_avatar_wins_and_identical_photo_has_one_snapshot(self):
        image = Image.new("RGB", (20, 20), "red")
        with patch.object(avatars, "_download", AsyncMock(return_value=image)), patch.object(
            avatars, "_profile_avatar", AsyncMock()
        ) as profile:
            await avatars.snapshot_avatar(None, 42)
            await avatars.snapshot_avatar(None, 42)
            profile.assert_not_awaited()
        self.assertEqual(len(list((avatars._SNAP_DIR / "42").glob("*.jpg"))), 1)

    async def test_expired_cache_or_history_wins_over_profile(self):
        image = Image.new("RGB", (20, 20), "blue")
        avatars._store(42, image)
        avatars._snapshot(42, image, 1)
        os.utime(avatars._latest_path(42), (1, 1))
        with patch.object(avatars, "_download", AsyncMock(return_value=None)), patch.object(
            avatars, "_profile_avatar", AsyncMock()
        ) as profile:
            self.assertIsNotNone(await avatars.snapshot_avatar(None, 42))
            avatars._latest_path(42).unlink()
            self.assertIsNotNone(await avatars.load_user_profile_avatar(None, 42))
            profile.assert_not_awaited()

    async def test_profile_only_when_no_telegram_photo_and_not_cached(self):
        image = Image.new("RGB", (20, 20), "green")
        with patch.object(avatars, "_download", AsyncMock(return_value=None)), patch.object(
            avatars, "_profile_avatar", AsyncMock(return_value=image)
        ) as profile:
            self.assertIs(await avatars.snapshot_avatar(None, 42), image)
            self.assertIs(await avatars.avatar_at(None, 42, None), image)
            self.assertEqual(profile.await_count, 2)
        self.assertFalse(avatars._latest_path(42).exists())
        self.assertFalse(avatars._SNAP_DIR.exists())

    async def test_history_uses_date_in_filename_not_file_mtime_or_latest(self):
        old = datetime(2026, 10, 1, 10, tzinfo=timezone.utc).timestamp()
        new = datetime(2026, 10, 2, 10, tzinfo=timezone.utc).timestamp()
        avatars._snapshot(42, Image.new("RGB", (20, 20), "red"), old)
        avatars._snapshot(42, Image.new("RGB", (20, 20), "blue"), new)
        avatars._store(42, Image.new("RGB", (20, 20), "blue"))
        # Копирование/восстановление файлов меняет mtime, но не дату снимка.
        for path in (avatars._SNAP_DIR / "42").glob("*.jpg"):
            os.utime(path, (new + 10000, new + 10000))
        with patch.object(avatars, "_download", AsyncMock()) as download:
            img = await avatars.avatar_at(None, 42, datetime(2026, 10, 1, 14))
            self.assertGreater(img.getpixel((0, 0))[0], 200)
            self.assertLess(img.getpixel((0, 0))[2], 30)
            download.assert_not_awaited()

    async def test_moscow_naive_time_does_not_select_future_snapshot(self):
        # 15:00 MSK = 12:00 UTC; снимок 13:00 UTC ещё из будущего.
        for hour, color in ((11, "red"), (13, "blue")):
            stamp = datetime(2026, 10, 1, hour, tzinfo=timezone.utc).timestamp()
            avatars._snapshot(42, Image.new("RGB", (20, 20), color), stamp)
        img = await avatars.avatar_at(None, 42, datetime(2026, 10, 1, 15))
        self.assertGreater(img.getpixel((0, 0))[0], 200)

    async def test_unknown_date_or_missing_period_never_uses_current_avatar(self):
        avatars._store(42, Image.new("RGB", (20, 20), "blue"))
        avatars._snapshot(42, Image.new("RGB", (20, 20), "blue"), 2000)
        with patch.object(avatars, "_download", AsyncMock()) as download, patch.object(
            avatars, "_profile_avatar", AsyncMock()
        ) as profile:
            self.assertIsNone(await avatars.avatar_at(None, 42, None))
            self.assertIsNone(await avatars.avatar_at(None, 42, 1000))
            download.assert_not_awaited()
            profile.assert_not_awaited()

    async def test_legacy_snapshot_filename(self):
        folder = avatars._SNAP_DIR / "42"
        folder.mkdir(parents=True)
        Image.new("RGB", (20, 20), "red").save(folder / "20261001_110000_000000.jpg")
        self.assertIsNotNone(await avatars.avatar_at(None, 42, datetime(2026, 10, 1, 15)))

    async def test_synced_profile_photos_try_second_if_first_unavailable(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            for table in (ActivistLink.__table__, ActivistPhoto.__table__):
                await conn.run_sync(table.create)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            session.add_all([ActivistLink(tg_id=42, activist_id=7),
                             ActivistPhoto(activist_id=7, slot=1, file_id="broken"),
                             ActivistPhoto(activist_id=7, slot=2, file_id="good")])
            await session.commit()
        from types import SimpleNamespace
        async def download(file_id, destination):
            if file_id == "broken":
                raise OSError("unavailable")
            Image.new("RGB", (20, 20), "green").save(destination, format="PNG")
        with patch("database.engine.async_session_maker", maker), patch(
            "database.profile_dao.ProfileDAO.by_tg_id", AsyncMock(return_value=SimpleNamespace(id=7))
        ):
            self.assertIsNotNone(await avatars._profile_avatar(SimpleNamespace(download=download), 42))
        await engine.dispose()


class MeterTests(unittest.IsolatedAsyncioTestCase):
    async def test_bottom_limits_and_sorts_including_negative_sizes(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Meter.__table__.create)
            async with async_sessionmaker(engine)() as session:
                self.assertEqual(await MeterDAO(session).bottom(), [])
                session.add_all(Meter(user_id=i, size=i - 5) for i in range(15))
                await session.commit()
                rows = await MeterDAO(session).bottom()
                self.assertEqual([r.size for r in rows], list(range(-5, 5)))
                self.assertEqual(len(await MeterDAO(session).top()), 10)
        finally:
            await engine.dispose()

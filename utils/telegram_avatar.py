import io
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from aiogram import Bot
from utils.helpers import MSK

logger = logging.getLogger(__name__)

# Аватарки на диске: каждый рендер цитаты дёргал getUserProfilePhotos +
# скачивание — медленно и роняет отрисовку, если телега не отдала фото.
# Кладём JPEG рядом с базой (volume database/ переживает ребилд, в git
# не едет — см. .gitignore) и обновляем не чаще TTL.
_AVATAR_DIR = Path("database") / "avatars"
_AVATAR_TTL = 30 * 24 * 3600
# История снапшотов: history/<tg_id>/<метка>.jpg. Новый файл — только если
# картинка реально сменилась (байты отличаются), иначе один и тот же файл
# покрывает весь период. Для рендера старой цитаты берём самый свежий
# снапшот не новее её даты; скрытую аватарку так находим последней открытой.
_SNAP_DIR = _AVATAR_DIR / "history"


def _latest_path(user_id: int) -> Path:
    return _AVATAR_DIR / f"{int(user_id)}.jpg"


def _cached(user_id: int) -> Image.Image | None:
    path = _latest_path(user_id)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > _AVATAR_TTL:
            return None
        img = Image.open(path)
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


def _store(user_id: int, img: Image.Image) -> None:
    try:
        _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(_latest_path(user_id),
                                format="JPEG", quality=85)
    except Exception:
        logger.debug("Не сохранил аватарку %s", user_id, exc_info=True)


def _snapshot(user_id: int, img: Image.Image, now: float) -> None:
    """Снапшот в историю, если картинка сменилась. Метка — время съёмки."""
    try:
        user_dir = _SNAP_DIR / str(int(user_id))
        user_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ")
        (user_dir / f"{stamp}.jpg").write_bytes(_jpeg_bytes(img))
    except Exception:
        logger.debug("Не сохранил снапшот %s", user_id, exc_info=True)


def _jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def _download(bot: Bot, user_id: int) -> Image.Image | None:
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
    except Exception:
        return None
    if not photos.total_count or not photos.photos:
        return None
    sizes = photos.photos[0]
    if not sizes:
        return None
    pic = sizes[-1]
    buf = io.BytesIO()
    try:
        await bot.download(pic, destination=buf)
        buf.seek(0)
        with Image.open(buf) as img:
            return img.convert("RGBA")
    except Exception:
        logger.debug("Не загрузил аватарку %s", user_id, exc_info=True)
        return None


def _last_known(user_id: int) -> Image.Image | None:
    """Последнее доступное Telegram-фото, даже если кэш старше TTL."""
    paths = [_latest_path(user_id)]
    user_dir = _SNAP_DIR / str(int(user_id))
    if user_dir.is_dir():
        paths.extend(sorted(user_dir.glob("*.jpg"), reverse=True))
    for path in paths:
        try:
            with Image.open(path) as img:
                return img.convert("RGBA")
        except (OSError, ValueError):
            continue
    return None


async def _profile_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Фото анкеты (в том числе синхронизированной из трекера).

    Не пишем его в кэш Telegram: при появлении аватарки она приоритетнее.
    """
    from database.engine import async_session_maker
    from database.profile_dao import ProfileDAO

    try:
        async with async_session_maker() as session:
            dao = ProfileDAO(session)
            person = await dao.by_tg_id(user_id)
            photos = await dao.photos(person.id) if person else {}
        for slot in sorted(photos):
            try:
                buf = io.BytesIO()
                await bot.download(photos[slot], destination=buf)
                buf.seek(0)
                with Image.open(buf) as img:
                    return img.convert("RGBA")
            except Exception:
                logger.debug("Не загрузил фото анкеты %s/%s", user_id, slot, exc_info=True)
    except Exception:
        logger.debug("Не нашёл фото анкеты %s", user_id, exc_info=True)
    return None


async def _fallback_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    known = _last_known(user_id)
    if known is not None:
        return known
    return await _profile_avatar(bot, user_id)


async def snapshot_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Свежая аватарка прямо сейчас + запись в latest и историю.

    Для новых цитат: снимаем текущий вид, даже если кэш свежий, — иначе
    в историю ляжет чужая давность. Не вышло скачать — последняя из кэша.
    """
    now = time.time()
    img = await _download(bot, user_id)
    if img is None:
        return await _fallback_avatar(bot, user_id)
    latest = _latest_path(user_id)
    try:
        same = latest.is_file() and latest.read_bytes() == _jpeg_bytes(img)
    except Exception:
        same = False
    _store(user_id, img)
    if not same:
        _snapshot(user_id, img, now)
    return img


async def avatar_at(bot: Bot, user_id: int, at) -> Image.Image | None:
    """Аватарка, актуальная на момент at (datetime|None).

    Только снимок не новее даты. Без даты/подходящего снимка не подставляем
    сегодняшнее Telegram-фото. Фото анкеты — лишь если Telegram-истории нет.
    """
    if at is not None:
        try:
            user_dir = _SNAP_DIR / str(int(user_id))
            if isinstance(at, datetime):
                # created_at записан через msk_now(): наивное московское время.
                stamp = (at if at.tzinfo else at.replace(tzinfo=MSK)).timestamp()
            else:
                stamp = float(at)
            candidates = []
            if user_dir.is_dir():
                for path in user_dir.glob("*.jpg"):
                    try:
                        # Старые файлы созданы сервером в UTC без суффикса Z.
                        taken = datetime.strptime(path.stem.rstrip("Z"), "%Y%m%d_%H%M%S_%f")
                        taken_stamp = taken.replace(tzinfo=timezone.utc).timestamp()
                        if taken_stamp <= stamp:
                            candidates.append((taken_stamp, path))
                    except ValueError:
                        continue
            for _, path in sorted(candidates, reverse=True):
                try:
                    with Image.open(path) as img:
                        return img.convert("RGBA")
                except (OSError, ValueError):
                    continue
        except Exception:
            logger.debug("Не нашёл снапшот %s", user_id, exc_info=True)
    # Нельзя приписать нынешнюю аватарку старой цитате. Историю не портим
    # живым скачиванием; уже существующие latest/history блокируют подмену.
    user_dir = _SNAP_DIR / str(int(user_id))
    if _latest_path(user_id).is_file() or (user_dir.is_dir() and any(user_dir.glob("*.jpg"))):
        return None
    return await _profile_avatar(bot, user_id)


async def load_user_profile_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Самый крупный вариант аватарки или None. Сначала диск, потом телега."""
    hit = _cached(user_id)
    if hit is not None:
        return hit
    img = await _download(bot, user_id)
    if img is None:
        return await _fallback_avatar(bot, user_id)
    _store(user_id, img)
    return img

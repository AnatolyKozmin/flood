import io
import logging
import time
from datetime import datetime
from pathlib import Path

from PIL import Image
from aiogram import Bot

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
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S_%f")
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
    await bot.download(pic, destination=buf)
    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return img.convert("RGBA")


async def snapshot_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Свежая аватарка прямо сейчас + запись в latest и историю.

    Для новых цитат: снимаем текущий вид, даже если кэш свежий, — иначе
    в историю ляжет чужая давность. Не вышло скачать — последняя из кэша.
    """
    now = time.time()
    img = await _download(bot, user_id)
    if img is None:
        return _cached(user_id)
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

    Самый свежий снапшот не новее даты; нет снапшотов или даты —
    обычное поведение (кэш latest, потом живое скачивание).
    """
    if at is not None:
        try:
            user_dir = _SNAP_DIR / str(int(user_id))
            stamp = at.timestamp() if hasattr(at, "timestamp") else float(at)
            best: Path | None = None
            if user_dir.is_dir():
                for path in sorted(user_dir.glob("*.jpg")):
                    if path.stat().st_mtime <= stamp and (
                            best is None or path.stat().st_mtime > best.stat().st_mtime):
                        best = path
            if best is not None:
                img = Image.open(best)
                img.load()
                return img.convert("RGBA")
        except Exception:
            logger.debug("Не нашёл снапшот %s", user_id, exc_info=True)
    return await load_user_profile_avatar(bot, user_id)


async def load_user_profile_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Самый крупный вариант аватарки или None. Сначала диск, потом телега."""
    hit = _cached(user_id)
    if hit is not None:
        return hit
    img = await _download(bot, user_id)
    if img is None:
        return None
    _store(user_id, img)
    return img

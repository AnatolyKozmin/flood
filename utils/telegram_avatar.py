import io
import logging
import time
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


def _cached(user_id: int) -> Image.Image | None:
    path = _AVATAR_DIR / f"{int(user_id)}.jpg"
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
        img.convert("RGB").save(_AVATAR_DIR / f"{int(user_id)}.jpg",
                                format="JPEG", quality=85)
    except Exception:
        logger.debug("Не сохранил аватарку %s", user_id, exc_info=True)


async def load_user_profile_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Самый крупный вариант аватарки или None. Сначала диск, потом телега."""
    hit = _cached(user_id)
    if hit is not None:
        return hit
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
    img = img.convert("RGBA")
    _store(user_id, img)
    return img

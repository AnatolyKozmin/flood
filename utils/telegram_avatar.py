import io

from PIL import Image
from aiogram import Bot


async def load_user_profile_avatar(bot: Bot, user_id: int) -> Image.Image | None:
    """Скачивает самый крупный вариант аватарки пользователя или None."""
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

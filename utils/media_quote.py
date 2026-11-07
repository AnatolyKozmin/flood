"""Рендер медиа без постоянного хранения исходных файлов на сервере."""
import asyncio
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from utils.create_quote import (
    _TEMPLATE_PATH, _load_font, _paste_circular_avatar, _truncate_footer_name,
    render_quote_pages,
)

SIZE = 1024
BOX = (64, 122, 960, 878)
MAX_BYTES = 20 * 1024 * 1024


def media_frame(author, avatar=None):
    with Image.open(_TEMPLATE_PATH) as template:
        template = template.convert("RGB")
        frame = Image.new("RGB", (SIZE, SIZE), "black")
        frame.paste(template.crop((0, 0, 1024, 118)), (0, 0))
        frame.paste(template.crop((0, 430, 1024, 576)), (0, 878))
    if avatar is not None:
        rgba = frame.convert("RGBA")
        _paste_circular_avatar(rgba, avatar, 86, 940, 98)
        frame = rgba.convert("RGB")
    draw = ImageDraw.Draw(frame)
    font = _load_font(34)
    name = _truncate_footer_name(draw, author, font, 660)
    draw.text((157, 940), name, fill="white", font=font, anchor="lm")
    return frame


def photo_card(source, destination, author, avatar=None):
    frame = media_frame(author, avatar)
    with Image.open(source) as image:
        if image.width * image.height > 30_000_000:
            raise ValueError("Слишком большое разрешение фотографии.")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image = ImageOps.contain(image, (BOX[2] - BOX[0], BOX[3] - BOX[1]))
        frame.paste(image, ((SIZE - image.width) // 2, BOX[1] + (BOX[3] - BOX[1] - image.height) // 2))
    frame.save(destination, format="JPEG", quality=92)


async def run_process(*args, timeout=180):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        # Не отдаём пути и низкоуровневый stderr пользователю.
        raise ValueError("Не удалось обработать медиа. Попробуй другой файл.")
    return stdout


async def download_media(bot, file_id, destination):
    file = await bot.get_file(file_id)
    if file.file_size and file.file_size > MAX_BYTES:
        raise ValueError("Вложение больше 20 МБ.")
    await bot.download_file(file.file_path, destination, timeout=60)
    if Path(destination).stat().st_size > MAX_BYTES:
        raise ValueError("Вложение больше 20 МБ.")


async def prepare_media(bot, part, folder, author, avatar):
    source, target = folder / "source", folder / "quote.mp4"
    await download_media(bot, part["file_id"], source)
    if part["kind"] == "photo":
        target = folder / "quote.jpg"
        await asyncio.to_thread(photo_card, source, target, author, avatar)
        return "photo", target
    probe = json.loads(await run_process(
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", source, timeout=20))
    duration = float(probe.get("format", {}).get("duration") or 0)
    videos = [s for s in probe.get("streams", []) if s["codec_type"] == "video"]
    if not videos or duration <= 0 or duration > 60:
        raise ValueError("Нужен видеоролик или GIF длительностью до 60 секунд.")
    if videos[0].get("width", 0) * videos[0].get("height", 0) > 3840 * 2160:
        raise ValueError("Слишком большое разрешение видео. Максимум 4K.")
    frame = folder / "frame.png"
    await asyncio.to_thread(lambda: media_frame(author, avatar).save(frame))
    # Видео вписывается целиком; звук берём только из источника, GIF остаётся без звука.
    filters = (
        "[0:v]scale=896:756:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        "setsar=1,fps=25[clip];"
        "[1:v][clip]overlay=x=(W-w)/2:y=122+(756-h)/2:shortest=1,format=yuv420p[out]"
    )
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "1", "-i", source,
            "-loop", "1", "-i", frame, "-filter_complex", filters, "-map", "[out]"]
    if part["kind"] == "animation":
        args += ["-an"]
    else:
        args += ["-map", "0:a?", "-c:a", "aac", "-b:a", "96k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-threads", "2",
             "-t", str(duration), "-movflags", "+faststart", target]
    await run_process(*args)
    if target.stat().st_size > 49 * 1024 * 1024:
        raise ValueError("Результат слишком большой для отправки. Пришли ролик покороче.")
    return ("animation" if part["kind"] == "animation" else "video"), target


def text_cards(text, author, avatar):
    """Длинные цепочки разбиваем на читаемые карточки, не теряя текст."""
    remaining, pages = text.strip(), []
    while remaining:
        cut = min(len(remaining), 700)
        if cut < len(remaining):
            boundary = remaining.rfind(" ", 0, cut)
            if boundary > 0:
                cut = boundary
        pages.extend(render_quote_pages(remaining[:cut], author, avatar=avatar))
        remaining = remaining[cut:].lstrip()
    return pages


async def content_preview(bot, parts, author, avatar):
    media = next((p for p in parts if p["kind"] != "text"), None)
    text = " ".join(p["text"] for p in parts if p["text"])
    if media:
        fid = media.get("thumbnail_id")
        if fid:
            try:
                buf = io.BytesIO()
                await bot.download(fid, destination=buf)
                buf.seek(0)
                out = io.BytesIO()
                await asyncio.to_thread(photo_card, buf, out, author, avatar)
                out.seek(0)
                return out
            except Exception:
                pass
        text = f"[{ {'photo': 'Фото', 'video': 'Видео', 'video_note': 'Кружок', 'animation': 'GIF'}.get(media['kind'], 'Медиа') }] " + text
    return render_quote_pages(text[:500] or "Медиа-цитата", author, avatar=avatar)[0]

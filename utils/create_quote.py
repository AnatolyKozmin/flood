import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

_MAX_CHARS = 8000
W, H = 1024, 576
CONTENT_Y0 = 118
CONTENT_Y1 = 430
_MARGIN_X = 80
_GU_MEASURE_PADDING = 48
_LINE_SPACING = 14
_AVATAR_CX = 86
_AVATAR_CY = 492
_AVATAR_DIAMETER = 98
_AUTHOR_GAP_AFTER_AVATAR = 22
_FOOTER_NAME_MAX_RIGHT = W - 200

_WHITE = (255, 255, 255)

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_TEMPLATE_PATH = _ASSETS_DIR / "quote_template.png"
_CYGRE_FONT = _ASSETS_DIR / "Cygre-Medium.ttf"

_FONT_FALLBACKS = (
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    _ASSETS_DIR / "DejaVuSans.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _CYGRE_FONT.is_file():
        try:
            return ImageFont.truetype(str(_CYGRE_FONT), size)
        except OSError:
            pass
    for p in _FONT_FALLBACKS:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _normalize_inner(text: str) -> str:
    """
    Пустые абзацы игнорируются. Строки внутри абзаца сливаются пробелом;
    абзацы разделяются одним \\n — при верстке перенос сохранится (пустую строку).
    Затем снимаем внешние типографские «…», если они обрамляют весь текст.
    """
    raw = text.replace("\r\n", "\n").strip()
    paragraphs: list[str] = []
    for block in raw.split("\n\n"):
        flat = " ".join(w.strip() for w in block.split("\n") if w.strip())
        flat = flat.strip()
        if flat:
            paragraphs.append(flat)
    inner = "\n".join(paragraphs)
    opened = "\u00ab\u2039\u300c"
    closed = "\u00bb\u203a\u300d"
    while len(inner) >= 2 and inner[0] in opened and inner[-1] in closed:
        inner = inner[1:-1].strip()
    return inner


def _tokens_from_inner(inner: str) -> list[str]:
    """Слова как токены; между строками исходного inner — токен \"\\n\" (отдельно от текста слов)."""
    rows = inner.split("\n")
    toks: list[str] = []
    for ri, row in enumerate(rows):
        for w in row.split():
            toks.append(w)
        if ri < len(rows) - 1:
            toks.append("\n")
    return toks


def _slice_tokens_to_fragment(tokens: list[str], start: int, end: int) -> str:
    """[start:end) — end exclusive."""
    chunk = tokens[start:end]
    rows: list[str] = []
    cur: list[str] = []
    for t in chunk:
        if t == "\n":
            rows.append(" ".join(cur))
            cur = []
        else:
            cur.append(t)
    rows.append(" ".join(cur))
    return "\n".join(rows)


def _decorate_quote_lines(
    parts: list[str], *, prepend_opening: bool, append_closing: bool
) -> list[str]:
    """
    добавляется к первой непустой строке, если prepend_opening
    к последней непустой, если append_closing.
    Серединные страницы — без открывающих/закрывающих кавычек.
    """
    first: int | None = None
    last: int | None = None
    for i, ln in enumerate(parts):
        if ln.strip():
            if first is None:
                first = i
            last = i
    if first is None:
        if prepend_opening and append_closing:
            return ["\u00ab\u2026\u00bb"]
        if prepend_opening:
            return ["\u00ab\u2026"]
        if append_closing:
            return ["\u2026\u00bb"]
        return ["\u2026"]

    out: list[str] = []
    for i, ln in enumerate(parts):
        if ln == "":
            out.append("")
            continue
        s = ln
        if prepend_opening and i == first:
            s = "\u00ab" + s
        if append_closing and last is not None and i == last:
            s = s + "\u00bb"
        out.append(s)
    return out


def _wrap_to_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
) -> str:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return ""
    lines: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            lines.append("")
            continue
        chunk = ""
        for word in block.split():
            trial = chunk + (" " if chunk else "") + word
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] <= max_width:
                chunk = trial
                continue
            if chunk:
                lines.append(chunk)
                chunk = ""
            w_bbox = draw.textbbox((0, 0), word, font=font)
            if w_bbox[2] - w_bbox[0] <= max_width:
                chunk = word
            else:
                acc = ""
                for ch in word:
                    t2 = acc + ch
                    bb = draw.textbbox((0, 0), t2, font=font)
                    if bb[2] - bb[0] <= max_width:
                        acc = t2
                    else:
                        if acc:
                            lines.append(acc)
                        acc = ch
                chunk = acc
        if chunk:
            lines.append(chunk)
    return "\n".join(lines)


def _paste_circular_avatar(
    base_rgba: Image.Image, avatar: Image.Image, cx: int, cy: int, d: int
) -> None:
    thumb = ImageOps.fit(avatar.convert("RGBA"), (d, d), Image.Resampling.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
    alpha = thumb.split()[3] if thumb.mode == "RGBA" else Image.new("L", thumb.size, 255)
    combined = ImageChops.multiply(alpha, mask)
    ox = cx - d // 2
    oy = cy - d // 2
    base_rgba.paste(thumb, (ox, oy), mask=combined)


def _wrapped_lines_heights(
    draw: ImageDraw.ImageDraw,
    wrapped: str,
    font: ImageFont.ImageFont,
    spacing: int,
) -> tuple[list[str], int]:
    if not wrapped.strip():
        return [], 0
    lines = wrapped.split("\n")
    h = 0
    for i, line in enumerate(lines):
        if line == "":
            bb = draw.textbbox((0, 0), " ", font=font)
            h += bb[3] - bb[1]
        else:
            bb = draw.textbbox((0, 0), line, font=font)
            h += bb[3] - bb[1]
        if i < len(lines) - 1:
            h += spacing
    return lines, h


def _text_width(draw: ImageDraw.ImageDraw, s: str, font: ImageFont.ImageFont) -> int:
    bb = draw.textbbox((0, 0), s, font=font)
    return bb[2] - bb[0]


def _truncate_footer_name(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int
) -> str:
    if _text_width(draw, text, font) <= max_w:
        return text
    ell = "\u2026"
    for cut in range(len(text), -1, -1):
        pref = text[:cut].rstrip()
        cand = pref + ell
        if _text_width(draw, cand, font) <= max_w:
            return cand
    return ell


def _fragment_text_height(
    draw: ImageDraw.ImageDraw,
    fragment: str,
    font: ImageFont.ImageFont,
    inner_wrap_w: int,
    *,
    lead: bool,
    trail: bool,
    spacing: int,
) -> int:
    wi = _wrap_to_width(draw, fragment, font, inner_wrap_w)
    parts = wi.split("\n") if wi else []
    deco = _decorate_quote_lines(parts, prepend_opening=lead, append_closing=trail)
    block = "\n".join(deco)
    _, hgt = _wrapped_lines_heights(draw, block, font, spacing)
    return hgt


def _compose_page_rgba(
    fragment: str,
    *,
    prepend_opening: bool,
    append_closing: bool,
    inner_wrap_w: int,
    region_h: int,
    author_disp: str,
    avatar: Image.Image | None,
    body_font: ImageFont.ImageFont,
) -> Image.Image:
    base = Image.open(_TEMPLATE_PATH).convert("RGBA")
    if avatar is not None:
        _paste_circular_avatar(
            base, avatar, _AVATAR_CX, _AVATAR_CY, _AVATAR_DIAMETER
        )
    draw = ImageDraw.Draw(base)

    footer_font = _load_font(34)
    name_x = _AVATAR_CX + _AVATAR_DIAMETER // 2 + _AUTHOR_GAP_AFTER_AVATAR
    name_max_w = max(120, _FOOTER_NAME_MAX_RIGHT - name_x)
    author_trunc = _truncate_footer_name(draw, author_disp or "кто‑то", footer_font, name_max_w)

    scratch_draw = ImageDraw.Draw(Image.new("RGBA", (W, H)))

    wi = _wrap_to_width(scratch_draw, fragment, body_font, inner_wrap_w)
    inner_parts = wi.split("\n") if wi else []
    body_lines = _decorate_quote_lines(
        inner_parts,
        prepend_opening=prepend_opening,
        append_closing=append_closing,
    )
    _, body_h = _wrapped_lines_heights(
        scratch_draw, "\n".join(body_lines), body_font, _LINE_SPACING
    )

    y = CONTENT_Y0 + max(0, (region_h - body_h) // 2)

    if body_lines:
        for line in body_lines:
            if line == "":
                bb = draw.textbbox((0, 0), " ", font=body_font)
                y += bb[3] - bb[1]
                continue
            lb = draw.textbbox((0, 0), line, font=body_font)
            lw = lb[2] - lb[0]
            lx = (W - lw) // 2
            draw.text((lx, y), line, font=body_font, fill=_WHITE)
            y += lb[3] - lb[1] + _LINE_SPACING
    else:
        fb = ["\u00ab\u2026\u00bb"] if prepend_opening and append_closing else []
        txt = fb[0] if fb else "\u2026"
        lb = draw.textbbox((0, 0), txt, font=body_font)
        draw.text(((W - (lb[2] - lb[0])) // 2, y), txt, font=body_font, fill=_WHITE)

    draw.text(
        (name_x, _AVATAR_CY),
        author_trunc,
        font=footer_font,
        fill=_WHITE,
        anchor="lm",
    )

    return base


def _rgba_to_png_buf(im_rgba: Image.Image) -> io.BytesIO:
    out_rgb = Image.new("RGB", im_rgba.size, (0, 0, 0))
    out_rgb.paste(im_rgba, mask=im_rgba.split()[3])
    buf = io.BytesIO()
    out_rgb.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


def render_quote_pages(
    text: str,
    author_username: str,
    avatar: Image.Image | None = None,
    *,
    quote_number: int | None = None,
    max_pages_hint: int | None = None,
) -> list[io.BytesIO]:
    """
    Одна или несколько плашек PNG. Первая страница начинается с «, последняя заканчивается ».

    max_pages_hint: если передан и после выбора кегля страниц всё ещё больше —
    пробуются меньшие размеры шрифта, пока всё поместится (или упираемся в минимальный кегль).
    Если None — количество страниц не ограничивают (бот потом режет альбомами по 10).
    """
    if not _TEMPLATE_PATH.is_file():
        raise FileNotFoundError(
            f"Нет файла шаблона {_TEMPLATE_PATH}. Положите туда PNG макета 1024×576."
        )

    raw = text[:_MAX_CHARS] if len(text) > _MAX_CHARS else text
    inner = _normalize_inner(raw)
    author_disp = (
        author_username.strip().removeprefix("@") if author_username else "аноним"
    )
    if quote_number is not None:
        author_disp = f"{author_disp}  #{quote_number}"

    max_w = W - 2 * _MARGIN_X
    region_h = CONTENT_Y1 - CONTENT_Y0
    scratch = Image.new("RGBA", (W, H))
    sdraw = ImageDraw.Draw(scratch)

    chosen_font = _load_font(36)
    inner_wrap_w = max(120, max_w - _GU_MEASURE_PADDING)
    pages_specs: list[tuple[str, bool, bool]] = []

    # Сначала крупный шрифт; если страниц не влезает в подсказку — уменьшаем кегль.
    for body_sz in range(42, 11, -2):
        body_font = _load_font(body_sz)
        inner_wrap_w = max(120, max_w - _GU_MEASURE_PADDING)
        pages_try = _paginate_inner_simple(
            inner,
            sdraw,
            inner_wrap_w=inner_wrap_w,
            region_h=region_h,
            font=body_font,
            spacing=_LINE_SPACING,
        )
        if pages_try is None:
            continue
        chosen_font = body_font
        pages_specs = pages_try
        if max_pages_hint is None or len(pages_try) <= max_pages_hint:
            break

    if not pages_specs:
        raise ValueError(
            "Цитату нельзя отрисовать: одно из слов слишком длинное для строки или "
            "нужно увеличить область текста."
        )

    bufs: list[io.BytesIO] = []
    for frag, op, cl in pages_specs:
        rgba = _compose_page_rgba(
            frag,
            prepend_opening=op,
            append_closing=cl,
            inner_wrap_w=inner_wrap_w,
            region_h=region_h,
            author_disp=author_disp,
            avatar=avatar,
            body_font=chosen_font,
        )
        bufs.append(_rgba_to_png_buf(rgba))
    return bufs


def _paginate_inner_simple(
    inner: str,
    draw_meas: ImageDraw.ImageDraw,
    *,
    inner_wrap_w: int,
    region_h: int,
    font: ImageFont.ImageFont,
    spacing: int,
) -> list[tuple[str, bool, bool]] | None:
    """Жадно набиваем страницу токенами (слова и \\n); « только на первой странице, » на последней."""
    if not inner.strip():
        return [("", True, True)]
    tokens = _tokens_from_inner(inner.strip())
    if not tokens:
        return [("", True, True)]

    idx = 0
    doc_started = False
    out_pages: list[tuple[str, bool, bool]] = []

    while idx < len(tokens):
        while idx < len(tokens) and tokens[idx] == "\n":
            idx += 1
        if idx >= len(tokens):
            break

        remainder = len(tokens) - idx
        lo, hi = 1, remainder
        chosen = None
        is_doc_first = not doc_started
        while lo <= hi:
            mid = (lo + hi) // 2
            frag = _slice_tokens_to_fragment(tokens, idx, idx + mid).strip()
            is_final = idx + mid >= len(tokens)
            h = _fragment_text_height(
                draw_meas,
                frag,
                font,
                inner_wrap_w,
                lead=is_doc_first,
                trail=is_final,
                spacing=spacing,
            )
            if h <= region_h:
                chosen = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if chosen is None:
            lone = _slice_tokens_to_fragment(tokens, idx, idx + 1).strip()
            lone_final = idx + 1 >= len(tokens)
            h0 = _fragment_text_height(
                draw_meas,
                lone,
                font,
                inner_wrap_w,
                lead=is_doc_first,
                trail=lone_final,
                spacing=spacing,
            )
            if h0 > region_h:
                return None
            chosen = 1

        frag_ok = _slice_tokens_to_fragment(tokens, idx, idx + chosen).strip()
        is_final_ok = idx + chosen >= len(tokens)
        out_pages.append((frag_ok, is_doc_first, is_final_ok))
        doc_started = True
        idx += chosen

    return out_pages

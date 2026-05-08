#!/usr/bin/env python3
"""Локально рендерит плашки цитаты (без бота): подгон расположения в create_quote (CONTENT_Y0/Y1 и т.д.)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from utils import create_quote as cq
from utils.create_quote import CONTENT_Y0, CONTENT_Y1, render_quote_pages


def _read_text(args: argparse.Namespace) -> str:
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8")
    if args.text is not None:
        return args.text
    print("Нужно указать --text или --text-file.", file=sys.stderr)
    sys.exit(1)


def _output_paths(out_arg: str, page_count: int) -> list[Path]:
    p = Path(out_arg)
    target = (ROOT / p).resolve() if not p.is_absolute() else p.resolve()

    if target.suffix.lower() == ".png":
        if page_count == 1:
            return [target]
        stem = target.with_suffix("")
        return [stem.parent / f"{stem.name}_p{i + 1:02d}.png" for i in range(page_count)]

    target.mkdir(parents=True, exist_ok=True)
    return [target / f"page_{i + 1:02d}.png" for i in range(page_count)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сгенерировать PNG-плашки (render_quote_pages)"
    )
    parser.add_argument("--text", "-t", default=None)
    parser.add_argument("--text-file", "-f", default=None)
    parser.add_argument("--author", "-a", default="Тест Автор")
    parser.add_argument(
        "--out",
        "-o",
        default="scratch/quote_preview",
        help="Каталог (page_01.png…) или файл.png для одной страницы",
    )
    parser.add_argument(
        "--dummy-avatar",
        action="store_true",
        help="Цветная заглушка вместо фото профиля",
    )
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--show-layout", action="store_true")
    args = parser.parse_args()

    if args.show_layout:
        print(
            cq.__file__,
            "| CONTENT_Y0, CONTENT_Y1 =",
            CONTENT_Y0,
            CONTENT_Y1,
        )

    avatar: Image.Image | None = (
        Image.new("RGBA", (256, 256), (140, 90, 160, 255))
        if args.dummy_avatar
        else None
    )

    pages = render_quote_pages(
        _read_text(args),
        args.author,
        avatar=avatar,
        max_pages_hint=args.max_pages,
    )

    outs = _output_paths(args.out, len(pages))
    for bf, dst in zip(pages, outs, strict=True):
        dst.parent.mkdir(parents=True, exist_ok=True)
        bf.seek(0)
        dst.write_bytes(bf.read())
        print(dst)

    print(f"Страниц: {len(pages)}")


if __name__ == "__main__":
    main()

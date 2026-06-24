#!/usr/bin/env python3
"""Convert a PNG image into a multi-resolution Windows ``.ico`` file.

A standalone utility — not tied to any particular project. Pillow is the only
dependency; run it through ``uv`` so Pillow is pulled into a throwaway
environment instead of being installed permanently:

    uv run --with Pillow python scripts/png_to_ico.py input.png

The output path is optional and defaults to the input path with an ``.ico``
extension. Override it positionally:

    uv run --with Pillow python scripts/png_to_ico.py input.png output.ico

The generated ``.ico`` packs several square sizes (16–256 px) so Windows can
pick the right one for the title bar, taskbar, Alt+Tab and Explorer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Standard Windows icon sizes, smallest to largest.
ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def png_to_ico(src: Path, dst: Path) -> None:
    """Write *src* PNG to *dst* as a multi-resolution ICO."""
    try:
        from PIL import Image
    except ModuleNotFoundError:
        sys.exit(
            "Pillow is required. Run via:\n"
            "  uv run --with Pillow python scripts/png_to_ico.py <input.png>"
        )

    if not src.is_file():
        sys.exit(f"Source image not found: {src}")

    img = Image.open(src).convert("RGBA")
    # Only embed sizes that don't upscale beyond the source, but always keep at
    # least the smallest size so the .ico is never empty.
    longest = max(img.size)
    sizes = [s for s in ICON_SIZES if s[0] <= longest] or [ICON_SIZES[0]]

    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="ICO", sizes=sizes)
    embedded = ", ".join(f"{w}px" for w, _ in sizes)
    print(f"Wrote {dst}  ({embedded})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path, help="Source PNG image")
    parser.add_argument(
        "dst",
        nargs="?",
        type=Path,
        default=None,
        help="Output ICO (default: source path with an .ico extension)",
    )
    args = parser.parse_args()
    dst = args.dst if args.dst is not None else args.src.with_suffix(".ico")
    png_to_ico(args.src, dst)


if __name__ == "__main__":
    main()

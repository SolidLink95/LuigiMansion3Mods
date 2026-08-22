"""Convert every PNG next to this script into a 128x128 WEBP.

Scans Path(__file__).parent (non-recursive) for *.png, resizes each to
128x128 (LANCZOS), and writes <name>.webp alongside the original. Existing
.webp files are overwritten; the source PNGs are left untouched.

Usage:  python tools/pngs_to_webp128.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

SIZE = (128, 128)
FOLDER = Path(__file__).parent


def main() -> None:
    pngs = sorted(FOLDER.glob("*.png"))
    if not pngs:
        print(f"no PNG files in {FOLDER}")
        return
    converted = failed = 0
    for png in pngs:
        target = png.with_suffix(".webp")
        try:
            with Image.open(png) as image:
                image.convert("RGBA").resize(SIZE, Image.Resampling.LANCZOS).save(
                    target, "WEBP"
                )
        except Exception as error:
            print(f"failed: {png.name} ({error})")
            failed += 1
            continue
        converted += 1
        print(f"{png.name} -> {target.name}")
    print(f"\ndone: {converted} converted, {failed} failed")


if __name__ == "__main__":
    main()

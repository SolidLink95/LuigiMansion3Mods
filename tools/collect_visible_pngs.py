"""Collect PNGs that are at least 5% visible.

Recursively finds *.png under the given root (default: current directory),
keeps every image where at least 5% of pixels are not fully transparent
(alpha > 0; images without an alpha channel always qualify), and copies the
keepers into ./res next to this script.

Usage:  python tools/collect_visible_pngs.py [root_dir]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

MIN_VISIBLE_RATIO = 0.05
RES = Path(__file__).parent / "res"


def visible_ratio(png: Path) -> float:
    with Image.open(png) as image:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        histogram = alpha.histogram()
    total = sum(histogram)
    if not total:
        return 0.0
    return 1.0 - histogram[0] / total


def main() -> None:
    root = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.cwd()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    RES.mkdir(parents=True, exist_ok=True)
    copied = skipped = failed = 0
    for png in sorted(root.rglob("*.png")):
        if RES in png.parents:
            continue  # never re-collect our own output
        try:
            ratio = visible_ratio(png)
        except Exception as error:
            print(f"unreadable, skipped: {png} ({error})")
            failed += 1
            continue
        if ratio >= MIN_VISIBLE_RATIO:
            shutil.copy2(png, RES / png.name)
            copied += 1
            print(f"copied {png} ({ratio:.1%} visible)")
        else:
            skipped += 1
    print(f"\ndone: {copied} copied to {RES}, {skipped} below "
          f"{MIN_VISIBLE_RATIO:.0%}, {failed} unreadable")


if __name__ == "__main__":
    main()

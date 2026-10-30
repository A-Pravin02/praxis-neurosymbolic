"""Crop the repeated sidebar out of the demo screenshots used in the report.

    python scripts/crop_screenshots.py

The browser pane captures at 800x550, and that cannot be raised from here, so the only
way to make the interface legible in print is to spend the pixels on the part that
carries the argument. In four of the five screenshots the left fifth is the same list
of sample questions, already shown whole in the landing figure, and the panel that
matters - candidate goals, symbolic facts, the verification checks, the released answer
- is squeezed into what is left.

Cropping that column out enlarges the panel by about a quarter at the same printed
width. Originals are left untouched; the crops are written beside them with a
`-cropped` suffix, so the report can be rebuilt from either.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs/screenshots"

# x where the sidebar's scrollbar ends and the detail panel begins, measured off the
# captures rather than guessed. The landing shot is deliberately absent: its whole
# point is the two-pane layout.
SIDEBAR_END = 160
# The app's title bar spans the full width, so cropping the sidebar away leaves its
# last few characters stranded at the top ("...fication layer"). The bar carries no
# information these figures need, so it goes with the sidebar.
HEADER_END = 22
TARGETS = ("02-case-neural-symbolic.jpg", "03-verification-release.jpg",
           "04-sample-suite.jpg", "05-not-covered-failure.jpg")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    from PIL import Image

    for name in TARGETS:
        source = SHOTS / name
        if not source.exists():
            print(f"  missing {name}")
            continue
        image = Image.open(source)
        width, height = image.size
        if width <= SIDEBAR_END:
            print(f"  {name}: too narrow to crop")
            continue
        cropped = image.crop((SIDEBAR_END, HEADER_END, width, height))
        target = source.with_name(f"{source.stem}-cropped.png")
        cropped.save(target)
        gain = width / cropped.size[0]
        print(f"  {name}: {width}x{height} -> {cropped.size[0]}x{cropped.size[1]} "
              f"({gain:.2f}x larger at the same printed width)")


if __name__ == "__main__":
    main()

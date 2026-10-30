"""Render each page of a PDF to a PNG, for visual inspection.

    python scripts/render_pdf_pages.py docs/PRAXIS_FINAL_REPORT.pdf --out <dir>

Text extraction is not enough to pass a document for submission. It cannot see an
overlapping element, a figure pushed off its caption, type that came out too small to
read, or a glyph that failed to embed - and it introduces artefacts of its own, so a
character that looks wrong in extracted text may be perfectly fine on the page. Looking
at the rendered page settles both directions.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=None, help="directory for the PNGs")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--pages", default=None, help="e.g. 1-4,9 (default: all)")
    args = ap.parse_args()

    import pymupdf

    pdf = pathlib.Path(args.pdf)
    out = pathlib.Path(args.out) if args.out else pdf.parent / f"{pdf.stem}_pages"
    out.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(pdf)
    wanted: set[int] | None = None
    if args.pages:
        wanted = set()
        for part in args.pages.split(","):
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-"))
                wanted.update(range(lo, hi + 1))
            else:
                wanted.add(int(part))

    written = []
    for index, page in enumerate(doc, 1):
        if wanted and index not in wanted:
            continue
        target = out / f"page-{index:02d}.png"
        page.get_pixmap(dpi=args.dpi).save(target)
        written.append(target)
    doc.close()

    print(f"{len(written)} page(s) -> {out}")
    for path in written:
        print(f"  {path}")


if __name__ == "__main__":
    main()

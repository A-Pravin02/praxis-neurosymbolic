"""Rebuild the paper from the records, end to end.

    python scripts/build_paper.py              # figures, document, and the checks
    python scripts/build_paper.py --pdf        # also export a PDF (Windows + Word)
    python scripts/build_paper.py --check-only # verify the inputs and stop

This is the one command behind every figure, table and number in
`docs/paper/PRAXIS_IEEE.docx`. Each stage is a separate script that can also be run on
its own; running them here fixes the order, because the document embeds the figures and
the claim check reads the built document.

    1  check_paper.py             inputs present, every figure has a generating script
    2  make_architecture_figure.py   Fig. 1, with its own type-size and placement audit
    3  make_results_figure.py        Fig. 2, from the test-split report
    4  build_paper_docx.py           the document, every number read from the records
    5  export_pdf.ps1                PDF and page count          (--pdf, Windows only)
    6  verify_claims.py              every claim in the built document, against the data
    7  independent_metrics.py        every metric, recomputed from the raw records

Nothing here estimates, caches or falls back. A stage that cannot do its job stops the
build, because a paper that was built around a missing input is worse than no paper:
the gap is invisible in the output.

PDF export needs Microsoft Word through COM, so it is opt-in and skipped with a note
anywhere else. The .docx is the artifact; the PDF is a rendering of it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time

from _paths import ROOT, show

PYTHON = sys.executable
TEMPLATE = ROOT / "docs/paper/templates/ieee_a4_transitional.docx"
DOCX = ROOT / "docs/paper/PRAXIS_IEEE.docx"


class Stage:
    """One step of the build, and whether it is allowed to fail."""

    def __init__(self, name: str, argv: list[str], *, optional: bool = False) -> None:
        self.name, self.argv, self.optional = name, argv, optional

    def run(self) -> bool:
        print(f"\n--- {self.name} " + "-" * max(0, 58 - len(self.name)))
        started = time.perf_counter()
        try:
            result = subprocess.run(self.argv, cwd=ROOT, check=False)
        except FileNotFoundError:
            print(f"  cannot run {self.argv[0]}")
            return self.optional
        elapsed = time.perf_counter() - started
        if result.returncode == 0:
            print(f"  [{elapsed:.1f}s] ok")
            return True
        print(f"  [{elapsed:.1f}s] exit {result.returncode}")
        return self.optional


def stages(pdf: bool) -> list[Stage]:
    out: list[Stage] = [
        Stage("check inputs", [PYTHON, "scripts/check_paper.py"]),
        # --audit is not decoration: it fails the build if a label would print below
        # six points, if a label is wider than its box, or if the width the paper
        # places the figure at stops matching the width it was drawn for.
        Stage("Fig. 1  architecture",
              [PYTHON, "scripts/make_architecture_figure.py", "--audit"]),
        Stage("Fig. 2  main results", [PYTHON, "scripts/make_results_figure.py"]),
        Stage("document", [PYTHON, "scripts/build_paper_docx.py",
                           "--template", str(TEMPLATE), "--out", str(DOCX)]),
    ]
    if pdf:
        if sys.platform != "win32":
            print("note: --pdf needs Word through COM; skipping on this platform")
        else:
            out.append(Stage("PDF export", [
                "powershell", "-ExecutionPolicy", "Bypass", "-File",
                "scripts/export_pdf.ps1", str(DOCX)]))
    out += [
        Stage("claims vs artifacts", [PYTHON, "scripts/verify_claims.py"]),
        Stage("metrics vs raw records", [PYTHON, "scripts/independent_metrics.py"]),
    ]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", action="store_true",
                    help="also export a PDF and report the page count (Windows + Word)")
    ap.add_argument("--check-only", action="store_true",
                    help="verify the build inputs and stop without writing anything")
    args = ap.parse_args()

    print(f"repository: {ROOT}")
    print(f"python    : {PYTHON}")

    if args.check_only:
        sys.exit(subprocess.run([PYTHON, "scripts/check_paper.py"],
                                cwd=ROOT, check=False).returncode)

    failed = [s.name for s in stages(args.pdf) if not s.run()]
    print("\n" + "=" * 64)
    if failed:
        print(f"build FAILED at: {', '.join(failed)}")
        sys.exit(1)
    print(f"built {show(DOCX)}")
    print("every figure regenerated from its script, every number from the records")


if __name__ == "__main__":
    main()

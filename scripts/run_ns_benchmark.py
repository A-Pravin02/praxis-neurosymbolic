"""Run the neuro-symbolic benchmark and write JSON + Markdown reports.

    python scripts/run_ns_benchmark.py                       # all systems, test split
    python scripts/run_ns_benchmark.py --systems neurosymbolic
    python scripts/run_ns_benchmark.py --split dev           # the calibration set
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
warnings.filterwarnings("ignore")

from app.evaluation.ns_benchmark import render_markdown, run_benchmark  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--systems", default="")
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=str(ROOT / "eval_reports"))
    parser.add_argument("--pack", default=None,
                        help="knowledge pack directory; defaults to the shipped one")
    args = parser.parse_args()

    report = run_benchmark(systems=[s for s in args.systems.split(",") if s] or None,
                           limit=args.limit, split=args.split, pack_dir=args.pack)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"ns-benchmark-{report['pack']}-{args.split}-{stamp}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=1, default=str),
                                         encoding="utf-8")
    markdown = render_markdown(report)
    base.with_suffix(".md").write_text(markdown, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(markdown)
    print(f"wrote {base}.json and .md")


if __name__ == "__main__":
    main()

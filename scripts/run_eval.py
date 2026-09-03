#!/usr/bin/env python
"""Run the evaluation harness and write a report.

    python scripts/run_eval.py [suite] [--modes a,b] [--limit N]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.core.logging import configure_logging  # noqa: E402
from app.evaluation import harness  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", nargs="?", default="demo")
    parser.add_argument("--modes", default=",".join(harness.MODES))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default="eval_reports")
    args = parser.parse_args()

    configure_logging()
    report = harness.run_suite(args.suite, args.modes.split(","), args.limit)
    paths = harness.write_report(report, args.out)
    print(harness.render_markdown(report))
    print(f"\nWritten: {paths['markdown']}")

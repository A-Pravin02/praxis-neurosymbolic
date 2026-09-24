"""Calibration of the computed confidence score.

The system never asks the model how sure it is: confidence is computed in code from
retrieval relevance, rule coverage and verification outcome. That is a claim about the
design, and this script is what turns it into a measurement. It answers one question:

    when the system says 0.8, is it right about 80% of the time?

Inputs are benchmark reports written by ``scripts/run_ns_benchmark.py``, which record a
``confidence`` alongside ``correct`` for every case. Confidence is never used in scoring;
it is read here, afterwards, and nowhere else.

Reported:

  ECE   expected calibration error, the mean gap between confidence and accuracy,
        weighted by how many cases fall in each bin. 0 is perfect.
  MCE   the worst single bin's gap.
  Brier the mean squared error of confidence against correctness.

Usage:

    python scripts/calibration.py eval_reports/ns-benchmark-test-<stamp>.json
    python scripts/calibration.py <report.json> --system neurosymbolic --bins 10
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def bins_for(cases: list[dict], n_bins: int) -> list[dict]:
    """Equal-width bins over [0, 1]. A bin with no case is reported empty, not skipped:
    an absent region of the confidence range is itself a finding."""
    out = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        # The last bin is closed so a confidence of exactly 1.0 lands somewhere.
        in_bin = [c for c in cases
                  if (lo <= c["confidence"] < hi) or (i == n_bins - 1 and c["confidence"] == 1.0)]
        if in_bin:
            acc = sum(1 for c in in_bin if c["correct"]) / len(in_bin)
            conf = sum(c["confidence"] for c in in_bin) / len(in_bin)
        else:
            acc = conf = None
        out.append({"lo": lo, "hi": hi, "n": len(in_bin), "accuracy": acc, "confidence": conf})
    return out


def metrics(cases: list[dict], n_bins: int) -> dict:
    total = len(cases)
    buckets = bins_for(cases, n_bins)
    ece = sum(b["n"] / total * abs(b["accuracy"] - b["confidence"])
              for b in buckets if b["n"])
    mce = max((abs(b["accuracy"] - b["confidence"]) for b in buckets if b["n"]), default=0.0)
    brier = sum((c["confidence"] - (1.0 if c["correct"] else 0.0)) ** 2 for c in cases) / total
    return {"n": total, "ece": ece, "mce": mce, "brier": brier, "bins": buckets,
            "accuracy": sum(1 for c in cases if c["correct"]) / total,
            "mean_confidence": sum(c["confidence"] for c in cases) / total}


def load(report: Path, system: str, answered_only: bool = False) -> list[dict]:
    data = json.loads(report.read_text(encoding="utf-8"))
    if system not in data["systems"]:
        raise SystemExit(f"{system!r} not in report; have: {', '.join(data['systems'])}")
    cases = data["systems"][system]["cases"]
    if answered_only:
        # An abstention reports confidence 0, but 0 there means "I am not answering",
        # not "this answer is almost certainly wrong". Mixing the two measures a
        # refusal policy as if it were a probability. Report both, separately.
        cases = [c for c in cases if c.get("outcome") == "answered"]
    scored = [c for c in cases if c.get("confidence") is not None]
    if not scored:
        raise SystemExit(
            f"No case in {report.name} records a confidence. Reports written before "
            "confidence was recorded cannot be calibrated; re-run the benchmark.")
    return scored


def render(name: str, split: str, m: dict, skipped: int) -> str:
    lines = [f"# Calibration — {name} ({split} split, {m['n']} scored cases)", "",
             f"Accuracy {m['accuracy']:.3f} · mean confidence {m['mean_confidence']:.3f} · "
             f"**ECE {m['ece']:.3f}** · MCE {m['mce']:.3f} · Brier {m['brier']:.3f}", ""]
    gap = m["mean_confidence"] - m["accuracy"]
    lines += [f"Mean confidence is {'above' if gap > 0 else 'below'} accuracy by "
              f"{abs(gap):.3f} — the system is "
              f"{'over' if gap > 0 else 'under'}confident on average.", ""]
    if skipped:
        lines += [f"{skipped} case(s) carried no confidence and are excluded.", ""]
    lines += ["| Confidence bin | Cases | Mean confidence | Accuracy | Gap |",
              "|---|---|---|---|---|"]
    for b in m["bins"]:
        if not b["n"]:
            lines.append(f"| {b['lo']:.1f}–{b['hi']:.1f} | 0 | — | — | — |")
            continue
        lines.append(f"| {b['lo']:.1f}–{b['hi']:.1f} | {b['n']} | {b['confidence']:.3f} | "
                     f"{b['accuracy']:.3f} | {b['accuracy'] - b['confidence']:+.3f} |")
    lines += ["", "A well-calibrated system has gaps near zero across bins that hold cases.",
              "Confidence is computed in code and is never read by any scoring metric."]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--system", default="neurosymbolic")
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--answered-only", action="store_true",
                        help="exclude abstentions, whose confidence 0 is a refusal, not a probability")
    args = parser.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    cases = load(args.report, args.system, args.answered_only)
    total = len(data["systems"][args.system]["cases"])
    label = args.system + (" (answered only)" if args.answered_only else "")
    text = render(label, data.get("split", "?"), metrics(cases, args.bins),
                  total - len(cases))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    print(text)


if __name__ == "__main__":
    main()

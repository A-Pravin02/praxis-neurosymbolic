"""Measure candidate relevance signals before building a gate on one of them.

    python scripts/relevance_signal.py

Five earlier attempts at the not-covered failure were wrong (commit 2706a80), and
the most promising died the same way: a threshold fitted on the generated worlds,
where questions are phrased formulaically, fired on a quarter of the colloquial
Meridian questions. Any signal considered here is therefore measured on both.

The hypothesis under test is that absolute similarity is the wrong quantity.
Colloquial phrasing lowers the score of *every* goal together, so an absolute
floor cannot tell "phrased unusually" from "not covered". A signal standardised
within each question's own score distribution - how far the best goal stands out
from the rest of the catalogue - should be insensitive to that shift, because the
shift is common to all goals.

Reports each signal's separation between covered and not-covered questions.
Nothing here changes the system; it decides whether there is anything to build on.
"""
from __future__ import annotations

import pathlib
import statistics
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
sys.path.insert(0, str(BACKEND))


def signals(detector, question: str) -> dict[str, float]:
    """Every candidate signal, from one scoring pass over the whole catalogue."""
    cat = detector._catalogue
    scores = [float(s) for s in
              detector.encoder.similarity([question], [c.description for c in cat])[0]]
    ranked = sorted(scores, reverse=True)
    top1, top2 = ranked[0], ranked[1] if len(ranked) > 1 else 0.0
    mean = statistics.fmean(scores)
    stdev = statistics.pstdev(scores) or 1e-9
    return {
        "top1": top1,
        "margin_12": top1 - top2,
        "top1_minus_mean": top1 - mean,
        # Standardised: a phrasing shift moves every goal together and cancels.
        "z_top1": (top1 - mean) / stdev,
        "z_margin": (top1 - top2) / stdev,
    }


def summarise(label: str, rows: list[tuple[str, dict]], keys: list[str]) -> None:
    covered = [s for tag, s in rows if tag == "covered"]
    missing = [s for tag, s in rows if tag == "not_covered"]
    if not missing:
        print(f"\n{label}: {len(covered)} covered, no not-covered cases")
        for k in keys:
            vals = [c[k] for c in covered]
            print(f"  {k:18} covered min {min(vals):7.3f}  p05 "
                  f"{sorted(vals)[max(0, len(vals)//20)]:7.3f}  mean "
                  f"{statistics.fmean(vals):7.3f}")
        return
    print(f"\n{label}: {len(covered)} covered, {len(missing)} not-covered")
    print(f"  {'signal':18}{'covered mean':>14}{'nc mean':>10}{'covered p05':>13}"
          f"{'nc p95':>9}{'separated':>11}")
    for k in keys:
        cv = sorted(c[k] for c in covered)
        nc = sorted(m[k] for m in missing)
        p05 = cv[max(0, len(cv) // 20)]
        p95 = nc[min(len(nc) - 1, int(len(nc) * 0.95))]
        # Separated means a cut exists that keeps every covered case and still
        # catches most not-covered ones.
        clean = p05 > p95
        print(f"  {k:18}{statistics.fmean(cv):14.3f}{statistics.fmean(nc):10.3f}"
              f"{p05:13.3f}{p95:9.3f}{'  YES' if clean else '   no':>11}")


def main() -> None:
    from app.hybrid.knowledge_pack import load_pack
    from app.neural.intent_detection import IntentDetector

    keys = ["top1", "margin_12", "top1_minus_mean", "z_top1", "z_margin"]

    # ---- generated worlds, frozen test split --------------------------------
    cases = yaml.safe_load((WORLDS / "test.yaml").read_text(encoding="utf-8"))["cases"]
    detectors: dict[int, object] = {}
    rows: list[tuple[str, dict]] = []
    per_cat: dict[str, list[dict]] = {}
    for i, case in enumerate(cases, 1):
        w = case["world"]
        if w not in detectors:
            pack = load_pack(str(WORLDS / f"world-{w}"), "semantic")
            detectors[w] = IntentDetector(pack.program, pack.encoder)
        s = signals(detectors[w], case["question"])
        tag = "not_covered" if case["category"] == "not_covered" else "covered"
        rows.append((tag, s))
        per_cat.setdefault(case["category"], []).append(s)
        if i % 60 == 0:
            print(f"  {i}/{len(cases)} generated", flush=True)
    summarise("GENERATED test split", rows, keys)

    print(f"\n  per category, z_top1:")
    for cat in sorted(per_cat, key=lambda c: statistics.fmean(x["z_top1"]
                                                              for x in per_cat[c])):
        vals = [x["z_top1"] for x in per_cat[cat]]
        print(f"    {cat:16} n={len(vals):3}  mean {statistics.fmean(vals):6.3f}  "
              f"min {min(vals):6.3f}  max {max(vals):6.3f}")

    # ---- curated Meridian, colloquial phrasing ------------------------------
    mer = BACKEND / "data/meridian"
    mer_cases = yaml.safe_load(
        (mer / "benchmark/test.yaml").read_text(encoding="utf-8"))
    mer_cases = mer_cases["cases"] if isinstance(mer_cases, dict) else mer_cases
    pack = load_pack(str(mer), "semantic")
    det = IntentDetector(pack.program, pack.encoder)
    mrows: list[tuple[str, dict]] = []
    mcat: dict[str, list[dict]] = {}
    for case in mer_cases:
        q = case.get("question")
        if not q:
            continue
        s = signals(det, q)
        cat = case.get("category", "?")
        mcat.setdefault(cat, []).append(s)
        mrows.append(("covered", s))
    summarise("CURATED Meridian test (all treated as covered)", mrows, keys)
    print(f"\n  per category, z_top1:")
    for cat in sorted(mcat, key=lambda c: statistics.fmean(x["z_top1"]
                                                           for x in mcat[c])):
        vals = [x["z_top1"] for x in mcat[cat]]
        print(f"    {cat:18} n={len(vals):3}  mean {statistics.fmean(vals):6.3f}  "
              f"min {min(vals):6.3f}")


if __name__ == "__main__":
    main()

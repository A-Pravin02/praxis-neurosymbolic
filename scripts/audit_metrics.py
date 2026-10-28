"""Recompute every reported metric from the per-question records.

    python scripts/audit_metrics.py

The reviewer asked whether accuracy, hallucination, coverage and selective accuracy are
mutually consistent, since a system cannot obviously be 0.945 accurate and 1.000
selectively accurate at once. They can be, but only under specific denominators, and
asserting that is not the same as checking it. This script recomputes each metric from
`eval_reports/research/<tag>/<split>.<system>.jsonl` and compares against the published
report, printing any disagreement.

Definitions recovered from `app/evaluation/research.py`:

  accuracy            correct / ALL cases. A `not_covered` case is correct only when
                      the system reports insufficient evidence, so refusals count here.
  hallucination       (wrong definite OR unsupported definite OR disclosure) / all cases.
  coverage            definite / answerable, where answerable is the whitelist
                      ("decision", "value", "result"). `conditional`, `denied` and
                      `not_covered` cases are excluded from the denominator entirely.
  selective accuracy  correct / definite, over answerable cases only.

Those three denominators differ, which is exactly why the numbers can look
incompatible while being correct.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOT_COVERED = "not_covered"
# The harness's whitelist, copied from app/evaluation/research.py. Kinds outside it
# (conditional, denied, not_covered) are excluded from coverage and selective accuracy.
ANSWERABLE = ("decision", "value", "result")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def recompute(rows: list[dict]) -> dict:
    """Mirror `research.summarise` exactly.

    A first version of this audit assumed "answerable" meant anything not
    `not_covered`, and "committed" meant not abstained. Both were wrong, and the audit
    accordingly disagreed with the published report on 26 values. The harness uses a
    whitelist of gold kinds, so `conditional` and `denied` cases are excluded from
    coverage entirely, and it keys coverage on `definite` rather than on the negation
    of `abstained` - a case can be non-abstaining without being definite. The published
    numbers were right; the audit was not.
    """
    n = len(rows)
    answerable = [r for r in rows if r.get("gold_kind") in ANSWERABLE]
    committed = [r for r in answerable if r.get("definite")]
    correct_committed = [r for r in committed if r.get("correct")]
    return {
        "cases": n,
        "answerable": len(answerable),
        "committed": len(committed),
        "accuracy": sum(bool(r.get("correct")) for r in rows) / n if n else None,
        "hallucination_rate": sum(bool(r.get("hallucinated")) for r in rows) / n if n else None,
        "coverage": len(committed) / len(answerable) if answerable else None,
        "selective_accuracy": (len(correct_committed) / len(committed)
                               if committed else None),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="paper")
    ap.add_argument("--split", default="test")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args()

    base = args.dir or ROOT / "eval_reports/research" / args.tag
    report_path = base / f"{args.split}.report.json"
    published = (json.loads(report_path.read_text(encoding="utf-8"))["systems"]
                 if report_path.exists() else {})

    print(f"auditing {base}  split={args.split}\n")
    header = (f"{'system':26s} {'n':>4s} {'answ':>5s} {'comm':>5s} "
              f"{'accuracy':>18s} {'halluc':>18s} {'coverage':>18s} {'sel.acc':>18s}")
    print(header)
    print("-" * len(header))

    disagreements = []
    for path in sorted(base.glob(f"{args.split}.*.jsonl")):
        system = path.stem.split(".", 1)[1]
        rows = load(path)
        got = recompute(rows)
        pub = published.get(system, {}).get("overall", {})

        cells = []
        for key in ("accuracy", "hallucination_rate", "coverage", "selective_accuracy"):
            mine = got[key]
            entry = pub.get(key)
            theirs = entry.get("value") if isinstance(entry, dict) else entry
            if mine is None:
                cells.append(f"{'n/a':>18s}")
                continue
            if theirs is None:
                cells.append(f"{mine:>10.4f} {'(new)':>7s}")
                continue
            ok = abs(mine - theirs) < 5e-4
            cells.append(f"{mine:>10.4f} {'match' if ok else f'!={theirs:.4f}':>7s}")
            if not ok:
                disagreements.append((system, key, mine, theirs))
        print(f"{system:26s} {got['cases']:4d} {got['answerable']:5d} "
              f"{got['committed']:5d} " + " ".join(cells))

    print()
    if disagreements:
        print(f"{len(disagreements)} DISAGREEMENT(S) between records and published report:")
        for system, key, mine, theirs in disagreements:
            print(f"  {system}.{key}: recomputed {mine:.4f}, published {theirs:.4f}")
    else:
        print("every published value reproduces from the per-question records.")

    # The consistency question the review raised, answered arithmetically.
    ns = base / f"{args.split}.ns_parser.jsonl"
    if ns.exists():
        rows = load(ns)
        g = recompute(rows)
        nc = [r for r in rows if r.get("gold_kind") == NOT_COVERED]
        other = [r for r in rows if r.get("gold_kind") not in ANSWERABLE
                 and r.get("gold_kind") != NOT_COVERED]
        nc_ok = sum(bool(r.get("correct")) for r in nc)
        print(f"\nwhy ns_parser can be {g['accuracy']:.3f} accurate and "
              f"{g['selective_accuracy']:.3f} selectively accurate:")
        print(f"  {g['cases']} cases = {g['answerable']} answerable "
              f"+ {len(nc)} not-covered + {len(other)} conditional/denied")
        print(f"  answerable: {g['committed']} committed, all correct "
              f"-> selective accuracy {g['selective_accuracy']:.3f}")
        print(f"  not-covered: {nc_ok}/{len(nc)} correctly refused")
        print(f"  accuracy = (correct answerable + correct refusals) / all cases "
              f"= {sum(bool(r.get('correct')) for r in rows)}/{g['cases']} "
              f"= {g['accuracy']:.3f}")
        print("  The denominators differ, which is why the figures look incompatible "
              "and are not.")


if __name__ == "__main__":
    main()

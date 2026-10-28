"""Recompute every published metric from the raw per-question records.

    python scripts/independent_metrics.py

`scripts/audit_metrics.py` mirrors `app.evaluation.research.summarise`, so if the
summariser and the paper's written definitions disagreed, both would agree with
each other and the audit would pass. This script instead implements the
definitions as the paper states them, in Section VI, and compares the result to
the stored report:

    N  all cases in the split
    A  answerable cases - gold kind is a decision, a value or a result
    D  the subset of A answered definitely

    accuracy           correct / N
    hallucination      (wrong definite | unsupported definite | disclosure) / N
    coverage           |D| / |A|
    selective accuracy correct among D
    over-abstention    (|A| - |D|) / |A|
    disclosure         leaks among the denied cases

Exits non-zero on any mismatch.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ANSWERABLE = ("decision", "value", "result")
TOL = 5e-4          # the paper prints three decimals


def load(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def recompute(rows: list[dict]) -> dict[str, float | None]:
    n = len(rows)
    answerable = [r for r in rows if r["gold_kind"] in ANSWERABLE]
    definite = [r for r in answerable if r["definite"]]
    denied = [r for r in rows if r["gold_kind"] == "denied"]

    def share(num: int, den: int) -> float | None:
        return num / den if den else None

    return {
        "accuracy": share(sum(1 for r in rows if r["correct"]), n),
        # The paper defines hallucination as the union of three disjoint faults.
        # Recomputed from the three components rather than from the stored flag.
        "hallucination_rate": share(
            sum(1 for r in rows if r["wrong_definite"] or r["unsupported_definite"]
                or r["leak"]), n),
        "wrong_definite_rate": share(sum(1 for r in rows if r["wrong_definite"]), n),
        "unsupported_definite_rate": share(
            sum(1 for r in rows if r["unsupported_definite"]), n),
        "coverage": share(len(definite), len(answerable)),
        "selective_accuracy": share(sum(1 for r in definite if r["correct"]),
                                    len(definite)),
        "over_abstention": share(len(answerable) - len(definite), len(answerable)),
        "leak_rate": share(sum(1 for r in denied if r["leak"]), len(denied)),
    }


def main() -> None:
    splits = (("test", ROOT / "eval_reports/research/paper"),
              ("confirm", ROOT / "eval_reports/research/confirm"))
    checked = mismatched = 0
    problems: list[str] = []

    for split, base in splits:
        report_path = base / f"{split}.report.json"
        if not report_path.exists():
            problems.append(f"{split}: missing {report_path.name}")
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        print(f"\n=== {split} split ===")
        for system, stored in sorted(report["systems"].items()):
            path = base / f"{split}.{system}.jsonl"
            if not path.exists():
                problems.append(f"{split}/{system}: records missing")
                continue
            mine = recompute(load(path))
            bad = []
            for key, value in mine.items():
                entry = stored["overall"].get(key)
                theirs = entry.get("value") if isinstance(entry, dict) else entry
                if value is None and theirs is None:
                    continue
                checked += 1
                if value is None or theirs is None or abs(value - theirs) > TOL:
                    mismatched += 1
                    bad.append(f"{key}: mine={value} stored={theirs}")
            flag = "ok" if not bad else "MISMATCH"
            print(f"  {system:24} {flag}")
            for b in bad:
                print(f"      {b}")
                problems.append(f"{split}/{system} {b}")

    # ---- the worked example in Section VI, recomputed term by term ----
    parser = base_parser = ROOT / "eval_reports/research/paper/test.ns_parser.jsonl"
    if parser.exists():
        rows = load(base_parser)
        answerable = [r for r in rows if r["gold_kind"] in ANSWERABLE]
        definite = [r for r in answerable if r["definite"]]
        terms = {
            "answerable (decision|value|result)": len(answerable),
            "  of those definite": len(definite),
            "  of those definite and correct": sum(1 for r in definite if r["correct"]),
            "conditional, correct": sum(1 for r in rows if r["gold_kind"] == "conditional"
                                        and r["correct"]),
            "denied, correct": sum(1 for r in rows if r["gold_kind"] == "denied"
                                   and r["correct"]),
            "not_covered, correctly refused": sum(1 for r in rows
                                                  if r["gold_kind"] == "not_covered"
                                                  and r["correct"]),
        }
        print("\n=== Section VI worked example, parser configuration ===")
        for label, value in terms.items():
            print(f"  {label:36} {value:4}")
        total = (terms["  of those definite and correct"] + terms["conditional, correct"]
                 + terms["denied, correct"] + terms["not_covered, correctly refused"])
        direct = sum(1 for r in rows if r["correct"])
        print(f"  {'sum of the four correct groups':36} {total:4}")
        print(f"  {'cases with correct == true':36} {direct:4}")
        print(f"  {'accuracy':36} {total}/{len(rows)} = {total/len(rows):.4f}")
        if total != direct:
            problems.append(f"worked example sums to {total} but {direct} records "
                            f"are marked correct")

    print(f"\n{checked} values recomputed from the records, {mismatched} mismatched")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("every published value reproduces from the raw records under the "
          "definitions as written")


if __name__ == "__main__":
    main()

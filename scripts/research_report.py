"""Aggregate every benchmark run into one results file the paper is written from.

    python scripts/research_report.py --split test

Reads the per-case rows every run appended (eval_reports/research/<tag>/<split>.<system>.jsonl),
recomputes every metric from them - never from a cached summary - and writes
docs/paper/results/<split>.json with, per system and model:

  * overall metrics with 95% bootstrap intervals, by category and by world
  * exact McNemar tests of each system against the full system on the same model
  * ablation deltas: each ablated configuration minus the full one, with a paired
    bootstrap interval on the difference

A system is identified as <system>@<tag>; the tag names the model the run used
(`deterministic` for systems that use none).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evaluation import research  # noqa: E402

RUNS = ROOT / "eval_reports" / "research"
OUT = ROOT / "docs" / "paper" / "results"


def load_rows(split: str, tags: list[str] | None) -> dict[str, list[dict]]:
    cases = {c["id"] for c in research.load_frozen(split)}
    found: dict[str, list[dict]] = {}
    for tag_dir in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        if tags and tag_dir.name not in tags:
            continue
        for path in sorted(tag_dir.glob(f"{split}.*.jsonl")):
            system = path.name[len(split) + 1:-len(".jsonl")]
            rows = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["case"] in cases:
                    rows[row["case"]] = row
            if len(rows) == len(cases):     # only complete runs are reported
                found[f"{system}@{tag_dir.name}"] = [rows[c] for c in sorted(rows)]
            else:
                print(f"skipping incomplete run {system}@{tag_dir.name}: "
                      f"{len(rows)}/{len(cases)} cases")
    return found


def paired_delta(base: list[dict], other: list[dict], key: str, seed: int = 11) -> dict:
    a = [float(r[key]) for r in base]
    b = [float(r[key]) for r in other]
    n = len(a)
    diff = sum(b) / n - sum(a) / n
    rng = random.Random(seed)
    samples = []
    for _ in range(research.BOOTSTRAP_SAMPLES):
        idx = [rng.randrange(n) for _ in range(n)]
        samples.append(sum(b[i] - a[i] for i in idx) / n)
    samples.sort()
    return {"delta": round(diff, 4),
            "ci95": [round(samples[int(0.025 * len(samples))], 4),
                     round(samples[int(0.975 * len(samples)) - 1], 4)]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    parser.add_argument("--tags", nargs="*")
    args = parser.parse_args()
    runs = load_rows(args.split, args.tags)
    results: dict = {"split": args.split, "cases": len(research.load_frozen(args.split)),
                     "systems": {}, "comparisons": {}, "ablations": {}}
    for key, rows in runs.items():
        categories = sorted({r["category"] for r in rows})
        results["systems"][key] = {
            "overall": research.summarise(rows),
            "by_category": {c: research.summarise([r for r in rows if r["category"] == c])
                            for c in categories},
            "by_world": {str(w): research.summarise([r for r in rows if r["world"] == w])
                         for w in sorted({r["world"] for r in rows})},
        }
    # Comparisons are made within a model: the full parser configuration against every
    # other system that used the same model, and the offline system against everything.
    references = [k for k in runs if k.startswith("ns_parser@")] + \
        [k for k in runs if k.startswith("neurosymbolic@")]
    for reference in references:
        tag = reference.split("@", 1)[1]
        base = runs[reference]
        for key, rows in runs.items():
            if key == reference or (not reference.startswith("neurosymbolic@")
                                    and key.split("@", 1)[1] not in (tag, "deterministic")):
                continue
            results["comparisons"].setdefault(reference, {})[key] = {
                metric: research.mcnemar([r[metric] for r in base], [r[metric] for r in rows])
                for metric in ("correct", "hallucinated")}
        for key, rows in runs.items():
            system = key.split("@", 1)[0]
            prefix = "ns_parser_" if reference.startswith("ns_parser@") else "ns_"
            if key.split("@", 1)[1] != tag or not system.startswith(prefix) or key == reference:
                continue
            if system in ("ns_parser_llm",):
                continue
            results["ablations"].setdefault(reference, {})[system] = {
                metric: paired_delta(base, rows, metric)
                for metric in ("correct", "hallucinated", "leak", "definite")}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.split}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {path} with {len(runs)} runs")
    for key in sorted(results["systems"]):
        o = results["systems"][key]["overall"]
        print(f"  {key:45} acc {o['accuracy']['value']:.3f}  halluc "
              f"{o['hallucination_rate']['value']:.3f}  coverage {o['coverage']['value']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

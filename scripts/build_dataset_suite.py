#!/usr/bin/env python
"""Generate an evaluation suite with computable ground truth.

    python scripts/build_dataset_suite.py merged_dataset_body_00.csv

The demonstration suite in `app/evaluation/cases.py` was written by hand against
a corpus that was also written by hand. That is fine for showing behaviour and
weak as evidence, because the author of the questions chose the answers.

Tabular data does not have that problem: the true answer to "what is the maximum
weekly sales in walmart.csv" is a fact about the file, computed by arithmetic over
1.2 million rows. This script recomputes those statistics directly from the source
and emits cases whose expected values come from that computation, never from the
system under test.

It also emits the two case types that matter most for a hallucination study:

* **absent-column cases** - a plausible column the table does not have. The
  correct behaviour is refusal, and a system that answers has hallucinated.
* **cross-dataset cases** - an attribute that exists in *some* table but not in
  the one named. The tempting failure is to answer from the wrong table.

Written to `eval_suites/dataset_suite.json`, loadable by the harness.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.ingestion.merged_dataset import (  # noqa: E402
    canonical, iter_source_files, read_merged,
)

# Columns a data table plausibly might have but these ones do not. Used to build
# refusal cases that look answerable.
PLAUSIBLE_ABSENT = [
    "carbon_footprint", "customer_lifetime_value", "churn_probability",
    "employee_satisfaction", "median_household_income", "net_promoter_score",
    "warranty_claims", "return_rate",
]

STAT_QUESTIONS = {
    "max": "What is the maximum {column} in the {dataset} dataset?",
    "min": "What is the minimum {column} in the {dataset} dataset?",
    "mean": "What is the mean {column} in the {dataset} dataset?",
}


def humanise(column: str) -> str:
    return column.replace("_", " ").strip()


def build(path: pathlib.Path, *, limit: int | None, per_dataset: int,
          seed: int) -> list[dict]:
    random.seed(seed)
    files = read_merged(path, limit=limit)
    sources = [s for s in iter_source_files(files) if not s.is_prose and s.rows > 20]
    cases: list[dict] = []

    for source in sources:
        name = pathlib.PurePath(source.source_file).stem
        subject = f"dataset:{source.slug}"

        # --- shape: row and column counts, exactly known -------------------
        cases.append({
            "id": f"rows-{source.slug}",
            "question": f"How many rows does the {name} dataset have?",
            "expected_kind": "grounded", "expected_values": [float(source.rows)],
            "gold_documents": [f"Dataset card: {pathlib.PurePath(source.source_file).name}"],
            "tags": ["dataset", "shape"], "user_email": "analyst@research.test",
            "notes": f"Counted directly from {source.source_file}.",
        })

        # --- statistics over numeric columns -------------------------------
        numeric = [c for c in source.columns.values()
                   if c.summary().get("kind") == "numeric" and c.numeric_count > 10]
        random.shuffle(numeric)
        for stats in numeric[:per_dataset]:
            summary = stats.summary()
            statistic = random.choice(list(STAT_QUESTIONS))
            if summary.get(statistic) is None:
                continue
            value = canonical(summary[statistic])
            cases.append({
                "id": f"{statistic}-{source.slug}-{stats.name.lower()[:24]}",
                "question": STAT_QUESTIONS[statistic].format(
                    column=humanise(stats.name), dataset=name),
                "expected_kind": "grounded",
                "expected_values": [float(value)],
                "gold_documents": [
                    f"Dataset card: {pathlib.PurePath(source.source_file).name}"],
                "tags": ["dataset", "statistic", statistic],
                "user_email": "analyst@research.test",
                "notes": (f"{statistic} of {stats.name} computed over "
                          f"{stats.numeric_count} values."),
            })

        # --- refusal: a column the table does not have ---------------------
        have = {c.lower() for c in source.header}
        absent = [c for c in PLAUSIBLE_ABSENT if c.lower() not in have]
        if absent:
            column = random.choice(absent)
            cases.append({
                "id": f"absent-{source.slug}",
                "question": (f"What is the mean {humanise(column)} in the "
                             f"{name} dataset?"),
                "expected_kind": "abstain",
                "tags": ["dataset", "absent-column"],
                "user_email": "analyst@research.test",
                "notes": (f"{source.source_file} has no {column} column. Answering "
                          f"with any number is a hallucination."),
            })

    return cases


def add_cross_dataset_cases(cases: list[dict], sources) -> None:
    """Attributes that exist somewhere, but not in the dataset being named.

    This is the failure mode plain retrieval falls into: the words match a real
    passage, so an answer is produced - about the wrong table.
    """
    by_slug = {s.slug: s for s in sources}
    columns_elsewhere: dict[str, str] = {}
    for source in sources:
        for column in source.header:
            columns_elsewhere.setdefault(column.lower(), source.slug)

    added = 0
    for source in sources:
        if added >= 12:
            break
        have = {c.lower() for c in source.header}
        foreign = [c for c, owner in columns_elsewhere.items()
                   if c not in have and owner != source.slug
                   and len(c) > 4 and c.isalpha()]
        if not foreign:
            continue
        column = foreign[0]
        name = pathlib.PurePath(source.source_file).stem
        cases.append({
            "id": f"cross-{source.slug}",
            "question": f"What is the mean {humanise(column)} in the {name} dataset?",
            "expected_kind": "abstain",
            "tags": ["dataset", "cross-dataset"],
            "user_email": "analyst@research.test",
            "notes": (f"`{column}` exists in `{by_slug[columns_elsewhere[column]].source_file}` "
                      f"but not in `{source.source_file}`. Answering from the other "
                      f"table would be a cross-dataset hallucination."),
        })
        added += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-dataset", type=int, default=2,
                        help="statistic questions per dataset")
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("eval_suites/dataset_suite.json"))
    args = parser.parse_args()

    print(f"recomputing ground truth from {args.path} ...")
    files = read_merged(args.path, limit=args.limit)
    sources = [s for s in iter_source_files(files) if not s.is_prose and s.rows > 20]

    cases = build(args.path, limit=args.limit, per_dataset=args.per_dataset,
                  seed=args.seed)
    add_cross_dataset_cases(cases, sources)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=2), encoding="utf-8")

    kinds: dict[str, int] = {}
    for case in cases:
        kinds[case["expected_kind"]] = kinds.get(case["expected_kind"], 0) + 1
    print(f"{len(cases)} cases written to {args.out}")
    print(f"by expected behaviour: {kinds}")
    print("\nsamples:")
    for case in cases[:6]:
        expected = case.get("expected_values", "refuse")
        print(f"  [{case['expected_kind']:<8}] {case['question']}  -> {expected}")


if __name__ == "__main__":
    main()

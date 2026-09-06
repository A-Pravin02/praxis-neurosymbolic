"""The curated suite borrows its ground truth; these tests keep that honest.

Every curated case reuses a generated case's expected values by id. That is the
whole point - phrasing is the only variable - and it is also the whole risk: if
the generated suite is rebuilt and an id moves, a curated case could silently
keep a stale expected value and the benchmark would report a number nobody
computed. These tests fail instead.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.cases import EvalCase, load_suite_from_json

SUITES = Path(__file__).resolve().parents[2] / "eval_suites"
GENERATED = SUITES / "dataset_suite.json"
CURATED = SUITES / "curated_suite.json"

pytestmark = pytest.mark.skipif(
    not (GENERATED.exists() and CURATED.exists()),
    reason="dataset suites are built from the real CSV, which is not in the repo",
)


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_curated_cases_load_as_valid_eval_cases():
    cases = load_suite_from_json("curated-under-test", load(CURATED))
    assert cases and all(isinstance(c, EvalCase) for c in cases)


def test_every_curated_case_matches_its_generated_ground_truth():
    """Only the question text may differ from the case it borrows from."""
    source = {c["id"]: c for c in load(GENERATED)}

    for case in load(CURATED):
        origin_id = case["id"].removeprefix("curated-")
        assert origin_id in source, (
            f"{case['id']} borrows from `{origin_id}`, which no longer exists in "
            "the generated suite - its expected value is now unverifiable")
        origin = source[origin_id]

        # `.get` throughout: an abstention case carries no expected value at all,
        # and omitting the key is how the generator says so.
        for field in ("expected_values", "expected_kind", "gold_documents",
                      "forbidden_values", "user_email"):
            assert case.get(field) == origin.get(field), (
                f"{origin_id}: {field} drifted from the ground truth it borrows")


def test_the_phrasing_actually_changed():
    """A rewrite that copies the template tests nothing."""
    source = {c["id"]: c["question"] for c in load(GENERATED)}
    unchanged = [
        case["id"] for case in load(CURATED)
        if case["question"] == source.get(case["id"].removeprefix("curated-"))
    ]
    assert not unchanged, f"these cases were not actually rephrased: {unchanged}"


def test_refusal_cases_are_a_meaningful_share_of_the_suite():
    """A suite that quietly drops its abstention cases flatters the system.

    These are the cases a system biased toward confident guessing fails, so they
    are the first ones to disappear from a benchmark written to look good.
    """
    cases = load(CURATED)
    refusals = [c for c in cases if c["expected_kind"] == "abstain"]
    assert len(refusals) / len(cases) >= 0.25, (
        f"only {len(refusals)}/{len(cases)} cases expect a refusal")


def test_both_refusal_traps_are_represented():
    """Absent-column and cross-dataset are different failures.

    A column the table simply lacks is the easy case. A column that exists in
    *another* table is the one plain retrieval answers fluently and wrongly,
    because the words match a real passage somewhere in the corpus.
    """
    tags = [set(c["tags"]) for c in load(CURATED)]
    assert sum("absent-column" in t for t in tags) >= 10
    assert sum("cross-dataset" in t for t in tags) >= 10

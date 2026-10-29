"""Measure the parser-side relevance gate exactly, with no language model.

    python scripts/relevance_gate_eval.py

Nine earlier attempts at the not-covered failure were refuted (docs/audit/RELEVANCE_GATE.md).
All nine kept nearest-neighbour goal selection and added a scalar on top of it, and a
nearest-neighbour search always returns a neighbour. The tenth is structural: the
semantic parser reads the goal list and may name *none*, and that verdict is now a hard
abstain for a question about one person's own case.

This script measures the gate's *mechanism* without running the parser, because the gate
reads the parser through exactly one bit - `Interpretation.out_of_scope`. The measurement
forces that bit on and compares the route the system takes with the gate against the
route it took without it. Setting `_text_may_answer` to return True reproduces the
previous code path verbatim, which is what "before" means here.

Read what this does and does not establish. On the out-of-scope questions it is decisive:
the parser does say "none" on all fifteen - the six it refused before are exactly the six
whose nearest clause fell below the acceptance floor - so forcing the bit reproduces the
real run, and the gate's effect there is exact.

On the covered questions it is only an upper bound, and a loose one: almost every covered
question in this benchmark names an order, so a parser that wrongly said "none" would
have its question refused. The bound comes out near the whole population and therefore
says little. It is reported anyway, because a bound that turns out vacuous is a fact
about the measurement and not a reason to leave it out. What decides the covered cost is
the end-to-end run, where the parser says "none" only when it actually judges a question
out of scope: scripts/run_research.py with the ns_parser system, compared against the
stored run made before this change.

Three populations, because the constraint differs:

    generated test, not_covered  15  the gate must fire on all of these
    generated test, covered     240  firing is a false positive
    curated Meridian             86  all covered, colloquially phrased - the set that
                                     refuted attempt 4, which passed on generated worlds
                                     and then fired on twelve of these

Reported per population, with every redirected question printed so a false positive can
be read and judged rather than counted.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import yaml

# The benchmark questions carry rupee amounts, and this prints them.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
sys.path.insert(0, str(BACKEND))

OUT = ROOT / "artifacts/relevance_gate.json"


def forced_none(pack, question: str, *, user: str | None = None):
    """The interpretation the parser produces when it names no goal.

    Everything the gate reads is reproduced faithfully: no candidates (the parser's
    "none" is never coerced to a goal), `out_of_scope` set, and `parsed` set so the
    router takes the parsed branch. Mentions and speech act come from the deterministic
    extractor and the grammar rules, which is what the parser path itself falls back to
    when the model's `request` is not one of the declared kinds.

    `selected_clause` is left unset. A clause the parser names is checked against the
    same gate, so leaving it out measures the generic fallback - the path that answered
    all nine of the test split's out-of-scope questions.
    """
    interpretation = pack.understanding.understand(question)
    interpretation.candidates = []
    interpretation.parsed = True
    interpretation.out_of_scope = True
    interpretation.selected_clause = None
    return interpretation


def both_routes(pack, interpretation) -> tuple[str, str]:
    """The route without the gate, and with it."""
    from app.hybrid.router import ReasoningRouter

    router = ReasoningRouter(pack)
    with_gate = router.route(interpretation).pipeline.value
    # Returning True everywhere is the previous code: the fallbacks were unconditional.
    router._text_may_answer = lambda _interpretation: True     # type: ignore[method-assign]
    without_gate = router.route(interpretation).pipeline.value
    return without_gate, with_gate


ABSTAIN = {"F", "G"}        # insufficient knowledge, general conversation


def population(label: str, items, *, should_fire: bool, verbose: bool) -> dict:
    """items: iterable of (case_id, pack, question, user)."""
    fired, changed, rows = 0, [], []
    for case_id, pack, question, user in items:
        interpretation = forced_none(pack, question, user=user)
        before, after = both_routes(pack, interpretation)
        answered_before = before not in ABSTAIN
        answered_after = after not in ABSTAIN
        if answered_before and not answered_after:
            fired += 1
            changed.append((case_id, question, before, after))
        rows.append({"case": case_id, "question": question, "before": before,
                     "after": after, "gate_fired": answered_before and not answered_after})

    print(f"\n=== {label} ===")
    print(f"  n                                 {len(rows)}")
    print(f"  answered from policy text before  "
          f"{sum(1 for r in rows if r['before'] not in ABSTAIN)}")
    print(f"  answered from policy text after   "
          f"{sum(1 for r in rows if r['after'] not in ABSTAIN)}")
    verdict = "correct refusals" if should_fire else "upper bound, not a measurement"
    print(f"  redirected to abstain by the gate {fired}   <- {verdict}")
    if changed and verbose:
        for case_id, question, before, after in changed:
            print(f"    {case_id:24} {before}->{after}  {question[:62]}")
    return {"n": len(rows), "redirected": fired, "rows": rows}


def generated(verbose: bool) -> dict:
    from app.hybrid.knowledge_pack import load_pack

    cases = yaml.safe_load((WORLDS / "test.yaml").read_text(encoding="utf-8"))["cases"]
    packs: dict[int, object] = {}

    def items(subset):
        for case in subset:
            world = case["world"]
            if world not in packs:
                packs[world] = load_pack(str(WORLDS / f"world-{world}"), "semantic")
            yield case["id"], packs[world], case["question"], case.get("user")

    nc = [c for c in cases if c["category"] == "not_covered"]
    covered = [c for c in cases if c["category"] != "not_covered"]
    out = {
        "not_covered": population("GENERATED test - not covered (the gate must fire)",
                                  items(nc), should_fire=True, verbose=verbose),
        "covered": population("GENERATED test - covered (any firing is a false positive)",
                              items(covered), should_fire=False, verbose=verbose),
    }
    by_category = collections.Counter(
        case["category"] for case, row in zip(covered, out["covered"]["rows"])
        if row["gate_fired"])
    if by_category:
        print("  redirected covered questions by category:")
        for category, count in by_category.most_common():
            print(f"    {category:18} {count}")
    out["covered"]["by_category"] = dict(by_category)
    return out


def meridian(verbose: bool) -> dict:
    from app.hybrid.knowledge_pack import load_pack

    root = BACKEND / "data/meridian"
    cases = yaml.safe_load((root / "benchmark/test.yaml").read_text(encoding="utf-8"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    cases = [c for c in cases if c.get("question")]
    pack = load_pack(str(root), "semantic")
    items = ((c.get("id", f"MER-{i}"), pack, c["question"],
              c.get("user", "support@meridian.test")) for i, c in enumerate(cases, 1))
    return population("CURATED Meridian - all covered (any firing is a false positive)",
                      items, should_fire=False, verbose=verbose)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true",
                        help="also list the correctly refused out-of-scope questions")
    args = parser.parse_args()

    result = {"generated": generated(args.verbose), "meridian": meridian(args.verbose)}
    nc = result["generated"]["not_covered"]
    cov_n = result["generated"]["covered"]["n"] + result["meridian"]["n"]
    covered = result["generated"]["covered"]["redirected"] + result["meridian"]["redirected"]
    print("\n--- summary ---")
    print(f"  out-of-scope questions the gate now refuses : {nc['redirected']}/{nc['n']}"
          "  (exact: the parser says \"none\" on all of them)")
    print(f"  upper bound on covered questions affected   : {covered}/{cov_n}")
    if covered > cov_n // 2:
        print("  That bound is vacuous - it is reached only by a parser that says "
              "\"none\" on nearly every")
        print("  covered question, and such a parser would already be failing them. "
              "The covered cost is")
        print("  decided by the end-to-end run, not here.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwritten to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

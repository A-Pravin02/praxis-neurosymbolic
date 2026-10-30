"""How many verification checks actually run, and what are they called?

    python scripts/count_checks.py

The paper says "ten deterministic checks run before release" and names ten. The
controller passes eleven slots to `run_checks`, one of them conditional, so the
number that runs varies by case. This settles the question from the records rather
than from the prose: it runs every case in the frozen test split through the
default configuration and tabulates the check names that appear.
"""
from __future__ import annotations

import collections
import re
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
sys.path.insert(0, str(BACKEND))

import yaml  # noqa: E402


def main() -> None:
    from app.hybrid.controller import NeuroSymbolicController
    from app.hybrid.knowledge_pack import load_pack
    from app.neuro.providers.mock import MockLLMProvider

    cases = yaml.safe_load((WORLDS / "test.yaml").read_text(encoding="utf-8"))["cases"]
    controllers: dict[int, NeuroSymbolicController] = {}
    names: collections.Counter = collections.Counter()
    per_case: collections.Counter = collections.Counter()
    by_outcome: collections.Counter = collections.Counter()

    for i, case in enumerate(cases, 1):
        world = case["world"]
        if world not in controllers:
            pack = load_pack(str(WORLDS / f"world-{world}"), "semantic")
            controllers[world] = NeuroSymbolicController(
                pack, llm=MockLLMProvider("praxis-mock-1"))
        ans = controllers[world].answer(case["question"], actor_email=case["user"],
                                        actor_roles=tuple(case.get("roles", ())))
        checks = (ans.trace.get("verification") or {}).get("checks") or []
        per_case[len(checks)] += 1
        by_outcome[(len(checks) == 0, ans.outcome, case["category"])] += 1
        for c in checks:
            names[c["check"]] += 1
        if i % 50 == 0:
            print(f"  {i}/{len(cases)} cases", flush=True)

    print(f"\n{len(cases)} cases, {len(names)} distinct check names\n")
    print(f"{'check':24}{'cases it ran on':>16}{'share':>9}")
    for name, n in names.most_common():
        print(f"{name:24}{n:16}{n / len(cases):9.3f}")

    print(f"\n{'checks run on a case':24}{'cases':>8}")
    for count in sorted(per_case):
        print(f"{count:<24}{per_case[count]:8}")

    print("\ncases running no checks at all")
    for (zero, outcome, cat), n in sorted(by_outcome.items()):
        if zero:
            print(f"  outcome {outcome:14} category {cat:16} {n}")

    always = [n for n, c in names.items() if c == len(cases)]
    print(f"\nrun on every case: {len(always)}")
    print(f"run on some cases: {len(names) - len(always)} "
          f"({', '.join(sorted(n for n in names if n not in always))})")

    # How many checks the verifier *defines*, read out of the source rather than counted
    # by hand, so "defined" and "observed" cannot drift apart in the write-up. They are
    # not the same number: a check only one pipeline runs cannot appear in a split whose
    # questions never reach that pipeline.
    defined = sorted(_defined_checks())
    never = [n for n in defined if n not in names]
    print(f"\ndefined in the verifier: {len(defined)}")
    print(f"never run on this split: {len(never)}"
          + (f" ({', '.join(never)})" if never else ""))

    # Written so the paper can state these from a record rather than from memory.
    import json
    out = ROOT / "artifacts/verification_checks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "split": "test",
        "cases": len(cases),
        "distinct_checks": len(names),
        "defined_checks": len(defined),
        "defined_names": defined,
        "never_ran": never,
        "check_names": sorted(names),
        "ran_on_cases": dict(names),
        "checks_per_case": {str(k): v for k, v in sorted(per_case.items())},
        "min_per_case": min(per_case),
        "max_per_case": max(per_case),
        "median_per_case": sorted(
            n for n, c in per_case.items() for _ in range(c))[len(cases) // 2],
        "zero_check_cases": sum(v for (z, _, _), v in by_outcome.items() if z),
        "zero_check_reason": sorted(
            f"{outcome}/{cat}: {v}" for (z, outcome, cat), v in by_outcome.items() if z),
    }, indent=1), encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}")


def _defined_checks() -> set[str]:
    """Every check name the verifier can emit, read from its source.

    The names are string literals in `Check(...)` constructions. Reading them from the
    file rather than calling the methods means the count needs no case to run against,
    and a check added to the verifier is counted the moment it exists.
    """
    source = (BACKEND / "app/hybrid/verification.py").read_text(encoding="utf-8")
    return set(re.findall(r"""Check\(\s*["']([^"']+)["']""", source))


if __name__ == "__main__":
    main()

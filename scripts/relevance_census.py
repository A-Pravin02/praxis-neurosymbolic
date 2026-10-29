"""Census two candidate relevance signals over both benchmarks.

    python scripts/relevance_census.py

The similarity family is dead: scripts/relevance_signal.py shows not-covered
questions stand out from the goal catalogue *more* than several covered
categories, so no absolute or standardised score separates them.

Two other signals are visible in the trace of a failing case:

  derivation   whether any rule proved the goal, or the answer fell through to a
               policy default. The warranty question proves nothing: there is no
               warranty rule to prove.
  understanding the controller's own understanding confidence factor.

Both are measured here on the 255 generated test cases, where 15 are genuinely
out of scope, and on the 86 curated Meridian cases, which are all covered and
colloquially phrased. A usable signal must separate on the first without firing
on the second - the failure that killed the previous attempt.
"""
from __future__ import annotations

import collections
import pathlib
import statistics
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
sys.path.insert(0, str(BACKEND))


def observe(ctl, case) -> dict:
    ans = ctl.answer(case["question"], actor_email=case.get("user"),
                     actor_roles=tuple(case.get("roles", ()) or ()))
    t = ans.trace
    rules = t.get("rules_used") or []
    factors = t.get("confidence_factors") or {}
    return {
        "proved": sum(1 for r in rules if r.get("status") == "proved"),
        "rules": len(rules),
        "understanding": float(factors.get("understanding") or 0.0),
        "determinacy": float(factors.get("symbolic_determinacy") or 0.0),
        "outcome": ans.outcome,
        "goal": ans.goal,
    }


def report(label: str, groups: dict[str, list[dict]]) -> None:
    print(f"\n=== {label} ===")
    print(f"  {'category':18}{'n':>4}{'proved=0':>10}{'understanding mean':>20}"
          f"{'u-min':>8}{'u-max':>8}")
    for cat in sorted(groups, key=lambda c: statistics.fmean(
            x["understanding"] for x in groups[c])):
        g = groups[cat]
        zero = sum(1 for x in g if x["proved"] == 0)
        us = [x["understanding"] for x in g]
        print(f"  {cat:18}{len(g):4}{zero:>6}/{len(g):<3}"
              f"{statistics.fmean(us):20.3f}{min(us):8.3f}{max(us):8.3f}")


def main() -> None:
    from app.hybrid.controller import NeuroSymbolicController
    from app.hybrid.knowledge_pack import load_pack
    from app.neuro.providers.mock import MockLLMProvider

    # ---- generated worlds ---------------------------------------------------
    cases = yaml.safe_load((WORLDS / "test.yaml").read_text(encoding="utf-8"))["cases"]
    ctls: dict[int, object] = {}
    groups: dict[str, list[dict]] = {}
    for i, case in enumerate(cases, 1):
        w = case["world"]
        if w not in ctls:
            pack = load_pack(str(WORLDS / f"world-{w}"), "semantic")
            ctls[w] = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
        groups.setdefault(case["category"], []).append(observe(ctls[w], case))
        if i % 60 == 0:
            print(f"  {i}/{len(cases)} generated", flush=True)
    report("GENERATED test split", groups)

    covered = [x for c, g in groups.items() if c != "not_covered" for x in g]
    nc = groups.get("not_covered", [])
    print(f"\n  covered with proved=0 : {sum(1 for x in covered if x['proved']==0)}"
          f"/{len(covered)}")
    print(f"  not-covered proved=0  : {sum(1 for x in nc if x['proved']==0)}/{len(nc)}")
    cu = sorted(x["understanding"] for x in covered)
    nu = sorted(x["understanding"] for x in nc)
    print(f"  understanding: covered p05 {cu[len(cu)//20]:.3f} min {cu[0]:.3f} | "
          f"not-covered max {nu[-1]:.3f} mean {statistics.fmean(nu):.3f}")

    # ---- curated Meridian, all covered, colloquial --------------------------
    mer = BACKEND / "data/meridian"
    mcases = yaml.safe_load((mer / "benchmark/test.yaml").read_text(encoding="utf-8"))
    mcases = mcases["cases"] if isinstance(mcases, dict) else mcases
    pack = load_pack(str(mer), "semantic")
    mctl = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
    mgroups: dict[str, list[dict]] = {}
    for case in mcases:
        if not case.get("question"):
            continue
        case.setdefault("user", "support@meridian.test")
        mgroups.setdefault(case.get("category", "?"), []).append(observe(mctl, case))
    report("CURATED Meridian (all covered)", mgroups)
    mall = [x for g in mgroups.values() for x in g]
    print(f"\n  Meridian with proved=0: {sum(1 for x in mall if x['proved']==0)}"
          f"/{len(mall)}  <- these would be false positives")
    mu = sorted(x["understanding"] for x in mall)
    print(f"  Meridian understanding: min {mu[0]:.3f} p05 {mu[len(mu)//20]:.3f}")


if __name__ == "__main__":
    main()

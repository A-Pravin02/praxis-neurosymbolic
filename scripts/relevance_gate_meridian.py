"""Does the relevance gate fire on real, colloquially phrased, covered questions?

    LLM_PROVIDER=local LOCAL_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct \
        python scripts/relevance_gate_meridian.py

This is the check that refuted attempt 4. That attempt separated covered from
out-of-scope questions cleanly on the generated worlds, where questions are phrased
formulaically, and then fired on twelve of the eighty-six curated Meridian questions,
which are phrased the way people actually write. Every one of those was a false
positive, so the attempt was reverted. Attempts 6 to 9 would have failed the same test.

The Meridian set is entirely covered, so the gate firing anywhere here is a false
positive and nothing else. Two numbers decide it:

    how often the parser says "none" on a covered question - its own error rate
    of those, how many the gate then refuses rather than answering from policy text

The second is the gate's cost. The first is not: a question the parser wrongly reads as
out of scope was already going to be answered from a clause chosen by similarity, which
is how the generated split's out-of-scope questions were answered out of the
cancellation policy. Refusing is not obviously worse than that - but it is a change, so
both are printed, with every affected question, to be read rather than counted.

The generated-world counterpart is the end-to-end benchmark run; see
docs/audit/RELEVANCE_GATE.md.
"""
from __future__ import annotations

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MERIDIAN = BACKEND / "data/meridian"
OUT = ROOT / "artifacts/relevance_gate_meridian.json"
ABSTAIN = {"F", "G"}


def main() -> None:
    from app.hybrid.knowledge_pack import load_pack
    from app.hybrid.router import ReasoningRouter
    from app.neural.semantic_parser import LLMSemanticParser
    from app.neuro.registry import get_llm

    cases = yaml.safe_load((MERIDIAN / "benchmark/test.yaml").read_text(encoding="utf-8"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    cases = [c for c in cases if c.get("question")]

    pack = load_pack(str(MERIDIAN), "semantic")
    llm = get_llm()
    parser = LLMSemanticParser(pack.program, pack.graph, pack.policies, llm,
                               pack.understanding)
    router = ReasoningRouter(pack)
    print(f"model: {getattr(llm, 'model_name', getattr(llm, 'model', llm.name))}\n",
          flush=True)

    rows = []
    for i, case in enumerate(cases, 1):
        interpretation = parser.understand(case["question"])
        with_gate = router.route(interpretation).pipeline.value
        # Returning True everywhere is the code as it stood before the gate.
        router._text_may_answer = lambda _i: True      # type: ignore[method-assign]
        without_gate = router.route(interpretation).pipeline.value
        del router._text_may_answer                    # restore the real method
        rows.append({
            "case": case.get("id", f"MER-{i}"),
            "question": case["question"],
            "parser_goal": (interpretation.parse or {}).get("goal"),
            "out_of_scope": interpretation.out_of_scope,
            "before": without_gate,
            "after": with_gate,
            "refused_by_gate": without_gate not in ABSTAIN and with_gate in ABSTAIN,
        })
        if i % 10 == 0:
            print(f"  {i}/{len(cases)}", flush=True)

    none_said = [r for r in rows if r["out_of_scope"]]
    refused = [r for r in rows if r["refused_by_gate"]]

    print(f"\n=== CURATED Meridian, {len(rows)} questions, all covered ===")
    print(f'  parser said "none" on          {len(none_said)}/{len(rows)}')
    print(f"  of those, refused by the gate  {len(refused)}"
          f"   <- false positives, the number that matters")
    if none_said:
        print('\n  every question the parser called out of scope:')
        for r in none_said:
            mark = "REFUSED" if r["refused_by_gate"] else "answered"
            print(f"    {mark:8} {r['before']}->{r['after']}  {r['question'][:60]}")
    if not refused:
        print("\n  The gate fires on none of them. Attempt 4 fired on twelve.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"n": len(rows), "said_none": len(none_said),
                               "refused": len(refused), "rows": rows}, indent=2),
                   encoding="utf-8")
    print(f"\nwritten to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

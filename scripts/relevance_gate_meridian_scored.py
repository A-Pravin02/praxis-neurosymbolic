"""What does the relevance gate cost on the curated Meridian suite, scored?

    LLM_PROVIDER=local LOCAL_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct \
        python scripts/relevance_gate_meridian_scored.py

scripts/relevance_gate_meridian.py established that on the 86 curated Meridian
questions - all covered, phrased the way people actually write - the 1.5B parser says
"none" on 29 and the gate then refuses 23 of them. That is a route change, not yet a
cost: the question is whether those 23 were being answered *correctly* before. A
refusal that replaces a wrong answer is an improvement; one that replaces a right
answer is the failure that killed attempt 4.

So both arms are scored with the suite's own scorer, `ns_benchmark.score_case`, rather
than with a rule written here.

The two arms share one parse. The parser is memoised per question, so the model runs
once for both arms and the only difference between them is `_text_may_answer` - the
gate itself. That makes the comparison exactly paired, with no decoding variation
between arms, and halves the runtime.
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MERIDIAN = BACKEND / "data/meridian"
OUT = ROOT / "artifacts/relevance_gate_meridian_scored.json"


class Memoised:
    """One parse per question, reused by both arms."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.cache: dict[str, object] = {}
        self.encoder = inner.encoder
        self.name = inner.name

    def understand(self, question: str):
        if question not in self.cache:
            self.cache[question] = self.inner.understand(question)
        return self.cache[question]

    def detect_inputs(self, *args, **kwargs):
        return self.inner.detect_inputs(*args, **kwargs)


def main() -> None:
    from app.evaluation.ns_benchmark import score_case
    from app.hybrid.knowledge_pack import load_pack
    from app.neural.semantic_parser import LLMSemanticParser
    from app.neuro.registry import get_llm

    cases = yaml.safe_load((MERIDIAN / "benchmark/test.yaml").read_text(encoding="utf-8"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    cases = [c for c in cases if c.get("question")]
    for case in cases:
        case.setdefault("user", "support@meridian.test")

    pack = load_pack(str(MERIDIAN), "semantic")
    llm = get_llm()
    parser = Memoised(LLMSemanticParser(pack.program, pack.graph, pack.policies, llm,
                                        pack.understanding))
    print(f"model: {getattr(llm, 'model_name', getattr(llm, 'model', llm.name))}\n",
          flush=True)

    from app.evaluation.ns_benchmark import SystemAnswer
    from app.hybrid.controller import NeuroSymbolicController
    from app.neuro.providers.mock import MockLLMProvider

    # Two controllers over the same pack and the same memoised parser. The "before" arm
    # has the gate's test forced true, which is the code as it stood before the change.
    rows = []
    for i, case in enumerate(cases, 1):
        results = {}
        for arm in ("before", "after"):
            controller = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
            controller.understanding = parser
            if arm == "before":
                controller.router._text_may_answer = lambda _i: True  # type: ignore
            answer = controller.answer(case["question"], actor_email=case["user"],
                                       actor_roles=tuple(case.get("roles", ()) or ()))
            trace = answer.trace
            sa = SystemAnswer(
                text=answer.answer, outcome=answer.outcome, goal=answer.goal,
                pipeline=answer.pipeline, decision=answer.decision, value=answer.value,
                subject=answer.subject, sources=[s["id"] for s in answer.sources],
                explanation=" ".join(answer.explanation
                                     + trace.get("reasoning_steps", [])),
                verified=trace.get("verification", {}).get("passed"),
                confidence=answer.confidence, latency_ms=0.0)
            results[arm] = (sa, score_case(case, sa, reasoning_system=True))

        interpretation = parser.cache[case["question"]]
        rows.append({
            "case": case.get("id", f"MER-{i}"),
            "question": case["question"],
            "category": case.get("category", "?"),
            "out_of_scope": bool(getattr(interpretation, "out_of_scope", False)),
            "before_outcome": results["before"][0].outcome,
            "after_outcome": results["after"][0].outcome,
            "before_correct": bool(results["before"][1].correct),
            "after_correct": bool(results["after"][1].correct),
        })
        if i % 10 == 0:
            print(f"  {i}/{len(cases)}", flush=True)

    bc = sum(r["before_correct"] for r in rows)
    ac = sum(r["after_correct"] for r in rows)
    lost = [r for r in rows if r["before_correct"] and not r["after_correct"]]
    gained = [r for r in rows if r["after_correct"] and not r["before_correct"]]

    print(f"\n=== CURATED Meridian, {len(rows)} questions, all covered ===")
    print(f"  correct before {bc}/{len(rows)} = {bc/len(rows):.3f}")
    print(f"  correct after  {ac}/{len(rows)} = {ac/len(rows):.3f}")
    print(f"  LOST   {len(lost)}   <- correct answers the gate refused")
    print(f"  gained {len(gained)}")
    if lost:
        print("\n  every correct answer the gate turned into a refusal:")
        for r in lost:
            print(f"    {r['category']:14} {r['before_outcome']:12}->"
                  f"{r['after_outcome']:12} {r['question'][:54]}")
        print("\n  by category:",
              dict(collections.Counter(r["category"] for r in lost)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"n": len(rows), "before_correct": bc, "after_correct": ac,
                               "lost": len(lost), "gained": len(gained), "rows": rows},
                              indent=2), encoding="utf-8")
    print(f"\nwritten to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

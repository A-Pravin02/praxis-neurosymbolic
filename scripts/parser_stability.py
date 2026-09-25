"""Does the semantic parser return the same reading of a question twice?

This matters for the evaluation, not just for tidiness: if the parser's goal varies
between runs, then a difference between two benchmark runs is partly noise, and a
paired test that assumes each system has one behaviour per question is measuring
something it does not mean to.

Decoding is greedy (temperature 0), so in principle the answer should be identical
every time. In practice the model is 4-bit quantized and served in batches, so a
prompt can be padded and batched with different neighbours on each call, and
floating-point reduction order is not guaranteed to be stable across those.

**The cache has to be defeated or this measures nothing.** The model server keys its
completion cache on (model, messages, max_tokens), so asking the same question again
replays the stored text and every run agrees trivially. Here `max_tokens` is varied
across a range wide enough to miss the cache and far larger than the ~40 tokens a
reply needs, so nothing is truncated and each call regenerates. Greedy decoding makes
the reply independent of that budget, so any disagreement is the model's own
nondeterminism rather than an artefact of asking differently.

    python scripts/parser_stability.py --repeats 5 --questions 20
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
warnings.filterwarnings("ignore")

from app.evaluation.research import load_frozen        # noqa: E402
from app.hybrid.knowledge_pack import load_pack        # noqa: E402
from app.neural.semantic_parser import LLMSemanticParser, _json  # noqa: E402
from app.neuro.registry import get_llm                 # noqa: E402

TAIL = ('\n\nJSON keys: "goal" (a goal name or "none"), "request" (a request kind), '
        '"clause" (for an "inform" request, the clause id that answers it, else null), '
        '"stated" (an object mapping fact names to values the question states; {} if none).')


def readings(parser, question: str, repeats: int, base: int) -> list[tuple]:
    """One reading per call, each with a different token budget so the cache misses."""
    out = []
    for i in range(repeats):
        raw = parser._ask(parser._schema + "\n\nQuestion: " + question + TAIL, base + i)
        parsed = _json(raw)
        if parsed is None:
            out.append(("<no json>", "<no json>"))
            continue
        out.append((str(parsed.get("goal")), str(parsed.get("request"))))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--questions", type=int, default=20)
    ap.add_argument("--base-tokens", type=int, default=220)
    ap.add_argument("--out", type=Path, default=ROOT / "eval_reports")
    args = ap.parse_args()

    pack = load_pack(None, "semantic")
    parser = LLMSemanticParser(pack.program, pack.graph, pack.policies,
                               get_llm(), pack.understanding)

    # One question per category first, then spread over the rest, so the sample is not
    # all of one kind.
    cases = load_frozen("test")
    by_category: dict[str, list] = collections.defaultdict(list)
    for case in cases:
        by_category[case["category"]].append(case)
    chosen, i = [], 0
    while len(chosen) < args.questions:
        added = False
        for category in sorted(by_category):
            if i < len(by_category[category]) and len(chosen) < args.questions:
                chosen.append(by_category[category][i])
                added = True
        if not added:
            break
        i += 1

    rows, unstable = [], []
    for case in chosen:
        seen = readings(parser, case["question"], args.repeats, args.base_tokens)
        distinct = collections.Counter(seen)
        stable = len(distinct) == 1
        rows.append({"id": case["id"], "category": case["category"],
                     "question": case["question"], "stable": stable,
                     "readings": [list(s) for s in seen],
                     "distinct": len(distinct)})
        if not stable:
            unstable.append(rows[-1])
        print(f"{'ok ' if stable else 'VARIES'} {case['id']:24s} {case['category']:14s} "
              f"{len(distinct)} distinct of {args.repeats}", flush=True)

    n = len(rows)
    agree = sum(r["stable"] for r in rows)
    lines = [f"# Parser stability — {n} questions × {args.repeats} regenerations", "",
             f"Greedy decoding at temperature 0. `max_tokens` varied over "
             f"{args.base_tokens}–{args.base_tokens + args.repeats - 1} so every call "
             f"misses the server's completion cache and regenerates; the budget is far "
             f"above the ~40 tokens a reply needs, so nothing is truncated.", "",
             f"**{agree}/{n} questions gave the same (goal, request) every time "
             f"({agree / n:.3f}).**", ""]
    if unstable:
        lines += ["| Question | Category | Distinct readings |", "|---|---|---|"]
        for r in unstable:
            variants = "; ".join(f"`{g}/{q}`" for g, q in
                                 sorted({tuple(x) for x in r["readings"]}))
            lines.append(f"| {r['question'][:70]} | {r['category']} | {variants} |")
        lines += ["", "A question that reads differently between calls is one whose "
                      "benchmark result is partly a coin toss, and a paired comparison "
                      "over such questions carries noise the test does not model."]
    else:
        lines += ["No question varied. On this sample the parser is deterministic, so a "
                  "difference between two runs is a difference in the system and not in "
                  "the decoding."]
    lines += ["", "Scope of the check: these calls are sequential from one client, so a "
                  "prompt is unlikely to be batched with the same neighbours a full "
                  "benchmark run would give it. Batch composition is the most likely "
                  "source of nondeterminism in a batched 4-bit server, and this sample "
                  "does not exercise it. A stable result here therefore bounds the "
                  "problem rather than ruling it out."]
    text = "\n".join(lines)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "parser-stability.json").write_text(json.dumps(rows, indent=1),
                                                    encoding="utf-8")
    (args.out / "parser-stability.md").write_text(text, encoding="utf-8")
    print("\n" + text)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

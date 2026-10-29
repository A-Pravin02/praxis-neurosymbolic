"""Test one more relevance signal: phrase-level alignment, not sentence similarity.

    python scripts/relevance_phrase.py

Seven approaches have failed. The five in commit 2706a80, plus two measured here
earlier: the standardised margin (not-covered questions stand out from the
catalogue *more* than several covered categories) and the derivation check
(proved=0 fires on 88 of 240 covered generated cases and 40 of 86 Meridian ones).

What they share is that they all read a whole-sentence embedding. A sentence
embedding averages everything in the sentence, and every question here is mostly
entity reference and interrogative boilerplate - "What is the ... on the Lumen
Tablet?" - so the topic word contributes a small part of the vector. That is why
"warranty" scores as confidently as "return": the vectors are dominated by what
the two questions share, not by what distinguishes them.

This measures the other direction, term by term: does the selected goal's own
defining vocabulary actually appear in the question? `return_allowed` is defined
as "an item that was bought can be sent back to the seller", and a colloquial
"can they go back?" contains `back`. The warranty question contains nothing from
`replacement_available`'s definition.

Designed on the development split, which exists for calibration and whose
out-of-scope topics (store pickup, loyalty points, price match) differ from the
test split's (instalments, gift wrap, international delivery, student discount).
Checked against the curated Meridian set, where any firing is a false positive.
"""
from __future__ import annotations

import pathlib
import re
import statistics
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORLDS = BACKEND / "data/benchmark_worlds"
sys.path.insert(0, str(BACKEND))

STOP = {
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "before", "by", "can",
    "could", "do", "does", "for", "from", "get", "give", "got", "has", "have", "how",
    "i", "if", "in", "is", "it", "its", "may", "me", "my", "no", "not", "of", "on",
    "or", "our", "out", "shall", "should", "so", "that", "the", "their", "them",
    "there", "these", "they", "this", "to", "up", "was", "we", "were", "what", "when",
    "whether", "which", "who", "will", "with", "would", "you", "your", "still", "much",
    "many", "am", "did", "був",
}


def terms(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return [w for w in words if w not in STOP and len(w) > 2]


def question_terms(question: str, graph) -> list[str]:
    """Question content words, with record ids and product names removed.

    The entity a question names is not its topic: every question names one, and
    the covered and the out-of-scope ones name the same kinds of thing.
    """
    text = re.sub(r"\b[A-Z]{3}-\d+\b", " ", question or "")
    for pool in ("products", "customers", "employees"):
        for rec in (getattr(graph, pool, None) or []):
            name = rec.get("name") if isinstance(rec, dict) else None
            if name:
                text = re.sub(re.escape(name), " ", text, flags=re.I)
    return terms(text)


def distinctiveness(catalogue) -> dict[str, float]:
    """Inverse document frequency of each term across the goal catalogue.

    `order`, `item` and `customer` occur in most descriptions and say nothing
    about which goal was meant; `seller`, `cancelled`, `leave`, `reimbursed`
    occur in one. Weighting by this is what separates a goal being *named* from
    a goal merely sharing its subject with the question.
    """
    import math
    docs = [set(terms(c.description)) for c in catalogue]
    n = len(docs) or 1
    vocab = {w for d in docs for w in d}
    return {w: math.log(n / sum(1 for d in docs if w in d)) for w in vocab}


def alignment(encoder, q_terms, d_terms, weights=None) -> float:
    """How well the goal's *distinctive* vocabulary is represented in the question.

    Weighted mean, not max: a single incidental word match saturates a max, which
    is why "Can I pick up order X from one of your stores?" scored 1.000 against a
    goal it has nothing to do with.
    """
    if not q_terms or not d_terms:
        return 0.0
    scores = encoder.similarity(d_terms, q_terms)
    best = [max(float(s) for s in row) for row in scores]
    if weights is None:
        return max(best)
    w = [max(weights.get(t, 0.0), 1e-6) for t in d_terms]
    return sum(b * wi for b, wi in zip(best, w)) / sum(w)


def run(label: str, pack_dirs, cases, is_nc) -> list[tuple[str, float, str]]:
    from app.hybrid.knowledge_pack import load_pack
    packs: dict[str, object] = {}
    from app.neural.intent_detection import IntentDetector
    out: list[tuple[str, float, str]] = []
    for i, (case, pdir) in enumerate(zip(cases, pack_dirs), 1):
        if pdir not in packs:
            packs[pdir] = load_pack(pdir, "semantic")
        pack = packs[pdir]
        det = IntentDetector(pack.program, pack.encoder)
        cand = det.candidates(case["question"], top=1)
        if not cand:
            continue
        key = pdir + "|idf"
        if key not in packs:
            packs[key] = distinctiveness(det._catalogue)
        qt = question_terms(case["question"], pack.graph)
        dt = terms(cand[0].description)
        out.append(("not_covered" if is_nc(case) else "covered",
                    alignment(pack.encoder, qt, dt, packs[key]), case["question"]))
        if i % 60 == 0:
            print(f"  {i}/{len(cases)} {label}", flush=True)
    return out


def summarise(label: str, rows) -> None:
    cov = sorted(s for t, s, _ in rows if t == "covered")
    nc = sorted((s, q) for t, s, q in rows if t == "not_covered")
    print(f"\n=== {label} ===")
    if cov:
        print(f"  covered     n={len(cov):3}  min {cov[0]:.3f}  p05 "
              f"{cov[len(cov)//20]:.3f}  mean {statistics.fmean(cov):.3f}")
    if nc:
        vals = [s for s, _ in nc]
        print(f"  not covered n={len(vals):3}  max {max(vals):.3f}  mean "
              f"{statistics.fmean(vals):.3f}")
        print("  highest-scoring not-covered questions:")
        for s, q in sorted(nc, reverse=True)[:3]:
            print(f"    {s:.3f}  {q[:66]}")
    if cov and nc:
        vals = [s for s, _ in nc]
        gap = cov[len(cov) // 20] - max(vals)
        print(f"  separation at covered-p05 vs not-covered-max: {gap:+.3f} "
              f"{'SEPARATES' if gap > 0 else 'overlaps'}")
        for cut in (0.30, 0.35, 0.40, 0.45, 0.50, 0.55):
            fp = sum(1 for s in cov if s < cut)
            tp = sum(1 for s in vals if s < cut)
            print(f"    cut {cut:.2f}: catches {tp}/{len(vals)} out-of-scope, "
                  f"{fp} false positives of {len(cov)}")


def main() -> None:
    dev = yaml.safe_load((WORLDS / "dev.yaml").read_text(encoding="utf-8"))["cases"]
    rows = run("dev", [str(WORLDS / f"world-{c['world']}") for c in dev], dev,
               lambda c: c["category"] == "not_covered")
    summarise("DEVELOPMENT split (design set)", rows)

    mer = BACKEND / "data/meridian"
    mcases = yaml.safe_load((mer / "benchmark/test.yaml").read_text(encoding="utf-8"))
    mcases = mcases["cases"] if isinstance(mcases, dict) else mcases
    mcases = [c for c in mcases if c.get("question")]
    mrows = run("meridian", [str(mer)] * len(mcases), mcases, lambda c: False)
    summarise("CURATED Meridian (all covered - any firing is a false positive)", mrows)


if __name__ == "__main__":
    main()

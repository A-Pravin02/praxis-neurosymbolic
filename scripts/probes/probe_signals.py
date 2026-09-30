"""Measure several candidate discriminators at once, rather than guessing one.

Three hypotheses are already refuted: raising GOAL_ACCEPT, counting novel words, and
word-level semantic alienness. This probe computes, for every test case, the signals
that could plausibly separate a spurious goal match from a real one, and reports each
signal's mean per category. Any signal worth building on must put not_covered clearly
outside the answerable categories.
"""
import sys, collections, io, statistics, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = Path(r"C:/Users/aprav/OneDrive/Desktop/praxisvit")
sys.path.insert(0, str(REPO / "backend"))

from app.evaluation.casegen import generate_cases
from app.evaluation.worlds import TEST_SEEDS, WORLDS_DIR, generate_world
from app.hybrid.knowledge_pack import load_pack
from app.hybrid.router import _content, _FUNCTION_WORDS

_ID = re.compile(r"\b[A-Z]{2,}-\d+\b")

rows = collections.defaultdict(list)
for seed in TEST_SEEDS:
    world = generate_world(seed)
    pack = load_pack(str(WORLDS_DIR / f"world-{seed}"), "semantic")
    understanding = pack.understanding
    enc = pack.encoder
    products = [p["name"] for p in world.products] if hasattr(world, "products") else []

    for case in generate_cases(world, "test"):
        q = case["question"]
        interp = understanding.understand(q)
        cands = interp.candidates
        if not cands:
            continue
        top = cands[0]
        second = cands[1].score if len(cands) > 1 else 0.0

        # the question with its record id and any product name removed
        bare = _ID.sub(" ", q)
        for name in products:
            if name.lower() in bare.lower():
                bare = re.sub(re.escape(name), " ", bare, flags=re.I)
        bare = re.sub(r"\border\b", " ", bare, flags=re.I).strip()

        stripped = float(enc.similarity([bare or q], [top.description])[0][0])

        # how much of the goal's own wording the question evidences, lexically
        goal_words = (_content(top.description) | _content(top.name)) - _FUNCTION_WORDS
        qwords = _content(q) - _FUNCTION_WORDS
        coverage = len(goal_words & qwords) / max(1, len(goal_words))

        rows[case["category"]].append(dict(
            top=top.score, margin=top.score - second, stripped=stripped,
            drop=top.score - stripped, coverage=coverage, q=q, goal=top.name))

SIGNALS = ["top", "margin", "stripped", "drop", "coverage"]
print("| category | n | " + " | ".join(SIGNALS) + " |")
print("|---|---|" + "---|" * len(SIGNALS))
for cat in sorted(rows):
    rs = rows[cat]
    cells = " | ".join(f"{statistics.mean(r[s] for r in rs):.3f}" for s in SIGNALS)
    star = "  **" if cat == "not_covered" else ""
    print(f"| {cat}{star} | {len(rs)} | {cells} |")

print("\nnot_covered cases, worst-coverage first:")
for r in sorted(rows["not_covered"], key=lambda r: r["coverage"])[:15]:
    print(f"  cov={r['coverage']:.2f} strip={r['stripped']:.2f} top={r['top']:.2f} "
          f"goal={r['goal'][:26]:26s} <- {r['q'][:62]}")

print("\nmulti_hop + paraphrased, worst-coverage first (these must NOT be caught):")
both = rows["multi_hop"] + rows["paraphrased"]
for r in sorted(both, key=lambda r: r["coverage"])[:12]:
    print(f"  cov={r['coverage']:.2f} strip={r['stripped']:.2f} top={r['top']:.2f} "
          f"goal={r['goal'][:26]:26s} <- {r['q'][:62]}")

print("\n\n=== stripped-score distribution ===")
print("| category | n | min | 5th | median | max |")
print("|---|---|---|---|---|---|")
for cat in sorted(rows, key=lambda c: statistics.mean(r["stripped"] for r in rows[c])):
    v = sorted(r["stripped"] for r in rows[cat])
    print(f"| {cat} | {len(v)} | {v[0]:.3f} | {v[max(0,len(v)//20)]:.3f} | "
          f"{statistics.median(v):.3f} | {v[-1]:.3f} |")

print("\n=== what a floor on the stripped score would cost ===")
print("| floor | not_covered caught | answerable wrongly caught |")
print("|---|---|---|")
answerable = [r for c, rs in rows.items() if c != "not_covered" for r in rs]
for floor in (0.66, 0.68, 0.69, 0.70, 0.71, 0.72, 0.74):
    caught = sum(1 for r in rows["not_covered"] if r["stripped"] < floor)
    bad = sum(1 for r in answerable if r["stripped"] < floor)
    print(f"| {floor:.2f} | {caught}/{len(rows['not_covered'])} | {bad}/{len(answerable)} |")

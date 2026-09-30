"""Is a not_covered question's novel term semantically far from everything the pack knows?

Counting novel words fails: multi_hop questions carry more of them than not_covered
ones. But their novel words are paraphrases of covered actions ("call off" = cancel)
or product names, while a not_covered question's novel word names a service that does
not exist ("gift-wrap", "instalments", "warranty").

This probe scores each novel content word against everything the pack describes, and
reports the *lowest* such score per question - how alien the most alien word is.
"""
import sys, collections, io, statistics
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = Path(r"C:/Users/aprav/OneDrive/Desktop/praxisvit")
sys.path.insert(0, str(REPO / "backend"))

from app.evaluation.casegen import generate_cases
from app.evaluation.worlds import TEST_SEEDS, WORLDS_DIR, generate_world
from app.hybrid.knowledge_pack import load_pack
from app.hybrid.router import ReasoningRouter, _content, _FUNCTION_WORDS


def novel(question: str, vocab: set[str]) -> list[str]:
    words = _content(question) - _FUNCTION_WORDS - vocab
    return sorted(w for w in words
                  if not any(ch.isdigit() for ch in w) and w != "ord")


def surface(pack) -> list[str]:
    """Everything the pack says about itself, as sentences to compare a word against."""
    program = pack.program
    out = []
    for name, goal in program.goals.items():
        out.append(f"{name.replace('_', ' ')}: {goal.description}")
    for name, lookup in program.lookups.items():
        out.append(f"{name.replace('_', ' ')}: {lookup.description}")
    for name in program.attributes:
        out.append(name.replace("_", " "))
    for name in program.entity_types:
        out.append(name.replace("_", " "))
    return out


by_cat = collections.defaultdict(list)
for seed in TEST_SEEDS:
    world = generate_world(seed)
    pack = load_pack(str(WORLDS_DIR / f"world-{seed}"), "semantic")
    vocab = ReasoningRouter(pack)._domain_vocabulary()
    described = surface(pack)
    labels = {w for r in (list(world.products) if hasattr(world, "products") else [])
              for w in _content(str(r))}
    cases = generate_cases(world, "test")
    for c in cases:
        words = [w for w in novel(c["question"], vocab) if w not in labels]
        if not words:
            by_cat[c["category"]].append((1.0, c["question"], None))
            continue
        sims = pack.encoder.similarity(words, described)   # (len(words), len(described))
        best = sims.max(axis=1)
        i = int(best.argmin())
        by_cat[c["category"]].append((float(best[i]), c["question"], words[i]))

print("For each question: the novel word that is least like anything the pack describes.\n")
for cat in sorted(by_cat, key=lambda k: statistics.mean(v[0] for v in by_cat[k])):
    rows = sorted(by_cat[cat])
    print(f"### {cat}  (n={len(rows)})  mean {statistics.mean(r[0] for r in rows):.3f}")
    for score, q, word in rows[:3]:
        print(f"   {score:.3f}  '{word}'  <- {q[:80]}")
    print()

print("| category | n | mean alienness | 25th pct | median | below 0.25 |")
print("|---|---|---|---|---|---|")
for cat in sorted(by_cat, key=lambda k: statistics.mean(v[0] for v in by_cat[k])):
    vals = sorted(v[0] for v in by_cat[cat])
    q1 = vals[len(vals) // 4]
    print(f"| {cat} | {len(vals)} | {statistics.mean(vals):.3f} | {q1:.3f} | "
          f"{statistics.median(vals):.3f} | {sum(1 for v in vals if v < 0.25)}/{len(vals)} |")

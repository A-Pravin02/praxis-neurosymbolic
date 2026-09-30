"""Does the DROP (raw score minus residual score) transfer across packs?

An absolute floor on the residual does not: Meridian's colloquial questions score
low in absolute terms whether or not they are covered, so any floor that catches
gift-wrap also catches "The nuts turned up fine but I'd rather not keep them".

The relative signal may survive. For a covered question the meaning lives in the
verb, so removing the record should not help the goal match. For a not-covered one
the match is carried BY the record, so removing it should cost.
"""
import sys, collections, io, statistics, yaml
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = Path(r"C:/Users/aprav/OneDrive/Desktop/praxisvit")
sys.path.insert(0, str(REPO / "backend"))

from app.evaluation.casegen import generate_cases
from app.evaluation.worlds import DEV_SEEDS, WORLDS_DIR, generate_world
from app.hybrid.knowledge_pack import load_pack
from app.hybrid.router import context_free_support


def drop_for(pack, question):
    i = pack.understanding.understand(question)
    if not i.candidates:
        return None
    top = i.candidates[0]
    bare = context_free_support(i, top, pack.encoder, pack.program.entity_types)
    return top.score - bare


print("=== generated dev worlds (where not_covered labels exist) ===")
rows = collections.defaultdict(list)
for seed in DEV_SEEDS:
    world = generate_world(seed)
    pack = load_pack(str(WORLDS_DIR / f"world-{seed}"), "semantic")
    for case in generate_cases(world, "dev"):
        d = drop_for(pack, case["question"])
        if d is not None:
            rows[case["category"]].append((d, case["question"]))

print("| category | n | mean drop | median | 90th pct | max |")
print("|---|---|---|---|---|---|")
for cat in sorted(rows, key=lambda c: -statistics.mean(r[0] for r in rows[c])):
    v = sorted(r[0] for r in rows[cat])
    print(f"| {cat} | {len(v)} | {statistics.mean(v):+.3f} | {statistics.median(v):+.3f} | "
          f"{v[min(len(v)-1, int(len(v)*0.9))]:+.3f} | {v[-1]:+.3f} |")

nc = rows.get("not_covered", [])
answerable = [r for c, rs in rows.items() if c != "not_covered" for r in rs]
print("\n| drop floor | not_covered caught | answerable wrongly caught |")
print("|---|---|---|")
for floor in (0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.08):
    print(f"| >{floor:.2f} | {sum(1 for d, _ in nc if d > floor)}/{len(nc)} | "
          f"{sum(1 for d, _ in answerable if d > floor)}/{len(answerable)} |")

print("\n=== meridian curated benchmark (all covered; none should be caught) ===")
pack = load_pack(str(REPO / "backend" / "data" / "meridian"))
for split in ("dev", "test"):
    path = REPO / "backend" / "data" / "meridian" / "benchmark" / f"{split}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    vals = []
    for c in cases:
        d = drop_for(pack, c.get("question") or c.get("q"))
        if d is not None:
            vals.append((d, c.get("question") or c.get("q")))
    vals.sort(reverse=True)
    v = [d for d, _ in vals]
    print(f"\nmeridian {split}: n={len(v)} mean {statistics.mean(v):+.3f} "
          f"median {statistics.median(v):+.3f} max {max(v):+.3f}")
    for floor in (0.02, 0.03, 0.04, 0.05):
        print(f"   caught at >{floor:.2f}: {sum(1 for d in v if d > floor)}/{len(v)}")
    print("   highest drops (these are all covered questions):")
    for d, q in vals[:6]:
        print(f"     {d:+.3f}  {q[:66]}")

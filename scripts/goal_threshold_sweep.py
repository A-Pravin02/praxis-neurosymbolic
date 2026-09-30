"""Does raising the goal-acceptance floor fix the not_covered failure, and what does it cost?

The audit's finding: on not_covered cases the router snaps to the nearest goal it
does have rules for and answers THAT question confidently. This sweeps the floor
and reports overall accuracy, hallucination, and the not_covered category alone.
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.hybrid import router
from app.evaluation import research

OUT = ROOT / "eval_reports" / "goal-sweep"
DEFAULT = router.GOAL_ACCEPT
print(f"shipped GOAL_ACCEPT = {DEFAULT}", flush=True)

rows = []
for goal in (0.60, 0.70, 0.75, 0.80, 0.85):
    router.GOAL_ACCEPT = goal
    rep = research.run("test", ["neurosymbolic"], out_dir=OUT / f"g{goal}",
                       workers=1, log=lambda m: None)
    o = rep["systems"]["neurosymbolic"]["overall"]
    cats = rep["systems"]["neurosymbolic"]["by_category"]
    nc = cats.get("not_covered", {})
    row = dict(goal=goal,
               acc=o["accuracy"]["value"], hall=o["hallucination_rate"]["value"],
               cov=o["coverage"]["value"], over=o["over_abstention"]["value"],
               sel=o["selective_accuracy"]["value"],
               nc_acc=nc.get("accuracy", {}).get("value"),
               nc_hall=nc.get("hallucination_rate", {}).get("value"))
    rows.append(row)
    print(json.dumps(row), flush=True)

router.GOAL_ACCEPT = DEFAULT
(OUT / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
print("\n| GOAL_ACCEPT | accuracy | halluc | coverage | over-abstain | selective | not_covered acc | not_covered halluc |")
print("|---|---|---|---|---|---|---|---|")
for r in rows:
    mark = " *(shipped)*" if r["goal"] == DEFAULT else ""
    print(f"| {r['goal']:.2f}{mark} | {r['acc']:.3f} | {r['hall']:.3f} | {r['cov']:.3f} | "
          f"{r['over']:.3f} | {r['sel']:.3f} | {r['nc_acc']:.2f} | {r['nc_hall']:.2f} |")

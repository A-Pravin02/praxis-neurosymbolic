"""How the router's acceptance thresholds were chosen.

The router turns two similarity scores into a routing decision:

    GOAL_ACCEPT       a goal this close to the question is acted on
    RETRIEVAL_ACCEPT  a policy clause this relevant answers an informational question

Both were originally hand-set, which is not a defensible answer to "why 0.60?". This
script replaces the hand-setting with a procedure.

It sweeps a grid on ``benchmark/dev.yaml`` — the file that exists for exactly this and
says so in its own header ("used ONLY to calibrate similarity thresholds") — and scores
each point on whether the router reaches the goal the question is labelled with. The
test split is never read here, so the accuracy reported on it afterwards is still
measured on questions no threshold was fitted to.

    python scripts/threshold_sweep.py
    python scripts/threshold_sweep.py --goal-grid 0.55,0.60,0.65

Two numbers are reported per point:

  goal accuracy   the router reached the labelled goal
  silence rate    the router reached no goal at all

A high threshold buys precision with silence, so both are needed: a point that routes
nothing is not a good point, and only reporting accuracy would hide that.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
warnings.filterwarnings("ignore")

from app.hybrid import router  # noqa: E402
from app.hybrid.controller import NeuroSymbolicController  # noqa: E402
from app.hybrid.knowledge_pack import load_pack  # noqa: E402
from app.neuro.providers.mock import MockLLMProvider  # noqa: E402

DEFAULTS = (router.GOAL_ACCEPT, router.RETRIEVAL_ACCEPT)
# A support agent: authorised over the workspace, so a routing miss is a routing miss
# and never an authorisation refusal wearing its clothes.
ACTOR = "sam@meridian.test"
ROLES = ("support_agent",)


def load_dev(pack_dir: Path) -> list[dict]:
    cases = yaml.safe_load((pack_dir / "benchmark" / "dev.yaml").read_text(
        encoding="utf-8"))["cases"]
    return [c for c in cases if c.get("q") and c.get("goal")]


def score_at(controller, cases: list[dict], goal: float, retrieval: float) -> dict:
    router.GOAL_ACCEPT, router.RETRIEVAL_ACCEPT = goal, retrieval
    hit = silent = 0
    try:
        for case in cases:
            answer = controller.answer(case["q"], actor_email=ACTOR, actor_roles=ROLES)
            if answer.goal == case["goal"]:
                hit += 1
            elif not answer.goal:
                silent += 1
    finally:
        router.GOAL_ACCEPT, router.RETRIEVAL_ACCEPT = DEFAULTS
    n = len(cases)
    return {"goal_accept": goal, "retrieval_accept": retrieval,
            "goal_accuracy": hit / n, "silence_rate": silent / n, "n": n}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-grid", default="0.50,0.55,0.60,0.65,0.70")
    parser.add_argument("--retrieval-grid", default="0.45,0.50,0.55,0.60,0.65")
    parser.add_argument("--out", type=Path, default=ROOT / "eval_reports")
    args = parser.parse_args()

    pack = load_pack(None, "semantic")
    cases = load_dev(pack.directory)
    controller = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
    print(f"{len(cases)} labelled dev questions\n", flush=True)

    rows = []
    for g in [float(x) for x in args.goal_grid.split(",")]:
        for r in [float(x) for x in args.retrieval_grid.split(",")]:
            row = score_at(controller, cases, g, r)
            rows.append(row)
            print(f"goal={g:.2f} retrieval={r:.2f}  goal_accuracy={row['goal_accuracy']:.3f} "
                  f"silence={row['silence_rate']:.3f}", flush=True)

    best = max(rows, key=lambda r: (r["goal_accuracy"], -r["silence_rate"]))
    shipped = next((r for r in rows
                    if (r["goal_accept"], r["retrieval_accept"]) == DEFAULTS), None)

    lines = [f"# Router threshold sweep — dev split ({rows[0]['n']} labelled questions)", "",
             f"Shipped defaults: GOAL_ACCEPT {DEFAULTS[0]}, RETRIEVAL_ACCEPT {DEFAULTS[1]}.",
             "",
             "Selection is on `benchmark/dev.yaml` only. The test split is not read by this "
             "script, so the accuracy reported on it elsewhere is measured on questions no "
             "threshold was fitted to.", "",
             "| GOAL_ACCEPT | RETRIEVAL_ACCEPT | Goal accuracy | Silence rate |",
             "|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (-r["goal_accuracy"], r["silence_rate"])):
        mark = " **(best)**" if r is best else ""
        tag = " *(shipped)*" if (r["goal_accept"], r["retrieval_accept"]) == DEFAULTS else ""
        lines.append(f"| {r['goal_accept']:.2f}{tag} | {r['retrieval_accept']:.2f} | "
                     f"{r['goal_accuracy']:.3f}{mark} | {r['silence_rate']:.3f} |")
    varies = len({r["goal_accuracy"] for r in rows
                  if r["goal_accept"] == rows[0]["goal_accept"]}) > 1
    if not varies:
        lines += ["", "**RETRIEVAL_ACCEPT does not move this metric.** It gates the path taken "
                      "when no goal is reached, so goal accuracy cannot see it. This sweep "
                      "therefore justifies GOAL_ACCEPT only; RETRIEVAL_ACCEPT needs its own "
                      "objective over informational questions, and is still hand-set."]
    step = 1.0 / rows[0]["n"]
    lines += ["", f"With {rows[0]['n']} dev questions one question is {step:.3f}, so "
                  f"differences of that size are a single case, not a trend."]
    lines += ["", f"Best on dev: GOAL_ACCEPT {best['goal_accept']:.2f}, "
                  f"RETRIEVAL_ACCEPT {best['retrieval_accept']:.2f} "
                  f"(goal accuracy {best['goal_accuracy']:.3f}, "
                  f"silence {best['silence_rate']:.3f})."]
    if shipped:
        lines += ["", f"Shipped point scores {shipped['goal_accuracy']:.3f} with "
                      f"{shipped['silence_rate']:.3f} silence."]
        if (shipped["goal_accept"], shipped["retrieval_accept"]) != \
                (best["goal_accept"], best["retrieval_accept"]):
            lines.append("The swept best differs from what ships; changing the default is a "
                         "separate decision, taken deliberately and re-measured on test.")
    text = "\n".join(lines)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    base = args.out / f"threshold-sweep-dev-{stamp}"
    base.with_suffix(".json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    base.with_suffix(".md").write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n" + text)
    print(f"\nwrote {base}.json and .md")


if __name__ == "__main__":
    main()

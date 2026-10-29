"""Record the ten demo sample cases to artifacts/demo_suite.json, with honest timings.

    python scripts/record_demo_suite.py

The project report reads this file. It used to be whatever `/api/suite` happened to
return from a running server, and that turned out to matter: the encoder memoises
embeddings by text (app/neural/encoder.py), so a server that had already answered these
questions answered them again about forty times faster. The recorded 39.9 ms for the set
was a cache-warm repeat, not the cost of answering ten questions.

Measured here in one cold process, after building the controller so that pack loading is
excluded from both passes:

    first pass    1436 ms for the set, 143.6 ms per case, of which the first case is
                  750 ms because it builds the goal catalogue and the policy index
    second pass     30.6 ms for the set, 3.1 ms per case

Both are real and they answer different questions, so both are recorded. `elapsed_ms`
and each case's `latency_ms` are the **first pass** - the cost of answering a question
the process has not seen - because that is what the benchmark's mean latency measures
and therefore the only one comparable to it. The repeat figures are kept alongside under
`repeat`, and the report states which is which.
"""
from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo"))

OUT = ROOT / "artifacts/demo_suite.json"


def run_pass(demo, cases) -> tuple[list[dict], float, list[float]]:
    started = time.perf_counter()
    results, per = [], []
    for case in cases:
        before = time.perf_counter()
        results.append(demo.run_case(case))
        per.append((time.perf_counter() - before) * 1000)
    return results, (time.perf_counter() - started) * 1000, per


def main() -> None:
    import server as demo

    cases = demo.sample_cases()
    # Build the controller first, as `demo/server.py --warm` does. Pack loading is a
    # start-up cost, not a per-question one, and counting it would misattribute it.
    demo.controller(cases[0]["world"])

    cold_results, cold_total, cold_per = run_pass(demo, cases)
    _, warm_total, warm_per = run_pass(demo, cases)

    correct = sum(1 for r in cold_results if r["verdict"]["correct"])
    payload = {
        "elapsed_ms": round(cold_total, 1),
        "total": len(cold_results),
        "correct": correct,
        "results": cold_results,
        "timing": {
            "protocol": (
                "One cold process. The controller is built first, so pack and model "
                "loading are excluded. elapsed_ms and each case's latency_ms are the "
                "first time this process answers that question, which is what the "
                "benchmark's mean latency measures. The first case also builds the goal "
                "catalogue and the policy index."
            ),
            "first_pass_ms": round(cold_total, 1),
            "first_pass_mean_ms": round(statistics.fmean(cold_per), 1),
            "first_case_ms": round(cold_per[0], 1),
            "repeat_pass_ms": round(warm_total, 1),
            "repeat_mean_ms": round(statistics.fmean(warm_per), 1),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    t = payload["timing"]
    print(f"{correct}/{len(cold_results)} correct")
    print(f"first pass  {t['first_pass_ms']:8.1f} ms for the set, "
          f"{t['first_pass_mean_ms']:.1f} ms per case "
          f"(first case {t['first_case_ms']:.1f} ms: index construction)")
    print(f"repeat pass {t['repeat_pass_ms']:8.1f} ms for the set, "
          f"{t['repeat_mean_ms']:.1f} ms per case")
    print(f"written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

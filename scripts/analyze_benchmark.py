"""Paired statistical analysis of a benchmark report.

Phase 6.3 of the specification: preserve per-question results, compute paired
differences, report confidence intervals, use a suitable test, and describe an
improvement that is not statistically persuasive as inconclusive.

    python scripts/analyze_benchmark.py eval_reports/ns-benchmark-...-copilot-....json
    python scripts/analyze_benchmark.py <report.json> --baseline llm_rag_rules

Because every system answers the *same* questions, the comparisons are paired. That
matters: a paired test on 53 shared items is far more sensitive than treating the two
systems as independent samples, and it is the only honest way to use a set this small.

Two tests are reported for each binary metric:
  * **McNemar's exact test** on the discordant pairs - the standard test for two
    classifiers on one sample. Exact (binomial), not the chi-square approximation,
    because discordant counts here are small.
  * **A paired bootstrap** over questions, giving a confidence interval for the
    difference in rates. Resampling questions, not answers, is what makes the interval
    reflect the uncertainty that comes from having only this many questions.

No result is called significant on the strength of an aggregate difference alone.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

# Binary per-case fields worth comparing, and the direction that counts as better.
METRICS = {
    "correct": "higher",
    "hallucinated": "lower",
    "compliant": "higher",
}
BOOTSTRAP_RESAMPLES = 20000
ALPHA = 0.05


def _per_case(report: dict, system: str, metric: str) -> dict[str, int]:
    """Map case id -> 0/1 for one metric. Missing or non-boolean values are dropped."""
    out: dict[str, int] = {}
    for case in report["systems"][system]["cases"]:
        value = case.get(metric)
        if isinstance(value, bool):
            out[case["id"]] = int(value)
    return out


def mcnemar_exact(a: list[int], b: list[int]) -> tuple[int, int, float]:
    """Exact two-sided McNemar test.

    Only the discordant pairs carry information: cases where both systems agree tell us
    nothing about which is better. Under the null the discordant split is Binomial(n, ½).
    """
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    n = only_a + only_b
    if n == 0:
        return only_a, only_b, 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return only_a, only_b, min(1.0, 2 * tail)


def paired_bootstrap(a: list[int], b: list[int], resamples: int = BOOTSTRAP_RESAMPLES,
                     seed: int = 0) -> tuple[float, float, float]:
    """Percentile CI for mean(a) - mean(b), resampling question indices together."""
    rng = random.Random(seed)
    n = len(a)
    observed = sum(a) / n - sum(b) / n
    diffs = []
    indices = range(n)
    for _ in range(resamples):
        pick = [rng.randrange(n) for _ in indices]
        diffs.append(sum(a[i] for i in pick) / n - sum(b[i] for i in pick) / n)
    diffs.sort()
    lo = diffs[int((ALPHA / 2) * resamples)]
    hi = diffs[min(resamples - 1, int((1 - ALPHA / 2) * resamples))]
    return observed, lo, hi


def compare(report: dict, system: str, baseline: str) -> list[dict]:
    rows = []
    for metric, direction in METRICS.items():
        sa, sb = _per_case(report, system, metric), _per_case(report, baseline, metric)
        shared = sorted(set(sa) & set(sb))
        if not shared:
            continue
        a = [sa[i] for i in shared]
        b = [sb[i] for i in shared]
        only_a, only_b, p = mcnemar_exact(a, b)
        observed, lo, hi = paired_bootstrap(a, b)
        # "Persuasive" requires both: a significant test AND an interval that excludes
        # no-difference. Either one alone is easy to over-read on 53 items.
        ci_excludes_zero = (lo > 0) or (hi < 0)
        favours = (observed > 0) == (direction == "higher")
        rows.append({
            "metric": metric, "better_is": direction, "n_paired": len(shared),
            f"{system}_rate": round(sum(a) / len(a), 4),
            f"{baseline}_rate": round(sum(b) / len(b), 4),
            "difference": round(observed, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "discordant": {f"only_{system}": only_a, f"only_{baseline}": only_b},
            "mcnemar_p": round(p, 6),
            "verdict": (
                ("favours " + system if favours else "favours " + baseline)
                if (p < ALPHA and ci_excludes_zero) else "inconclusive"),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--system", default="neurosymbolic")
    ap.add_argument("--baseline", default=None,
                    help="compare against one system; default is every other system")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the analysis as JSON next to the report")
    args = ap.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    available = list(report["systems"])
    if args.system not in available:
        ap.error(f"{args.system} not in report. Available: {', '.join(available)}")
    baselines = [args.baseline] if args.baseline else [s for s in available
                                                       if s != args.system]

    analysis = {
        "report": str(args.report),
        "split": report["split"],
        "cases": report["cases"],
        "llm_provider": report.get("llm_provider"),
        "llm_model": report.get("llm_model"),
        "system": args.system,
        "test": "McNemar exact (two-sided) + paired percentile bootstrap, alpha=0.05",
        "resamples": BOOTSTRAP_RESAMPLES,
        "comparisons": {},
    }

    print(f"{args.system} vs each baseline — {report['split']} split, "
          f"{report['cases']} cases, model {report.get('llm_model') or 'n/a'}\n")
    header = (f"{'baseline':22s} {'metric':12s} {'ours':>6s} {'theirs':>7s} "
              f"{'diff':>7s} {'95% CI':>18s} {'p':>9s}  verdict")
    print(header)
    print("-" * len(header))
    for baseline in baselines:
        rows = compare(report, args.system, baseline)
        analysis["comparisons"][baseline] = rows
        for r in rows:
            ci = f"[{r['ci95'][0]:+.3f},{r['ci95'][1]:+.3f}]"
            print(f"{baseline:22s} {r['metric']:12s} "
                  f"{r[f'{args.system}_rate']:6.3f} {r[f'{baseline}_rate']:7.3f} "
                  f"{r['difference']:+7.3f} {ci:>18s} {r['mcnemar_p']:9.5f}  "
                  f"{r['verdict']}")
        print()

    out = args.out or args.report.with_name(args.report.stem + "-stats.json")
    out.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

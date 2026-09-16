"""Run the research benchmark and write the report.

    python scripts/run_research.py --split dev --systems neurosymbolic rag_extractive
    LLM_PROVIDER=local LOCAL_LLM_MODEL=C:/models/Qwen2.5-7B-Instruct \
        python scripts/run_research.py --split test --systems llm_only llm_rag ... --tag qwen7b

Per-case rows are appended to eval_reports/research/<tag>/<split>.<system>.jsonl as they
finish, so an interrupted run resumes where it stopped and never re-asks a model a
question it has already answered. The report is rebuilt from those rows every time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.evaluation import research  # noqa: E402

METRICS = [
    ("accuracy", "Accuracy", "higher"),
    ("hallucination_rate", "Hallucination rate", "lower"),
    ("wrong_definite_rate", "  wrong definite answer", "lower"),
    ("unsupported_definite_rate", "  definite answer without support", "lower"),
    ("leak_rate", "Disclosure rate (unauthorized)", "lower"),
    ("coverage", "Coverage (answerable questions)", "higher"),
    ("over_abstention", "Over-abstention", "lower"),
    ("selective_accuracy", "Selective accuracy", "higher"),
    ("answers_with_unsupported_number", "Answers with an unsupported number", "lower"),
]


def _cell(metric: dict | float | None) -> str:
    if metric is None:
        return "n/a"
    if isinstance(metric, dict):
        if metric.get("value") is None:
            return "n/a"
        ci = metric.get("ci95")
        return f"{metric['value']:.3f} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else f"{metric['value']:.3f}"
    return f"{metric:.3f}" if isinstance(metric, float) else str(metric)


def render(report: dict, tag: str) -> str:
    systems = list(report["systems"])
    lines = [f"# Research benchmark — {report['split']} split ({report['cases']} cases)", "",
             f"Run tag: `{tag}` · LLM provider: `{os.environ.get('LLM_PROVIDER', 'mock')}` · "
             f"model: `{os.environ.get('LOCAL_LLM_MODEL', os.environ.get('LLM_MODEL', 'n/a'))}`",
             "", "Values are rates with 95% bootstrap intervals.", "",
             "| Metric | " + " | ".join(systems) + " | better |",
             "|---|" + "---|" * len(systems) + "---|"]
    for key, label, better in METRICS:
        lines.append(f"| {label} | " + " | ".join(
            _cell(report["systems"][s]["overall"].get(key)) for s in systems) + f" | {better} |")
    lines.append("| Share of stated numbers unsupported | " + " | ".join(
        _cell(report["systems"][s]["overall"].get("unsupported_number_share"))
        for s in systems) + " | lower |")
    lines.append("| Mean latency (ms) | " + " | ".join(
        f"{report['systems'][s]['overall']['mean_latency_ms']:.0f}" for s in systems) + " | lower |")
    for name, reason in report.get("not_run", {}).items():
        lines += ["", f"`{name}` not run: {reason}."]
    categories = sorted({c for s in systems for c in report["systems"][s]["by_category"]})
    for key, title in (("accuracy", "Accuracy by category"),
                       ("hallucination_rate", "Hallucination rate by category")):
        lines += ["", f"## {title}", "", "| Category | n | " + " | ".join(systems) + " |",
                  "|---|---|" + "---|" * len(systems)]
        for category in categories:
            first = report["systems"][systems[0]]["by_category"].get(category, {})
            lines.append(f"| {category} | {first.get('cases', '')} | " + " | ".join(
                f"{report['systems'][s]['by_category'].get(category, {}).get(key, {}).get('value', 0):.2f}"
                if report['systems'][s]['by_category'].get(category, {}).get(key, {}).get('value')
                is not None else "n/a" for s in systems) + " |")
    if report.get("significance"):
        lines += ["", "## Paired comparison with `neurosymbolic` (exact McNemar)", "",
                  "| System | correct only NS | correct only system | p | hallucinated only NS | "
                  "hallucinated only system | p |", "|---|---|---|---|---|---|---|"]
        for name, tests in report["significance"].items():
            c, h = tests["correct"], tests["hallucinated"]
            lines.append(f"| {name} | {c['only_a']} | {c['only_b']} | {c['p_value']:.2g} | "
                         f"{h['only_a']} | {h['only_b']} | {h['p_value']:.2g} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--systems", nargs="+", default=["neurosymbolic", "rag_extractive"])
    parser.add_argument("--tag", default="deterministic")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    out = ROOT / "eval_reports" / "research" / args.tag
    report = research.run(args.split, args.systems, out_dir=out, limit=args.limit,
                          workers=args.workers, log=lambda m: print(m, flush=True))
    slim = {**report, "systems": {k: {kk: vv for kk, vv in v.items() if kk != "rows"}
                                  for k, v in report["systems"].items()}}
    stem = f"{args.split}.{'+'.join(args.systems)}" if len(args.systems) < 4 else \
        f"{args.split}.report"
    (out / f"{stem}.json").write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    (out / f"{stem}.md").write_text(render(report, args.tag), encoding="utf-8")
    print(render(report, args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

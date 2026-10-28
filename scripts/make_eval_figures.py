"""Generate paper figures from benchmark report files.

    python scripts/make_figures.py                      # newest copilot report
    python scripts/make_figures.py --reports a.json b.json --out docs/paper/figures

Every figure is drawn from a report JSON on disk and is annotated with the source file,
the split, the case count and the base model. Nothing is typed in by hand, so a figure
can always be traced to the run that produced it.

If a report lacks a metric, the figure omits it rather than substituting a value.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                       # no display on a headless machine
import matplotlib.pyplot as plt             # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Presentation order: the proposed system first, then ablations, then LLM baselines.
ORDER = ["neurosymbolic", "neurosymbolic_lexical", "rag_extractive", "llm_only",
         "llm_cot", "llm_rag", "llm_rag_rules", "llm_rag_selfcheck"]
LABELS = {
    "neurosymbolic": "PRAXIS\n(full)",
    "neurosymbolic_lexical": "lexical\nablation",
    "rag_extractive": "extractive\nRAG",
    "llm_only": "LLM\nonly",
    "llm_cot": "LLM\n+CoT",
    "llm_rag": "LLM\n+RAG",
    "llm_rag_rules": "LLM+RAG\n+rules",
    "llm_rag_selfcheck": "LLM+RAG\n+selfcheck",
}
HIGHLIGHT = "#1a7f4b"
PLAIN = "#5c6270"


def _ordered(report: dict) -> list[str]:
    present = set(report["systems"])
    return [s for s in ORDER if s in present] + sorted(present - set(ORDER))


def _series(report: dict, metric: str) -> dict[str, float]:
    out = {}
    for name in _ordered(report):
        value = report["systems"][name]["overall"].get(metric)
        if isinstance(value, (int, float)):
            out[name] = float(value)
    return out


def _provenance(report: dict, path: Path) -> str:
    return (f"source: {path.name} · split={report['split']} · n={report['cases']} · "
            f"base model: {report.get('llm_model') or 'n/a'}")


def _bar(ax, values: dict[str, float], title: str, ylabel: str,
         fmt: str = "{:.3f}") -> None:
    names = list(values)
    colors = [HIGHLIGHT if n == "neurosymbolic" else PLAIN for n in names]
    bars = ax.bar(range(len(names)), [values[n] for n in names], color=colors)
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([LABELS.get(n, n) for n in names], fontsize=7)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_title(title, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    for bar, name in zip(bars, names):
        ax.annotate(fmt.format(values[name]),
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center", va="bottom", fontsize=6.5)


def figure_accuracy_and_hallucination(report: dict, path: Path, out: Path) -> Path | None:
    accuracy = _series(report, "final_answer_accuracy")
    hallucination = _series(report, "hallucination_rate")
    if not accuracy or not hallucination:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4))
    _bar(axes[0], accuracy, "Final answer accuracy (higher is better)", "accuracy")
    _bar(axes[1], hallucination, "Hallucination rate (lower is better)", "rate")
    for ax in axes:
        ax.set_ylim(0, 1.0)
    fig.suptitle("Per-system results", fontsize=11)
    fig.text(0.5, 0.005, _provenance(report, path), ha="center", fontsize=6.5,
             color="#555")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    target = out / "accuracy_hallucination.png"
    fig.savefig(target, dpi=200)
    plt.close(fig)
    return target


def figure_latency(report: dict, path: Path, out: Path) -> Path | None:
    latency = _series(report, "mean_latency_ms")
    if not latency:
        return None
    fig, ax = plt.subplots(figsize=(6.5, 4))
    _bar(ax, latency, "Mean end-to-end latency (lower is better)", "milliseconds",
         fmt="{:.0f}")
    # Log scale: the spread is two orders of magnitude, which a linear axis hides.
    ax.set_yscale("log")
    ax.set_ylabel("milliseconds (log scale)", fontsize=9)
    fig.text(0.5, 0.005, _provenance(report, path), ha="center", fontsize=6.5,
             color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    target = out / "latency.png"
    fig.savefig(target, dpi=200)
    plt.close(fig)
    return target


def figure_by_category(report: dict, path: Path, out: Path) -> Path | None:
    systems = [s for s in _ordered(report)
               if s in ("neurosymbolic", "llm_rag_rules", "llm_rag", "llm_only")
               and s in report["systems"]]
    if len(systems) < 2:
        return None
    categories = sorted({c for s in systems
                         for c in report["systems"][s]["by_category"]})
    if not categories:
        return None
    fig, ax = plt.subplots(figsize=(10, 4.2))
    width = 0.8 / len(systems)
    for i, name in enumerate(systems):
        by_cat = report["systems"][name]["by_category"]
        values, positions = [], []
        for j, cat in enumerate(categories):
            entry = by_cat.get(cat) or {}
            value = entry.get("final_answer_accuracy")
            if isinstance(value, (int, float)):
                values.append(float(value))
                positions.append(j + i * width - 0.4 + width / 2)
        ax.bar(positions, values, width=width,
               label=LABELS.get(name, name).replace("\n", " "),
               color=HIGHLIGHT if name == "neurosymbolic" else None)
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels([c.replace("_", "\n") for c in categories], fontsize=7.5)
    ax.set_ylabel("final answer accuracy", fontsize=9)
    ax.set_ylim(0, 1.05)
    ax.set_title("Accuracy by question category", fontsize=10)
    ax.legend(fontsize=7.5, frameon=False, ncol=len(systems))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    counts = ", ".join(
        f"{c}={(report['systems'][systems[0]]['by_category'].get(c) or {}).get('cases', '?')}"
        for c in categories)
    fig.text(0.5, 0.005, _provenance(report, path) + f" · cases per category: {counts}",
             ha="center", fontsize=6, color="#555")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    target = out / "accuracy_by_category.png"
    fig.savefig(target, dpi=200)
    plt.close(fig)
    return target


def figure_cross_model(reports: list[tuple[Path, dict]], out: Path) -> Path | None:
    """Does the architecture's advantage hold across base models? (E6)"""
    usable = [(p, r) for p, r in reports if r.get("llm_model")]
    if len(usable) < 2:
        return None
    models = [r["llm_model"].split("/")[-1] for _, r in usable]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax, metric, title, better in (
            (axes[0], "final_answer_accuracy", "Final answer accuracy", "higher"),
            (axes[1], "hallucination_rate", "Hallucination rate", "lower")):
        shared = [s for s in ORDER
                  if all(s in r["systems"] for _, r in usable)]
        width = 0.8 / len(usable)
        for i, (_, report) in enumerate(usable):
            series = _series(report, metric)
            ax.bar([j + i * width - 0.4 + width / 2 for j in range(len(shared))],
                   [series.get(s, 0.0) for s in shared], width=width,
                   label=models[i])
        ax.set_xticks(range(len(shared)))
        ax.set_xticklabels([LABELS.get(s, s) for s in shared], fontsize=7)
        ax.set_title(f"{title} ({better} is better)", fontsize=10)
        ax.set_ylim(0, 1.0)
        ax.legend(fontsize=7.5, frameon=False)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25, linewidth=0.5)
        ax.set_axisbelow(True)
    fig.suptitle("Cross-model comparison (E6): same questions, different base models",
                 fontsize=11)
    fig.text(0.5, 0.005, " · ".join(f"{m}: {p.name}" for (p, _), m
                                    in zip(usable, models)),
             ha="center", fontsize=6, color="#555")
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    target = out / "cross_model.png"
    fig.savefig(target, dpi=200)
    plt.close(fig)
    return target


def figure_training_curve(run_dir: Path, out: Path) -> Path | None:
    """Loss curves from a real training run's metrics.jsonl, if one exists."""
    metrics = run_dir / "metrics.jsonl"
    if not metrics.exists():
        return None
    train_x, train_y, val_x, val_y = [], [], [], []
    for line in metrics.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "train_loss" in record:
            train_x.append(record["step"])
            train_y.append(record["train_loss"])
        if "val_loss" in record:
            val_x.append(record["step"])
            val_y.append(record["val_loss"])
    if not train_y:
        return None
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(train_x, train_y, label="training loss", linewidth=1, color=PLAIN)
    if val_y:
        ax.plot(val_x, val_y, label="validation loss", linewidth=1.6,
                marker="o", markersize=3, color=HIGHLIGHT)
    ax.set_xlabel("step", fontsize=9)
    ax.set_ylabel("cross-entropy loss", fontsize=9)
    ax.set_title("From-scratch model training", fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    fig.text(0.5, 0.005, f"source: {metrics}", ha="center", fontsize=6.5, color="#555")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    target = out / "training_curve.png"
    fig.savefig(target, dpi=200)
    plt.close(fig)
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", nargs="*", type=Path, default=None,
                    help="report JSONs; default is the newest copilot report")
    ap.add_argument("--eval-dir", type=Path, default=ROOT / "eval_reports")
    ap.add_argument("--run-dir", type=Path, default=ROOT / "artifacts/slm/runs/small",
                    help="training run directory for the loss-curve figure")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/figures")
    args = ap.parse_args()

    paths = args.reports
    if not paths:
        candidates = sorted(args.eval_dir.glob("ns-benchmark-*copilot*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        candidates = [p for p in candidates if not p.name.endswith("-stats.json")]
        if not candidates:
            raise SystemExit(f"no copilot reports found in {args.eval_dir}")
        paths = candidates[:2]        # newest two: enables the cross-model figure

    reports = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if "systems" not in report:
            print(f"skipping {path.name}: not a benchmark report")
            continue
        reports.append((path, report))
    if not reports:
        raise SystemExit("no usable reports")

    args.out.mkdir(parents=True, exist_ok=True)
    primary_path, primary = reports[0]
    print(f"primary report: {primary_path.name} "
          f"(model {primary.get('llm_model') or 'n/a'})")

    written = [
        figure_accuracy_and_hallucination(primary, primary_path, args.out),
        figure_latency(primary, primary_path, args.out),
        figure_by_category(primary, primary_path, args.out),
        figure_cross_model(reports, args.out),
        figure_training_curve(args.run_dir, args.out),
    ]
    for target in written:
        if target:
            print(f"  wrote {target.relative_to(ROOT)}")
    skipped = sum(1 for t in written if t is None)
    if skipped:
        print(f"  {skipped} figure(s) skipped: the source data is not present yet")


if __name__ == "__main__":
    main()

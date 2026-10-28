"""Draw the main baseline comparison from the verified test-split report.

    python scripts/make_results_figure.py

Two panels rather than one. Accuracy and hallucination share a 0-1 axis but not a
denominator - accuracy is over every case, hallucination over every case, coverage only
over answerable ones - and plotting them as one grouped bar invites the reader to treat
them as commensurable. They are drawn side by side with the denominators named in the
axis labels instead.

Error bars are the 95% bootstrap intervals already stored in the report. Coverage is
drawn beside accuracy so a system cannot look good merely by refusing to answer.

Greyscale with hatching, matching the rest of the paper.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "eval_reports/research/paper/test.report.json"

ORDER = ["neurosymbolic", "ns_parser", "llm_rag_rules", "llm_rag_selfcheck",
         "llm_rag", "llm_only"]
LABEL = {"neurosymbolic": "PRAXIS\n(no LLM)", "ns_parser": "PRAXIS\n(LLM parser)",
         "llm_rag_rules": "LLM+RAG\n+rules", "llm_rag_selfcheck": "LLM+RAG\n+selfcheck",
         "llm_rag": "LLM\n+RAG", "llm_only": "LLM\nonly"}
OURS = ("neurosymbolic", "ns_parser")
FILL_OURS = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///", linewidth=0.9)
FILL_BASE = dict(facecolor="#9a9a9a", edgecolor="#000000", linewidth=0.9)


def value_ci(report: dict, system: str, key: str):
    entry = report["systems"][system]["overall"].get(key)
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("ci95")
    return entry, None


def panel(ax, report, systems, key, title, ylabel):
    vals, los, his, styles = [], [], [], []
    for s in systems:
        v, ci = value_ci(report, s, key)
        vals.append(v or 0.0)
        if ci:
            los.append(max(0.0, (v or 0) - ci[0]))
            his.append(max(0.0, ci[1] - (v or 0)))
        else:
            los.append(0.0)
            his.append(0.0)
        styles.append(FILL_OURS if s in OURS else FILL_BASE)

    xs = range(len(systems))
    for x, v, st in zip(xs, vals, styles):
        ax.bar(x, v, width=0.68, **st)
    ax.errorbar(list(xs), vals, yerr=[los, his], fmt="none", ecolor="#000000",
                elinewidth=0.9, capsize=2.5)
    for x, v in zip(xs, vals):
        ax.annotate(f"{v:.3f}", (x, v + (his[x] if x < len(his) else 0)),
                    ha="center", va="bottom", fontsize=5.4)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([LABEL.get(s, s) for s in systems], fontsize=5.6)
    ax.set_ylim(0, 1.12)
    ax.set_ylabel(ylabel, fontsize=6.0)
    ax.set_title(title, fontsize=7.0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)


def build(out: Path) -> Path:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    systems = [s for s in ORDER if s in report["systems"]]

    # Two panels, not three: drawn near its printed size so the labels stay legible,
    # a third panel would cost the height the page budget does not have. Coverage is
    # in Table II instead, beside over-abstention.
    fig, axes = plt.subplots(1, 2, figsize=(6.9, 1.95))
    panel(axes[0], report, systems, "accuracy",
          "Accuracy (higher better)", "correct / all cases")
    panel(axes[1], report, systems, "hallucination_rate",
          "Hallucination (lower better)", "hallucinated / all cases")

    fig.suptitle("Test split, 255 questions. Bars are 95% bootstrap intervals; "
                 "hatched bars are the proposed system.", fontsize=6.6)
    fig.text(0.5, 0.005,
             "source: eval_reports/research/paper/test.report.json · coverage is in Table II",
             ha="center", fontsize=5.2, color="#555555")
    fig.tight_layout(rect=(0, 0.05, 1, 0.90))
    out.mkdir(parents=True, exist_ok=True)
    target = out / "main_results.png"
    fig.savefig(target, dpi=300)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    fig.savefig(target.with_suffix('.svg'))
    plt.close(fig)
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/figures")
    args = ap.parse_args()
    if not REPORT.exists():
        raise SystemExit(f"missing {REPORT}")
    print(f"  wrote {build(args.out).relative_to(ROOT)}")


if __name__ == "__main__":
    main()

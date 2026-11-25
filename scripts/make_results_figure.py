"""Draw the main baseline comparison from the verified test-split report.

    python scripts/make_results_figure.py

Two panels rather than one. Accuracy and hallucination share a 0-1 axis but not a
denominator - accuracy is over every case, hallucination over every case, coverage only
over answerable ones - and plotting them as one grouped bar invites the reader to treat
them as commensurable. They are drawn side by side with the denominators named in the
axis labels instead.

Error bars are the 95% bootstrap intervals already stored in the report. Coverage is
drawn beside accuracy so a system cannot look good merely by refusing to answer.

Every bar is white with a black outline; the proposed systems are hatched and the
baselines are plain. Nothing in the figure depends on a fill tone, so it survives a
greyscale print and a photocopy unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
# A fixed salt makes the SVG's element ids deterministic; without it they come from
# object addresses and every build rewrites the file without changing the drawing.
matplotlib.rcParams["svg.hashsalt"] = "praxis-results"
import matplotlib.pyplot as plt   # noqa: E402
from _paths import show               # noqa: E402  (after matplotlib setup)

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "eval_reports/research/paper/test.report.json"

ORDER = ["neurosymbolic", "ns_parser", "llm_rag_rules", "llm_rag_selfcheck",
         "llm_rag", "llm_only", "rag_extractive"]
# Seven bars where there were six, so the parenthesised labels no longer fit their
# slot and ran into their neighbours. Shortened to what distinguishes each system.
LABEL = {"neurosymbolic": "PRAXIS\nno LLM", "ns_parser": "PRAXIS\nparser",
         "llm_rag_rules": "LLM+RAG\n+rules", "llm_rag_selfcheck": "LLM+RAG\n+check",
         "llm_rag": "LLM\n+RAG", "llm_only": "LLM\nonly",
         "rag_extractive": "Extractive\nRAG"}
OURS = ("neurosymbolic", "ns_parser")
# Every bar is white. The grey fill the baselines used to carry prints as a muddy
# mid-tone on a laser printer and photocopies worse, and it carried no information the
# hatching does not: the proposed systems are hatched, the baselines are plain, and
# both read the same in colour, in greyscale and in a photocopy of a photocopy.
FILL_OURS = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///", linewidth=0.9)
FILL_BASE = dict(facecolor="#ffffff", edgecolor="#000000", linewidth=0.9)


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
    # The printed value over each bar is gone with the width: at one column the
    # labels collided with their own error bars, and Table II beside the figure gives
    # every one of these numbers to three places. A bar chart that has to be read to
    # three places was the wrong instrument anyway; this one shows the shape.
    ax.set_xticks(list(xs))
    # Numbered, not named. At one column each of the seven slots is a third of an
    # inch, which no system name fits; the numbers are the row order of Table II,
    # which carries the names and the same values to three places.
    ax.set_xticklabels([str(i + 1) for i in range(len(systems))], fontsize=6.0)
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
    # One column wide, not two. Spanning the page cost roughly thirty lines of
    # column space for two panels of seven bars each, and the numbers are now
    # tabulated beside it, so the figure only has to show the shape of the comparison.
    fig, axes = plt.subplots(1, 2, figsize=(3.30, 1.45))
    panel(axes[0], report, systems, "accuracy",
          "Accuracy", "correct / all cases")
    panel(axes[1], report, systems, "hallucination_rate",
          "Hallucination", "hallucinated / all cases")

    # No title and no footer: the caption carries both, and reclaiming the bands they
    # reserved removes the blank gap above the plots and lets the panels print larger.
    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    target = out / "main_results.png"
    fig.savefig(target, dpi=300)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    # metadata={"Date": None} drops the embedded timestamp, so an unchanged figure
    # produces an unchanged file and a rebuild leaves the working tree clean.
    fig.savefig(target.with_suffix('.svg'), metadata={"Date": None})
    plt.close(fig)
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/figures")
    args = ap.parse_args()
    if not REPORT.exists():
        raise SystemExit(f"missing {REPORT}")
    print(f"  wrote {show(build(args.out))}")


if __name__ == "__main__":
    main()

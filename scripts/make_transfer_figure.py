"""Draw the test-versus-confirmatory comparison for both PRAXIS configurations.

    python scripts/make_transfer_figure.py

The question this answers is the one a reader should ask of any result tuned on the
data it is reported on: does it survive companies the system has never seen? The
confirmatory split is five new worlds, written after the system was final and scored
once, so the pair of bars per configuration is the whole transfer claim.

It is drawn because the two configurations behave differently, and the difference is
easy to miss in prose. The LLM-free configuration transfers essentially unchanged. The
parser configuration does not: its accuracy falls, and its selective accuracy falls off
a perfect score on the test split, which is exactly the number a reader would otherwise
carry away as the system's reliability.

Error bars are the 95% bootstrap intervals already stored in the two reports - 2,000
seeded resamples of cases, percentile method - not recomputed here. Greyscale with
hatching, matching the rest of the document.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "eval_reports/research/paper/test.report.json"
CONFIRM = ROOT / "eval_reports/research/confirm/confirm.report.json"

SYSTEMS = [("neurosymbolic", "PRAXIS\n(no LLM)"), ("ns_parser", "PRAXIS\n(LLM parser)")]
PANELS = [("accuracy", "Accuracy\n(all cases)"),
          ("selective_accuracy", "Selective accuracy\n(cases answered definitely)")]
FILL_TEST = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///", linewidth=0.9)
FILL_CONF = dict(facecolor="#9a9a9a", edgecolor="#000000", linewidth=0.9)


def value_ci(report: dict, system: str, key: str):
    entry = report["systems"][system]["overall"].get(key)
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("ci95")
    return entry, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs/paper/figures/transfer.png"))
    args = ap.parse_args()

    test = json.loads(TEST.read_text(encoding="utf-8"))
    confirm = json.loads(CONFIRM.read_text(encoding="utf-8"))

    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.5), dpi=400)
    width = 0.34
    for ax, (key, title) in zip(axes, PANELS):
        positions = range(len(SYSTEMS))
        for offset, (report, fill, label) in enumerate((
                (test, FILL_TEST, "Test (255)"), (confirm, FILL_CONF, "Confirmatory (255)"))):
            values, errs = [], [[], []]
            for name, _ in SYSTEMS:
                value, ci = value_ci(report, name, key)
                values.append(value)
                # Asymmetric bars: the stored interval is a percentile interval and is
                # not centred on the point estimate, so halving a width would move it.
                errs[0].append(0.0 if not ci else max(0.0, value - ci[0]))
                errs[1].append(0.0 if not ci else max(0.0, ci[1] - value))
            xs = [p + (offset - 0.5) * width for p in positions]
            ax.bar(xs, values, width, yerr=errs, capsize=2.5, label=label,
                   error_kw=dict(linewidth=0.8), **fill)
            # Above the interval's upper cap, not above the bar: sitting at a fixed
            # offset from the value put the number through the error bar.
            for x, value, upper in zip(xs, values, errs[1]):
                ax.text(x, value + upper + 0.03, f"{value:.3f}", ha="center",
                        fontsize=6.0)
        ax.set_xticks(list(positions))
        ax.set_xticklabels([label for _, label in SYSTEMS], fontsize=7)
        ax.set_ylim(0, 1.18)
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.tick_params(labelsize=7)
        ax.set_title(title, fontsize=7.5, pad=16)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("rate", fontsize=7.5)
    # Above the axes rather than inside them: in the panel the bars reach 1.0 and any
    # in-axes corner the legend can take is already occupied by a bar.
    axes[0].legend(fontsize=6.5, frameon=False, ncol=2, loc="lower left",
                   bbox_to_anchor=(0.0, 1.0))
    fig.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)

    for name, label in SYSTEMS:
        for key, _ in PANELS:
            t, _ = value_ci(test, name, key)
            c, _ = value_ci(confirm, name, key)
            print(f"  {name:16} {key:20} test {t:.3f} -> confirm {c:.3f} "
                  f"({c - t:+.3f})")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

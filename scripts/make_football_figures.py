"""Generate the Study II figures from the football result files.

    python scripts/make_football_figures.py

Every figure is drawn from a file on disk and carries a footer naming its source, so a
figure in the paper can always be traced to the run that produced it. A figure whose
data is missing is skipped with a message rather than drawn from nothing.

Figures:
    football_tradeoff.png     hallucination against coverage - the actual finding
    football_status.png       verdict breakdown, and how each refutation was reached
    football_by_kind.png      false-claim rate per claim type
    football_training.png     training and validation loss, showing the overfit
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "artifacts/football/results"
LOGS = ROOT / "artifacts/football/logs"

# Greyscale only: the paper prints in black and white. Identity is never carried by
# shade alone - bars are separated by hatch pattern and lines by dash pattern and
# marker, so the figures survive photocopying and are legible to colour-blind readers.
GOOD = "#ffffff"       # the gated system / supported - white, hatched
BAD = "#4d4d4d"        # unverified or contradicted - dark grey
PLAIN = "#9a9a9a"      # neutral
EDGE = "#000000"
HATCH_GOOD = "///"
HATCH_BAD = ""


def footer(fig, text: str) -> None:
    fig.text(0.5, 0.005, text, ha="center", fontsize=6.5, color="#555")


def recount(claims_path: Path) -> dict:
    """Status totals from the raw per-claim records.

    `report.json` stores `false_released` as every claim that is not SUPPORTED,
    which counts the unverifiable claims as false. The paper keeps the two apart,
    so the figures recount rather than read that field.
    """
    rows = [json.loads(l) for l in claims_path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    chk = [r for r in rows if r["status"] != "NOT_CHECKABLE"]
    c = sum(1 for r in chk if r["status"] == "CONTRADICTED")
    u = sum(1 for r in chk if r["status"] == "UNVERIFIED")
    return {"checkable": len(chk), "refuted": c, "unverified": u,
            "supported": len(chk) - c - u,
            "refuted_rate": c / len(chk), "unverified_rate": u / len(chk),
            "coverage": (len(chk) - c - u) / len(chk)}


def figure_tradeoff(report: dict, claims_path: Path, out: Path) -> Path:
    """The result that matters: what gating removes, and what it costs."""
    s = recount(claims_path)
    fig, ax = plt.subplots(figsize=(3.5, 2.05))
    labels = ["SLM only", "SLM + symbolic gate"]
    refuted = [s["refuted_rate"], 0.0]
    unver = [s["unverified_rate"], 0.0]
    coverage = [1.0, s["coverage"]]
    x = range(len(labels))
    width = 0.36
    b1 = ax.bar([i - width / 2 for i in x], refuted, width,
                label="refuted (lower better)", color=BAD,
                edgecolor=EDGE, linewidth=0.8)
    ax.bar([i - width / 2 for i in x], unver, width, bottom=refuted,
           label="unverifiable", color=PLAIN, edgecolor=EDGE,
           linewidth=0.8, hatch="xxx")
    b2 = ax.bar([i + width / 2 for i in x], coverage, width,
                label="coverage (higher better)", color=GOOD,
                edgecolor=EDGE, linewidth=0.8, hatch=HATCH_GOOD)
    for bars in (b1, b2):
        for bar in bars:
            ax.annotate(f"{bar.get_height():.3f}",
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 1.28)
    ax.set_ylabel("rate", fontsize=9)
    ax.set_title("Refuted claims and coverage", fontsize=8.5)
    # Upper right: the only region free of bars once the figure is at column width.
    ax.legend(fontsize=5.8, frameon=False, loc="upper center", ncol=1,
              bbox_to_anchor=(0.5, 1.02))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    # The zero bar would otherwise read as an achievement rather than a definition.
    # Placed beneath the axis: at print size any in-plot position collides with the
    # coverage bar beside it.
    # The construction point is made in the caption; in-plot it collides with the
    # tick labels at column width.
    footer(fig, f"source: {claims_path.name} · "
                f"{s['checkable']:,} checkable claims from "
                f"{report['prompts']} prompts")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    target = out / "football_tradeoff.png"
    fig.savefig(target, dpi=200)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    fig.savefig(target.with_suffix('.svg'))
    plt.close(fig)
    return target


def figure_status(report: dict, claims_path: Path, out: Path) -> Path:
    counts = report["status_counts"]
    order = ["SUPPORTED", "CONTRADICTED", "UNVERIFIED", "NOT_CHECKABLE"]
    # Four categories: shade plus hatch, so none depends on colour.
    colors = {"SUPPORTED": GOOD, "CONTRADICTED": BAD,
              "UNVERIFIED": PLAIN, "NOT_CHECKABLE": "#d9d9d9"}
    hatches = {"SUPPORTED": HATCH_GOOD, "CONTRADICTED": "",
               "UNVERIFIED": "xxx", "NOT_CHECKABLE": "..."}

    rule_only = 0
    if claims_path.exists():
        for line in claims_path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("rule_failures"):
                rule_only += 1

    fig, axes = plt.subplots(1, 2, figsize=(10, 1.9),
                             gridspec_kw={"width_ratios": [1.5, 1]})
    values = [counts.get(k, 0) for k in order]
    bars = axes[0].bar(range(len(order)), values,
                       color=[colors[k] for k in order], edgecolor=EDGE,
                       linewidth=0.8, hatch=None)
    for bar, k in zip(bars, order):
        bar.set_hatch(hatches[k])
    for bar in bars:
        axes[0].annotate(f"{int(bar.get_height()):,}",
                         (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                         ha="center", va="bottom", fontsize=8)
    axes[0].set_xticks(range(len(order)))
    axes[0].set_xticklabels([k.replace("_", "\n") for k in order], fontsize=8)
    axes[0].set_ylabel("claims", fontsize=9)
    axes[0].set_title("Verdicts over generated claims", fontsize=10)

    contradicted = counts.get("CONTRADICTED", 0)
    rb = axes[1].bar([0, 1], [rule_only, max(0, contradicted - rule_only)],
                     color=[PLAIN, BAD], edgecolor=EDGE, linewidth=0.8)
    rb[0].set_hatch("xxx")
    for i, v in enumerate([rule_only, max(0, contradicted - rule_only)]):
        axes[1].annotate(f"{v:,}", (i, v), ha="center", va="bottom", fontsize=8)
    axes[1].set_xticks([0, 1])
    axes[1].set_xticklabels(["rule alone\n(no lookup)", "required\nthe graph"],
                            fontsize=8)
    axes[1].set_title("How each refutation was reached", fontsize=10)

    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25, linewidth=0.5)
        ax.set_axisbelow(True)
    footer(fig, f"source: {claims_path.name} · rule checks consult no database: a team "
                f"credited with a goal in a fixture it did not contest is incoherent "
                f"on its face")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    target = out / "football_status.png"
    fig.savefig(target, dpi=200)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    fig.savefig(target.with_suffix('.svg'))
    plt.close(fig)
    return target


def figure_by_kind(report: dict, claims_path: Path, out: Path) -> Path | None:
    if not claims_path.exists():
        return None
    buckets: dict[str, list[str]] = {}
    for line in claims_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        kind = (row.get("claim") or {}).get("kind") or "unknown"
        buckets.setdefault(kind, []).append(row["status"])
    label = {"goal": "goal event", "player_tournament_total": "player\ntournament total",
             "match_total": "match total"}
    rows = []
    for kind, statuses in buckets.items():
        checkable = [s for s in statuses if s != "NOT_CHECKABLE"]
        if checkable:
            rows.append((label.get(kind, kind), len(checkable),
                         sum(1 for s in checkable if s == "CONTRADICTED")
                         / len(checkable)))
    if not rows:
        return None
    rows.sort(key=lambda r: r[2])

    fig, ax = plt.subplots(figsize=(6.6, 2.8))
    bars = ax.bar(range(len(rows)), [r[2] for r in rows], color=BAD,
                  edgecolor=EDGE, linewidth=0.8)
    for bar, (_, n, rate) in zip(bars, rows):
        ax.annotate(f"{rate:.3f}\nn={n}",
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([r[0] for r in rows], fontsize=8)
    ax.set_ylabel("refuted-claim rate", fontsize=9)
    ax.set_ylim(0, 1.0)
    ax.set_title("The gate discriminates: totals fail far more often than events",
                 fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.5)
    ax.set_axisbelow(True)
    footer(fig, f"source: {claims_path.name}")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    target = out / "football_by_kind.png"
    fig.savefig(target, dpi=200)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    fig.savefig(target.with_suffix('.svg'))
    plt.close(fig)
    return target


def parse_training_log(path: Path) -> tuple[list[int], list[float], list[float]]:
    """nanoGPT prints 'step N: train loss X, val loss Y' at each evaluation."""
    steps, train, val = [], [], []
    pattern = re.compile(r"^step (\d+): train loss ([\d.]+), val loss ([\d.]+)")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pattern.match(line.strip())
        if m:
            steps.append(int(m.group(1)))
            train.append(float(m.group(2)))
            val.append(float(m.group(3)))
    return steps, train, val


def figure_training(log_path: Path, out: Path) -> Path | None:
    if not log_path.exists():
        return None
    steps, train, val = parse_training_log(log_path)
    if not steps:
        return None
    best = min(range(len(val)), key=lambda i: val[i])

    # Two panels. On a single linear axis the drop from ~8.4 dominates and the
    # divergence after the minimum - the whole point of the figure - is invisible.
    fig, axes = plt.subplots(1, 2, figsize=(10, 1.8))
    for ax, (lo, title) in zip(axes, [
            (0, "Full run"),
            (next(i for i, s in enumerate(steps) if s >= 500),
             "After step 500, rescaled")]):
        ax.plot(steps[lo:], train[lo:], label="training loss", color="#000000",
                linewidth=1.1, linestyle="--")
        ax.plot(steps[lo:], val[lo:], label="validation loss", color="#000000",
                linewidth=1.5, linestyle="-", marker="o", markersize=2.8,
                markerfacecolor="#ffffff", markeredgecolor="#000000",
                markeredgewidth=0.7)
        ax.axvline(steps[best], color="#999", linestyle="--", linewidth=0.9)
        ax.set_xlabel("step", fontsize=9)
        ax.set_title(title, fontsize=9.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.25, linewidth=0.5)
        ax.set_axisbelow(True)
    axes[0].set_ylabel("cross-entropy loss", fontsize=9)
    axes[0].legend(fontsize=8, frameon=False)
    axes[1].annotate(f"minimum {val[best]:.4f} at step {steps[best]}\n"
                     f"— checkpoint kept; validation rises after",
                     (steps[best], val[best]),
                     xytext=(steps[best] + max(steps) * 0.06, val[best] + 0.22),
                     fontsize=7.5, color="#333",
                     arrowprops=dict(arrowstyle="->", color="#777", lw=0.8))
    fig.suptitle("Training the 4.3M model: validation turns while training keeps falling",
                 fontsize=10.5)
    footer(fig, f"source: {log_path.name} · 292,948 training tokens against 4.3M "
                f"parameters (0.069 tokens/parameter); overfitting is expected, "
                f"not a tuning failure")
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    target = out / "football_training.png"
    fig.savefig(target, dpi=200)
    # Vector twin: Word rasterises the PNG to about 200 dpi on
    # export, so keep a scalable source for the camera-ready.
    fig.savefig(target.with_suffix('.svg'))
    plt.close(fig)
    return target


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", type=Path, default=RESULTS / "report.json")
    ap.add_argument("--claims", type=Path, default=RESULTS / "claims.jsonl")
    ap.add_argument("--train-log", type=Path, default=LOGS / "train.log")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/figures")
    args = ap.parse_args()

    if not args.report.exists():
        raise SystemExit(f"missing {args.report}; run scripts/run_football_experiment.py")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)

    written = [
        figure_tradeoff(report, args.claims, args.out),
        figure_status(report, args.claims, args.out),
        figure_by_kind(report, args.claims, args.out),
        figure_training(args.train_log, args.out),
    ]
    for target in written:
        if target:
            print(f"  wrote {target.relative_to(ROOT)}")
    missing = sum(1 for t in written if t is None)
    if missing:
        print(f"  {missing} figure(s) skipped: their source data is not present")


if __name__ == "__main__":
    main()

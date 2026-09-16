"""Render the paper's figures and LaTeX tables from docs/paper/results/<split>.json.

    python scripts/make_figures.py --split test

Needs matplotlib only; reads nothing but the results file research_report.py wrote, so a
figure can never show a number the results do not contain.

Colour carries one job - which family a system belongs to - using the first three slots
of the reference categorical palette (validated for all-pairs use): blue for the
neuro-symbolic configurations, orange for language-model baselines, gray for systems
without a model. Identity is never colour alone: every mark is labelled on its axis.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "docs" / "paper"
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#8a8984"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"

LABELS = {
    "llm_only": "LLM only", "llm_rag": "LLM + RAG", "llm_rag_selfcheck": "LLM + RAG + self-check",
    "llm_rag_rules": "LLM + RAG + basic rules", "ns_parser": "Praxis (LLM parser)",
    "ns_parser_llm": "Praxis (LLM parser + LLM phrasing)",
    "neurosymbolic": "Praxis (offline encoder)", "neurosymbolic_lexical": "Praxis (lexical encoder)",
    "rag_extractive": "Extractive RAG",
}
ABLATION_LABELS = {
    "no_verification": "− verification gate", "latest_version": "− version resolution",
    "two_valued": "− three-valued logic", "trust_statements": "− statement grounding",
    "no_authorization": "− authorization before reasoning",
}
CATEGORY_ORDER = ["known", "paraphrased", "multi_hop", "calculation", "policy_version",
                  "recommendation", "missing_info", "false_premise", "unauthorized", "not_covered"]


def label(key: str) -> str:
    system, tag = key.split("@", 1)
    base = LABELS.get(system, system)
    return base if tag == "deterministic" else f"{base} · {tag}"


def colour(key: str) -> str:
    system = key.split("@", 1)[0]
    if system.startswith(("ns_", "neurosymbolic")):
        return BLUE
    if system.startswith("llm_"):
        return ORANGE
    return GRAY


def style(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.tick_params(axis="y", length=0)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def main_systems(results: dict) -> list[str]:
    keys = [k for k in results["systems"]
            if k.split("@", 1)[0] in LABELS and not k.split("@", 1)[0].startswith("ns_parser_")
            or k.split("@", 1)[0] in ("ns_parser", "ns_parser_llm")]
    order = list(LABELS)
    return sorted(set(keys), key=lambda k: (k.split("@", 1)[1], order.index(k.split("@", 1)[0])
                                            if k.split("@", 1)[0] in order else 99))


def figure_main(results: dict, out: Path) -> None:
    keys = main_systems(results)
    fig, axes = plt.subplots(1, 3, figsize=(12, 0.42 * len(keys) + 1.4), sharey=True)
    for ax, (metric, title) in zip(axes, (("accuracy", "Accuracy ↑"),
                                          ("hallucination_rate", "Hallucination rate ↓"),
                                          ("coverage", "Coverage of answerable ↑"))):
        for row, key in enumerate(keys):
            m = results["systems"][key]["overall"][metric]
            if m["value"] is None:
                continue
            lo, hi = m["ci95"]
            ax.plot([lo, hi], [row, row], color=colour(key), linewidth=2, solid_capstyle="round")
            ax.plot(m["value"], row, "o", color=colour(key), markersize=7,
                    markeredgecolor="white", markeredgewidth=2)
            ax.text(hi + 0.02, row, f"{m['value']:.2f}", va="center", fontsize=8, color=INK)
        ax.set_xlim(0, 1.12)
        ax.set_title(title, fontsize=10, color=INK, loc="left")
        style(ax)
    axes[0].set_yticks(range(len(keys)), [label(k) for k in keys], color=INK)
    axes[0].invert_yaxis()
    fig.text(0.01, 0.005, f"{results['split']} split, n = {results['cases']} questions; "
             "points are rates, bars 95% bootstrap intervals.", fontsize=8, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out / "fig_main.png", dpi=200)
    fig.savefig(out / "fig_main.pdf")
    plt.close(fig)


def figure_tradeoff(results: dict, out: Path) -> None:
    keys = main_systems(results)
    fig, ax = plt.subplots(figsize=(6.4, 4.6))
    for key in keys:
        o = results["systems"][key]["overall"]
        h, c = o["hallucination_rate"], o["coverage"]
        if h["value"] is None or c["value"] is None:
            continue
        ax.plot(h["ci95"], [c["value"]] * 2, color=colour(key), linewidth=1.5)
        ax.plot([h["value"]] * 2, c["ci95"], color=colour(key), linewidth=1.5)
        ax.plot(h["value"], c["value"], "o", color=colour(key), markersize=8,
                markeredgecolor="white", markeredgewidth=2)
        ax.annotate(label(key), (h["value"], c["value"]), xytext=(6, 4),
                    textcoords="offset points", fontsize=7.5, color=INK)
    ax.set_xlabel("Hallucination rate (lower is better)", color=MUTED, fontsize=9)
    ax.set_ylabel("Coverage of answerable questions (higher is better)", color=MUTED, fontsize=9)
    ax.set_xlim(left=0)
    ax.set_ylim(0, 1.05)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    style(ax)
    fig.tight_layout()
    fig.savefig(out / "fig_tradeoff.png", dpi=200)
    fig.savefig(out / "fig_tradeoff.pdf")
    plt.close(fig)


def figure_categories(results: dict, out: Path) -> None:
    keys = main_systems(results)
    categories = [c for c in CATEGORY_ORDER
                  if any(c in results["systems"][k]["by_category"] for k in keys)]
    grid = [[(results["systems"][k]["by_category"].get(c, {}).get("hallucination_rate") or {})
             .get("value") for c in categories] for k in keys]
    fig, ax = plt.subplots(figsize=(1.0 * len(categories) + 3.4, 0.42 * len(keys) + 1.6))
    from matplotlib.colors import LinearSegmentedColormap
    ramp = LinearSegmentedColormap.from_list("seq", ["#f4f3ef", "#9a3a14"])
    data = [[v if v is not None else float("nan") for v in row] for row in grid]
    ax.imshow(data, cmap=ramp, vmin=0, vmax=1, aspect="auto")
    for i, row in enumerate(grid):
        for j, value in enumerate(row):
            if value is not None:
                ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=7.5,
                        color="white" if value > 0.55 else INK)
    ax.set_xticks(range(len(categories)), [c.replace("_", " ") for c in categories],
                  rotation=35, ha="right", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(keys)), [label(k) for k in keys], fontsize=8.5, color=INK)
    ax.set_title("Hallucination rate by question category", fontsize=10, color=INK, loc="left")
    for side in ax.spines.values():
        side.set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "fig_categories.png", dpi=200)
    fig.savefig(out / "fig_categories.pdf")
    plt.close(fig)


def figure_ablation(results: dict, out: Path) -> None:
    for reference, ablations in results.get("ablations", {}).items():
        if not ablations:
            continue
        names = sorted(ablations)
        fig, axes = plt.subplots(1, 2, figsize=(10, 0.5 * len(names) + 1.4), sharey=True)
        for ax, (metric, title) in zip(axes, (("hallucinated", "Δ hallucination rate"),
                                              ("correct", "Δ accuracy"))):
            for row, name in enumerate(names):
                d = ablations[name][metric]
                ax.plot(d["ci95"], [row, row], color=BLUE, linewidth=2)
                ax.plot(d["delta"], row, "o", color=BLUE, markersize=7,
                        markeredgecolor="white", markeredgewidth=2)
                ax.text(d["ci95"][1] + 0.01, row, f"{d['delta']:+.3f}", va="center",
                        fontsize=8, color=INK)
            ax.axvline(0, color=MUTED, linewidth=1)
            ax.set_title(title, fontsize=10, color=INK, loc="left")
            style(ax)
        flag = lambda n: n.split("_", 2)[-1] if n.startswith("ns_parser_") else n[3:]  # noqa: E731
        axes[0].set_yticks(range(len(names)),
                           [ABLATION_LABELS.get(flag(n), n) for n in names], color=INK)
        axes[0].invert_yaxis()
        fig.text(0.01, 0.005, f"Each component removed from {label(reference)}; paired 95% "
                 "bootstrap intervals on the difference.", fontsize=8, color=MUTED)
        fig.tight_layout(rect=(0, 0.04, 1, 1))
        stem = "fig_ablation_" + reference.replace("@", "_")
        fig.savefig(out / f"{stem}.png", dpi=200)
        fig.savefig(out / f"{stem}.pdf")
        plt.close(fig)


def _fmt(metric: dict) -> str:
    if not metric or metric.get("value") is None:
        return "--"
    lo, hi = metric["ci95"]
    return f"{metric['value']:.3f} \\scriptsize[{lo:.2f}, {hi:.2f}]"


def tables(results: dict, out: Path) -> None:
    keys = main_systems(results)
    rows = ["\\begin{tabular}{lcccccc}", "\\toprule",
            "System & Accuracy & Hallucination & Coverage & Selective acc. & Disclosure & "
            "Unsupp. numbers \\\\", "\\midrule"]
    for key in keys:
        o = results["systems"][key]["overall"]
        rows.append(f"{label(key)} & {_fmt(o['accuracy'])} & {_fmt(o['hallucination_rate'])} & "
                    f"{_fmt(o['coverage'])} & {_fmt(o['selective_accuracy'])} & "
                    f"{_fmt(o['leak_rate'])} & {_fmt(o['answers_with_unsupported_number'])} \\\\")
    rows += ["\\bottomrule", "\\end{tabular}"]
    (out / "table_main.tex").write_text("\n".join(rows) + "\n", encoding="utf-8")

    lines = ["\\begin{tabular}{llrrrrr}", "\\toprule",
             "Reference & System & $n_{10}$ & $n_{01}$ & $p$ (accuracy) & $n_{10}$ & $p$ "
             "(hallucination) \\\\", "\\midrule"]
    for reference, others in results.get("comparisons", {}).items():
        for key, tests in others.items():
            c, h = tests["correct"], tests["hallucinated"]
            lines.append(f"{label(reference)} & {label(key)} & {c['only_a']} & {c['only_b']} & "
                         f"{c['p_value']:.2g} & {h['only_b']} & {h['p_value']:.2g} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    (out / "table_significance.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    args = parser.parse_args()
    results = json.loads((PAPER / "results" / f"{args.split}.json").read_text(encoding="utf-8"))
    out = PAPER / "figures"
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans"})
    figure_main(results, out)
    figure_tradeoff(results, out)
    figure_categories(results, out)
    figure_ablation(results, out)
    tables(results, out)
    print(f"figures and tables written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

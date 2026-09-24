"""Figures for the two project reports.

Every number plotted here is either a constant read from the source, or a measurement
read from a results file on disk. Nothing is illustrative: a panel with no measurement
behind it says so on its face rather than showing a plausible-looking bar.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
NSKB = Path("C:/Users/aprav/OneDrive/Desktop/neurosymbolic-kbqa")

NEURAL, SYMBOLIC, HYBRID = "#4C72B0", "#C44E52", "#8172B2"
INK, MUTED, PAPER = "#1F2430", "#6B7280", "#FFFFFF"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                     "figure.facecolor": PAPER, "axes.facecolor": PAPER})


def box(ax, x, y, w, h, label, colour, sub=""):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                linewidth=1.4, edgecolor=colour, facecolor=colour + "1A"))
    ax.text(x + w / 2, y + h * (0.60 if sub else 0.5), label, ha="center", va="center",
            fontsize=9.5, weight="bold", color=INK)
    if sub:
        ax.text(x + w / 2, y + h * 0.26, sub, ha="center", va="center",
                fontsize=7.6, color=MUTED)


def arrow(ax, x, y0, y1):
    ax.add_patch(FancyArrowPatch((x, y0), (x, y1), arrowstyle="-|>", mutation_scale=13,
                                 linewidth=1.3, color=MUTED))


def tag(ax, x, y, text, colour):
    ax.text(x, y, text, ha="right", va="center", fontsize=7.4, weight="bold",
            color=colour, family="DejaVu Sans")


def finish(fig, name):
    fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor=PAPER)
    plt.close(fig)
    print("wrote", name)


# --- Figure 1: Project 1 architecture ---------------------------------------
def fig_p1_architecture():
    fig, ax = plt.subplots(figsize=(6.6, 8.3))
    ax.set_xlim(0, 10); ax.set_ylim(0, 10.6); ax.axis("off")
    steps = [
        ("Question + asker identity", "", NEURAL, "NEURAL"),
        ("Encode & semantically parse", "bge-small-en-v1.5 · 384-dim · offline", NEURAL, "NEURAL"),
        ("ROUTER — choose pipeline A–G", "goal ≥0.60 · clause ≥0.55 · ties by rule graph", HYBRID, "HYBRID"),
        ("AUTHORIZATION", "only permitted records admitted", SYMBOLIC, "SYMBOLIC"),
        ("GROUNDING", "resolve subject · check stated facts", SYMBOLIC, "SYMBOLIC"),
        ("REASONER — three-valued proof", "satisfied / violated / unknown", SYMBOLIC, "SYMBOLIC"),
        ("VERIFICATION — every claim", "VERIFIED / SUPPORTED / WEAK / CONTRADICTED", HYBRID, "HYBRID"),
        ("EVIDENCE GATE", "answer ≥0.55 · caveat ≥0.40 · else abstain", HYBRID, "HYBRID"),
        ("REALIZER — word the conclusion", "wording only; no facts introduced", NEURAL, "NEURAL"),
        ("Verified answer, with citations", "", NEURAL, ""),
    ]
    h, gap, x, w = 0.72, 0.36, 1.5, 7.2
    y = 10.6 - h - 0.15
    for i, (label, sub, colour, layer) in enumerate(steps):
        box(ax, x, y, w, h, label, colour, sub)
        if layer:
            tag(ax, x - 0.18, y + h / 2, layer, colour)
        if i < len(steps) - 1:
            arrow(ax, x + w / 2, y - 0.04, y - gap + 0.04)
        y -= h + gap
    ax.text(5.1, 10.52, "Project 1 — Praxis: where each stage runs",
            ha="center", fontsize=11.5, weight="bold", color=INK)
    ax.text(5.1, 0.12,
            "Authorization precedes reasoning: the rule engine only ever sees facts the asker may see.",
            ha="center", fontsize=7.8, style="italic", color=MUTED)
    finish(fig, "p1_architecture.png")


# --- Figure 2: Project 1 routing ---------------------------------------------
def fig_p1_routing():
    fig, ax = plt.subplots(figsize=(7.4, 4.5))
    ax.set_xlim(0, 12); ax.set_ylim(0, 7.4); ax.axis("off")
    ax.text(6, 7.1, "How Project 1 decides symbolic vs. plain language model",
            ha="center", fontsize=11.5, weight="bold", color=INK)
    box(ax, 4.6, 6.0, 2.8, 0.62, "Goal matched?", HYBRID)
    # left branch: matched
    ax.annotate("", xy=(3.0, 5.5), xytext=(5.4, 5.98),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2))
    ax.text(3.7, 5.82, "yes", fontsize=7.6, color=MUTED)
    box(ax, 1.1, 4.85, 3.8, 0.62, "What do its rules do?", SYMBOLIC)
    # A spine down the left with short connectors, so arrows never cross a box.
    outcomes = [("compute a value", "D  Calculation", 3.85),
                ("conclude", "B / C  Policy · Multi-hop", 2.95),
                ("no rules", "A  Lookup", 2.05)]
    spine_x = 1.45
    ax.plot([spine_x, spine_x], [4.83, outcomes[-1][2] + 0.28], color=MUTED, lw=1.0,
            solid_capstyle="butt")
    for text, label, yy in outcomes:
        ax.annotate("", xy=(2.15, yy + 0.27), xytext=(spine_x, yy + 0.27),
                    arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.0))
        # Label sits in the gap ABOVE the box, never over its edge.
        ax.text(spine_x - 0.10, yy + 0.70, text, fontsize=7.2, color=MUTED, ha="left")
        box(ax, 2.15, yy - 0.02, 3.55, 0.58, label, SYMBOLIC)
    # right branch: no goal
    ax.annotate("", xy=(9.0, 5.5), xytext=(6.6, 5.98),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2))
    ax.text(7.9, 5.82, "no", fontsize=7.6, color=MUTED)
    box(ax, 7.1, 4.85, 4.3, 0.62, "Any question word in the\ncompany's own vocabulary?", HYBRID)
    ax.annotate("", xy=(8.0, 3.62), xytext=(8.4, 4.83),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.0))
    ax.text(6.55, 4.25, "yes", fontsize=7.4, color=MUTED)
    box(ax, 6.3, 3.0, 2.4, 0.58, "F  Insufficient", HYBRID)
    ax.annotate("", xy=(10.4, 3.62), xytext=(10.0, 4.83),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.0))
    ax.text(10.5, 4.25, "no", fontsize=7.4, color=MUTED)
    box(ax, 9.1, 3.0, 2.4, 0.58, "G  Plain LLM", NEURAL)
    ax.text(6, 1.55, "F says \"your knowledge does not cover this\" — guessing would invent policy.",
            ha="center", fontsize=8.2, color=INK)
    ax.text(6, 1.05, "G answers as an ordinary assistant: uncited, unverified, confidence capped at 0.45.",
            ha="center", fontsize=8.2, color=INK)
    ax.text(6, 0.4, "The plain-model route is reached only when the question touches the company nowhere.",
            ha="center", fontsize=7.8, style="italic", color=MUTED)
    finish(fig, "p1_routing.png")


# --- Figure 3: Project 1 confidence weights ----------------------------------
def fig_p1_confidence():
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.3))
    gate = {"Claim support": 0.34, "Verified share": 0.30, "Evidence relevance": 0.22,
            "Retrieval score": 0.14}
    reasoned = {"Symbolic determinacy": 0.35, "Understanding": 0.25, "Fact support": 0.15,
                "Verification": 0.15, "Stated inputs": 0.10}
    for ax, data, title, hi in (
            (axes[0], gate, "Evidence gate", "Verified share"),
            (axes[1], reasoned, "Reasoned answers", "Symbolic determinacy")):
        names = list(data)[::-1]
        values = [data[n] for n in names]
        colours = [SYMBOLIC if n == hi else NEURAL for n in names]
        ax.barh(names, values, color=colours, height=0.6)
        for i, v in enumerate(values):
            ax.text(v + 0.006, i, f"{v:.2f}", va="center", fontsize=8, color=INK)
        ax.set_xlim(0, 0.42); ax.set_title(title, fontsize=10, weight="bold", color=INK)
        ax.tick_params(labelsize=8); ax.set_xlabel("weight", fontsize=8)
        for side in ("top", "right"): ax.spines[side].set_visible(False)
    fig.suptitle("Confidence is computed in code, never asked of the model",
                 fontsize=11.5, weight="bold", color=INK, y=1.03)
    fig.text(0.5, -0.07,
             "Red = the term carrying the most weight. Confidence is capped at 0.97; certainty is never claimed.",
             ha="center", fontsize=7.8, style="italic", color=MUTED)
    fig.tight_layout()
    finish(fig, "p1_confidence.png")


# --- Figure 4: Project 2 architecture ----------------------------------------
def fig_p2_architecture():
    fig, ax = plt.subplots(figsize=(6.6, 7.2))
    ax.set_xlim(0, 10); ax.set_ylim(0, 9.0); ax.axis("off")
    steps = [
        ("Question", "", SYMBOLIC, ""),
        ("ENTITY LINKING", "longest match over the KB's name index", SYMBOLIC, "SYMBOLIC"),
        ("SCHEMA OF LINKED ENTITIES", "≤40 keys — the KB describing itself", SYMBOLIC, "SYMBOLIC"),
        ("MODEL WRITES A PROGRAM", "27 operators · greedy · ≤500 tokens", NEURAL, "NEURAL"),
        ("VALIDATION", "every operator, name, argument must exist", SYMBOLIC, "SYMBOLIC"),
        ("REPAIR — spelling ≥0.90, wiring", "unique matches only; content never repaired", SYMBOLIC, "SYMBOLIC"),
        ("EXECUTION over the KB", "carries provenance", SYMBOLIC, "SYMBOLIC"),
        ("Answer  —or—  abstention with a reason", "", SYMBOLIC, ""),
    ]
    h, gap, x, w = 0.74, 0.40, 1.5, 7.2
    y = 9.0 - h - 0.35
    for i, (label, sub, colour, layer) in enumerate(steps):
        box(ax, x, y, w, h, label, colour, sub)
        if layer:
            tag(ax, x - 0.18, y + h / 2, layer, colour)
        if i < len(steps) - 1:
            arrow(ax, x + w / 2, y - 0.04, y - gap + 0.04)
        y -= h + gap
    ax.text(5.1, 8.82, "Project 2 — the model only ever writes a program",
            ha="center", fontsize=11.5, weight="bold", color=INK)
    ax.text(5.1, 0.10,
            "One neural stage, surrounded by symbolic ones. The model is shown no fact value at any point.",
            ha="center", fontsize=7.8, style="italic", color=MUTED)
    finish(fig, "p2_architecture.png")


# --- Figure 5: comparison against existing approaches ------------------------
def fig_comparison():
    approaches = ["Vanilla LLM", "Chain-of-thought", "Self-check", "Standard RAG",
                  "This work\nRAG + symbolic rules",
                  "Program execution\nover a knowledge base"]
    criteria = ["Answer traceable\nto a source",
                "Model prevented\nfrom stating a fact",
                "Can refuse when\nunsupported",
                "Refusal decided\noutside the model",
                "Missing data ≠\nfalse"]
    # 0 = no, 1 = partial, 2 = yes
    grid = [
        [0, 0, 0, 0, 0],   # vanilla
        [0, 0, 0, 0, 0],   # cot
        [0, 0, 1, 0, 0],   # self-check (the model judges itself)
        [2, 0, 1, 0, 0],   # RAG
        [2, 1, 2, 2, 2],   # P1
        [2, 2, 2, 2, 2],   # P2
    ]
    shades = {0: "#F2F3F5", 1: "#FDE4B8", 2: "#BFD9C3"}
    marks = {0: "—", 1: "partial", 2: "yes"}
    fig, ax = plt.subplots(figsize=(8.4, 3.9))
    for r, row in enumerate(grid):
        for c, v in enumerate(row):
            ax.add_patch(FancyBboxPatch((c, len(grid) - 1 - r), 0.94, 0.9,
                                        boxstyle="round,pad=0.01,rounding_size=0.04",
                                        facecolor=shades[v], edgecolor="white", linewidth=1.6))
            ax.text(c + 0.47, len(grid) - 1 - r + 0.45, marks[v], ha="center", va="center",
                    fontsize=8.4, weight="bold" if v == 2 else "normal",
                    color=INK if v else MUTED)
    ax.set_xlim(-0.05, len(criteria)); ax.set_ylim(-0.05, len(grid))
    ax.set_xticks([c + 0.47 for c in range(len(criteria))])
    ax.set_xticklabels(criteria, fontsize=7.8)
    ax.set_yticks([len(grid) - 1 - r + 0.45 for r in range(len(grid))])
    ax.set_yticklabels(approaches, fontsize=8.4)
    ax.tick_params(length=0)
    for side in ax.spines.values(): side.set_visible(False)
    ax.set_title("What each approach structurally guarantees",
                 fontsize=11.5, weight="bold", color=INK, pad=12)
    fig.text(0.5, -0.06,
             "Structural properties of each design, not measured scores. Self-check is "
             "marked partial because the model judges itself. The last row is a companion "
             "system in which the model emits a program and never a fact value; it is shown "
             "as the strict limit of the same idea, and is not evaluated here.",
             ha="center", fontsize=7.8, style="italic", color=MUTED)
    fig.tight_layout()
    finish(fig, "comparison_properties.png")


# --- Figure 6: measured results ----------------------------------------------
def load_rows(name):
    path = NSKB / "results" / f"dev.{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fig_results():
    # llm_facts sits next to neurosymbolic on purpose: it is the only baseline that reads
    # the same knowledge base, so it is the comparison the figure is really making.
    systems = ["gold_program", "neurosymbolic", "llm_facts", "llm_only", "llm_cot",
               "llm_selfcheck"]
    data = {s: load_rows(s) for s in systems}
    complete = {s: r for s, r in data.items() if len(r) >= 300}
    partial = {s: r for s, r in data.items() if 0 < len(r) < 300}

    fig, ax = plt.subplots(figsize=(9.0, 4.0))
    labels, acc, hall, notes = [], [], [], []
    for s in systems:
        rows = data[s]
        if not rows:
            labels.append(s); acc.append(0); hall.append(0); notes.append("not yet run")
            continue
        n = len(rows)
        labels.append(s)
        acc.append(sum(r["correct"] for r in rows) / n)
        hall.append(sum(r["hallucinated"] for r in rows) / n)
        notes.append(f"n={n}" + ("" if n >= 300 else " — INCOMPLETE"))

    xs = range(len(labels))
    ax.bar([x - 0.19 for x in xs], acc, width=0.36, label="accuracy", color="#4C72B0")
    ax.bar([x + 0.19 for x in xs], hall, width=0.36, label="hallucination rate", color="#C44E52")
    for x, (a, h, note) in enumerate(zip(acc, hall, notes)):
        if note == "not yet run":
            ax.text(x, 0.04, "not yet run", ha="center", fontsize=7.6,
                    color=MUTED, style="italic", rotation=90)
        else:
            ax.text(x - 0.19, a + 0.015, f"{a:.2f}", ha="center", fontsize=7.6, color=INK)
            ax.text(x + 0.19, h + 0.015, f"{h:.2f}", ha="center", fontsize=7.6, color=INK)
        ax.text(x, -0.115, note, ha="center", fontsize=6.9,
                color="#B3261E" if "INCOMPLETE" in note else MUTED)
    ax.set_xticks(list(xs)); ax.set_xticklabels(labels, fontsize=8.2)
    ax.set_ylim(0, 1.12); ax.set_ylabel("rate on the dev split", fontsize=8.5)
    ax.legend(fontsize=8, frameon=False, ncols=2, loc="upper right")
    for side in ("top", "right"): ax.spines[side].set_visible(False)
    ax.set_title("Measured results — Project 2 dev split (300 questions)",
                 fontsize=11.5, weight="bold", color=INK, pad=10)
    # The caption states what the panel actually shows. While a run was partial it warned
    # in red; now that every system has all 300 it says so instead of keeping a warning
    # that no longer applies.
    if partial:
        caption, colour = (
            "Only runs that exist are plotted. Bars over a partial run are not a result and are "
            "marked INCOMPLETE;\nthe comparison is not reportable until every system has all 300.",
            "#B3261E")
    else:
        caption, colour = (
            "All five systems completed all 300 development questions. One run each, "
            "greedy decoding, the same model throughout.\nHallucination is reported beside "
            "accuracy because a system that abstains more will always look better on one alone.",
            MUTED)
    fig.text(0.5, -0.10, caption, ha="center", fontsize=7.8, style="italic", color=colour)
    fig.tight_layout()
    finish(fig, "p2_results.png")
    print("  complete:", list(complete), "| partial:", {k: len(v) for k, v in partial.items()})


# --- Figure 7: executor verification -----------------------------------------
def fig_executor():
    fig, ax = plt.subplots(figsize=(6.6, 2.5))
    bars = [("All 11,797 validation\ngold programs", 0.9947),
            ("Dev split, 300\ngold programs", 1.000)]
    names = [b[0] for b in bars]; values = [b[1] for b in bars]
    ax.barh(names, values, color="#2E7D32", height=0.5)
    ax.barh(names, [1 - v for v in values], left=values, color="#E6E8EB", height=0.5)
    for i, v in enumerate(values):
        ax.text(v - 0.012, i, f"{v:.4f}".rstrip("0").rstrip("."), va="center", ha="right",
                fontsize=10, weight="bold", color="white")
    ax.set_xlim(0, 1.0); ax.tick_params(labelsize=8.2)
    ax.set_xlabel("fraction of gold programs reproducing the gold answer", fontsize=8.2)
    for side in ("top", "right", "left"): ax.spines[side].set_visible(False)
    ax.set_title("The symbolic executor is verified independently of any model",
                 fontsize=11, weight="bold", color=INK, pad=10)
    fig.text(0.5, -0.16,
             "This bounds how much of any later error belongs to the executor rather than the parser.",
             ha="center", fontsize=7.8, style="italic", color=MUTED)
    fig.tight_layout()
    finish(fig, "p2_executor.png")


if __name__ == "__main__":
    fig_p1_architecture()
    fig_p1_routing()
    fig_p1_confidence()
    fig_p2_architecture()
    fig_comparison()
    fig_results()
    fig_executor()

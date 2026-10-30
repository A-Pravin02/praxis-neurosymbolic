"""Draw the PRAXIS architecture figure for the paper.

    python scripts/make_architecture_figure.py

Drawn with matplotlib rather than Graphviz so the output needs no system binary, and
emitted as both SVG (editable) and PNG at publication resolution.

The diagram shows the system **as implemented**, not as hoped for. Two consequences:

  * The relevance check is drawn as absent, with the failure it causes marked, because
    it is not implemented. Drawing it would misrepresent the system and contradict the
    error analysis.
  * The language model is shown at the two points it may occupy - question parsing and
    final wording - with the decision path between them marked as containing none. The
    default configuration uses neither.

Greyscale with hatching, matching the other figures: the paper prints in black and
white, and identity must not depend on colour.

Sized for print, which is the whole reason this is a vertical figure. An earlier
version laid the pipeline out horizontally in four lanes at `figsize=(7.6, 4.0)`.
Placed in a column it had to be scaled to roughly two-fifths of that width, which
took its 6.2 pt labels down to about 2.5 pt on the page - present in the file,
unreadable in the paper. `FIG_W` and `FIG_H` below are the inches the figure actually
occupies in the manuscript, so every `fontsize` here is the size it prints at. Keep
them that way: if the figure is rescaled when placed, the type sizes stop meaning
anything. `--audit` reports the smallest printed label.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHECK_CENSUS = ROOT / "artifacts/verification_checks.json"


def _checks_defined() -> int:
    """How many checks the verifier defines, from the census `count_checks.py` writes."""
    import json

    if not CHECK_CENSUS.exists():
        raise SystemExit(f"missing {CHECK_CENSUS}\nRun scripts/count_checks.py first.")
    return json.loads(CHECK_CENSUS.read_text(encoding="utf-8"))["defined_checks"]

# The printed size. One IEEE column is about 3.4 in inside the margins. Data units
# are square - ylim is derived from the aspect - so a box of height 5 is the same
# number of points whichever way it is measured.
FIG_W, FIG_H = 3.30, 3.00
MIN_PT = 6.0            # nothing smaller than this may be drawn
UNIT_PT = FIG_W / 100 * 72          # points per data unit
ROW = 5.4                           # one line of type plus padding

NEURAL = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///", linewidth=0.9)
SYMBOLIC = dict(facecolor="#d9d9d9", edgecolor="#000000", hatch="", linewidth=0.9)
GATE = dict(facecolor="#4d4d4d", edgecolor="#000000", hatch="", linewidth=1.1)
IO = dict(facecolor="#ffffff", edgecolor="#000000", hatch="", linewidth=1.1)
STORE = dict(facecolor="#f0f0f0", edgecolor="#000000", hatch="...", linewidth=0.9)

_sizes: list[float] = []
_overflow: list[str] = []


def box(ax, x, y, w, label, style, fontsize=6.8, textcolor="#000000", h=ROW):
    """One box with a single line of type, checked against its own width.

    Labels are single-line by design: at this size a second line does not fit a
    row, and silently overflowing the box is how the previous figure became
    unreadable. A label too wide for its box is reported rather than drawn badly.
    """
    _sizes.append(fontsize)
    # ~0.5 em per character for this face, plus a little padding.
    need_pt = len(label) * fontsize * 0.5 + 4
    if need_pt > w * UNIT_PT:
        _overflow.append(f"{label!r} needs ~{need_pt:.0f}pt, box is "
                         f"{w * UNIT_PT:.0f}pt")
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.25,rounding_size=0.8",
                                **style))
    # The label sits on an opaque patch of the box's own fill colour. Without it the
    # hatching and the dot fill run through the letters, which is legible on screen and
    # muddy at 6.8pt in print; the pad clears the strokes rather than just the glyphs.
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
            fontsize=fontsize, color=textcolor, zorder=5,
            bbox=dict(facecolor=style.get("facecolor", "#ffffff"), edgecolor="none",
                      boxstyle="square,pad=0.3", alpha=1.0))


def note(ax, x, y, text, fontsize=6.0, **kw):
    """Loose type. `shield` sets it on white so a line passing behind stays clear."""
    _sizes.append(fontsize)
    shield = kw.pop("shield", False)
    if shield:
        kw["bbox"] = dict(facecolor="#ffffff", edgecolor="none",
                          boxstyle="square,pad=0.1")
    ax.text(x, y, text, fontsize=fontsize, color=kw.pop("color", "#444444"),
            linespacing=1.2, **kw)


def arrow(ax, p, q, linestyle="-", lw=0.9, rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=6,
                                 linewidth=lw, linestyle=linestyle, color="#000000",
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=1.0, shrinkB=1.0))


def build(out_dir: Path) -> list[Path]:
    _sizes.clear()
    _overflow.clear()
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    top = FIG_H / FIG_W * 100
    ax.set_xlim(0, 100)
    ax.set_ylim(0, top)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    L, R = 3.0, 97.0                    # drawable span

    # Vertical bands, top down. Every box sits on one of these, so a lane always
    # encloses its own contents and nothing is placed by eye.
    Y_Q, Y_NEU, Y_STORE, Y_AUTH, Y_CONC, Y_CHECK, Y_GATE, Y_OUT, Y_GAP = (
        84.0, 73.0, 56.5, 48.0, 39.5, 30.5, 16.0, 7.5, 0.8)

    # ---- lane backgrounds ------------------------------------------------
    for y0, y1, name in ((Y_NEU - 5.5, Y_NEU + ROW + 1.2, "NEURAL"),
                         (Y_CHECK - 1.6, Y_STORE + ROW + 1.6, "SYMBOLIC"),
                         (Y_OUT - 1.6, Y_GATE + ROW + 1.6, "RELEASE")):
        ax.add_patch(FancyBboxPatch((L + 4.5, y0), R - L - 4.5, y1 - y0,
                                    boxstyle="round,pad=0.3,rounding_size=0.8",
                                    facecolor="#fbfbfb", edgecolor="#bbbbbb",
                                    linewidth=0.6, linestyle=(0, (3, 2))))
        note(ax, L + 1.4, (y0 + y1) / 2, name, 6.0, color="#888888", rotation=90,
             va="center", ha="center", style="italic")

    # ---- question in -----------------------------------------------------
    box(ax, 30.0, Y_Q, 40.0, "user question", IO, 7.0)
    arrow(ax, (50, Y_Q), (50, Y_NEU + ROW))

    # ---- neural lane -----------------------------------------------------
    box(ax, 10.0, Y_NEU, 40.0, "understanding", NEURAL)
    box(ax, 55.0, Y_NEU, 40.0, "retrieval", NEURAL)
    arrow(ax, (50.0, Y_NEU + ROW / 2), (55.0, Y_NEU + ROW / 2))
    note(ax, 52.0, Y_NEU - 3.4, "33M encoder, or the model as parser", 6.0,
         color="#666666", ha="center", shield=True)

    # ---- knowledge and symbolic lane -------------------------------------
    box(ax, 55.0, Y_STORE, 40.0, "records and graph", STORE)
    arrow(ax, (75.0, Y_NEU), (75.0, Y_STORE + ROW))
    box(ax, 7.0, Y_STORE, 42.0, "policy version on date", SYMBOLIC, 6.6)
    arrow(ax, (55.0, Y_STORE + ROW / 2), (49.0, Y_STORE + ROW / 2))

    box(ax, 7.0, Y_AUTH, 42.0, "authorized view", SYMBOLIC, 6.6)
    box(ax, 55.0, Y_AUTH, 40.0, "rule engine, three-valued", SYMBOLIC, 6.3)
    arrow(ax, (28.0, Y_STORE), (28.0, Y_AUTH + ROW))
    arrow(ax, (49.0, Y_AUTH + ROW / 2), (55.0, Y_AUTH + ROW / 2))

    box(ax, 28.0, Y_CONC, 44.0, "established conclusion", SYMBOLIC, 6.6)
    arrow(ax, (75.0, Y_AUTH), (66.0, Y_CONC + ROW), rad=0.12)

    # The count is read from the census, never written here: the diagram said "ten"
    # while the text said eleven, and the true figure is twelve defined of which
    # eleven run on the test split. A literal in a drawing script is exactly how that
    # drifts, so the number comes from the same artifact the prose quotes.
    box(ax, 7.0, Y_CHECK, 88.0,
        f"{_checks_defined()} checks: supported / contradicted / unverified / "
        f"not checkable",
        SYMBOLIC, 6.1)
    arrow(ax, (50.0, Y_CONC), (50.0, Y_CHECK + ROW))

    # ---- release lane ----------------------------------------------------
    box(ax, 32.0, Y_GATE, 36.0, "release gate", GATE, 6.8, "#ffffff")
    arrow(ax, (50.0, Y_CHECK), (50.0, Y_GATE + ROW))
    note(ax, 51.0, Y_GATE + ROW + 2.6,
         "no language model acts on this decision path", 6.0,
         color="#555555", ha="center", style="italic", shield=True)

    box(ax, 55.0, Y_OUT, 40.0, "answer released", IO, 6.6)
    box(ax, 7.0, Y_OUT, 40.0, "withhold, abstain", GATE, 6.6, "#ffffff")
    arrow(ax, (64.0, Y_GATE), (72.0, Y_OUT + ROW), rad=-0.12)
    arrow(ax, (36.0, Y_GATE), (28.0, Y_OUT + ROW), rad=0.12)
    note(ax, 77.0, Y_GATE - 2.2, "pass", 6.0, ha="center")
    note(ax, 23.0, Y_GATE - 2.2, "fail", 6.0, ha="center")

    # ---- the gap the error analysis found --------------------------------
    ax.add_patch(FancyBboxPatch((7.0, Y_GAP), 88.0, ROW,
                                boxstyle="round,pad=0.25,rounding_size=0.8",
                                facecolor="#ffffff", edgecolor="#000000",
                                linewidth=1.0, linestyle=(0, (3.5, 2))))
    # Not simply "NOT IMPLEMENTED" any more. The relevance check exists on the parser
    # path, where understanding can name no goal, and does not exist on the encoder
    # path, where nearest-neighbour selection always returns one. Showing it as wholly
    # absent would understate what is built; showing it as present would overstate it.
    note(ax, 51.0, Y_GAP + ROW / 2,
         "relevance of goal to question: parser path only, not the encoder",
         6.2, color="#000000", ha="center", va="center")

    # The shading legend lives in the caption, where it prints at caption size
    # instead of competing with the diagram for a row.
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, kwargs in ((".svg", {}), (".png", {"dpi": 400})):
        target = out_dir / f"architecture{suffix}"
        # No bbox_inches="tight": cropping changes the width the figure was sized
        # for, which is what silently shrank the type in the first place.
        fig.savefig(target, **kwargs)
        written.append(target)
    plt.close(fig)
    return written


def audit(out_dir: Path) -> int:
    """Report the smallest printed label and the width the figure assumes."""
    smallest = min(_sizes) if _sizes else 0.0
    png = out_dir / "architecture.png"
    print(f"  printed size assumed : {FIG_W} x {FIG_H} in")
    print(f"  smallest label       : {smallest:.1f} pt")
    if png.exists():
        from PIL import Image
        im = Image.open(png)
        print(f"  png                  : {im.width}x{im.height}px "
              f"({im.width/FIG_W:.0f} px/in)")
    # The builder must place the figure at FIG_W, or the type sizes are fiction.
    builder = ROOT / "scripts/build_paper_docx.py"
    if builder.exists():
        m = re.search(r'architecture\.png"[^)]*?width_in=([\d.]+)',
                      builder.read_text(encoding="utf-8"), re.S)
        if m:
            placed = float(m.group(1))
            ok = abs(placed - FIG_W) < 0.05
            print(f"  placed in paper at   : {placed} in "
                  f"{'(matches)' if ok else '(MISMATCH - type will not print at size)'}")
            if not ok:
                return 1
    if smallest < MIN_PT:
        print(f"  FAIL: {smallest:.1f} pt is below the {MIN_PT} pt floor")
        return 1
    print(f"  OK: every label prints at {MIN_PT} pt or larger")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/figures")
    ap.add_argument("--audit", action="store_true",
                    help="check the printed type sizes and the placement width")
    args = ap.parse_args()
    for path in build(args.out):
        print(f"  wrote {path.relative_to(ROOT)}")
    if args.audit:
        raise SystemExit(audit(args.out))


if __name__ == "__main__":
    main()

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

The flow is one straight column, top to bottom. An earlier version snaked left and
right across the page, which made the reading order something a reader had to work out
rather than follow.

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
# Without a fixed salt, matplotlib derives SVG element ids from object addresses, so
# every build renumbers every clip-path and the file differs from the committed one
# although the drawing is identical. Pair it with metadata={"Date": None} at save time
# - the embedded timestamp is the other reason an unchanged figure looks changed.
matplotlib.rcParams["svg.hashsalt"] = "praxis-architecture"
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
from _paths import show               # noqa: E402  (after matplotlib setup)

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
# Height reduced from 4.10 in for the six-page limit. Only the vertical pitch
# changes: the width, every box width and every type size are untouched, so the audit
# that governs this figure - no label below six points, no label wider than its box -
# tests exactly what it tested before, and still passes.
FIG_W, FIG_H = 3.30, 3.55
MIN_PT = 6.0            # nothing smaller than this may be drawn
UNIT_PT = FIG_W / 100 * 72          # points per data unit
ROW = 4.9                           # one line of type plus padding
PITCH = 6.7                         # one row plus the arrow beneath it

NEURAL = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///", linewidth=0.9)
# The realizer is optional - the default configuration has none - so it is drawn with
# the neural fill and a dashed edge, which is what "may be present" looks like here.
NEURAL_OPT = dict(facecolor="#ffffff", edgecolor="#000000", hatch="///",
                  linewidth=0.9, linestyle=(0, (3, 2)))
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
    if not label:                       # a zero-height spacer, drawn for alignment
        return
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


def line(ax, p, q, linestyle="-", lw=0.9):
    """One leg of a routed connector, with no arrowhead."""
    ax.plot([p[0], q[0]], [p[1], q[1]], linestyle=linestyle, linewidth=lw,
            color="#000000", solid_capstyle="round", zorder=1)


def arrow(ax, p, q, linestyle="-", lw=0.9, rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=6,
                                 linewidth=lw, linestyle=linestyle, color="#000000",
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=1.0, shrinkB=1.0))


def build(out_dir: Path) -> list[Path]:
    """Draw the pipeline as a single straight top-to-bottom flow.

    Six things the previous drawing did not say, each of which the text does:

      * the asker's identity and the date of the case are *inputs*, and they are what
        select the authorized view and the policy version, so each gets its own box
        and its own arrow into the box it selects;
      * stored records reach the reasoner through the authorized view, not through the
        policy-version resolver, which was where the arrow pointed;
      * a model may write the final wording, so the realizer is on the diagram - as an
        optional box, because the default configuration has none;
      * claims are extracted from that wording before anything is checked;
      * the controller re-verifies what the realizer writes, which is a loop, not a
        straight line;
      * the relevance check belongs between understanding and the rule engine, where it
        would act, with the branch it would take.

    Boxes are tagged A1-A2, B1-B4 and C1-C4, matching Sections IV-A, IV-B and IV-C, so
    a reader can move between drawing and prose without guessing. The flow is one
    column top to bottom: the earlier version snaked left and right, which made the
    reading order a puzzle rather than a sequence.

    Nothing is written beneath a box any more. Every such note in the previous version
    collided with the box below it at print size - the encoder note sat across the
    relevance panel, the realizer note across claim extraction - and all of them say
    what the caption already says.
    """
    _sizes.clear()
    _overflow.clear()
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    top = FIG_H / FIG_W * 100
    ax.set_xlim(0, 100)
    ax.set_ylim(0, top)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    X, W = 11.0, 78.0                   # the single flow column
    MID = X + W / 2
    HALF = W / 2 - 2.0                  # a half-width box, for the two paired rows
    XR = X + W / 2 + 2.0                # left edge of the right-hand half box
    LEFT_LANE, RIGHT_LANE = X - 6.5, X + W + 5.0   # margins kept clear for routing

    def row(i: int) -> float:
        return top - 3.5 - ROW - i * PITCH

    (Y_Q, Y_UND, Y_REL, Y_RET, Y_IN, Y_VER, Y_AUTH, Y_ENG, Y_CONC,
     Y_REAL, Y_CLAIM, Y_CHECK, Y_GATE, Y_OUT) = (row(i) for i in range(14))

    # ---- lane backgrounds ------------------------------------------------
    for y0, y1, name in ((Y_RET - 1.4, Y_UND + ROW + 1.4, "NEURAL"),
                         (Y_CONC - 1.4, Y_IN + ROW + 1.4, "SYMBOLIC"),
                         (Y_OUT - 1.4, Y_REAL + ROW + 1.4, "RELEASE")):
        ax.add_patch(FancyBboxPatch((X - 3.0, y0), W + 6.0, y1 - y0,
                                    boxstyle="round,pad=0.3,rounding_size=0.8",
                                    facecolor="#fbfbfb", edgecolor="#999999",
                                    linewidth=0.6, linestyle=(0, (3, 2))))
        # Darkened from #888888: at 6 pt these were the faintest type on the page.
        note(ax, X - 5.6, (y0 + y1) / 2, name, 6.0, color="#333333", rotation=90,
             va="center", ha="center", style="italic")

    def down(y_from: float, y_to: float, x: float = MID, **kw) -> None:
        arrow(ax, (x, y_from), (x, y_to + ROW), **kw)

    # ---- question in -----------------------------------------------------
    box(ax, X + 16.0, Y_Q, W - 32.0, "user question", IO, 7.0)
    down(Y_Q, Y_UND)

    # ---- A. neural understanding ----------------------------------------
    box(ax, X, Y_UND, W, "A1  understanding: goal, records, facts", NEURAL, 6.1)
    down(Y_UND, Y_REL)

    # ---- the relevance check, drawn where it would act -------------------
    REL_W = W - 15.0
    ax.add_patch(FancyBboxPatch((X, Y_REL), REL_W, ROW,
                                boxstyle="round,pad=0.25,rounding_size=0.8",
                                facecolor="#ffffff", edgecolor="#000000",
                                linewidth=1.0, linestyle=(0, (3.5, 2))))
    note(ax, X + REL_W / 2, Y_REL + ROW / 2,
         "does the goal answer the question?  parser only", 6.0,
         color="#000000", ha="center", va="center")
    # The branch the check takes on the parser path, routed inside the figure: the
    # label previously ran off the right edge and was cropped out of the paper.
    arrow(ax, (X + REL_W, Y_REL + ROW / 2), (X + REL_W + 4.5, Y_REL + ROW / 2))
    note(ax, 97.5, Y_REL + ROW / 2, "no goal:\nabstain", 6.0,
         color="#333333", ha="right", va="center")
    down(Y_REL, Y_RET)

    box(ax, X, Y_RET, W, "A2  retrieval of policy text and records", NEURAL, 6.1)
    down(Y_RET, Y_IN, x=XR + HALF / 2)

    # ---- B. the two inputs, then symbolic reasoning ----------------------
    box(ax, X, Y_IN, HALF, "who is asking", IO, 6.2)
    box(ax, XR, Y_IN, HALF, "date of the case", IO, 6.2)
    box(ax, X, Y_VER, HALF, "records and graph", STORE, 6.1)
    box(ax, XR, Y_VER, HALF, "B1  policy version on date", SYMBOLIC, 6.1)
    # The date selects the version; the identity selects the view, two rows down, so
    # its arrow is routed round the left margin rather than through the store.
    down(Y_IN, Y_VER, x=XR + HALF / 2)
    line(ax, (X, Y_IN + ROW / 2), (LEFT_LANE, Y_IN + ROW / 2))
    line(ax, (LEFT_LANE, Y_IN + ROW / 2), (LEFT_LANE, Y_AUTH + ROW / 2))
    arrow(ax, (LEFT_LANE, Y_AUTH + ROW / 2), (X, Y_AUTH + ROW / 2))

    box(ax, X, Y_AUTH, W, "B2  authorized view of the records", SYMBOLIC, 6.1)
    down(Y_VER, Y_AUTH, x=X + HALF / 2)          # records into the view
    down(Y_VER, Y_AUTH, x=XR + HALF / 2)         # version into the view
    down(Y_AUTH, Y_ENG)

    box(ax, X, Y_ENG, W, "B3  rule engine, three-valued", SYMBOLIC, 6.1)
    down(Y_ENG, Y_CONC)
    box(ax, X, Y_CONC, W, "B4  established conclusion", SYMBOLIC, 6.1)
    down(Y_CONC, Y_REAL)

    # ---- C. the release gate --------------------------------------------
    box(ax, X, Y_REAL, W, "C1  realizer: wording only (optional)", NEURAL_OPT, 6.1)
    down(Y_REAL, Y_CLAIM)
    box(ax, X, Y_CLAIM, W, "C2  claim extraction from the wording", SYMBOLIC, 6.1)
    down(Y_CLAIM, Y_CHECK)
    box(ax, X, Y_CHECK, W, f"C3  {_checks_defined()} checks, four-valued",
        SYMBOLIC, 6.1)
    down(Y_CHECK, Y_GATE)

    box(ax, X + 18.0, Y_GATE, W - 36.0, "C4  release gate", GATE, 6.6, "#ffffff")
    # The controller's re-verification: a failed check sends the wording back to be
    # written again and checked again. Routed up the right margin, which is clear
    # between these rows, so it crosses no box.
    line(ax, (X + W - 18.0, Y_GATE + ROW / 2), (RIGHT_LANE, Y_GATE + ROW / 2),
         linestyle=(0, (2.5, 1.5)))
    line(ax, (RIGHT_LANE, Y_GATE + ROW / 2), (RIGHT_LANE, Y_REAL + ROW / 2),
         linestyle=(0, (2.5, 1.5)))
    arrow(ax, (RIGHT_LANE, Y_REAL + ROW / 2), (X + W, Y_REAL + ROW / 2),
          linestyle=(0, (2.5, 1.5)))
    note(ax, RIGHT_LANE + 1.0, (Y_GATE + Y_REAL) / 2 + ROW / 2, "re-verify", 6.0,
         color="#333333", ha="center", va="center", rotation=90)

    box(ax, X, Y_OUT, HALF, "withhold, abstain", GATE, 6.2, "#ffffff")
    box(ax, XR, Y_OUT, HALF, "answer released", IO, 6.2)
    arrow(ax, (MID + 7.0, Y_GATE), (XR + HALF / 2, Y_OUT + ROW), rad=-0.14)
    arrow(ax, (MID - 7.0, Y_GATE), (X + HALF / 2, Y_OUT + ROW), rad=0.14)
    note(ax, X + HALF / 2 - 9.0, Y_GATE - 1.8, "fail", 6.0, ha="center")
    note(ax, XR + HALF / 2 + 9.0, Y_GATE - 1.8, "pass", 6.0, ha="center")

    # ---- legend ----------------------------------------------------------
    # The shading was explained in the caption alone, which makes a reader cross back
    # and forth to decode four fills. Widths are computed from the type size, because
    # the first version of this row was laid out by guess and ran off the page.
    sw_w, sw_h = 5.0, 2.9
    y0 = Y_OUT - 6.6
    x0 = X - 3.0
    for style, name in ((NEURAL, "neural"), (SYMBOLIC, "symbolic"),
                        (STORE, "stored"), (GATE, "gate")):
        ax.add_patch(FancyBboxPatch((x0, y0), sw_w, sw_h,
                                    boxstyle="round,pad=0.12,rounding_size=0.4",
                                    **{k: v for k, v in style.items()
                                       if k != "linestyle"}))
        note(ax, x0 + sw_w + 1.0, y0 + sw_h / 2, name, 6.0, color="#333333",
             ha="left", va="center")
        x0 += sw_w + 1.0 + len(name) * 6.0 * 0.5 / UNIT_PT + 3.0
    note(ax, X - 3.0, y0 - 3.4, "dashed edge: optional, or not implemented", 6.0,
         color="#333333", ha="left", va="center")

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, kwargs in ((".svg", {"metadata": {"Date": None}}),
                           (".png", {"dpi": 400})):
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
        # `[^)]*?` could not cross a parenthesis, and the caption now contains several,
        # so this check quietly stopped running - the one check that keeps the printed
        # type sizes from becoming fiction. It now spans anything up to the next
        # mention of the figure, and a miss is a failure rather than a silent skip.
        m = re.search(r'architecture\.png"(?:.(?!architecture))*?width_in=([\d.]+)',
                      builder.read_text(encoding="utf-8"), re.S)
        if not m:
            print("  FAIL: cannot find the width the paper places this figure at")
            return 1
        placed = float(m.group(1))
        ok = abs(placed - FIG_W) < 0.05
        print(f"  placed in paper at   : {placed} in "
              f"{'(matches)' if ok else '(MISMATCH - type will not print at size)'}")
        if not ok:
            return 1
    if _overflow:
        # Collected since this script was written and never reported, so a label wider
        # than its box was recorded and silently drawn anyway.
        for line in _overflow:
            print(f"  FAIL: label overflows its box: {line}")
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
        print(f"  wrote {show(path)}")
    if args.audit:
        raise SystemExit(audit(args.out))


if __name__ == "__main__":
    main()

# Where these figures come from

Every figure in the paper is drawn by matplotlib from a Python script in this
repository. None was drawn by hand, exported from a drawing tool, or copied in from
anywhere else, and the test suite fails if one ever is.

| In the paper | File here | Drawn by |
|---|---|---|
| Fig. 1, architecture | `architecture.png`, `architecture.svg` | [`scripts/make_architecture_figure.py`](../../../scripts/make_architecture_figure.py) |
| Fig. 2, main results | `main_results.png`, `main_results.svg` | [`scripts/make_results_figure.py`](../../../scripts/make_results_figure.py) |

The remaining files here belong to the football study and the project report, not to
the IEEE paper.

## Redrawing them

```bash
pip install -r backend/requirements-paper.txt
python scripts/make_architecture_figure.py --audit
python scripts/make_results_figure.py
```

Or, as part of the whole paper:

```bash
python scripts/build_paper.py
```

Both scripts take `--out` if you want the output somewhere other than this directory.

## How the repository proves it

Three checks, all of which run in the ordinary test suite:

* **`backend/tests/test_paper_build.py`** regenerates each figure into a temporary
  directory and compares it with the committed file — byte for byte for the PNG,
  and with line endings normalised for the SVG, since git stores it with LF and
  matplotlib writes CRLF on Windows. A figure edited by hand fails the suite.
  The comparison is pinned to **matplotlib 3.11.2** (the version in
  `backend/requirements-paper.txt`) and skips with a note on any other version,
  because matplotlib's output moves between releases and "cannot compare" is not
  "wrong".
* The same file checks that the paper embeds *only* figures a script here draws, by
  reading the figure names out of `scripts/build_paper_docx.py`. A figure arriving by
  any other route fails rather than passing unnoticed.
* **`python scripts/make_architecture_figure.py --audit`** re-reads the PNG it has
  just written and fails if any label would print below six points, if a label is
  wider than the box drawn around it, or if the width the paper places the figure at
  stops matching the width it was drawn for.

## Why they look the way they do

Both figures are black and white: no greys, no fills that carry meaning. Kinds are
told apart by hatching and line weight, which survive a greyscale print and a
photocopy, where a mid-tone fill goes muddy and reversed-out type disappears.

Fig. 2 is one column wide and its systems are numbered rather than named, because at
that width no system name fits a slot a third of an inch across. The numbers are the
row order of Table I, which carries the names and every plotted value to three
decimal places — so the figure only has to show the shape of the comparison, and a
reader checking or quoting a number uses the table.

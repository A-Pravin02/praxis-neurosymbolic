"""Where the repository is, and how to name a file inside it.

Two things every script here needs and several got wrong.

`ROOT` is derived from this file's own location, never from the working directory and
never written out. `scripts/check_paper.py` carried an absolute path to one developer's
home directory and so could not run on anyone else's machine, which is the sort of thing
that stays invisible until someone clones the repository.
`backend/tests/test_paper_build.py` now fails if any script grows one.

`show()` is for printing a path. Several scripts printed `path.relative_to(ROOT)`,
which raises `ValueError` the moment the path is outside the repository - and these
scripts take `--out`, so pointing one at a temporary directory crashed it after the
work was already done. `show()` falls back to the absolute path instead of failing.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def show(path: Path | str) -> str:
    """A path as the repository sees it, or absolute if it lies outside."""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)

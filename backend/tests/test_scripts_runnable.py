"""Every command-line script in `scripts/` can at least start.

`scripts/setup_local_db.py` called `_find_pg_bin()` three lines above the `def` that
defines it. The script raised NameError at import and had therefore never run at all -
not once, not even `--help` - while sitting in the repository as the documented way to
set up a local database. Nothing caught it, because no test ever executed it and a
syntax check cannot see an ordering mistake.

`--help` is the cheapest thing that proves a script's module body executes: argparse
prints the usage and exits 0 before any work begins. It catches an import-time
NameError, a missing dependency, a typo in a module-level constant, and a module-level
statement that fails on a machine other than the author's - which is the whole class of
defect that makes a repository look fine and behave otherwise.

Scripts without argparse are skipped: they do their work at module level, so running
them here would do that work.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _argparse_scripts() -> list[Path]:
    found = []
    for path in sorted(SCRIPTS.glob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        if "argparse.ArgumentParser" in source and '__name__ == "__main__"' in source:
            found.append(path)
    return found


SCRIPTS_WITH_CLI = _argparse_scripts()


def test_there_are_scripts_to_check():
    """Guard the guard: a discovery bug would silently make this file test nothing."""
    assert len(SCRIPTS_WITH_CLI) >= 20, (
        f"only found {len(SCRIPTS_WITH_CLI)} scripts with a command line; has the "
        f"layout changed?")


@pytest.mark.parametrize("script", SCRIPTS_WITH_CLI, ids=lambda p: p.name)
def test_the_script_starts_and_prints_its_usage(script):
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, (
        f"{script.name} --help exited {result.returncode}; it cannot be run at all:\n"
        f"{(result.stderr or result.stdout).strip()[-1500:]}")
    assert "usage:" in result.stdout.lower(), (
        f"{script.name} exited 0 without printing a usage line")

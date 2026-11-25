"""Tests that the paper in the repository is the one the code produces.

A reader checking this project has to take two things on trust unless something checks
them: that the figures in `docs/paper/` were drawn by the scripts next to them rather
than by hand somewhere else, and that the statistics the paper quotes are computed
correctly rather than merely printed confidently.

So:

  * the two figures the paper embeds are regenerated here into a temporary directory
    and compared byte for byte with the committed files;
  * every figure the paper embeds must name a generating script, and every script
    named must still produce the file the paper embeds;
  * the two significance tests written out by hand in `build_paper_docx.py` - an exact
    McNemar and a two-sided Fisher exact - are checked against values worked out
    independently, including the two the paper actually reports;
  * the helpers that read per-question records are checked against the records.

These are `no_db` tests: nothing here touches the database, and none of them needs a
language model.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FIGURES = ROOT / "docs/paper/figures"

# The matplotlib the committed PNGs were drawn with. Its output is stable for a given
# version and moves between versions, so a mismatch means "cannot compare", not
# "the figure is wrong" - the test says which.
PINNED_MATPLOTLIB = "3.11.2"

# Each figure the paper embeds, and the script that draws it.
GENERATORS = {
    "architecture.png": "make_architecture_figure.py",
    "main_results.png": "make_results_figure.py",
}


def _load(name: str):
    """Import a script from scripts/ by path, as a module."""
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))          # the scripts import _paths as a sibling
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# the figures are drawn by the code, not pasted in
# ---------------------------------------------------------------------------

def test_the_paper_embeds_only_figures_a_script_in_this_repository_draws():
    builder = (SCRIPTS / "build_paper_docx.py").read_text(encoding="utf-8")
    import re

    embedded = set(re.findall(r'FIGURES / "([^"]+)"', builder))
    assert embedded, "no figures found in the builder - has the embedding changed?"
    assert embedded == set(GENERATORS), (
        f"the paper embeds {sorted(embedded)} but this test knows how to regenerate "
        f"{sorted(GENERATORS)}; a figure nobody can regenerate is the thing this "
        f"test exists to prevent")
    for figure, script in GENERATORS.items():
        assert (SCRIPTS / script).exists(), f"{figure} names a script that is missing"
        assert (FIGURES / figure).exists(), f"{figure} has not been generated"


@pytest.mark.parametrize("figure,script", sorted(GENERATORS.items()))
def test_regenerating_a_figure_reproduces_the_committed_file(figure, script, tmp_path):
    matplotlib = pytest.importorskip("matplotlib")
    if matplotlib.__version__ != PINNED_MATPLOTLIB:
        pytest.skip(
            f"matplotlib {matplotlib.__version__}, committed figures were drawn with "
            f"{PINNED_MATPLOTLIB}; install backend/requirements-paper.txt to compare")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script), "--out", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    # The raster the paper embeds and the vector twin kept for the camera-ready. The
    # SVG was not reproducible at first: matplotlib stamps a build timestamp into it
    # and derives element ids from object addresses, so every build rewrote both files
    # without changing the drawing and a rebuild left the working tree dirty. A fixed
    # svg.hashsalt and metadata={"Date": None} in each script settle both, and this
    # covers the SVG so it stays settled.
    for name in (figure, Path(figure).with_suffix(".svg").name):
        produced = tmp_path / name
        assert produced.exists(), f"{script} did not write {name}"
        assert _sha(produced) == _sha(FIGURES / name), (
            f"{name} in docs/paper/figures differs from what {script} draws; "
            f"regenerate it with scripts/build_paper.py rather than editing it")


def test_a_figure_script_can_write_outside_the_repository(tmp_path):
    """--out anywhere, not just inside the repository.

    Both scripts printed `path.relative_to(ROOT)` when they finished, which raises
    ValueError for a path outside the repository - so pointing one at a temporary
    directory crashed it after the figure had already been written. The test above
    needs exactly that, which is how it was found.
    """
    outside = tmp_path / "elsewhere"
    for script in GENERATORS.values():
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / script), "--out", str(outside)],
            cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, f"{script}: {result.stderr}"


def test_the_architecture_figure_audits_its_own_printed_type():
    """The audit is a build gate, so it has to pass, and it has to really run.

    Its placement check compares the width the figure is drawn for against the width
    the paper places it at. That check reads the builder with a regular expression
    which, for a while, could not cross a parenthesis - so when the caption gained
    one, the check silently stopped reporting. Asserting on its output means a check
    that stops running is a failing test rather than a quiet pass.
    """
    pytest.importorskip("matplotlib")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "make_architecture_figure.py"), "--audit"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "placed in paper at" in result.stdout, (
        "the placement check did not run; it is the only thing keeping the figure's "
        "printed type sizes honest")
    assert "(matches)" in result.stdout


# ---------------------------------------------------------------------------
# the statistics the paper reports
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def builder():
    pytest.importorskip("docx")
    return _load("build_paper_docx.py")


def test_the_exact_mcnemar_matches_values_computed_by_hand(builder):
    f = builder.exact_mcnemar
    assert f(0, 0) == pytest.approx(1.0)        # no discordant pairs: no information
    assert f(1, 2) == pytest.approx(1.0)        # 1 of 2, perfectly split
    assert f(2, 2) == pytest.approx(0.5)        # 2 of 2: 2 * (1/4)
    assert f(0, 2) == pytest.approx(0.5)        # the same, from the other tail
    assert f(4, 4) == pytest.approx(0.125)      # 2 * (1/16)
    assert f(0, 10) == pytest.approx(2 / 1024)
    # Symmetric in k and n - k, which is what "two-sided" means here.
    for n in range(1, 12):
        for k in range(n + 1):
            assert f(k, n) == pytest.approx(f(n - k, n))
            assert 0.0 < f(k, n) <= 1.0


def test_the_gate_result_the_paper_reports_is_not_significant(builder):
    """4 unsupported figures against 6 is two discordant cases and p = 0.5.

    The paper used to call the gate's contribution demonstrated on the strength of
    that difference. This is the arithmetic that says it cannot.
    """
    assert builder.exact_mcnemar(2, 2) == pytest.approx(0.5)


def test_fisher_exact_matches_values_computed_by_hand(builder):
    f = builder.fisher_exact_2x2
    # A table with no information either way.
    assert f(1, 1, 1, 1) == pytest.approx(1.0)
    # The textbook 2x2: both tails of the hypergeometric.
    assert f(3, 1, 1, 3) == pytest.approx(0.4857142857, abs=1e-9)
    assert f(5, 0, 0, 5) == pytest.approx(0.0079365079, abs=1e-9)
    # Degenerate margins carry no information and must not divide by zero.
    assert f(0, 0, 3, 3) == pytest.approx(1.0)
    assert f(4, 0, 2, 0) == pytest.approx(1.0)
    # Swapping the rows cannot change a two-sided p-value.
    assert f(207, 224, 286, 339) == pytest.approx(f(286, 339, 207, 224))


def test_study_two_seen_and_held_out_are_statistically_indistinguishable(builder):
    """The comparison Section XI-D calls indistinguishable, from its own table.

    207 of 431 against 286 of 625. The paper says this is a difference of +0.023 and
    not significant; before the review it said "indistinguishable" with no test and
    no sample sizes behind it.
    """
    p = builder.fisher_exact_2x2(207, 224, 286, 339)
    assert p == pytest.approx(0.4902124628, abs=1e-6)
    assert p > 0.05
    assert 207 / 431 - 286 / 625 == pytest.approx(0.0227, abs=5e-4)


# ---------------------------------------------------------------------------
# the record readers
# ---------------------------------------------------------------------------

def test_per_case_reads_the_frozen_test_split(builder):
    rows = builder.per_case("neurosymbolic")
    if not rows:
        pytest.skip("test-split records not present in this checkout")
    assert len(rows) == 255, "the test split is 255 questions and is frozen"
    assert all(isinstance(r.get("correct"), bool) for r in rows.values())
    wrong = [c for c, r in rows.items() if not r["correct"]]
    assert len(wrong) == 52, (
        "Section X analyses 52 errors by category; if this count moves, that section "
        "and Table V move with it")


def test_per_case_returns_empty_for_a_system_that_never_ran(builder):
    assert builder.per_case("a_system_that_does_not_exist") == {}


def test_the_gate_experiment_recounts_both_arms_from_the_records(builder):
    gate = builder.gate_experiment()
    if not gate:
        pytest.skip("gate-ablation records not present in this checkout")
    assert gate["cases"] == 255
    assert gate["differ"] == 52
    assert (gate["gated_unsupported"], gate["ablated_unsupported"]) == (4, 6)
    assert gate["discordant"] == 2
    assert gate["p_value"] == pytest.approx(0.5)
    # The two 52s in the paper are different sets of questions. If they ever became
    # the same set, the paragraph that calls them a coincidence would be wrong.
    wrong = {c for c, r in builder.per_case("neurosymbolic").items()
             if not r.get("correct")}
    assert gate["differ_cases"] != wrong
    assert len(gate["differ_cases"] & wrong) == 16


def test_the_worked_trace_still_shows_the_reversal_it_is_quoted_for(builder):
    """Section IV-D traces one question and quotes an ablation reversing it.

    `worked_trace` raises rather than returning a case that no longer demonstrates
    the point, so calling it here is the assertion.
    """
    pytest.importorskip("yaml")
    trace = builder.worked_trace()
    if trace is None:
        pytest.skip("test split or records not present in this checkout")
    case, full, latest = trace
    assert full["correct"] and not latest["correct"]
    assert full["decision"] is False and latest["decision"] is True
    assert case["expect"]["goal"] == "return_allowed"


def test_the_paired_accuracy_gap_is_the_one_the_abstract_reports(builder):
    gap, ci, n = builder.paired_accuracy_gap("neurosymbolic", "llm_rag_rules")
    if gap is None:
        pytest.skip("test-split records not present in this checkout")
    assert n == 255
    assert gap == pytest.approx(0.2588, abs=5e-4)
    assert ci[0] < gap < ci[1]
    assert ci[0] > 0, "the interval must exclude zero for the claim the paper makes"


def test_the_bootstrap_interval_is_deterministic(builder):
    """Two runs give the same interval, or the paper's numbers move between builds."""
    first = builder.paired_accuracy_gap("neurosymbolic", "llm_rag_rules")
    second = builder.paired_accuracy_gap("neurosymbolic", "llm_rag_rules")
    if first[0] is None:
        pytest.skip("test-split records not present in this checkout")
    assert first == second


# ---------------------------------------------------------------------------
# the build is runnable by someone who is not the author
# ---------------------------------------------------------------------------

def test_no_script_hard_codes_an_author_path():
    """A path under /Users/<someone> is a script that runs on exactly one machine."""
    import re

    offenders = []
    pattern = re.compile(r"(?:[A-Za-z]:[\\/]Users[\\/]|/home/|/Users/)", re.I)
    for path in sorted(SCRIPTS.glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and "sys.executable" not in line:
                offenders.append(f"{path.name}:{n}: {line.strip()[:90]}")
    assert not offenders, "hard-coded paths:\n  " + "\n  ".join(offenders)


def test_checking_the_build_inputs_passes():
    result = subprocess.run([sys.executable, str(SCRIPTS / "check_paper.py")],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr

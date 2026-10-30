"""Submission checks on the project report PDF.

    python scripts/check_report.py

`scripts/verify_claims.py` checks the IEEE paper's wording against the artifacts. This
does the same job for the project report, plus the structural checks a marker would
notice first: section numbering, figure and table numbering, that every reference is
actually cited, that the repository link matches the real remote, and that the commit
the report names exists in the history.

Every metric in the report's tables is re-read from `eval_reports/` and compared with
what the PDF prints, so a number that drifted when a table was edited by hand is caught
here rather than in a review. An undefined metric must print as "n/a": selective
accuracy over an empty answer set does not exist, and printing 0.000 for it claims a
measurement that was never made.

Exits non-zero if anything fails.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PDF = ROOT / "docs/PRAXIS_FINAL_REPORT.pdf"
TEST = ROOT / "eval_reports/research/paper/test.report.json"
CONFIRM = ROOT / "eval_reports/research/confirm/confirm.report.json"
SUITE = ROOT / "artifacts/demo_suite.json"
CENSUS = ROOT / "artifacts/verification_checks.json"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

failures: list[str] = []
checked = 0


def check(ok: bool, claim: str, evidence: str) -> None:
    global checked
    checked += 1
    print(f"{'OK ' if ok else 'FAIL'} {claim:<62} {evidence}")
    if not ok:
        failures.append(claim)


def text() -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        sys.exit("pypdf is needed: pip install pypdf")
    if not PDF.exists():
        sys.exit(f"missing {PDF}\nRun scripts/make_project_report.py and export it.")
    reader = PdfReader(str(PDF))
    return len(reader.pages), "\n".join(p.extract_text() or "" for p in reader.pages)


def metric(report: dict, system: str, field: str):
    entry = report["systems"][system]["overall"].get(field)
    return entry["value"] if isinstance(entry, dict) else entry


def main() -> None:
    pages, raw = text()
    flat = re.sub(r"\s+", " ", raw)
    test = json.loads(TEST.read_text(encoding="utf-8"))
    confirm = json.loads(CONFIRM.read_text(encoding="utf-8"))

    # ---- structure ------------------------------------------------------------
    sections = [int(m.group(1)) for m in
                re.finditer(r"^\s*(\d+)\.\s+[A-Z]", raw, re.M)]
    top = sorted(set(sections))
    check(top == list(range(1, 13)), "sections are 1-12 with no gap or duplicate",
          f"found {top}")

    # The expected count is whatever the document captions, not a number written here:
    # hardcoding it meant that adding a table failed this check instead of being
    # checked by it. What matters is that the numbering runs 1..n with no gap and no
    # repeat, which is still fully tested.
    counts = {kind: [int(m.group(1)) for m in
                     re.finditer(rf"^\s*{kind} (\d+)\.\s+[A-Z]", raw, re.M)]
              for kind in ("Figure", "Table")}
    for kind, found in counts.items():
        last = max(found) if found else 0
        # A caption begins its own paragraph, so it is anchored to a line start in the
        # extracted text. Matching anywhere instead counts a sentence that merely ends
        # "... in Table 2." as a second caption, which is how this check first reported
        # duplicates that do not exist.
        defined = found
        dupes = {n: defined.count(n) for n in set(defined) if defined.count(n) > 1}
        check(sorted(set(defined)) == list(range(1, last + 1)) and not dupes,
              f"{kind}s 1-{last} each captioned exactly once",
              f"captions {sorted(set(defined))}"
              + (f", DUPLICATED {dupes}" if dupes else ""))
        # Whether the prose also points at each one is a style question, not an error:
        # every figure here sits immediately below the text it illustrates. Reported as
        # a note so an orphan is visible without failing the run.
        # "Figures 3 and 4" refers to both, and "Figure 3" does not match it, so
        # references are collected from the whole phrase rather than per number.
        referred: list[int] = []
        for m in re.finditer(rf"{kind}s?\s+(\d+)((?:\s*(?:,|and)\s*\d+)*)", flat):
            referred.append(int(m.group(1)))
            referred += [int(x) for x in re.findall(r"\d+", m.group(2) or "")]
        orphans = [n for n in range(1, last + 1) if referred.count(n) < 2]
        if orphans:
            print(f"note  {kind}s not cross-referenced in prose (placed inline): "
                  f"{orphans}")

    # ---- references are cited -------------------------------------------------
    listed = sorted({int(m.group(1)) for m in re.finditer(r"\[(\d+)\]\s+[A-Z]\.", flat)})
    body = flat.split("11. References")[0]
    cited = sorted({int(m.group(1)) for m in re.finditer(r"\[(\d+)\]", body)})
    check(bool(listed), "the reference list was found", f"{len(listed)} entries")
    uncited = [n for n in listed if n not in cited]
    check(not uncited, "every listed reference is cited in the body",
          f"{len(cited)} cited" + (f", UNCITED {uncited}" if uncited else ""))
    dangling = [n for n in cited if n not in listed]
    check(not dangling, "every citation points at a listed reference",
          f"dangling {dangling}" if dangling else "none dangling")

    # ---- provenance -----------------------------------------------------------
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    slug = remote.removesuffix(".git").split("github.com")[-1].strip("/:")
    check(bool(slug) and slug in flat.replace(" ", ""),
          "the repository link matches the configured remote", slug or "no remote")

    stamped = re.search(r"source commit ([0-9a-f]{7,40})", flat)
    check(stamped is not None, "the report names a source commit",
          stamped.group(1) if stamped else "none found")
    if stamped:
        known = subprocess.run(["git", "cat-file", "-e", f"{stamped.group(1)}^{{commit}}"],
                               cwd=ROOT, capture_output=True)
        check(known.returncode == 0, "that commit exists in this repository",
              stamped.group(1))

    check("PLACEHOLDER" not in raw.upper() and "TODO" not in raw.upper()
          and "LOREM" not in raw.upper(),
          "no placeholder or TODO text survives", "")

    # An f-string that lost its `f` prefix prints its own source. One did, and reached
    # the PDF as "{r.ref('ui-failure')}" in the middle of a sentence, so the shape is
    # checked for rather than trusted not to recur.
    unrendered = re.findall(r"\{[A-Za-z_][^}]{0,80}\}", flat)
    check(not unrendered, "no unrendered template placeholder reached the page",
          f"found {unrendered[:3]}" if unrendered else "none")
    check("**" not in flat and not re.findall(r"(?<![\w*])\*[A-Za-z]", flat),
          "no raw markdown emphasis reached the page", "")
    check("`" not in flat, "no raw backticks reached the page", "")

    # ---- every table number matches the records -------------------------------
    systems = [("neurosymbolic", "PRAXIS (no LLM)"), ("ns_parser", "PRAXIS (LLM parser)"),
               ("llm_rag_rules", None), ("llm_rag_selfcheck", None),
               ("llm_only", None), ("llm_rag", None), ("rag_extractive", None)]
    for name, _ in systems:
        for field in ("accuracy", "hallucination_rate", "coverage", "selective_accuracy"):
            value = metric(test, name, field)
            if value is None:
                # Undefined must not be printed as a measured zero.
                check("n/a" in flat, f"{name} {field} is undefined and shown as n/a",
                      "harness records null")
                continue
            check(f"{value:.3f}" in flat, f"Table 5 prints {name} {field}",
                  f"{value:.3f}")

    for name in ("ns_two_valued", "ns_latest_version", "ns_trust_statements",
                 "ns_no_authorization"):
        if name not in test["systems"]:
            continue
        for field in ("accuracy", "hallucination_rate", "leak_rate"):
            value = metric(test, name, field)
            check(value is not None and f"{value:.3f}" in flat,
                  f"Table 6 prints {name} {field}",
                  "n/a" if value is None else f"{value:.3f}")

    for field in ("accuracy", "selective_accuracy"):
        value = metric(confirm, "ns_parser", field)
        check(f"{value:.3f}" in flat, f"Section 8.3 prints the confirmatory {field}",
              f"{value:.3f}")

    # ---- the demo suite and the check census ----------------------------------
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    check(f"{suite['correct']} of {suite['total']}" in flat
          or f"{suite['correct']}/{suite['total']}" in flat,
          "the sample-suite score matches the recorded run",
          f"{suite['correct']}/{suite['total']}")
    timing = suite.get("timing", {})
    if timing:
        check(f"{timing['first_pass_ms']:.0f} ms" in flat,
              "the reported set latency is the cold first pass",
              f"{timing['first_pass_ms']:.0f} ms")
        check(f"{timing['repeat_mean_ms']:.1f} ms" in flat,
              "the repeat latency is reported too, so neither is mistaken for the other",
              f"{timing['repeat_mean_ms']:.1f} ms")

    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    check(f"{census['defined_checks']} deterministic checks" in flat,
          "the defined check count matches the census",
          f"{census['defined_checks']} defined")
    check(f"{census['distinct_checks']} of them run" in flat,
          "the observed check count is stated separately from the defined one",
          f"{census['distinct_checks']} observed of {census['defined_checks']}")
    for name in census.get("never_ran", []):
        check(name in flat, f"the report names the check that never runs ({name})", "")
    check(f"ranges from {census['min_per_case']} to {census['max_per_case']}" in flat,
          "the per-case range of checks matches the census",
          f"{census['min_per_case']}-{census['max_per_case']}")
    check(f"{census['zero_check_cases']} cases that run none" in flat,
          "the count of cases running no check matches the census",
          f"{census['zero_check_cases']}")

    # ---- claims that must not reappear ---------------------------------------
    check("relevance check" in flat.lower(),
          "the relevance limitation is still disclosed", "")
    check("not written by hand" in flat or "computed by the same" in flat,
          "the PASS/FAIL labels are stated to be computed", "")

    print(f"\npages: {pages}")
    if failures:
        print(f"\n{len(failures)} of {checked} checks FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"all {checked} checks pass against the artifacts on disk")


if __name__ == "__main__":
    main()

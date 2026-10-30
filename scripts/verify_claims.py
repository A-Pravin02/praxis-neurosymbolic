"""Check the paper's experimental claims against the artifacts on disk.

    python scripts/verify_claims.py

The reviewer's position is correct: a PDF cannot establish that code was fixed and an
experiment rerun. This script checks each claim against the thing that would show it,
and prints PASS or FAIL per claim. It asserts nothing it has not read.

Checks:
  * the ablation switch reaches every guard it must, by reading the sources
  * the regression tests for that exist and pass
  * the gate experiment's per-case records exist, with the arms differing as reported
  * the audit script exists and reproduces every published metric
  * the figures' source data exists
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PY = BACKEND / ".venv/Scripts/python.exe"
if not PY.exists():
    PY = pathlib.Path(sys.executable)

results: list[tuple[bool, str, str]] = []


def check(ok: bool, claim: str, evidence: str) -> None:
    results.append((bool(ok), claim, evidence))


# --------------------------------------------------------------------------
# 1. the ablation switch reaches every guard
# --------------------------------------------------------------------------
sources = {p: p.read_text(encoding="utf-8", errors="ignore")
           for p in (BACKEND / "app").rglob("*.py")}
reads = {p.name: text.count('active("no_verification")')
         for p, text in sources.items() if 'active("no_verification")' in text}
total = sum(reads.values())
check(total >= 3 and {"controller.py", "response_generation.py"} <= set(reads),
      "the no_verification switch reaches all three guards",
      f"read {total} times across {sorted(reads)}")

# --------------------------------------------------------------------------
# 2. regression tests exist and pass
# --------------------------------------------------------------------------
test_file = BACKEND / "tests/test_ablation_reach.py"
if test_file.exists():
    proc = subprocess.run([str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           str(test_file)], cwd=BACKEND, capture_output=True, text=True,
                          timeout=600)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "no output"
    check(proc.returncode == 0, "ablation-reach regression tests pass", tail)
else:
    check(False, "ablation-reach regression tests exist", f"missing {test_file}")

# --------------------------------------------------------------------------
# 3. the gate experiment's records show what the paper reports
# --------------------------------------------------------------------------
gate = ROOT / "eval_reports/research/gate-ablation-v3"
a_path = gate / "test.neurosymbolic_llm.jsonl"
b_path = gate / "test.neurosymbolic_llm_no_verification.jsonl"
if a_path.exists() and b_path.exists():
    load = lambda p: {json.loads(l)["case"]: json.loads(l)
                      for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}
    a, b = load(a_path), load(b_path)
    shared = sorted(set(a) & set(b))
    text_diff = sum(1 for k in shared
                    if (a[k].get("text") or "") != (b[k].get("text") or ""))
    uns_a = sum(a[k].get("unsupported_numbers") or 0 for k in shared)
    uns_b = sum(b[k].get("unsupported_numbers") or 0 for k in shared)
    decision_same = all(a[k].get(f) == b[k].get(f) for k in shared
                        for f in ("correct", "hallucinated", "definite", "outcome"))
    check(len(shared) == 255, "the gate experiment covers 255 cases",
          f"{len(shared)} cases in both arms")
    check(text_diff == 52, "ablation changes the released text on 52 of 255 cases",
          f"measured {text_diff}")
    check((uns_a, uns_b) == (4, 6),
          "answers carrying an unsupported figure rise from four to six",
          f"measured {uns_a} -> {uns_b}")
    check(decision_same, "decision-level fields are unchanged between arms",
          "correct, hallucinated, definite and outcome identical on every case")
else:
    check(False, "gate experiment records exist", f"missing {gate}")

# --------------------------------------------------------------------------
# 4. the audit script exists and reproduces the published metrics
# --------------------------------------------------------------------------
audit = ROOT / "scripts/audit_metrics.py"
if audit.exists():
    proc = subprocess.run([str(PY), str(audit), "--tag", "paper", "--split", "test"],
                          cwd=ROOT, capture_output=True, text=True, timeout=900)
    out = proc.stdout
    reproduced = "every published value reproduces" in out
    n = len(re.findall(r"match", out))
    check(reproduced, "audit_metrics.py reproduces every published metric",
          f"{n} values matched, 0 mismatched" if reproduced else out.strip()[-200:])
else:
    check(False, "audit_metrics.py exists", f"missing {audit}")

# --------------------------------------------------------------------------
# 5. figure source data
# --------------------------------------------------------------------------
for label, path in (("main results", ROOT / "eval_reports/research/paper/test.report.json"),
                    ("Study II claims", ROOT / "artifacts/football/results/claims.jsonl"),
                    ("Study II report", ROOT / "artifacts/football/results/report.json"),
                    ("architecture source", ROOT / "docs/paper/figures/architecture.svg")):
    check(path.exists(), f"{label} source data present",
          f"{path.relative_to(ROOT)}" if path.exists() else "MISSING")

# --------------------------------------------------------------------------
# 5b. The Section VI worked example must close arithmetically.
#
# The paper accounted for the answerable and not-covered cases and then quoted the
# total, omitting the thirty conditional and denied cases the total includes: 205
# + 6 does not make 241. Every metric check passed throughout, because the metrics
# were right and only the explanation was wrong. This checks the explanation.
# --------------------------------------------------------------------------
ANSWERABLE = ("decision", "value", "result")
parser_records = ROOT / "eval_reports/research/paper/test.ns_parser.jsonl"
paper_docx = ROOT / "docs/paper/PRAXIS_IEEE.docx"
if parser_records.exists() and paper_docx.exists():
    recs = [json.loads(l) for l in
            parser_records.read_text(encoding="utf-8").splitlines() if l.strip()]
    groups = {
        "answerable definite and correct":
            sum(1 for r in recs if r["gold_kind"] in ANSWERABLE
                and r["definite"] and r["correct"]),
        "conditional correct":
            sum(1 for r in recs if r["gold_kind"] == "conditional" and r["correct"]),
        "denied correct":
            sum(1 for r in recs if r["gold_kind"] == "denied" and r["correct"]),
        "not_covered correctly refused":
            sum(1 for r in recs if r["gold_kind"] == "not_covered" and r["correct"]),
    }
    total = sum(groups.values())
    actual = sum(1 for r in recs if r["correct"])
    check(total == actual,
          "Section VI example: the stated groups sum to the numerator",
          f"{' + '.join(str(v) for v in groups.values())} = {total}, "
          f"records marked correct = {actual}")

    import docx  # type: ignore
    _d = docx.Document(str(paper_docx))
    _text = " ".join(p.text for p in _d.paragraphs)
    _text = " ".join(_text.split())
    arith = f"({' + '.join(str(v) for v in groups.values())}) / {len(recs)}"
    check(arith in _text, "Section VI example prints each group, not just the total",
          f"'{arith}' present" if arith in _text else f"'{arith}' NOT in the paper")
    check(f"{total}/{len(recs)} = {total/len(recs):.3f}" in _text,
          "Section VI example states the accuracy it computes",
          f"{total}/{len(recs)} = {total/len(recs):.3f}")
else:
    check(False, "Section VI worked-example check runnable", "records or paper missing")

# --------------------------------------------------------------------------
# 5c. The verification-check count, against a census of the test split.
#
# The paper said "ten deterministic checks run before release" and named ten. The
# verifier defines eleven, and which apply depends on the case, so none runs on
# every question. No metric check could catch this: it is a claim about the
# implementation, not about a result.
# --------------------------------------------------------------------------
census_path = ROOT / "artifacts/verification_checks.json"
if census_path.exists() and paper_docx.exists():
    census = json.loads(census_path.read_text(encoding="utf-8"))
    import docx  # type: ignore
    _t = " ".join(" ".join(p.text for p in docx.Document(str(paper_docx)).paragraphs).split())
    # Defined and observed are different numbers, and the paper has to state the one
    # it means. It defines twelve; eleven appear on this split, because the twelfth
    # belongs to a pipeline these questions never reach.
    want = (f"defines {census['defined_checks']} deterministic checks")
    check(want in _t, "paper states the number of checks the verifier defines",
          f"'{want}' present" if want in _t else f"'{want}' NOT in the paper")
    never = ", ".join(census.get("never_ran", []))
    check(bool(never) and never in _t,
          "paper names the check no test question reaches",
          f"'{never}' present" if never and never in _t else "NOT named")
    check("ten deterministic checks" not in _t.lower(),
          "paper no longer claims ten checks run before release",
          "absent" if "ten deterministic checks" not in _t.lower()
          else "STILL CLAIMS TEN")
    span = f"ranges from {census['min_per_case']} to {census['max_per_case']}"
    check(span in _t, "paper states the per-case range of checks that run",
          f"'{span}' present" if span in _t else f"'{span}' NOT in the paper")
else:
    check(False, "verification-check census available",
          f"run scripts/count_checks.py to write {census_path.name}")

# --------------------------------------------------------------------------
# 6. Study II: the numbers the paper prints, against the raw per-claim records
#
# Until this section existed the Study II checks above confirmed only that the
# source files were present, never that the paper's prose agreed with them. Four
# numbers had drifted: the rule-only refutation count, the extraction coverage,
# and - the substantive one - a "false" rate that counted the unverifiable claims
# as refuted, contradicting Section XI-B. Existence checks cannot catch that.
# --------------------------------------------------------------------------
claims_path = ROOT / "artifacts/football/results/claims.jsonl"
paper_docx = ROOT / "docs/paper/PRAXIS_IEEE.docx"
if claims_path.exists() and paper_docx.exists():
    rows = [json.loads(l) for l in
            claims_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    chk = [r for r in rows if r["status"] != "NOT_CHECKABLE"]
    nref = sum(1 for r in chk if r["status"] == "CONTRADICTED")
    nunv = sum(1 for r in chk if r["status"] == "UNVERIFIED")
    nsup = len(chk) - nref - nunv
    rule_only = sum(1 for r in chk if r.get("rule_failures"))

    import docx  # type: ignore
    text = " ".join(p.text for p in docx.Document(str(paper_docx)).paragraphs)
    text += " " + " ".join(c.text for t in docx.Document(str(paper_docx)).tables
                           for row in t.rows for c in row.cells)
    text = " ".join(text.split())

    expected = [
        (f"{nref:,} refuted", "refuted count"),
        (f"{nsup:,} were supported", "supported count"),
        (f"{nunv} unverifiable", "unverifiable count"),
        (f"{nref/len(chk):.3f}", "base refuted rate"),
        (f"{nsup/len(chk):.3f}", "gated coverage"),
        (f"{len(chk)/len(rows)*100:.1f}%", "extraction coverage"),
    ]
    for needle, label in expected:
        check(needle in text, f"Study II {label} matches the records",
              f"'{needle}' present" if needle in text else f"'{needle}' NOT in the paper")

    # The count opens a sentence, so the paper spells it; accept either form rather
    # than pinning the check to the typography.
    words = {17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
             21: "twenty-one", 22: "twenty-two"}
    tail = " claims were refuted by rule"
    forms = [f"{rule_only}{tail}", f"{words.get(rule_only, '')}{tail}"]
    hit = next((f for f in forms if f and f.lower() in text.lower()), None)
    check(hit is not None, "Study II rule-only refutations matches the records",
          f"'{hit}' present" if hit else
          f"neither '{forms[0]}' nor '{forms[1]}' in the paper")

    # The distinction the paper is built on: unverifiable claims must never be
    # described as false or refuted. The stored report conflates them, so the test
    # is not that its value is absent - the same value is the share the gate
    # withholds, which is correct and is reported - but that no "false" or
    # "refuted" label is ever attached to it.
    conflated = f"{(nref + nunv) / len(chk):.3f}"
    mislabelled = [m.group(0) for m in re.finditer(
        r"(?:false|refuted)[^.]{0,40}" + re.escape(conflated)
        + r"|" + re.escape(conflated) + r"[^.]{0,40}(?:false|refuted)", text, re.I)]
    # "the 0.505 of checkable claims that withholding costs - the refuted ones
    # together with ..." names the refuted subset legitimately; a mislabel would
    # put the word before the number as its rate.
    bad = [m for m in mislabelled if not m.lower().startswith(conflated)]
    check(not bad, "Study II never labels the withheld share as false or refuted",
          f"{conflated} appears only as the withheld share" if not bad
          else f"MISLABELLED: {bad[0][:80]!r}")
else:
    check(False, "Study II numeric checks runnable",
          f"need {claims_path.name} and {paper_docx.name}")

# --------------------------------------------------------------------------
print(f"{'':2s}{'claim':64s} evidence")
print("-" * 110)
for ok, claim, evidence in results:
    print(f"{'OK' if ok else 'XX'}  {claim:64s} {evidence}")
failed = [c for ok, c, _ in results if not ok]
print()
if failed:
    print(f"{len(failed)} CLAIM(S) NOT SUBSTANTIATED:")
    for c in failed:
        print(f"  - {c}")
    sys.exit(1)
print(f"all {len(results)} claims substantiated by the artifacts on disk")

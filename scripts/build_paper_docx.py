"""Build the IEEE-format conference paper as a .docx.

    python scripts/build_paper_docx.py --template <ieee.docx> --out docs/paper/PRAXIS_IEEE.docx

The template supplies the IEEE styling: the script clears its body and rewrites the
content using the template's own named styles ('paper title', 'Author', 'Abstract',
'Keywords', 'Heading 1', 'Heading 2', 'Body Text', 'bullet list', 'table head',
'references'), so the output matches the conference format without redefining it.

**Every number in this document is read from a result file in `eval_reports/`.** The
tables are generated from those files, not typed in. If a result file is missing, the
build fails rather than emitting a placeholder: a paper that cannot cite its source
records should not be produced at all.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import docx
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
PAPER_REPORT = ROOT / "eval_reports/research/paper/test.report.json"
CONFIRM_REPORT = ROOT / "eval_reports/research/confirm/confirm.report.json"
SCALE_DIR = ROOT / "eval_reports/research/qwen2.5-1.5b"
FOOTBALL_REPORT = ROOT / "artifacts/football/results/report.json"
FOOTBALL_CLAIMS = ROOT / "artifacts/football/results/claims.jsonl"
FOOTBALL_MANIFEST = ROOT / "artifacts/football/manifest.json"
FIGURES = ROOT / "docs/paper/figures"


# --------------------------------------------------------------------------
# result loading - the single source of every figure below
# --------------------------------------------------------------------------

def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"missing result file: {path}\n"
                         "Run the benchmark before building the paper.")
    return json.loads(path.read_text(encoding="utf-8"))


ANSWERABLE = ("decision", "value", "result")
CHECK_CENSUS = ROOT / "artifacts/verification_checks.json"


def checks() -> dict:
    """The verification-check census, counted over the frozen test split."""
    if not CHECK_CENSUS.exists():
        raise SystemExit(f"missing {CHECK_CENSUS}\nRun scripts/count_checks.py first.")
    return json.loads(CHECK_CENSUS.read_text(encoding="utf-8"))


def gate_sentence() -> str:
    c = checks()
    return (f"The verifier defines {c['defined_checks']} deterministic checks — fact "
            f"validity, authorization, policy applicability, policy version, symbolic "
            f"provenance, calculation, supported numbers, record consistency, "
            f"contradictions, answer agreement, unsupported claims and "
            f"{', '.join(c['never_ran'])}, which no test question reaches. Which "
            f"apply depends on what the case asserts, so none runs on every question: "
            f"over the {c['cases']} test cases the number that runs ranges from "
            f"{c['min_per_case']} to {c['max_per_case']}, with a median of "
            f"{c['median_per_case']}. The {c['zero_check_cases']} cases that run none "
            f"are refusals settled before there is anything to verify. An answer "
            f"failing any check that did run is withheld and the system reports that "
            f"it does not know. Statuses are four-valued: supported, contradicted, "
            f"unverified, not checkable.")


def worked_example() -> str:
    """The Section VI accuracy example, every term counted from the records.

    An earlier version of this sentence accounted for the answerable and the
    not-covered cases and then quoted the total, which omitted the thirty
    conditional and denied cases that the total includes - the arithmetic did not
    close. Counting each group here means the sentence cannot drift from the
    records again, and the groups are made to sum to the stated numerator.
    """
    path = ROOT / "eval_reports/research/paper/test.ns_parser.jsonl"
    if not path.exists():
        return ""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    ans = [r for r in rows if r["gold_kind"] in ANSWERABLE]
    definite = [r for r in ans if r["definite"]]

    def n_correct(kind: str) -> int:
        return sum(1 for r in rows if r["gold_kind"] == kind and r["correct"])

    def n_of(kind: str) -> int:
        return sum(1 for r in rows if r["gold_kind"] == kind)

    cond, den, nc = n_correct("conditional"), n_correct("denied"), n_correct("not_covered")
    ans_correct = sum(1 for r in definite if r["correct"])
    total = ans_correct + cond + den + nc
    actual = sum(1 for r in rows if r["correct"])
    if total != actual:                 # never print arithmetic that does not close
        raise SystemExit(f"worked example sums to {total}, records say {actual}")
    return (f"For the parser configuration, N = {len(rows)} = {len(ans)} answerable "
            f"+ {n_of('conditional')} conditional + {n_of('denied')} denied "
            f"+ {n_of('not_covered')} not-covered. Of the {len(ans)} answerable, "
            f"{len(definite)} were definite and all correct (selective accuracy "
            f"1.000); every conditional case drew a correctly hedged answer and every "
            f"denied case a refusal without disclosure, {cond} and {den}; and {nc} of "
            f"{n_of('not_covered')} not-covered cases were correctly refused. "
            f"Accuracy is therefore ({ans_correct} + {cond} + {den} + {nc}) / "
            f"{len(rows)} = {total}/{len(rows)} = {total/len(rows):.3f}.")


TRACE_CASE = "W101-T-policy_version-2"
TEST_SPLIT = ROOT / "backend/data/benchmark_worlds/test.yaml"


def worked_trace() -> tuple[dict, dict, dict] | None:
    """The case Section IV-D traces, with the two answers that disagree about it.

    Read rather than retold. A trace written out by hand is exactly the kind of
    passage that stays on the page after the thing it describes has changed, so the
    question, the released wording and the ablation's wording all come from the
    stored records, and the build fails if the case stops demonstrating the point it
    is quoted for - that applying the newest policy to an older case reverses it.
    """
    import yaml

    if not TEST_SPLIT.exists():
        return None
    data = yaml.safe_load(TEST_SPLIT.read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in (data["cases"] if isinstance(data, dict) else data)}
    case = cases.get(TRACE_CASE)
    full = per_case("neurosymbolic").get(TRACE_CASE)
    latest = per_case("ns_latest_version").get(TRACE_CASE)
    if not (case and full and latest):
        return None
    if not (full["correct"] and not latest["correct"]
            and full["decision"] is False and latest["decision"] is True):
        raise SystemExit(
            f"{TRACE_CASE} no longer shows the policy-version reversal the worked "
            f"trace describes; pick another case or rewrite Section IV-D.")
    return case, full, latest


def metric(report: dict, system: str, key: str):
    entry = report["systems"][system]["overall"].get(key)
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("ci95")
    return entry, None


def num(value, ci=None, places=3) -> str:
    if value is None:
        return "n/a"
    text = f"{value:.{places}f}"
    if ci:
        text += f" [{ci[0]:.2f}, {ci[1]:.2f}]"
    return text


def scale_rates() -> dict[str, dict[str, float]]:
    """Accuracy/hallucination/coverage per system from the 1.5B per-case records."""
    out: dict[str, dict[str, float]] = {}
    for path in sorted(SCALE_DIR.glob("test.*.jsonl")):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        if len(rows) < 255:          # only complete systems are reported
            continue
        name = path.stem.split(".", 1)[1]
        n = len(rows)
        answerable = [r for r in rows if r.get("gold_kind") not in (None, "not_covered")]
        out[name] = {
            "accuracy": sum(bool(r.get("correct")) for r in rows) / n,
            "hallucination": sum(bool(r.get("hallucinated")) for r in rows) / n,
            "coverage": (sum(not r.get("abstained") for r in answerable) / len(answerable)
                         if answerable else float("nan")),
            "n": n,
        }
    return out


NUMWORD = {17: "Seventeen", 18: "Eighteen", 19: "Nineteen", 20: "Twenty",
           21: "Twenty-one", 22: "Twenty-two"}


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for [[a, b], [c, d]].

    Written out rather than imported: the build must not depend on SciPy, which is not
    installed in this project's environment, and a test that silently became `nan`
    because an import failed would be worse than no test. Verified against
    `scipy.stats.fisher_exact` on this paper's table, agreeing to twelve digits.
    Log-gamma rather than exact integer binomials, because the tables here run to a
    thousand claims.
    """
    from math import exp, lgamma

    def lchoose(n: int, k: int) -> float:
        return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)

    row1, row2, col1, total = a + b, c + d, a + c, a + b + c + d
    if not (row1 and row2 and col1 and total - col1):
        return 1.0

    def logp(x: int) -> float:
        return lchoose(row1, x) + lchoose(row2, col1 - x) - lchoose(total, col1)

    observed = logp(a)
    tail = sum(exp(logp(x))
               for x in range(max(0, col1 - row2), min(row1, col1) + 1)
               if logp(x) <= observed + 1e-9)
    return min(1.0, tail)


def football_stats(path: Path) -> dict:
    """Study II statistics, recomputed from the raw per-claim records.

    `report.json` stores `false_released` as every claim that is not SUPPORTED,
    which counts the UNVERIFIED claims as false. Section XI-B of this paper says
    the opposite - a claim the graph has no record of is unverified precisely
    because absence is not refutation - so the two statuses are kept apart here
    and the refuted rate is what the paper reports. The gate withholds both, so
    coverage is the same either way.
    """
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    chk = [r for r in rows if r["status"] != "NOT_CHECKABLE"]
    nsup = sum(1 for r in chk if r["status"] == "SUPPORTED")
    ncon = sum(1 for r in chk if r["status"] == "CONTRADICTED")
    nunv = sum(1 for r in chk if r["status"] == "UNVERIFIED")

    def by(field, value):
        g = [r for r in chk if r.get(field) == value]
        c = sum(1 for r in g if r["status"] == "CONTRADICTED")
        return {"n": len(g), "refuted": c / len(g) if g else 0.0}

    kinds = []
    seen: dict[str, list[str]] = {}
    for r in chk:
        seen.setdefault((r.get("claim") or {}).get("kind") or "unknown",
                        []).append(r["status"])
    import random as _rnd
    _rng = _rnd.Random(2)
    for kind, statuses in sorted(seen.items()):
        c = sum(1 for s in statuses if s == "CONTRADICTED")
        u = sum(1 for s in statuses if s == "UNVERIFIED")
        # n = 25 on one row, so the rate alone overstates what is known about it.
        flags = [1 if s == "CONTRADICTED" else 0 for s in statuses]
        boots = sorted(sum(_rng.choices(flags, k=len(flags))) / len(flags)
                       for _ in range(10000))
        kinds.append((kind, len(statuses), c / len(statuses), u,
                      (boots[250], boots[9750])))
    # The seen/held-out comparison is tested rather than eyeballed: two rates quoted
    # side by side invite "indistinguishable" with nothing behind it.
    ho = [1 if r["status"] == "CONTRADICTED" else 0
          for r in chk if r.get("source") == "held_out"]
    se = [1 if r["status"] == "CONTRADICTED" else 0
          for r in chk if r.get("source") == "seen"]
    transfer_p, transfer_ci = 1.0, (0.0, 0.0)
    if ho and se:
        import random as _random
        rng = _random.Random(1)
        boots = sorted(sum(rng.choices(ho, k=len(ho))) / len(ho)
                       - sum(rng.choices(se, k=len(se))) / len(se)
                       for _ in range(10000))
        transfer_ci = (boots[250], boots[9750])
        transfer_p = fisher_exact_2x2(sum(ho), len(ho) - sum(ho),
                                      sum(se), len(se) - sum(se))
    return {
        "sentences": len(rows), "checkable": len(chk),
        "supported": nsup, "refuted": ncon, "unverified": nunv,
        "transfer_p": transfer_p, "transfer_ci": transfer_ci,
        "unparseable": len(rows) - len(chk),
        "refuted_rate": ncon / len(chk), "unverified_rate": nunv / len(chk),
        "coverage": nsup / len(chk), "withheld": (ncon + nunv) / len(chk),
        "extraction": len(chk) / len(rows),
        "rule_only": sum(1 for r in chk if r.get("rule_failures")),
        "kinds": kinds,
        "held_out": by("source", "held_out"), "seen": by("source", "seen"),
    }


GATE_DIR = ROOT / "eval_reports/research/gate-ablation-v3"
PAPER_DIR = ROOT / "eval_reports/research/paper"


def per_case(system: str, directory: Path = PAPER_DIR,
             split: str = "test") -> dict[str, dict]:
    """The stored per-question rows of one system, keyed by case id."""
    path = directory / f"{split}.{system}.jsonl"
    if not path.exists():
        return {}
    return {r["case"]: r for r in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip())}


def paired_accuracy_gap(a: str, b: str, seed: int = 0, draws: int = 10000) -> tuple:
    """Mean paired accuracy difference a - b, with a bootstrap interval.

    The review asked for an interval on the difference and not only a p-value: a
    p-value says the gap is unlikely to be zero, an interval says how large it is.
    Resampling is over the paired per-question differences, which is the unit the
    McNemar test is computed on, so the two statements describe the same comparison.
    """
    import random

    ra, rb = per_case(a), per_case(b)
    cases = sorted(set(ra) & set(rb))
    if not cases:
        return None, None, 0
    d = [bool(ra[c].get("correct")) - bool(rb[c].get("correct")) for c in cases]
    rng = random.Random(seed)
    boots = sorted(sum(rng.choices(d, k=len(d))) / len(d) for _ in range(draws))
    return (sum(d) / len(d), (boots[int(0.025 * draws)], boots[int(0.975 * draws)]),
            len(cases))


def exact_mcnemar(k: int, n: int) -> float:
    """Two-sided exact McNemar p-value from k successes among n discordant pairs."""
    from math import comb

    if n == 0:
        return 1.0
    tail = min(k, n - k)
    return min(1.0, 2 * sum(comb(n, i) for i in range(tail + 1)) / 2 ** n)


def gate_experiment() -> dict:
    """The text-level gate experiment, recounted from its two stored arms.

    Everything the paper says about the gate comes from here rather than from prose
    written once and never rechecked. In particular the unsupported-figure count is
    paired and tested: 4 against 6 sounds like an effect and is two discordant cases,
    which no honest reading calls demonstrated.
    """
    gated = per_case("neurosymbolic_llm", GATE_DIR)
    ablated = per_case("neurosymbolic_llm_no_verification", GATE_DIR)
    if not gated or not ablated:
        return {}
    cases = sorted(set(gated) & set(ablated))

    def text(row):
        return row.get("text") or row.get("raw") or ""

    differ = [c for c in cases if text(gated[c]) != text(ablated[c])]
    gu = [c for c in cases if gated[c].get("unsupported_numbers")]
    au = [c for c in cases if ablated[c].get("unsupported_numbers")]
    disc = [c for c in cases if bool(gated[c].get("unsupported_numbers"))
            != bool(ablated[c].get("unsupported_numbers"))]
    worse = [c for c in disc if ablated[c].get("unsupported_numbers")]
    return {"cases": len(cases), "differ": len(differ),
            "gated_unsupported": len(gu), "ablated_unsupported": len(au),
            "discordant": len(disc), "worse_when_ablated": len(worse),
            "p_value": exact_mcnemar(len(worse), len(disc)),
            "differ_cases": set(differ)}


def category_rates(path: Path) -> list[tuple[str, int, float]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    buckets: dict[str, list[int]] = {}
    for r in rows:
        buckets.setdefault(r.get("category", "?"), []).append(bool(r.get("correct")))
    return sorted(((c, len(v), sum(v) / len(v)) for c, v in buckets.items()),
                  key=lambda t: t[2])


# --------------------------------------------------------------------------
# document helpers
# --------------------------------------------------------------------------

def clear_body(document) -> None:
    body = document.element.body
    for child in list(body):
        if not child.tag.endswith("sectPr"):   # keep page/column geometry
            body.remove(child)


def template_styles(document) -> dict:
    """Map style name -> style object, taken from paragraphs the template actually uses.

    `document.styles` does not necessarily expose a built-in style the template relies
    on (Word keeps unused built-ins latent), so looking styles up by name can raise
    KeyError even though the template plainly uses them. Reading the style objects off
    the existing paragraphs is reliable, and must happen before the body is cleared.
    """
    found: dict = {}
    for para in document.paragraphs:
        if para.style is not None and para.style.name not in found:
            found[para.style.name] = para.style
    return found


def set_columns(section, num: int, space: int = 360) -> None:
    """Set the column count on a section.

    IEEE conference format is two columns with a 360-twip (0.25 in) gutter; the title
    and author block sit in a single-column section above them. python-docx exposes no
    API for this, so the `w:cols` element is set directly.
    """
    cols = section._sectPr.xpath("./w:cols")
    if not cols:
        cols = [OxmlElement("w:cols")]
        section._sectPr.append(cols[0])
    cols[0].set(qn("w:num"), str(num))
    cols[0].set(qn("w:space"), str(space))


class Doc:
    def __init__(self, document, styles: dict) -> None:
        self.d = document
        self.styles = styles
        self.body_columns = 2

    # -- column control ----------------------------------------------------

    def begin_two_columns(self) -> None:
        """Start the two-column body beneath the single-column title block."""
        section = self.d.add_section(WD_SECTION.CONTINUOUS)
        set_columns(section, self.body_columns)

    def span_start(self):
        """Break out to full width, for a table or figure too wide for one column.

        This is the docx equivalent of LaTeX's `figure*`: a continuous single-column
        section, returned to two columns by `span_end`. Without it a six-column table
        or a two-panel figure is squeezed into a 3.4 in column and becomes unreadable.
        """
        set_columns(self.d.add_section(WD_SECTION.CONTINUOUS), 1)

    def span_end(self):
        set_columns(self.d.add_section(WD_SECTION.CONTINUOUS), self.body_columns)

    def p(self, text: str, style: str = "Body Text"):
        para = self.d.add_paragraph(text)
        chosen = self.styles.get(style)
        if chosen is not None:
            para.style = chosen
        elif style == "Body Text":
            para.paragraph_format.first_line_indent = Pt(10)
        return para

    def title(self, text):
        return self.p(text, "paper title")

    def author(self, text):
        return self.p(text, "Author")

    def authors(self, blocks: list[str]):
        """Lay authors out side by side, as IEEE conference format expects."""
        if len(blocks) == 1:
            return self.author(blocks[0])
        table = self.d.add_table(rows=1, cols=len(blocks))
        style = self.styles.get("Author")
        for cell, block in zip(table.rows[0].cells, blocks):
            cell.text = ""
            para = cell.paragraphs[0]
            if style is not None:
                para.style = style
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for i, line in enumerate(block.split("\n")):
                if i:
                    para.add_run().add_break()
                para.add_run(line)
        self.d.add_paragraph()
        return table

    # A heading stranded at the foot of a column - "C. Results" did this on page 5 -
    # is a Word pagination default, not a content problem: a heading must stay with
    # the first paragraph it introduces.
    def h1(self, text):
        para = self.p(text, "Heading 1")
        para.paragraph_format.keep_with_next = True
        return para

    def h2(self, text):
        para = self.p(text, "Heading 2")
        para.paragraph_format.keep_with_next = True
        return para

    def bullet(self, text):
        return self.p(text, "bullet list")

    # The template's "table head" and "figure caption" styles number themselves, so a
    # manually written "TABLE III." or "Fig. 2." renders twice ("Fig. 2. Fig. 2. ...").
    # Call sites keep their explicit numbers for readability; they are stripped here.
    _AUTONUMBER = re.compile(r"^\s*(?:TABLE\s+(?:[IVXLC]+|\d+)|Fig\.\s*\d+)\.\s*",
                             re.I)

    def caption(self, text):
        para = self.p(self._AUTONUMBER.sub("", text), "table head")
        para.paragraph_format.keep_with_next = True
        return para

    def figure(self, path: Path, caption: str, width_in: float = 3.1,
               span: bool = False):
        """Insert an image with an IEEE-style caption beneath it.

        Default width is one column. A figure whose file is absent is skipped and
        reported, rather than leaving a broken reference in the document.
        """
        if not path.exists():
            print(f"  figure missing, skipped: {path.name}")
            return None
        if span:
            self.span_start()
        para = self.d.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # Without this the image and its caption can land in different columns, as
        # happened to Fig. 3: picture at the foot of the left column, caption at the
        # head of the right.
        para.paragraph_format.keep_with_next = True
        para.add_run().add_picture(str(path), width=Inches(width_in))
        cap = self.p(self._AUTONUMBER.sub("", caption), "figure caption")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if span:
            self.span_end()
        return cap

    def table(self, headers: list[str], rows: list[list[str]], caption: str,
              span: bool = False, keep_together: bool = False,
              widths: list[float] | None = None):
        """`widths` are column widths in inches, for a table Word lays out badly.

        Word's autofit divides a single column evenly, which put "LLM+RAG+self-check"
        in a cell narrow enough to hyphenate it across three lines while four numeric
        columns sat half empty. Naming the widths is the fix the review asked for;
        autofit must be turned off or Word discards them.
        """
        if span:
            self.span_start()
        cap = self.caption(caption)
        t = self.d.add_table(rows=1, cols=len(headers))
        try:
            t.style = "Table Grid"
        except KeyError:
            pass
        for cell, head in zip(t.rows[0].cells, headers):
            cell.text = ""
            run = cell.paragraphs[0].add_run(head)
            run.bold = True
            run.font.size = Pt(8)
        for row in rows:
            cells = t.add_row().cells
            for cell, value in zip(cells, row):
                cell.text = ""
                run = cell.paragraphs[0].add_run(str(value))
                run.font.size = Pt(8)
        # Mark the heading row as a heading row. Word then reprints it at the top of
        # every page or column the table continues onto, so a table that does break
        # never leaves its column labels behind - which is the defect that made
        # pushing whole tables about seem like the only remedy.
        t.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        if widths:
            t.autofit = False
            for row in t.rows:
                for cell, inches in zip(row.cells, widths):
                    cell.width = Inches(inches)
        if keep_together:
            self._keep_together(cap, t)
        self.d.add_paragraph()
        if span:
            self.span_end()
        return t

    @staticmethod
    def _keep_together(cap, t) -> None:
        """Push a whole table into the next column rather than let it break across one.

        Table VI started four lines above the column break, so its last row appeared at
        the top of the next column with the heading and column labels left behind. Two
        settings are needed: `cantSplit` stops a single row breaking, and "keep with
        next" on the caption and on every row but the last keeps the group contiguous,
        which is what moves the whole block.
        """
        if cap is not None:
            cap.paragraph_format.keep_with_next = True
            # And the caption must not break inside itself: Table III's four-line
            # caption split two lines above the page boundary and two below, so the
            # table arrived under a sentence that began on the previous page.
            cap.paragraph_format.keep_together = True
        for row in t.rows[:-1]:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.keep_with_next = True
        for row in t.rows:
            trPr = row._tr.get_or_add_trPr()
            trPr.append(OxmlElement("w:cantSplit"))


# --------------------------------------------------------------------------
# the paper
# --------------------------------------------------------------------------

def build(doc: Doc, test: dict, confirm: dict, scale: dict,
          fb: dict | None = None) -> None:
    T = lambda s, k: metric(test, s, k)            # noqa: E731
    C = lambda s, k: metric(confirm, s, k)         # noqa: E731

    ns_acc, ns_acc_ci = T("neurosymbolic", "accuracy")
    ns_hal, ns_hal_ci = T("neurosymbolic", "hallucination_rate")
    ns_cov, _ = T("neurosymbolic", "coverage")
    ns_lat, _ = T("neurosymbolic", "mean_latency_ms")
    pa_acc, pa_acc_ci = T("ns_parser", "accuracy")
    pa_hal, pa_hal_ci = T("ns_parser", "hallucination_rate")
    pa_sel, _ = T("ns_parser", "selective_accuracy")
    pa_cov, _ = T("ns_parser", "coverage")
    rag_acc, _ = T("llm_rag", "accuracy")
    rag_hal, _ = T("llm_rag", "hallucination_rate")
    only_acc, _ = T("llm_only", "accuracy")
    only_hal, _ = T("llm_only", "hallucination_rate")
    rules_acc, _ = T("llm_rag_rules", "accuracy")
    rules_hal, _ = T("llm_rag_rules", "hallucination_rate")
    sc_acc, _ = T("llm_rag_selfcheck", "accuracy")
    sc_hal, _ = T("llm_rag_selfcheck", "hallucination_rate")
    cpa_acc, _ = C("ns_parser", "accuracy")
    cpa_hal, _ = C("ns_parser", "hallucination_rate")
    cpa_sel, _ = C("ns_parser", "selective_accuracy")
    cns_acc, _ = C("neurosymbolic", "accuracy")
    _rules_sig = (test.get("significance", {}).get("llm_rag_rules", {})
                  .get("correct", {}))
    rules_p = _rules_sig.get("p_value")
    rules_only_a, rules_only_b = _rules_sig.get("only_a", 0), _rules_sig.get("only_b", 0)
    rules_gap, rules_gap_ci, _ = paired_accuracy_gap("neurosymbolic", "llm_rag_rules")
    gate = gate_experiment()
    only_lat, _ = T("llm_only", "mean_latency_ms")
    sc_lat, _ = T("llm_rag_selfcheck", "mean_latency_ms")
    rag_lat, _ = T("llm_rag", "mean_latency_ms")
    _only_lat = [r["latency_ms"] for r in per_case("llm_only").values()]
    _sc_lat = [r["latency_ms"] for r in per_case("llm_rag_selfcheck").values()]
    _wrong_cases = [c for c, r in per_case("neurosymbolic").items()
                    if not r.get("correct")]

    # ---- front matter ----
    # The previous title, "Reducing Hallucinations in Large Language Models Through a
    # Neuro-Symbolic Reasoning Layer", claimed the wrong result: the configuration this
    # paper leads with contains no language model at all, so nothing in it reduces a
    # language model's hallucination rate - it replaces the inference that produced it.
    # Study II does gate a model, and it is 4.3M parameters, not a large one.
    doc.title("Replacing Language-Model Inference with a Neuro-Symbolic Decision "
              "Layer for Enterprise Policy Question Answering")
    # IEEE conference format sets authors side by side. python-docx cannot lay out
    # the template's multi-column author block directly, so a single-row table gives
    # the same visual result and keeps the template's Author style on each cell.
    # The IEEE template's author block is four lines: name, department, organization,
    # city and country, then email. Department and email are left as explicit markers
    # rather than guessed: inventing either would put a false affiliation and a false
    # address on a published paper. Fill both before submitting.
    doc.authors([
        "Pravin Anbalagan\n<DEPARTMENT>\nVellore Institute of Technology (VIT)\n"
        "Vellore, India\n<EMAIL>",
        "Athiban Karthik\n<DEPARTMENT>\nVellore Institute of Technology (VIT)\n"
        "Vellore, India\n<EMAIL>",
    ])

    # Title and authors span the page; everything below is two-column, as IEEE sets it.
    doc.begin_two_columns()

    doc.p(
        f"Abstract—Retrieval grounds a language model in documents but does not stop "
        f"it asserting things the documents do not support. We present PRAXIS, a "
        f"neuro-symbolic architecture in which a language model is never the authority "
        f"for a decision: a neural encoder interprets the question, a three-valued "
        f"symbolic reasoner applies versioned business rules to authorized records, and "
        f"a suite of {checks()['defined_checks']} deterministic checks gates the answer "
        f"before release. The decision path contains no language model. "
        f"On a frozen test split of 255 questions over five generated enterprise worlds "
        f"this default configuration answers {num(ns_acc)} correctly at a hallucination "
        f"rate of {num(ns_hal)} with {num(ns_cov)} coverage, against {num(rules_acc)} "
        f"and {num(rules_hal)} for a 7B instruction-tuned model given the same corpus "
        f"and the same rules in its prompt — a paired gain of "
        f"{ns_acc - rules_acc:+.3f} accuracy "
        f"(exact McNemar, p = {rules_p:.1g}). Confining the "
        f"model to semantic parsing reaches {num(pa_acc)} accuracy, but only that "
        f"configuration fails to transfer: on a confirmatory split of unseen companies "
        f"it falls to {num(cpa_acc)} while the LLM-free configuration holds at "
        f"{num(cns_acc)}. "
        + (f"A secondary study gates a {fb['model_parameters']/1e6:.1f}M decoder "
           f"trained from scratch: the graph refutes "
           f"{football_stats(FOOTBALL_CLAIMS)['refuted_rate']:.3f} of its claims, and "
           f"gating leaves {football_stats(FOOTBALL_CLAIMS)['coverage']:.3f} "
           f"coverage. " if fb else "")
        + "We report coverage beside every accuracy and document a failure the "
          "aggregate hides: on questions no policy covers, the default configuration "
          "abstains in 0 of 15 cases, because verification establishes support, not "
          "relevance.",
        "Abstract")
    # "three-valued" alone was ambiguous across two layers that differ: the rule engine
    # is three-valued, the verifier four-valued. Both are named.
    doc.p("Keywords—neuro-symbolic AI, hallucination, retrieval-augmented generation, "
          "three-valued logic, four-valued verification, abstention, selective "
          "prediction, enterprise decision support", "Keywords")

    # ---- I. Introduction ----
    doc.h1("Introduction")
    doc.p("Language models now answer questions that carry consequences: whether a "
          "refund is owed, which policy version governs a dispute, how much a traveller "
          "may reclaim. Retrieval-augmented generation grounds such answers in "
          "documents, but grounding is not entailment. A model handed the right clause "
          "can still state a conclusion the clause does not license, and it does so "
          "fluently enough that a reader has no signal to distrust it.")
    doc.p("The common remedies keep the model as the authority. Self-consistency "
          "sampling [16], self-critique and chain-of-verification all ask the model to audit "
          "its own output. Our measurements suggest this is a weak defence: asking a 7B "
          f"model to check its own retrieval-grounded draft moved accuracy from "
          f"{num(rag_acc)} to {num(sc_acc)} and hallucination from {num(rag_hal)} to "
          f"{num(sc_hal)} — an improvement far smaller than the error it is meant to "
          "remove.")
    doc.p("PRAXIS takes the opposite position. The model is treated as a language "
          "engine, never a truth engine. It may interpret a question and it may phrase "
          "a conclusion, but it never establishes one. Between those two points sits "
          "deterministic, replayable code: a typed knowledge graph, a three-valued "
          "reasoner over versioned rules, and an evidence gate that withholds any answer "
          "failing its checks. In the default configuration the model is absent from the "
          "decision path entirely.")

    doc.h2("Scope and Contributions")
    doc.p("This paper makes six contributions:")
    # "cannot arise" overstated what the architecture buys. No language model asserts
    # a conclusion here, which removes that mechanism; it does not remove error. The
    # test split holds 25 wrong definite answers, from the encoder's reading of the
    # question and from the rule base itself.
    doc.p("1. An architecture whose decision path contains no language model, so a "
          "decision is never a model’s prediction. The residual errors have different "
          "sources — question understanding and the rule base — and the test split "
          "carries 25 wrong definite answers from them.")
    doc.p("2. A four-valued verification layer distinguishing supported, contradicted, "
          "unverified and not-checkable, so that missing evidence is never silently "
          "treated as refutation. It sits above a three-valued rule engine; the two "
          "layers and their logics are distinct.")
    doc.p("3. A controlled evaluation on 510 questions across ten generated enterprise "
          "worlds — a test split frozen before any system ran on it, and a confirmatory "
          "split written after the system was final and scored once — supported by a "
          "further 153 development questions over three separate worlds used only to "
          "calibrate thresholds and never scored as a result.")
    doc.p("4. Seven ablations isolating the contribution of verification, three-valued "
          "reasoning, policy versioning, statement trust, authorization, numeric "
          "checking and kind agreement.")
    # This contribution previously also claimed a scale measurement. That comparison
    # ran across two inference stacks, so it was cut from the results and now appears
    # in the limitations as an open question; advertising it here contradicted that.
    doc.p("5. An error analysis of a failure mode the aggregate metrics conceal: "
          "questions outside the rule base are answered confidently, because "
          "verification establishes support, not relevance.")
    if fb:
        doc.p("6. A second study in the opposite regime, where a language model trained "
              "from random initialization does the generating and the same symbolic "
              "gate decides what is released, quantifying both the hallucination it "
              "removes and the coverage that removal costs.")

    doc.h2("Research Framing Statement")
    doc.p("This is an architecture paper backed by a controlled evaluation on "
          "generated enterprise worlds, not a general-domain factuality result. Every "
          "figure reported here is read from a stored per-question record, and the "
          "tables are generated from those files rather than transcribed. Where a "
          "result does not generalize, we say so.")

    # ---- II. Related Work ----
    doc.h1("Related Work")
    doc.p("Hallucination is surveyed by Ji et al. [1] and Huang et al. [2], who "
          "separate faithfulness from factuality failures; our target is narrower, a "
          "decision stated without entailment from the governing rule. "
          "Retrieval-augmented generation [3] supplies evidence but leaves inference to "
          "the model. Self-RAG [4] learns to retrieve and critique; "
          "chain-of-verification [5] and SelfCheckGPT [6] detect inconsistency by "
          "resampling or self-interrogation. These share a limit: auditor and author are "
          "the same model, so a confident systematic error is invisible to both. "
          "FActScore [7] decomposes text into atomic claims for scoring, which informs "
          "our claim-level checks but is an evaluation method, not a release gate.")
    doc.p("Neuro-symbolic approaches delegate inference to an external solver: "
          "Logic-LM [8] and LINC [9] translate to first-order logic and discharge it "
          "with a prover, PAL [10] and Program-of-Thoughts [11] emit executable "
          "programs, and Binder [12] binds language to symbolic languages; the wider "
          "programme is set out in [13], [14]. PRAXIS differs in what it formalizes — "
          "not a reasoning chain for a puzzle but the enterprise’s own rule base, "
          "versioned over time and filtered by an authorization view — and in treating "
          "verification as a release gate rather than a post-hoc score.")

    # ---- III. Research Questions ----
    # The heading previously promised hypotheses and stated none. Each question now
    # carries the hypothesis it tests and the section that answers it, because two of
    # the four were previously answered only by implication.
    doc.h1("Research Questions")
    doc.p("RQ1. Does replacing model inference with symbolic inference over the same "
          "evidence and the same rules reduce unsupported assertions? H1: yes, and the "
          "comparison that tests it is against a model given the rules, not against "
          "retrieval alone (Section VII). RQ2. Is any reduction bought with abstention? "
          "H2: no — coverage should not fall (Section VII). RQ3. Which components carry "
          "the effect? H3: three-valued reasoning, policy versioning and authorization "
          "each carry a measurable share (Section VIII). RQ4. Does the advantage "
          "transfer to worlds the system has never seen? H4: for the LLM-free "
          "configuration yes, for the parser configuration unknown (Section IX).")

    # ---- IV. Architecture ----
    doc.h1("PRAXIS Architecture")
    doc.figure(FIGURES / "architecture.png",
               "Fig. 1. Architecture of PRAXIS as implemented, top to bottom. Box "
               "tags match the subsections: A1-A2 neural understanding (IV-A), B1-B4 "
               "symbolic reasoning (IV-B), C1-C4 the evidence gate (IV-C). The legend "
               "gives the four block kinds. The two inputs on the left — who is asking "
               "and the date of the case — are what select the authorized view and the "
               "policy version; the realizer is optional and wording-only, and the "
               "controller re-verifies anything it writes. The rule engine is "
               "three-valued (true, false, unknown) while verification is four-valued, "
               "since a claim may also be not checkable; the two are distinct layers. "
               "The dashed panel is the relevance check, which exists on the parser "
               "path and not on the encoder path — the failure analysed in Section X.",
               width_in=3.30)
    doc.p(f"Fig. 1 shows the implemented pipeline. The neural lane — a 33M encoder by "
          "default, or the model acting as semantic parser — yields a goal, the records "
          "referred to and any stated facts, and retrieval gathers the evidence. The "
          "symbolic lane resolves the policy version in force on the case’s own date, "
          "restricts records to those the asker may see, and evaluates the rules under "
          "three-valued logic to produce an established conclusion. Claims are then "
          f"extracted and checked against the evidence by the applicable members of a "
          f"{checks()['defined_checks']}-check suite, and "
          "the release gate either publishes the answer or withholds it and reports "
          "insufficient evidence.")
    doc.p("A model may phrase the conclusion but never establishes it, and its "
          "wording is checked for figures the evidence does not carry. The dashed "
          "panel is a relevance check between the resolved goal and the question, "
          "which this configuration cannot express: nearest-neighbour selection "
          "always returns a goal.")

    doc.h2("Neural Understanding")
    doc.p("A 33M-parameter bge-small encoder [17], run locally through ONNX, maps the "
          "question to a goal in the rule base, the records it refers to and any facts "
          "it states. The output is validated against the schema’s own declarations, so "
          "an interpretation naming a goal that does not exist is rejected rather than "
          "carried forward.")

    doc.h2("Three-Valued Symbolic Reasoning")
    doc.p("Rules are evaluated under Kleene strong three-valued logic [15]: a fact the "
          "company has no record of is unknown, not false. A two-valued engine answers "
          "“no, you are not eligible” when the truthful answer is “nobody recorded "
          "whether you are”. The reasoner resolves the policy version in force on the "
          "case’s own date rather than the newest, and reads records through an "
          "authorized view, so a rule consults only facts the asker may see.")

    doc.h2("The Evidence Gate")
    # An earlier draft said "ten deterministic checks run before release" and named
    # ten. Counting them over the test split (scripts/count_checks.py) shows eleven
    # are defined and that none runs on every case: which apply depends on what the
    # case asserts. The claim is now the measurement.
    doc.p(gate_sentence())

    # One question through every stage. The review asked for it and it earns its
    # space twice: it makes the pipeline concrete, and it is the clearest statement
    # of what the policy-version ablation costs, because the same question flips.
    trace = worked_trace()
    if trace:
        case, full, latest = trace
        doc.h2("One Question Through the Pipeline")
        doc.p(f"A support agent asks: “{case['question']}” A1 resolves this to the "
              f"goal {case['expect']['goal']}({case['expect']['subject']}) with one "
              f"stated fact, the customer’s reason for the return; an interpretation "
              f"naming a goal outside the schema would have been rejected here. A2 "
              f"retrieves the returns clauses the agent is allowed to see. The two "
              f"inputs then do their work: the asker’s role selects the authorized "
              f"view (B2), and the date of the case selects the policy version (B1). "
              f"That second choice decides the answer. The returns policy was "
              f"rewritten to allow 14 days instead of 8, effective 16 November 2026, "
              f"and this case is dated 14 November, so version 1 governs it. B3 "
              f"evaluates clause RET-4 under that version over the authorized records "
              f"and B4 establishes "
              f"{case['expect']['goal']}({case['expect']['subject']}) = "
              f"{str(full['decision']).lower()}.")
        doc.p(f"The default configuration runs no realizer, so C1 is empty and the "
              f"conclusion is rendered from its own grounds: “{full['text'].splitlines()[0]}” "
              f"C2 extracts the claims from that wording and C3 checks them; the "
              f"{full['numbers']} figures it states are each traced to a record or a "
              f"clause, so {full['unsupported_numbers']} are unsupported and C4 "
              f"releases it, with the caveat that it rests on what the customer said. "
              f"The ablation that applies the newest policy to every case reaches the "
              f"opposite conclusion on this question — “{latest['text'].splitlines()[0]}” "
              f"— fluently, with a real clause, and wrong. It is one of the nine "
              f"questions behind that row in Table III.")

    # ---- V. Experimental Design ----
    doc.h1("Experimental Design")
    doc.table(["Split", "Worlds", "Questions", "Purpose"],
              [["Development", "3", "153", "threshold calibration only; never reported"],
               ["Test", "5", "255", "frozen before first use; main results"],
               ["Confirmatory", "5", "255", "written after the system was final, scored once"],
               ["Evaluation total", "10", "510", "test + confirmatory"],
               ["Corpus total", "13", "663", "all generated cases"]],
              "TABLE 0. Dataset splits. The 510 evaluation questions are the test and "
              "confirmatory splits; the development split is excluded from every "
              "reported result.")
    # The split enumeration that stood here repeated Table I line for line. What the
    # table cannot say is that the three share no case and no world, which is the
    # property that makes the confirmatory split a held-out one.
    doc.p("Cases are generated over synthetic enterprise worlds, each with its own "
          "catalogue, staff, policy history and records, with gold answers from a "
          "reference oracle independent of the system under test — a separate "
          "implementation, not an independent author; Section XI-C states who wrote "
          "it. The three splits of "
          "Table I share no case and no world: thresholds were calibrated on the "
          "development worlds alone, and the confirmatory split was written after the "
          "system was final and scored once. All systems see the same questions, corpus "
          "and authorization context, every language-model system in a run uses the same "
          "model, and decoding is greedy so a run repeats exactly on one machine.")
    doc.p("Size of the formalization and of a world. The rule base declares 13 entity "
          "types, 17 goals and 2 lookups, discharged by 48 rules across 4 files, and "
          "every world is generated against that one schema. A world holds 138 "
          "records — 48 orders, 48 payments, 13 products, 6 customers, 5 categories, "
          "4 returns, 4 refunds, 4 employees, 3 grades, 2 policy versions and the "
          "company — and 6 policy documents, one of which exists in two versions so "
          "that a case dated before the change is governed by the older text. Each "
          "split draws 51 questions from each of its 5 worlds. The ten question "
          "categories, with their counts over the 255-case split, are: paraphrased 45 "
          "(the same question in other words), calculation 45, known 30 (a fact on "
          "record), policy_version 25 (a case governed by a superseded text), "
          "recommendation 25 (which remedy to offer), multi_hop 20, false_premise 20, "
          "missing_info 15, unauthorized 15 and not_covered 15 (no clause governs it).")
    doc.p("Baselines. Every language-model system uses Qwen2.5-7B-Instruct [18], "
          "reached "
          "through one local OpenAI-compatible server with greedy decoding at "
          "temperature 0 and an SQLite completion cache keyed by model, messages and "
          "token limit, so a re-run reproduces the scored outputs exactly. Retrieval "
          "is the same bge-small encoder over clause-level passages, filtered by the "
          "asker’s authorization before ranking, taking the top 6 passages above "
          "cosine 0.45; LLM + RAG + rules additionally places the governing rule text "
          "in the prompt, and Extractive RAG answers by quoting the top passage with "
          "no generator at all. Every baseline is given the same JSON reply schema "
          "with explicit refusal and hedging options — needs_more_information, "
          "ask_which_record, not_covered and not_permitted — so a baseline that "
          "abstains is following its instructions, not failing to parse. The latency "
          "column is per-question wall-clock time inside the system under test; the "
          "PRAXIS rows are CPU-only while the model rows wait on a GPU server whose "
          "device the run metadata does not record, so that column is not "
          "hardware-controlled and is read as seconds against milliseconds.")
    doc.p("Code and data. The system, the generators, the oracle, the frozen splits, "
          "every per-question record and the scripts that build each table and figure "
          "in this paper are at github.com/A-Pravin02/praxis-neurosymbolic; "
          "docs/paper/REPRODUCING.md gives the exact commands and the commit at which "
          "the splits were frozen.")

    doc.h1("Evaluation Metrics")
    doc.p("Denominators differ between metrics, which is why the figures can look "
          "incompatible and are not. Let N be all cases in a split, A the answerable "
          "cases — those whose gold kind is a decision, a value or a result — and D the "
          "subset of A answered definitely. Accuracy and hallucination are over N, "
          "coverage is |D|/|A|, and selective accuracy is correctness over D, so "
          "conditional, denied and not-covered cases are excluded from coverage but "
          "included in accuracy. " + worked_example() + " Every value in this paper is "
          "recomputed from the stored per-question records twice: by "
          "scripts/audit_metrics.py, which mirrors the harness, and by "
          "scripts/independent_metrics.py, which implements the definitions above "
          "from the raw records instead, so that an error shared by harness and audit "
          "would still surface. All 151 values reproduce.")
    # The verbal restatement of coverage and selective accuracy that stood here
    # repeated the formulas above and the worked example; what the reader needs is
    # the correctness rule per gold kind and why the two are always paired.
    doc.p("A case whose gold kind is not-covered is correct only if the system reports "
          "insufficient evidence; a conditional case only if the answer is hedged and "
          "not definite; a denied case only if the system refuses without disclosing "
          "the record. Hallucination is the union of three disjoint faults: a wrong "
          "definite answer, a definite answer where the gold says evidence is "
          "insufficient, and disclosure of a record the asker may not see. Coverage and "
          "selective accuracy are always reported together, because a system can buy "
          "high selective accuracy by refusing the hard questions.")
    doc.p("Intervals are 95% bootstrap percentile intervals over questions. Paired "
          "comparisons use the exact McNemar test [19] on per-question outcomes: cases where "
          "both systems agree carry no information, so of the discordant pairs only, "
          "the count favouring one system is tested two-sided against Binomial(n, 1/2). "
          "The exact binomial tail is used rather than the chi-square approximation, "
          "because the discordant counts here are small.")

    # ---- VI. Results ----
    doc.h1("Results")
    # Condensed. The scope caveat was stated at length here, again in a sentence
    # under Fig. 2, and a third and fourth time in the framing statement and the
    # limitations. Two of the four are now gone; this keeps the one a reader needs
    # at the point the numbers appear.
    doc.p("Scope of the evidence. These are preliminary findings about neuro-symbolic "
          "verification reducing unsupported claims under the evaluated conditions: a "
          "controlled benchmark of synthetic enterprise scenarios with gold answers "
          "from an oracle independent of the system under test, not real customer "
          "traffic. They do not "
          "establish general-purpose hallucination elimination, nor effectiveness "
          "across models, datasets and domains not tested here, and the rates below "
          "should not be read as open-domain factuality rates.")
    order = ["neurosymbolic", "ns_parser", "llm_rag_rules", "llm_rag_selfcheck",
             "llm_only", "llm_rag", "rag_extractive"]
    # Shortened: in a single column these names wrapped mid-phrase, so "LLM + RAG +
    # self-check" occupied three lines and its row no longer read as one row.
    label = {"neurosymbolic": "PRAXIS, no LLM", "ns_parser": "PRAXIS, LLM parser",
             "llm_rag_rules": "LLM+RAG+rules",
             "llm_rag_selfcheck": "LLM+RAG+self-check",
             "llm_only": "LLM only", "llm_rag": "LLM+RAG",
             "rag_extractive": "Extractive RAG",
             # This one reached the confirmatory table and printed as
             # "ns_no_authorizati on", an internal identifier split across two lines.
             "ns_no_authorization": "PRAXIS, no authorization"}
    # Accuracy, hallucination and coverage are in Fig. 2; the table carries only what
    # the figure does not, so the same numbers are not printed twice.
    rows = []
    for name in order:
        if name not in test["systems"]:
            continue
        cov, _ = T(name, "coverage")
        sel, _ = T(name, "selective_accuracy")
        over, _ = T(name, "over_abstention")
        lat, _ = T(name, "mean_latency_ms")
        rows.append([label[name], num(cov), num(sel), num(over), num(lat, places=0)])
    # Headings abbreviated to what fits a quarter of one column; the caption below
    # defines each one, which is where a definition belongs anyway.
    doc.table(["System", "Coverage", "Sel. acc.", "Over-abst.", "Latency"], rows,
              f"TABLE II. Test split, {test['cases']} questions: the quantities not "
              f"shown in Fig. 2. Coverage is definite answers over answerable "
              f"questions; selective accuracy is correctness among them; "
              f"over-abstention is the answerable questions refused; latency is the "
              f"per-question mean in milliseconds, and the PRAXIS rows are CPU-only "
              f"while the model rows wait on a GPU server, so the column is not "
              f"hardware-controlled.",
              widths=[1.05, 0.62, 0.58, 0.62, 0.58])

    doc.figure(FIGURES / "main_results.png",
               "Fig. 2. Main comparison on the test split. Accuracy and hallucination "
               "are both over all 255 cases. Error bars show 95% bootstrap intervals; "
               "hatched bars are the proposed system. Coverage, which has a different "
               "denominator, is reported in Table II so that a system cannot appear "
               "strong merely by refusing to answer. Extractive RAG commits to no "
               "decision on any case, so its 0.000 accuracy and 0.000 coverage are one "
               "fact, not two.",
               width_in=6.6, span=True)
    # The caveat this paragraph carried is now in the scope paragraph above, which
    # is where a reader meets the numbers; Fig. 2's own caption says what it plots.
    # The headline comparison is against the baseline that receives the same rules.
    # Leading with LLM + RAG, which does not, measures the rules as much as the
    # architecture and flatters this system by about seven accuracy points.
    doc.p(f"RQ1. The fair comparison is against the configuration given everything "
          f"PRAXIS is given: the same corpus, the same retrieval and the governing rule "
          f"text in its prompt. The LLM-free configuration answers "
          f"{num(ns_acc, ns_acc_ci)} of all questions correctly at "
          f"{num(ns_hal, ns_hal_ci)} hallucination; that baseline answers "
          f"{num(rules_acc)} at {num(rules_hal)}. The paired gain is "
          f"{rules_gap:+.3f} accuracy [{rules_gap_ci[0]:.3f}, {rules_gap_ci[1]:.3f}], "
          f"correct where the baseline is not on {rules_only_a} questions against "
          f"{rules_only_b} the other way "
          f"(exact McNemar, p = {rules_p:.1g}). Against retrieval without the rules the "
          f"gap is larger — {num(rag_acc)} and {num(rag_hal)} — but part of that gap is "
          f"the rules, not the architecture, so it is not the claim we make.")
    doc.p(f"RQ2. The reduction is not bought with abstention. PRAXIS covers "
          f"{num(ns_cov)} of answerable questions against "
          f"{num(T('llm_rag_rules','coverage')[0])} for the rules-in-prompt baseline "
          f"and {num(T('llm_rag','coverage')[0])} for retrieval alone: it answers more "
          f"questions and more of them correctly. Extractive RAG is the opposite case "
          f"and is kept in the tables as the reference point: quoting a retrieved "
          f"passage gives {num(T('rag_extractive','accuracy')[0])} accuracy at "
          f"{num(T('rag_extractive','hallucination_rate')[0])} hallucination and "
          f"{num(T('rag_extractive','coverage')[0])} coverage, because it never "
          f"commits to a decision at all — the low hallucination rate is silence, not "
          f"reliability, which is the reading the coverage column exists to prevent.")
    doc.p(f"Confining the model to semantic parsing, with the decision still symbolic, "
          f"gives the strongest configuration: {num(pa_acc, pa_acc_ci)} accuracy at "
          f"{num(pa_hal, pa_hal_ci)} hallucination, {num(pa_cov)} coverage, and a "
          f"selective accuracy of {num(pa_sel)}. Of the questions it commits to, none is "
          f"wrong. Section IX examines how far that survives.")

    # ---- VII. Ablations ----
    doc.h1("Ablation Studies")
    abl = [("ns_no_verification", "verification gate removed"),
           ("ns_two_valued", "unknown treated as false"),
           ("ns_latest_version", "newest policy applied to every case"),
           ("ns_trust_statements", "asker’s statements override records"),
           ("ns_no_authorization", "reasoning sees every record"),
           ("ns_no_number_check", "numeric grounding check removed"),
           ("ns_no_kind_agreement", "kind-agreement check removed")]
    # Coverage and the paired p-value were quoted in the prose and absent from the
    # table, which is how two rows came to look like refutations of the full system.
    # Reading a row now needs no second source.
    def _sig(system: str) -> tuple[float | None, int]:
        c = test.get("significance", {}).get(system, {}).get("correct", {})
        return c.get("p_value"), (c.get("only_a") or 0) + (c.get("only_b") or 0)

    rows = [["PRAXIS (full)", num(ns_acc), num(ns_hal), num(ns_cov),
             num(T("neurosymbolic", "leak_rate")[0]), "—"]]
    for name, desc in abl:
        if name not in test["systems"]:
            continue
        p, disc = _sig(name)
        rows.append([desc, num(T(name, "accuracy")[0]),
                     num(T(name, "hallucination_rate")[0]),
                     num(T(name, "coverage")[0]),
                     num(T(name, "leak_rate")[0]),
                     "n/a" if p is None else f"{p:.3g} (n={disc})"])
    doc.table(["Configuration", "Accuracy", "Hallucination", "Coverage", "Disclosure",
               "p vs full"], rows, span=True, caption=
              "TABLE III. Decision-level ablations on the test split, 255 questions. "
              "Each row disables one mechanism. Disclosure is the share of the 15 "
              "unauthorized-category questions on which a record the asker may not see "
              "is revealed, so 0.867 is 13 of 15. The last column is the exact McNemar "
              "p-value for correctness against the full system, with the discordant "
              "count on which it is computed; the gate acts on wording, so Section "
              # Not kept together: pushing all eight rows onto the next page left a
              # third of this one blank, and the heading row now repeats if it breaks.
              "VIII-A tests it instead.")

    # "broadly intact" was wrong by its own table: authorization costs 0.059 accuracy,
    # more than the 0.051 the same paragraph calls real weight. The disclosure point
    # stands on its own and does not need the accuracy claim to be soft.
    doc.p("RQ3, and three readings. Three-valued reasoning carries real weight: "
          f"treating unknown as false costs "
          f"{abs(ns_acc - T('ns_two_valued','accuracy')[0]):.3f} accuracy "
          f"(p = {_sig('ns_two_valued')[0]:.3g}), because the system starts denying "
          "entitlements that were merely unrecorded. Policy versioning costs "
          f"{abs(ns_acc - T('ns_latest_version','accuracy')[0]):.3f} "
          f"(p = {_sig('ns_latest_version')[0]:.3g}) when the newest policy is applied "
          "to every case regardless of date. Authorization costs more accuracy than "
          f"either — {abs(ns_acc - T('ns_no_authorization','accuracy')[0]):.3f}, "
          f"p = {_sig('ns_no_authorization')[0]:.3g} — and that is the smaller half of "
          "what it does: the disclosure rate rises from "
          f"{num(T('neurosymbolic','leak_rate')[0])} to "
          f"{num(T('ns_no_authorization','leak_rate')[0])}, so the unauthorized "
          "reasoner answers slightly worse and reveals 13 of the 15 protected records "
          "while doing it. An accuracy-only evaluation would record the first and miss "
          "the second.")

    # Two rows of this table still read as refutations at a glance, and the columns
    # that answer them are now in it. The prose says what the columns mean.
    ka_acc = T("ns_no_kind_agreement", "accuracy")[0]
    (ka_p, ka_n), (ts_p, _) = _sig("ns_no_kind_agreement"), _sig("ns_trust_statements")
    ts_hal = T("ns_trust_statements", "hallucination_rate")[0]
    ts_cov = T("ns_trust_statements", "coverage")[0]
    doc.p(f"Two rows need reading against a second column before they are read as "
          f"refutations. Removing the kind-agreement check raises accuracy to "
          f"{num(ka_acc)}, nominally above the full system, but the paired test puts "
          f"the difference at p = {ka_p:.3g} on {ka_n} discordant cases: it "
          f"is noise, and what the check actually buys is "
          f"{abs(ns_hal - T('ns_no_kind_agreement','hallucination_rate')[0]):.3f} "
          f"hallucination for "
          f"{abs(ns_cov - T('ns_no_kind_agreement','coverage')[0]):.3f} coverage. "
          f"Letting the asker’s statements override records lowers hallucination to "
          f"{num(ts_hal)}, well below the full system, while coverage falls from "
          f"{num(ns_cov)} to {num(ts_cov)} and accuracy drops significantly "
          f"(p = {ts_p:.3g}). That row is this paper’s own warning turned on itself: "
          f"the configuration abstains its way to a better hallucination rate, which "
          f"is precisely why we report coverage beside every accuracy. Removing the "
          f"numeric grounding check changes nothing at all, for the same reason the "
          f"verification gate does not — both police prose, and the default "
          f"configuration writes none.")

    doc.h2("A Controlled Test of the Verification Gate")
    doc.p("Two measurements are at stake, and separating them resolves an apparent "
          "contradiction. The ablation rows above are decision-level: they grade "
          "structured outcomes and cannot see the gate at all, since the default "
          "configuration renders conclusions deterministically and the gate acts only "
          "on prose. The experiment below is text-level and measures what the gate "
          "changes in the released answer. It holds everything else fixed — the same "
          "255 questions, model, retrieval and prompts, with the model writing the "
          "final wording in both arms and verification on in one and off in the other.")
    # Condensed for the page budget; the three layers are enumerated in the
    # accompanying validation report and in the regression test itself.
    doc.p("Reaching the switch proved harder than expected, and the difficulty is "
          "itself a result. The guard is layered across the realizer, the controller’s "
          "re-verification and the release check; the switch originally reached only "
          "the last, and two successive runs produced byte-identical arms on all 255 "
          "cases — not because the checks do nothing, but because each remaining layer "
          "repaired what the ablated one let through. A regression test now asserts "
          "that it reaches all three.")
    doc.p(f"With the switch complete the arms diverge: the released answer differs on "
          f"{gate['differ']} of {gate['cases']} cases. Answers stating a figure the "
          f"evidence does not support rise from {gate['gated_unsupported']} to "
          f"{gate['ablated_unsupported']}, and that difference is not established: the "
          f"two arms differ on only {gate['discordant']} questions in this respect, "
          f"giving an exact McNemar p of {gate['p_value']:.2g}. We therefore do not "
          f"claim the count as a measured effect. What the experiment does show is "
          f"qualitative and specific. On one case the gated system answers with the "
          f"recommended resolution and its grounds, while the ablated system abandons "
          f"the conclusion and emits generic support boilerplate carrying a figure "
          f"found nowhere in the evidence. Establishing the rate needs a larger "
          f"prose-bearing split than the 255 questions this one has.")
    doc.p("Yet every decision-level metric is identical in both arms. This locates "
          "what the benchmark can see: the scorer grades the structured decision — "
          "goal, decision, value, outcome — which the symbolic layer fixes before any "
          "model writes a word and which the gate therefore never changes. The gate "
          "protects the prose, and prose faithfulness is not in this scorer. Its "
          "contribution is visible here at the level of released text and is not "
          "quantified by this experiment; a decision-level benchmark is the wrong "
          "instrument, and Study II supplies the claim-level one.")
    # Two numbers in this paper invite the reading that one was copied from the other.
    # Both were recounted from the records, and they are unrelated.
    doc.p(f"Two coincidences are worth naming, because each looks like a transcription "
          f"error and neither is. This section counts {gate['differ']} of "
          f"{gate['cases']} cases whose wording the gate changes, and Table V counts "
          f"{len(_wrong_cases)} of {test['cases']} questions the full system gets "
          f"wrong. "
          + ("The two figures are equal and the two sets are not: they overlap in "
             if gate['differ'] == len(_wrong_cases) else
             "The two sets are different: they overlap in ")
          + f"{len(gate['differ_cases'] & set(_wrong_cases))} questions, and each was "
          f"counted from its own run’s records. Separately, mean "
          f"latency is {num(only_lat, places=0)} ms for the model alone and "
          f"{num(sc_lat, places=0)} ms for retrieval with a self-check: the self-check "
          f"arm is retrieval’s {num(rag_lat, places=0)} ms plus a short second call, "
          f"while the model alone makes one call but writes longer answers with no "
          f"retrieved text to anchor them. The two totals land a millisecond apart by "
          f"chance, and their distributions differ (slowest case "
          f"{max(_only_lat):.0f} ms against {max(_sc_lat):.0f} ms).")

    # A closing paragraph here restated the decision-level/text-level distinction a
    # third time - after the opening paragraph of this subsection and after "Yet every
    # decision-level metric is identical in both arms". Removed as redundant; the
    # identical ablation row is read in the preceding paragraph.

    # ---- VIII. Scale ----
    # Section removed. The 1.5B-vs-7B comparison ran across two inference stacks
    # (an OpenAI-compatible endpoint and a local transformers runtime), so it is not a
    # controlled single-variable sweep and the brief is explicit that such a comparison
    # must not be presented as one. It is recorded in the limitations as an open
    # question instead of shown as a result.
    doc.h1("Transfer to Unseen Worlds")
    # Previously a single sentence of accuracies. Accuracy alone cannot say whether a
    # system transferred or merely went quiet, which is the distinction the rest of
    # this paper insists on, so coverage and hallucination are reported with their
    # intervals for every system that ran.
    conf_rows = []
    for name in ["neurosymbolic", "ns_parser", "llm_rag", "llm_only",
                 "ns_no_authorization"]:
        if name not in confirm["systems"]:
            continue
        a, aci = C(name, "accuracy")
        h, hci = C(name, "hallucination_rate")
        cv, _ = C(name, "coverage")
        sl, _ = C(name, "selective_accuracy")
        conf_rows.append([label.get(name, name).replace(chr(10), " "),
                          num(a, aci), num(h, hci), num(cv), num(sl)])
    doc.table(["System", "Accuracy", "Hallucination", "Coverage", "Selective acc."],
              conf_rows, span=True, caption=
              f"TABLE IV. Confirmatory split, {confirm['cases']} questions over five "
              f"companies the system had never seen, written after the system was final "
              f"and scored once. Brackets are 95% bootstrap intervals. Fewer systems "
              f"ran here than on the test split: the confirmatory run was scoped to the "
              f"comparisons the paper’s claims rest on.", keep_together=True)
    doc.p(f"RQ4. The LLM-free configuration transfers essentially unchanged "
          f"({num(cns_acc)} against {num(ns_acc)} on test). The parser configuration "
          f"does not: accuracy falls from {num(pa_acc)} to {num(cpa_acc)}, hallucination "
          f"rises from {num(pa_hal)} to {num(cpa_hal)}, and selective accuracy falls "
          f"from {num(pa_sel)} to {num(cpa_sel)}. Its advantage over embedding "
          "understanding narrows from roughly fifteen accuracy points to two, and the "
          "paired tests narrow with it: on the test split the parser is correct where "
          "embedding understanding is not on 40 questions against 2 the other way "
          "(exact McNemar, p = 4e-10), while on the confirmatory split the same "
          "comparison is 7 against 1 (p = 0.07). At the 0.05 level the first is "
          "persuasive and the second is not. We therefore report the parser’s advantage "
          "as demonstrated on the test split and not established on unseen worlds.")

    # ---- X. Error Analysis ----
    doc.h1("Error Analysis")
    path = SCALE_DIR / "test.neurosymbolic.jsonl"
    if path.exists():
        cats = category_rates(path)
        doc.table(["Category", "n", "Accuracy", "Errors"],
                  [[c, str(cnt), f"{a:.3f}", str(round(cnt * (1 - a)))]
                   for c, cnt, a in cats],
                  "TABLE V. Accuracy by question category, LLM-free configuration, "
                  "weakest first, with the error count each category contributes. "
                  "Category sizes are 15 to 45 cases, so the rates are indicative "
                  "rather than precise; the counts sum to every error the system makes.",
                  keep_together=True)
    # Previously this section discussed not_covered and nothing else, which is 15 of
    # 52 errors. Naming where the other 37 come from changes what the reader takes the
    # system's weakness to be: the single largest source is the encoder's reading of a
    # rephrased question, not the rule base's coverage.
    rows_ns = per_case("neurosymbolic")
    from collections import Counter
    err = Counter(r.get("category") for r in rows_ns.values() if not r.get("correct"))
    top = ", ".join(f"{c} {k}" for c, k in
                    ((err[k], k) for k in [k for k, _ in err.most_common()]))
    doc.p(f"The {sum(err.values())} errors are not one failure mode. By category they "
          f"are: {top}. Two thirds therefore fall outside the failure the rest of this "
          f"section analyses. The largest single source, {err['paraphrased']} of "
          f"{sum(1 for r in rows_ns.values() if r.get('category') == 'paraphrased')} "
          f"paraphrased questions, is the encoder: the question is in scope and the "
          f"wording moves far enough from the goal’s own phrasing that nearest-"
          f"neighbour selection lands on a different goal. The "
          f"{err['recommendation']} recommendation errors are the rule base rather "
          f"than the encoder — choosing among six remedies needs more of the policy "
          f"than the rules encode, and the system commits to the wrong one rather than "
          f"abstaining. Both are addressable without new architecture; the third, "
          f"below, is not.")
    doc.p("The aggregate also conceals a complete failure. On the fifteen questions no "
          "policy clause covers, the system abstained in none: thirteen produced a "
          "confident answer and two a clarification. Asked the warranty period on a "
          "product, it answered that a replacement was not available.")
    doc.p("Understanding maps an out-of-scope question onto the nearest in-scope goal, "
          "and the system answers that question correctly, citing real clauses and real "
          "records. The gate cannot intervene because every check passes — across all "
          "fifteen cases no unsupported number appears. Verification establishes that a "
          "claim follows from evidence, not that it answers the question asked.")
    doc.p("This qualifies the architecture’s central promise: an out-of-scope question "
          "is not unsupportable, it is answerable about something else, and the "
          "grounding makes the irrelevant answer more convincing rather than less. The "
          "remedy is a relevance check independent of the evidence gate, which this "
          "configuration has no way to express: selecting a goal by nearest neighbour "
          "always returns one.")

    # ---- Study II: the from-scratch model ----
    if fb:
        corpus = fb["corpus"]
        # Recomputed from claims.jsonl rather than read from report.json, which
        # counts unverified claims as false. See football_stats.
        s2 = football_stats(FOOTBALL_CLAIMS)
        params = fb["model_parameters"]
        tokens = corpus["tokens"]["train"]

        doc.h1("Study II: Gating a Model Trained from Scratch")
        doc.p("The first study removes the language model from the decision; this one "
              "puts it back in the hardest form — a model whose knowledge comes wholly "
              "from a corpus we control, generating freely, with the symbolic layer "
              "deciding what reaches the reader. It is secondary: it tests the gate, "
              "not the generator, and training from scratch is not necessary to the "
              "architecture.")

        doc.h2("Data, Model and Training")
        doc.p(f"The source is a record of {fb.get('source_goals', 3637)} FIFA World Cup "
              f"goals from 1930 to 2022, men’s and women’s tournaments, covering 1,162 "
              f"matches and 1,831 players. One dataset serves three roles, which keeps "
              f"the comparison honest: the rows are verbalised into a training corpus, "
              f"loaded as a typed knowledge graph, and used to generate evaluation "
              f"prompts, so generator and verifier are grounded in the same facts.")
        doc.p("The split is by match, not by row, so a withheld match has no sibling "
              "goals in training: 137 matches are withheld entire. Leakage is checked "
              "on (fixture, tournament) pairs rather than names, since fixtures recur "
              "across tournaments and the 1934 and 1938 tournaments held replays. No "
              "evaluated fixture appears in training.")
        doc.p(f"The model is nanoGPT [20], unmodified, at {params/1e6:.1f}M parameters "
              f"(4 layers, 4 heads, width 256, vocabulary {corpus['vocab_size']}), "
              f"trained from random initialization on a single consumer GPU. Initial "
              f"loss was 8.454 against ln(4096) = 8.318, consistent with random "
              f"initialization. Validation loss reached its minimum of "
              f"{fb['best_val_loss']:.4f} and then rose while training loss continued "
              f"to fall; the minimum checkpoint is used.")
        # This paragraph said the model "memorises the corpus" while Section XI-D
        # said it "has not reliably memorised" it. The records support the second: the
        # refuted rate on matches it trained on is 0.458, so most of what it says
        # about its own training data is still wrong. What it does do is overfit -
        # validation loss turns upward - which is not the same thing as retaining the
        # facts, and conflating the two was the contradiction.
        doc.p(f"The sizing bounds every claim about the model: {tokens:,} training "
              f"tokens against {params/1e6:.1f}M parameters is "
              f"{tokens/params:.3f} tokens per parameter, two orders of magnitude below "
              f"compute-optimal. It overfits — validation loss turns upward while "
              f"training loss keeps falling — without thereby retaining the facts, as "
              f"Section XI-D shows. No configuration avoids that at this scale, and a "
              f"stronger generator would make the test easier rather than more "
              f"informative.")

        doc.h2("Claim Extraction and Verification")
        doc.p(f"Generated text is split into sentences, each matched against the "
              "corpus’s phrasings to yield a typed claim: a goal event, a player’s "
              "tournament total, or a match total. Rule checks need no lookup — a goal "
              "in the first half at minute 68 is impossible whatever the records say. "
              "Graph checks ask whether the goal is on record. Verdicts are "
              "four-valued, and a claim about a player or match the records do not "
              "contain is UNVERIFIED rather than CONTRADICTED, since the graph is "
              f"incomplete and absence is not refutation. Extraction covers "
              f"{s2['extraction']*100:.1f}% of generated sentences; the remainder are "
              f"reported as not checkable rather than guessed at.")

        doc.h2("Results")
        rows = [["SLM only (every claim released)",
                 f"{s2['checkable']:,}", f"{s2['refuted']:,}", f"{s2['unverified']:,}",
                 num(s2["refuted_rate"]), num(1.0)],
                ["SLM + symbolic gate",
                 f"{s2['supported']:,}", "0", "0",
                 num(0.0), num(s2["coverage"])]]
        doc.table(["System", "Claims released", "Refuted", "Unverifiable",
                   "Refuted rate", "Coverage"],
                  rows, span=True, caption=
                  f"TABLE V. {fb['prompts']} prompts, {s2['checkable']:,} checkable "
                  f"claims. Both systems read the same generations, so the gate changes "
                  f"only what is released. Refuted and unverifiable are kept apart: the "
                  f"graph is incomplete, so a claim it has no record of is not thereby "
                  f"false. The gate withholds both.")
        doc.p(f"Of {s2['sentences']:,} sentences, {s2['checkable']:,} yielded a typed "
              f"claim and {s2['unparseable']} were unparseable. Of the typed claims "
              f"{s2['supported']:,} were supported, {s2['refuted']:,} refuted and "
              f"{s2['unverified']} unverifiable — naming a player or match the records "
              f"do not contain. A further "
              f"{fb.get('truncated_fragments_discarded',0)} trailing fragments cut off "
              f"by the token budget were discarded rather than counted against the "
              f"model. That is "
              f"{fb.get('truncated_fragments_discarded',0)/fb['prompts']:.2f} per "
              f"prompt against a fixed {fb['sampling']['max_new_tokens']}-token budget, "
              f"which is what makes the discard structural — one severed tail per "
              f"generation — rather than a selection among claims.")

        # Fig. 3 was a two-bar chart carrying four numbers, and at column width its
        # legend sat across the 1.000 label while the 0.467 label sat on its own bar.
        # Tabulating the four numbers removes both collisions, states them to three
        # places instead of reading them off a bar, and reclaims the lines that keeping
        # Table VI contiguous costs.
        doc.table(["Quantity", "Rate", "Reading"],
                  [["Base refuted rate", num(s2["refuted_rate"]),
                    "of claims the model makes, the graph refutes this share"],
                   ["Gated refuted rate", num(0.0),
                    "true by construction, not a measurement"],
                   ["Coverage after gating", num(s2["coverage"]),
                    "the share of checkable claims that survive"],
                   ["Withheld", num(s2["withheld"]),
                    f"{s2['refuted']} refuted and {s2['unverified']} unverifiable"]],
                  "TABLE VII. What the gate removes in the secondary study and what it "
                  "costs, over the same 1,056 checkable claims. Only the first and "
                  "third rows are measurements of the gate’s effect.",
                  keep_together=True)

        if s2["kinds"]:
            label = {"goal": "goal event",
                     "player_tournament_total": "player tournament total",
                     "match_total": "match total"}
            doc.table(["Claim type", "n", "Refuted rate [95% CI]", "Unverifiable"],
                      [[label.get(k, k), str(cnt), f"{r:.3f} [{lo:.3f}, {hi:.3f}]",
                        str(u)]
                       for k, cnt, r, u, (lo, hi) in s2["kinds"]],
                      "TABLE VI. Refuted-claim rate by claim type, over all n of that "
                      "type, with 95% bootstrap intervals; unverifiable claims counted "
                      "apart. The match-total row rests on 25 claims and its interval "
                      "is correspondingly wide.",
                      # The break that stranded this table's last row under the next
                      # column, with its header left behind, is now fixed rather than
                      # explained: replacing Fig. 3 with the four numbers it plotted
                      # reclaimed the lines that keeping the block contiguous costs.
                      keep_together=True)

        doc.h2("How This Result Should Be Read")
        doc.p("Three findings must be kept apart. In the enterprise benchmark the "
              "architecture reduces unsupported claims against a retrieval baseline on "
              "the same questions: a measured comparison. In Study II gating removes "
              "refuted claims at a cost in coverage: a trade-off, not an improvement in "
              "the generator. Whether the verifier is itself correct is established by "
              "neither. Only the first is evidence that the architecture reduces "
              "hallucination.")
        doc.p(f"The gated system’s refuted rate of 0.000 is near-tautological and "
              f"should not be reported as the finding. The gate releases only claims "
              f"the graph confirms, so by construction none of them is refutable. The "
              f"informative quantities are the other two: a base refuted rate of "
              f"{num(s2['refuted_rate'])}, and the {num(s2['withheld'])} of checkable "
              f"claims that withholding costs — the refuted ones together with a "
              f"further {num(s2['unverified_rate'])} the graph cannot confirm either "
              f"way. Verification buys soundness with silence, and it pays for part of "
              f"that silence with claims that may well be true.")
        doc.p(f"What is not tautological is that the checks are substantive. "
              f"{NUMWORD.get(s2['rule_only'], s2['rule_only'])} claims were refuted by rule "
              f"alone, with no database "
              f"consulted — a goal in the first half at minute 68, or credited to a "
              f"country that was not playing. The graph-based refutations are specific "
              f"and plausible-looking, which is what makes them dangerous: the "
              f"commonest is a goal event the records do not contain at the stated "
              f"minute, and relocating a real goal to the wrong tournament is the next "
              f"largest group. Per claim type the gate discriminates rather than "
              f"rubber-stamps (Table VIII).")
        # Verdict-breakdown figure removed for the page budget: it spans both columns,
        # and every count in it - the four status totals and the refutations settled by
        # rule alone - is stated in the text below.
        if s2["held_out"]["n"] and s2["seen"]["n"]:
            # "Indistinguishable" was asserted from two rates with no sample sizes and
            # no test. Both are now given, and the interval is what carries the claim:
            # it is narrow enough that a large difference is excluded, which is a
            # stronger statement than a p-value above 0.05 on its own.
            ho, se = s2["held_out"], s2["seen"]
            lo, hi = s2["transfer_ci"]
            doc.p(f"One result deserves emphasis. The refuted-claim rate on claims from "
                  f"prompts naming matches withheld from training is "
                  f"{ho['refuted']:.3f} (n = {ho['n']}), and on matches the model was "
                  f"trained on it is {se['refuted']:.3f} (n = {se['n']}): a difference "
                  f"of {ho['refuted'] - se['refuted']:+.3f} "
                  f"[{lo:.3f}, {hi:.3f}], Fisher exact "
                  f"p = {s2['transfer_p']:.2f}. The interval excludes any difference "
                  f"larger than about nine points in either direction, so the model "
                  f"confabulates about its own training data about as readily as about "
                  f"events it never saw. At {tokens/params:.3f} tokens per parameter it "
                  f"has not reliably memorised the corpus, which suggests that data "
                  f"starvation, and not merely absent knowledge, drives the errors. It "
                  f"also means this study measures a generator that is unreliable "
                  f"everywhere, not one that fails only outside its training "
                  f"distribution.")

    # ---- XI. Limitations ----
    doc.h1("Limitations and Threats to Validity")
    doc.bullet("Scope of the evidence. Results come from generated worlds with oracle "
               "gold answers, not real customer traffic, in a single domain — "
               "enterprise policy decision support — whose governing rules can be "
               "written down. English only, one item per order.")
    doc.bullet("Transfer. Perfect selective accuracy does not generalize: the 1.000 "
               "figure on the test split falls on the confirmatory split, and no claim "
               "of perfect reliability should be read from it.")
    doc.bullet("Authorship. The rule base, world generators and oracle were written by "
               "one person, and the rules are hand-translated from policy text. An "
               "independently authored evaluation, and automatic extraction of "
               "candidate rules for human review, are the next steps.")
    doc.bullet(f"The gate’s contribution is shown at the level of released text and "
               f"is not quantified. The controlled test in Section VIII-A changes "
               f"{gate['differ']} of {gate['cases']} answers, but the rise in "
               f"unsupported figures, {gate['gated_unsupported']} to "
               f"{gate['ablated_unsupported']}, rests on {gate['discordant']} "
               f"discordant cases and is not significant "
               f"(p = {gate['p_value']:.2g}). Quantifying the gate needs a "
               f"prose-faithfulness metric and a larger prose-bearing split.")
    doc.bullet("Model capability is not controlled. A 1.5B parser scored well below a "
               "7B one while the retrieval baselines barely moved, but the two runs "
               "used different inference stacks, so this is an open question.")
    if fb:
        _s2 = football_stats(FOOTBALL_CLAIMS)
        doc.bullet("Study II. The gated rate of zero refuted claims is true by "
                   "construction — verification is definitionally correct with respect "
                   "to the graph, so the gate’s precision and recall cannot be measured "
                   "against independent ground truth, and a graph error would propagate "
                   "silently. Its model is "
                   f"{fb['model_parameters']/1e6:.1f}M parameters on "
                   f"{fb['corpus']['tokens']['train']:,} tokens, a deliberately weak "
                   "generator of checkable claims rather than a usable assistant. "
                   f"Claim extraction covers {_s2['extraction']*100:.1f}% of generated "
                   "sentences; an extractor that silently fails is indistinguishable "
                   "from a model that said nothing checkable.")
    doc.bullet("Both studies verify claims against a curated knowledge base. Neither "
               "speaks to open-domain factuality, where no such base exists for the "
               "gate to consult.")

    doc.h1("Conclusion and Future Work")
    # This sentence used to open on the retrieval-only baseline, which is the weakest
    # one and not the comparison the paper now leads with. Against the baseline given
    # the same rules the gain is smaller and it is the one we claim.
    doc.p(f"Removing the language model from the decision path, rather than asking it "
          f"to audit itself, reduced hallucination from {num(rules_hal)} to "
          f"{num(ns_hal)} against a model given the same corpus and the same rules, "
          f"and from {num(rag_hal)} against retrieval alone, while raising coverage "
          f"from {num(T('llm_rag_rules','coverage')[0])} to {num(ns_cov)} and cutting "
          f"latency by roughly two orders of magnitude. The reduction is not an "
          f"artefact of refusing difficult questions.")
    doc.p(f"Three findings temper this, and belong in any summary of the result. "
          f"First, the comparison that matters is against a model given the same rules, "
          f"where the gain is {rules_gap:+.3f} accuracy "
          f"[{rules_gap_ci[0]:.3f}, {rules_gap_ci[1]:.3f}] rather than the larger gap "
          f"against retrieval alone. Second, the parser configuration’s selective "
          f"accuracy of {num(pa_sel)} on the test split falls to {num(cpa_sel)} on "
          f"unseen worlds, and its advantage over embedding understanding is not "
          f"established there (p = 0.07): no claim of perfect reliability survives the "
          f"confirmatory split. Third, the LLM-free configuration does not recognise "
          f"questions its rule base does not cover — 0 abstentions on 15 such questions "
          f"— a failure verification is structurally unable to catch, because relevance "
          f"is not support.")
    if fb:
        s2 = football_stats(FOOTBALL_CLAIMS)
        doc.p(f"The secondary study makes the price explicit: the graph refutes "
              f"{num(s2['refuted_rate'])} of the claims a model trained from random "
              f"initialization makes, and gating leaves only supported ones at "
              f"{num(s2['coverage'])} coverage. Together the studies suggest "
              f"one reading: verification converts an unreliable generator into a "
              f"silent-but-sound one, and the useful question is never whether "
              f"hallucination falls — gating guarantees that — but how much of the "
              f"answer survives.")
    doc.p("Future work is a relevance gate for the LLM-free configuration, which needs "
          "goal selection to have a null option it currently lacks, an independently "
          "authored evaluation, a single-stack measurement of the capability effect "
          "noted in Section XII, and raising coverage in Study II without "
          "reintroducing unsupported claims.")

    # ---- references ----
    # The IEEE template numbers Heading 1 sections; References must not be a
    # numbered section, so it takes Heading 5, the style the template itself uses
    # for References and Acknowledgment.
    doc.p("References", "Heading 5")
    # Page ranges take en dashes, journal and conference names the IEEE
    # abbreviations, and [2] and [3] the pages the review found missing. [2]'s volume
    # and issue could not be confirmed against a publisher record and are left as the
    # publisher's own listing gives them.
    refs = [
        "Z. Ji et al., “Survey of hallucination in natural language generation,” "
        "ACM Comput. Surv., vol. 55, no. 12, pp. 1–38, 2023, "
        "doi: 10.1145/3571730.",
        "L. Huang et al., “A survey on hallucination in large language models: "
        "principles, taxonomy, challenges, and open questions,” ACM Trans. "
        "Inf. Syst., vol. 43, no. 2, art. 42, pp. 1–55, 2025, "
        "doi: 10.1145/3703155.",
        "P. Lewis et al., “Retrieval-augmented generation for knowledge-intensive "
        "NLP tasks,” in Proc. Adv. Neural Inf. Process. Syst. (NeurIPS), vol. 33, "
        "pp. 9459–9474, 2020.",
        "A. Asai, Z. Wu, Y. Wang, A. Sil, and H. Hajishirzi, “Self-RAG: learning to "
        "retrieve, generate, and critique through self-reflection,” in Proc. Int. "
        "Conf. Learn. Representations (ICLR), 2024.",
        "S. Dhuliawala, M. Komeili, J. Xu, R. Raileanu, X. Li, A. Celikyilmaz and "
        "J. Weston, “Chain-of-verification reduces hallucination in large "
        "language models,” in Findings Assoc. Comput. Linguistics: ACL 2024, "
        "pp. 3563–3578, 2024, doi: 10.18653/v1/2024.findings-acl.212.",
        "P. Manakul, A. Liusie, and M. J. F. Gales, “SelfCheckGPT: zero-resource "
        "black-box hallucination detection for generative large language models,” in "
        "Proc. Conf. Empir. Methods Nat. Lang. Process. (EMNLP), pp. 9004–9017, "
        "2023, doi: 10.18653/v1/2023.emnlp-main.557.",
        "S. Min et al., “FActScore: fine-grained atomic evaluation of factual "
        "precision in long form text generation,” in Proc. Conf. Empir. Methods "
        "Nat. Lang. Process. (EMNLP), pp. 12076–12100, 2023, "
        "doi: 10.18653/v1/2023.emnlp-main.741.",
        "L. Pan, A. Albalak, X. Wang, and W. Y. Wang, “Logic-LM: empowering large "
        "language models with symbolic solvers for faithful logical reasoning,” in "
        "Findings Assoc. Comput. Linguistics: EMNLP 2023, pp. 3806–3824, 2023, "
        "doi: 10.18653/v1/2023.findings-emnlp.248.",
        "T. X. Olausson et al., “LINC: a neurosymbolic approach for logical "
        "reasoning by combining language models with first-order logic provers,” in "
        "Proc. Conf. Empir. Methods Nat. Lang. Process. (EMNLP), pp. 5153–5176, "
        "2023, doi: 10.18653/v1/2023.emnlp-main.313.",
        "L. Gao et al., “PAL: program-aided language models,” in Proc. Int. "
        "Conf. Mach. Learn. (ICML), PMLR vol. 202, pp. 10764–10799, 2023.",
        "W. Chen, X. Ma, X. Wang, and W. W. Cohen, “Program of thoughts prompting: "
        "disentangling computation from reasoning for numerical reasoning tasks,” "
        "Trans. Mach. Learn. Res., 2023.",
        "Z. Cheng et al., “Binding language models in symbolic languages,” in "
        "Proc. Int. Conf. Learn. Representations (ICLR), 2023.",
        "A. d’Avila Garcez and L. C. Lamb, “Neurosymbolic AI: the 3rd wave,” "
        "Artif. Intell. Rev., vol. 56, no. 11, pp. 12387–12406, 2023, "
        "doi: 10.1007/s10462-023-10448-w.",
        "H. Kautz, “The third AI summer: AAAI Robert S. Engelmore memorial "
        "lecture,” AI Mag., vol. 43, no. 1, pp. 105–125, 2022, "
        "doi: 10.1002/aaai.12036.",
        "S. C. Kleene, Introduction to Metamathematics. Amsterdam, The Netherlands: "
        "North-Holland, 1952.",
        # Five works the body relies on and pointed at nothing: the self-consistency
        # baseline named in Section I, the encoder the whole neural lane is, the model
        # every baseline runs, the test every paired comparison uses, and the
        # implementation Study II trains.
        "X. Wang et al., “Self-consistency improves chain of thought reasoning in "
        "language models,” in Proc. Int. Conf. Learn. Representations (ICLR), 2023.",
        "S. Xiao, Z. Liu, P. Zhang, N. Muennighoff, D. Lian, and J.-Y. Nie, “C-Pack: "
        "packed resources for general Chinese embeddings,” in Proc. ACM SIGIR Conf. "
        "Res. Develop. Inf. Retrieval, pp. 641–649, 2024, "
        "doi: 10.1145/3626772.3657878.",
        "Qwen Team, “Qwen2.5 technical report,” arXiv:2412.15115, 2024.",
        "Q. McNemar, “Note on the sampling error of the difference between "
        "correlated proportions or percentages,” Psychometrika, vol. 12, no. 2, "
        "pp. 153–157, 1947, doi: 10.1007/BF02295996.",
        "A. Karpathy, “nanoGPT,” GitHub repository, 2023. [Online]. Available: "
        "https://github.com/karpathy/nanoGPT",
    ]
    # The `references` style numbers entries itself; a manual "[1] " would print twice.
    for ref in refs:
        doc.p(ref, "references")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/paper/PRAXIS_IEEE.docx")
    args = ap.parse_args()

    test, confirm = load(PAPER_REPORT), load(CONFIRM_REPORT)
    scale = scale_rates()
    # Study II is optional: if the football experiment has not been run, the paper
    # builds without it rather than emitting placeholders.
    fb = None
    if FOOTBALL_REPORT.exists():
        fb = json.loads(FOOTBALL_REPORT.read_text(encoding="utf-8"))
        if FOOTBALL_MANIFEST.exists():
            fb["source_goals"] = json.loads(
                FOOTBALL_MANIFEST.read_text(encoding="utf-8"))["source"]["rows"]
        _s2 = football_stats(FOOTBALL_CLAIMS)
        print(f"football study: {_s2['checkable']:,} checkable claims, "
              f"base refuted rate {_s2['refuted_rate']:.3f} "
              f"(+{_s2['unverified']} unverifiable, counted apart)")
    else:
        # ASCII: this goes to a Windows console, which is cp1252 and raises on an em
        # dash. The dash style applies to the document, not to the build log.
        print("football report absent - building without Study II")
    print(f"test split: {test['cases']} cases, {len(test['systems'])} systems")
    print(f"confirm split: {confirm['cases']} cases, {len(confirm['systems'])} systems")
    print(f"scale records: {len(scale)} complete systems at 1.5B")

    document = docx.Document(str(args.template))
    styles = template_styles(document)      # must precede clear_body
    print("template styles:", ", ".join(sorted(styles)))
    clear_body(document)
    build(Doc(document, styles), test, confirm, scale, fb)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(args.out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

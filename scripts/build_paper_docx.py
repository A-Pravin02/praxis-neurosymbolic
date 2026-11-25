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
    return (f"The verifier defines {c['defined_checks']} deterministic checks — among "
            f"them authorization, policy version, symbolic provenance and unsupported "
            f"numbers — one of which, {', '.join(c['never_ran'])}, no test question "
            f"reaches. Which apply depends on what the case asserts, so none runs on "
            f"every question: over the {c['cases']} test cases the number that runs "
            f"ranges from {c['min_per_case']} to {c['max_per_case']}, median "
            f"{c['median_per_case']}, and the {c['zero_check_cases']} cases running "
            f"none are refusals settled before there is anything to verify. An answer "
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
            f"+ {n_of('not_covered')} not-covered; of the answerable, {len(definite)} "
            f"were definite and all correct (selective accuracy 1.000), every "
            f"conditional case drew a correctly hedged answer and every denied case a "
            f"refusal without disclosure, and {nc} not-covered cases were correctly "
            f"refused, so accuracy is ({ans_correct} + {cond} + {den} + {nc}) / "
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
        # Table II's caption split across the column break, so its last clause printed
        # at the head of the right-hand column with nothing to say what it described.
        # A caption is one unit of text and is never worth breaking: it is short, and
        # half a caption is not half as useful but useless.
        para.paragraph_format.keep_together = True
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

    # IEEE tables are ruled horizontally and never vertically: one rule above the
    # headings, one below them, one under the last row, and nothing else. A full grid
    # boxes every cell, which is what Word draws when asked for "Table Grid" and what
    # this code asked for until the style turned out not to exist in the template.
    @staticmethod
    def _border(kind: str, size: int = 8) -> OxmlElement:
        el = OxmlElement(f"w:{kind}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))        # eighths of a point: 8 = 1pt
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        return el

    @staticmethod
    def _no_border(kind: str) -> OxmlElement:
        el = OxmlElement(f"w:{kind}")
        el.set(qn("w:val"), "none")
        el.set(qn("w:sz"), "0")
        el.set(qn("w:space"), "0")
        return el

    def _rule_table(self, t) -> None:
        borders = OxmlElement("w:tblBorders")
        borders.append(self._border("top", 12))        # 1.5pt rule above the headings
        borders.append(self._border("bottom", 12))     # and under the last row
        for kind in ("left", "right", "insideH", "insideV"):
            borders.append(self._no_border(kind))
        t._tbl.tblPr.append(borders)

        # Word indents every cell by 0.08 in on each side. Across seven columns that
        # is 1.1 in of the 3.4 in a column has, which is why "0.796" wrapped as "0.79"
        # and "6" once Table II gained two columns. With no vertical rules the padding
        # separates nothing, so it goes down to a hair either side; the column widths
        # then mean what they say.
        margins = OxmlElement("w:tblCellMar")
        for kind, twips in (("left", 36), ("right", 36), ("top", 0), ("bottom", 0)):
            el = OxmlElement(f"w:{kind}")
            el.set(qn("w:w"), str(twips))              # twentieths of a point
            el.set(qn("w:type"), "dxa")
            margins.append(el)
        t._tbl.tblPr.append(margins)

    def _rule_under_headings(self, t) -> None:
        """The thinner rule that separates the heading row from the body."""
        for cell in t.rows[0].cells:
            tc = cell._tc.get_or_add_tcPr()
            edges = OxmlElement("w:tcBorders")
            edges.append(self._border("bottom", 8))    # 1pt
            tc.append(edges)

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
        # This asked for "Table Grid", which the template does not define - the only
        # table style in it is "Normal Table" - so the KeyError below was swallowed and
        # every table took whatever Word defaulted to. The rules are now drawn here, in
        # the three places IEEE puts them, so the same file renders the same way
        # wherever it is opened.
        self._rule_table(t)
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
        # Drawn after the body rows exist, so the rule lands under the headings rather
        # than under the only row the table had when it was created.
        self._rule_under_headings(t)
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
    # An earlier title, "Reducing Hallucinations in Large Language Models Through a
    # Neuro-Symbolic Reasoning Layer", claimed a result the paper does not have: the
    # configuration it leads with contains no language model at all, so nothing in it
    # reduces a *language model's* hallucination rate, and Study II's model is 4.3M
    # parameters rather than a large one. The title below drops that clause and keeps
    # what the paper measures - a reduction in hallucination, achieved by a
    # neuro-symbolic layer - so it is the author's wording and it is accurate.
    doc.title("PRAXIS: Reducing Hallucinations Through a Neuro-Symbolic "
              "Decision Layer")
    # IEEE conference format sets authors side by side. python-docx cannot lay out
    # the template's multi-column author block directly, so a single-row table gives
    # the same visual result and keeps the template's Author style on each cell.
    # The IEEE template's author block is four lines: name, department, organization,
    # city and country, then email. Department and email are left as explicit markers
    # rather than guessed: inventing either would put a false affiliation and a false
    # address on a published paper. Fill both before submitting.
    # The department line is dropped at the authors' request; IEEE's block is
    # name / affiliation / city, country / email, and the department line is optional.
    # Athiban Karthik's address is still a placeholder because nobody has given it:
    # inventing an address for a named co-author would put a false contact on a
    # published paper, so it stays visible until it is filled in.
    doc.authors([
        "Pravin Anbalagan\nVellore Institute of Technology (VIT)\n"
        "Vellore, India\npravinanbalagan02@gmail.com",
        "Athiban Karthik\nVellore Institute of Technology (VIT)\n"
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
        f"before release, so the decision path contains no language model. "
        f"On a frozen test split of 255 questions over five generated enterprise worlds "
        f"this default configuration answers {num(ns_acc)} correctly at a hallucination "
        f"rate of {num(ns_hal)} with {num(ns_cov)} coverage, against {num(rules_acc)} "
        f"and {num(rules_hal)} for a 7B instruction-tuned model given the same corpus "
        f"and rules in its prompt — a paired gain of {ns_acc - rules_acc:+.3f} accuracy "
        f"(exact McNemar, p = {rules_p:.1g}). Confining the model to semantic parsing "
        f"reaches {num(pa_acc)} accuracy but fails to transfer, falling to "
        f"{num(cpa_acc)} on a confirmatory split of unseen companies while the LLM-free "
        f"configuration holds at {num(cns_acc)}. "
        + (f"A secondary study gates a {fb['model_parameters']/1e6:.1f}M decoder "
           f"trained from scratch: the graph refutes "
           f"{football_stats(FOOTBALL_CLAIMS)['refuted_rate']:.3f} of its claims, and "
           f"gating leaves {football_stats(FOOTBALL_CLAIMS)['coverage']:.3f} "
           f"coverage. " if fb else "")
        + "We report coverage beside every accuracy and document a failure the "
          "aggregate hides: on questions no policy covers the default configuration "
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
          "documents, but grounding is not entailment: a model handed the right clause "
          "can still state a conclusion the clause does not license, fluently enough "
          "that a reader has no signal to distrust it.")
    doc.p(f"The common remedies keep the model as the authority: self-consistency "
          f"sampling [16], self-critique and chain-of-verification all ask it to audit "
          f"its own output. That is a weak defence — asking a 7B model to check its own "
          f"retrieval-grounded draft moved accuracy from {num(rag_acc)} to "
          f"{num(sc_acc)} and hallucination from {num(rag_hal)} to {num(sc_hal)}, far "
          f"smaller than the error it is meant to remove.")
    doc.p("PRAXIS takes the opposite position. The model is a language engine, never a "
          "truth engine: it may interpret a question and phrase a conclusion, but never "
          "establishes one. Between those points sits deterministic, replayable code — "
          "a typed knowledge graph, a three-valued reasoner over versioned rules, and "
          "an evidence gate that withholds any answer failing its checks. In the "
          "default configuration the model is absent from the decision path entirely.")

    doc.h2("Scope and Contributions")
    # Six contributions as six separate paragraphs cost six paragraph gaps and six
    # part-filled last lines. Running prose carries the same content in a third of the
    # vertical space, which is what a six-page limit is bought with.
    #
    # "cannot arise" overstated what the architecture buys. No language model asserts
    # a conclusion here, which removes that mechanism; it does not remove error. The
    # test split holds 25 wrong definite answers, from the encoder's reading of the
    # question and from the rule base itself.
    #
    # Contribution 5 previously also claimed a scale measurement. That comparison ran
    # across two inference stacks, so it was cut from the results and now appears in
    # the limitations as an open question; advertising it here contradicted that.
    contributions = (
        "This paper contributes: (1) an architecture whose decision path contains no "
        "language model, so a decision is never a model’s prediction — the residual "
        "errors come instead from question understanding and from the rule base, 25 "
        "wrong definite answers on the test split; (2) a four-valued verification layer "
        "separating supported, contradicted, unverified and not-checkable, so missing "
        "evidence is never read as refutation, above a three-valued rule engine; (3) a "
        "controlled evaluation on 510 questions across ten generated enterprise worlds, "
        "with a frozen test split and a confirmatory split scored once; (4) seven "
        "ablations isolating verification, three-valued reasoning, policy versioning, "
        "statement trust, authorization, numeric checking and kind agreement; and (5) "
        "an error analysis of a failure the aggregate metrics conceal, in which "
        "questions outside the rule base are answered confidently because verification "
        "establishes support, not relevance.")
    if fb:
        contributions += (
            " A sixth is a second study in the opposite regime, where a language model "
            "trained from random initialization does the generating and the same "
            "symbolic gate decides what is released, quantifying both the hallucination "
            "it removes and the coverage that removal costs.")
    doc.p(contributions)
    doc.p("Every figure in this paper is read from a stored per-question record and "
          "every table generated from those files rather than transcribed.")

    # ---- II. Related Work ----
    doc.h1("Related Work")
    doc.p("Hallucination is surveyed by Ji et al. [1] and Huang et al. [2], who "
          "separate faithfulness from factuality failures; our target is narrower, a "
          "decision stated without entailment from the governing rule. "
          "Retrieval-augmented generation [3] supplies evidence but leaves inference to "
          "the model. Self-RAG [4] learns to retrieve and critique; "
          "chain-of-verification [5] and SelfCheckGPT [6] detect inconsistency by "
          "resampling or self-interrogation. These share a limit: auditor and author "
          "are the same model, so a confident systematic error is invisible to both. "
          "FActScore [7] decomposes text into atomic claims, which informs our "
          "claim-level checks but is an evaluation method, not a release gate.")
    doc.p("Neuro-symbolic approaches delegate inference to an external solver: "
          "Logic-LM [8] and LINC [9] translate to first-order logic for a prover, "
          "PAL [10] and Program-of-Thoughts [11] emit executable programs, Binder [12] "
          "binds language to symbolic languages, and [13], [14] set out the wider "
          "programme. PRAXIS differs in what it formalizes — not a reasoning chain for "
          "a puzzle but the enterprise’s own rule base, versioned over time and "
          "filtered by an authorization view — and in treating verification as a "
          "release gate rather than a post-hoc score.")

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
               "Fig. 1. Architecture as implemented, top to bottom. Box tags match the "
               "subsections: A1-A2 neural understanding (IV-A), B1-B4 symbolic "
               "reasoning (IV-B), C1-C4 the evidence gate (IV-C). The two inputs on "
               "the left select the authorized view and the policy version; the "
               "realizer is optional and wording-only, and the controller re-verifies "
               "anything it writes. The rule engine is three-valued while verification "
               "is four-valued, since a claim may also be not checkable. The dashed "
               "panel is the relevance check, present on the parser path and not the "
               "encoder path — the failure analysed in Section X.",
               width_in=3.30)
    doc.p(f"The neural lane — a 33M encoder by default, or the model acting as "
          f"semantic parser — yields a goal, the records referred to and any stated "
          f"facts, and retrieval gathers the evidence. The symbolic lane resolves the "
          f"policy version in force on the case’s own date, restricts records to those "
          f"the asker may see, and evaluates the rules under three-valued logic. Claims "
          f"are then extracted and checked against the evidence by the applicable "
          f"members of a {checks()['defined_checks']}-check suite, and the release gate "
          f"either publishes the answer or reports insufficient evidence. A model may "
          f"phrase the conclusion but never establishes it.")

    doc.h2("Neural Understanding")
    doc.p("A 33M-parameter bge-small encoder [17], run locally through ONNX, maps the "
          "question to a goal, the records it refers to and any facts it states, "
          "validated against the schema’s own declarations so that an interpretation "
          "naming a goal that does not exist is rejected rather than carried "
          "forward.")

    doc.h2("Three-Valued Symbolic Reasoning")
    doc.p("Rules are evaluated under Kleene strong three-valued logic [15]: a fact the "
          "company has no record of is unknown, not false, where a two-valued engine "
          "answers “no, you are not eligible” when the truthful answer is “nobody "
          "recorded whether you are”. The reasoner resolves the policy version in force "
          "on the case’s own date rather than the newest and reads records through an "
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
        # Two paragraphs into one for the six-page limit. The reviewer asked for a
        # question traced through the pipeline and this still traces one through every
        # stage, including the ablation that reverses it; what went is the restatement
        # of what each box does, which Fig. 1 already shows.
        doc.p(f"A support agent asks: “{case['question']}” A1 resolves this to "
              f"{case['expect']['goal']}({case['expect']['subject']}) with one stated "
              f"fact, and A2 retrieves the returns clauses the agent may see. The two "
              f"inputs then do their work: the asker’s role selects the authorized "
              f"view (B2) and the date of the case selects the policy version (B1). "
              f"That second choice decides the answer — the returns policy was "
              f"rewritten to allow 14 days instead of 8, effective 16 November 2026, "
              f"and this case is dated 14 November, so version 1 governs it. B3 "
              f"evaluates clause RET-4 under that version and B4 establishes "
              f"{case['expect']['goal']}({case['expect']['subject']}) = "
              f"{str(full['decision']).lower()}. No realizer runs, so the conclusion is "
              f"rendered from its own grounds: “{full['text'].splitlines()[0]}” C3 "
              f"traces each of the {full['numbers']} figures stated to a record or a "
              f"clause, so {full['unsupported_numbers']} are unsupported and C4 "
              f"releases it. The ablation applying the newest policy to every case "
              f"reaches the opposite conclusion here — "
              f"“{latest['text'].splitlines()[0]}” — fluently, with a real clause, and "
              f"wrong.")

    # ---- V. Experimental Design ----
    doc.h1("Experimental Design")
    # This was a five-row table of three splits and two totals. Three splits are a
    # sentence; the two totals are sums of them. Dropping it costs a reader nothing
    # and buys a tenth of a page, which at six pages is the trade every time.
    doc.p("The corpus is 663 cases over 13 worlds in three splits that share no case "
          "and no world: 153 development questions over 3 worlds, used only to "
          "calibrate thresholds and never reported; 255 test questions over 5 worlds, "
          "frozen before any system ran, carrying the main results; and 255 "
          "confirmatory questions over 5 further worlds, written after the system was "
          "final and scored once. The 510 test and confirmatory questions are the "
          "evaluation.")
    # "Section XI-C" named a subsection of Study II that no longer exists: compressing
    # that section removed its headings, and a cross-reference to a heading is only as
    # stable as the heading. It points at the limitation that states the authorship.
    doc.p("Cases are generated over synthetic enterprise worlds, each with its own "
          "catalogue, staff, policy history and records, with gold answers from a "
          "reference oracle independent of the system under test — a separate "
          "implementation, not an independent author (Section XII). All systems see "
          "the same questions, corpus and authorization context, and every "
          "language-model system in a run uses the same model.")
    doc.p("Size of the formalization. The rule base declares 13 entity types, 17 goals "
          "and 2 lookups, discharged by 48 rules across 4 files, and every world is "
          "generated against that schema, holding 138 records and 6 policy documents, "
          "one of them in two versions so that a case dated before the change is "
          "governed by the older text. Each split draws 51 questions from each of its "
          "5 worlds. The ten categories, with counts over the 255-case split: "
          "paraphrased 45, calculation 45, known 30, policy_version 25 (governed by a "
          "superseded text), recommendation 25, multi_hop 20, false_premise 20, "
          "missing_info 15, unauthorized 15 and not_covered 15 (no clause governs it).")
    doc.p("Baselines. Every language-model system uses Qwen2.5-7B-Instruct [18] through "
          "one local OpenAI-compatible server, greedy at temperature 0, with a "
          "completion cache keyed by model, messages and token limit, so a re-run "
          "reproduces the scored outputs exactly. Retrieval is the same bge-small "
          "encoder over clause-level passages, filtered by the asker’s authorization "
          "before ranking, top 6 above cosine 0.45; LLM + RAG + rules also places the "
          "governing rule text in the prompt, and Extractive RAG quotes the top passage "
          "with no generator. Every baseline gets the same JSON reply schema with "
          "explicit refusal and hedging options — needs_more_information, "
          "ask_which_record, not_covered, not_permitted — so a baseline that abstains "
          "is following its instructions, not failing to parse. The system, generators, "
          "oracle, frozen splits, per-question records and the scripts building every "
          "table and figure here are at github.com/A-Pravin02/praxis-neurosymbolic, "
          "with the commands and the freezing commit in docs/paper/REPRODUCING.md.")

    doc.h1("Evaluation Metrics")
    doc.p("Denominators differ between metrics, which is why the figures can look "
          "incompatible and are not. Let N be all cases in a split, A the answerable "
          "ones — gold kind a decision, a value or a result — and D the subset of A "
          "answered definitely. Accuracy and hallucination are over N, coverage is "
          "|D|/|A| and selective accuracy is correctness over D, so conditional, "
          "denied and not-covered cases are excluded from coverage but included in "
          "accuracy. " + worked_example() + " Every value here is recomputed from the "
          "stored records twice, once mirroring the harness and once implementing "
          "these definitions from the raw records, so an error shared by harness and "
          "audit would still surface. All 151 reproduce.")
    # The verbal restatement of coverage and selective accuracy that stood here
    # repeated the formulas above and the worked example; what the reader needs is
    # the correctness rule per gold kind and why the two are always paired.
    doc.p("A not-covered case is correct only if the system reports insufficient "
          "evidence, a conditional case only if the answer is hedged, a denied case "
          "only if the system refuses without disclosing the record. Hallucination is "
          "the union of three disjoint faults: a wrong definite answer, a definite "
          "answer where the gold says evidence is insufficient, and disclosure of a "
          "record the asker may not see. Coverage and selective accuracy are always "
          "reported together, because a system can buy high selective accuracy by "
          "refusing hard questions. Intervals are 95% bootstrap percentile intervals "
          "over questions; paired comparisons use the exact McNemar test [19], testing "
          "the discordant pairs two-sided against Binomial(n, 1/2), the exact tail "
          "rather than the chi-square approximation because those counts are small.")

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
          "traffic. They establish neither general-purpose hallucination elimination "
          "nor effectiveness across untested models and domains, and the rates below "
          "are not open-domain factuality rates.")
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
        acc, _ = T(name, "accuracy")
        hal, _ = T(name, "hallucination_rate")
        rows.append([label[name], num(acc), num(hal), num(cov), num(sel), num(over),
                     num(lat, places=0)])
    # Accuracy and hallucination are plotted in Fig. 2 and now tabulated here too. A
    # bar read off an axis is an estimate; a reader checking a claim, or quoting one,
    # needs the figure to three places. The two come from the same report file, so
    # they cannot disagree - scripts/independent_metrics.py recomputes both.
    #
    # Headings abbreviated to what fits a seventh of the width; the caption defines
    # each one, which is where a definition belongs anyway.
    # One column, not spanning. Spanning it cost most of a page: a spanning table
    # opens and closes a section, and Word balances the columns around the break. Seven
    # narrow columns fit a single column at 8pt if the headings are abbreviated, and
    # the caption defines them.
    # The first column was 0.92 in, which broke "LLM+RAG+rules" across two lines as
    # "LLM+RAG+rule" and "s". It is the only column holding words, so it takes the
    # width and the six numeric columns give it up: a figure of five characters at 8pt
    # needs about a third of an inch, and no more.
    doc.table(["System", "Acc.", "Hall.", "Cov.", "Sel.", "Over", "ms"], rows,
              widths=[1.06, 0.37, 0.37, 0.37, 0.37, 0.37, 0.43],
              caption=
              f"TABLE I. Test split, {test['cases']} questions. Acc. and Hall. are "
              f"over every case and are what Fig. 2 plots, to three places. Cov. is "
              f"definite answers over answerable questions, Sel. correctness among "
              f"them, Over the answerable questions refused, ms the mean latency; the "
              f"PRAXIS rows are CPU-only and the model rows wait on a GPU server, so "
              f"that column is not hardware-controlled.")

    doc.figure(FIGURES / "main_results.png",
               "Fig. 2. Main comparison on the test split, both measures over all 255 "
               "cases: accuracy higher is better, hallucination lower is better. "
               "Systems are numbered in the row order of Table I, which gives every "
               "value to three places; error bars are 95% bootstrap intervals and "
               "hatched bars are the proposed system.",
               width_in=3.30)
    # The caveat this paragraph carried is now in the scope paragraph above, which
    # is where a reader meets the numbers; Fig. 2's own caption says what it plots.
    # The headline comparison is against the baseline that receives the same rules.
    # Leading with LLM + RAG, which does not, measures the rules as much as the
    # architecture and flatters this system by about seven accuracy points.
    doc.p(f"RQ1. The fair comparison is against the configuration given everything "
          f"PRAXIS is given: the same corpus, the same retrieval, the governing rule "
          f"text in its prompt. The LLM-free configuration answers "
          f"{num(ns_acc, ns_acc_ci)} correctly at {num(ns_hal, ns_hal_ci)} "
          f"hallucination against that baseline’s {num(rules_acc)} and "
          f"{num(rules_hal)}: a paired gain of {rules_gap:+.3f} accuracy "
          f"[{rules_gap_ci[0]:.3f}, {rules_gap_ci[1]:.3f}], correct where the baseline "
          f"is not on {rules_only_a} questions against {rules_only_b} the other way "
          f"(exact McNemar, p = {rules_p:.1g}). Against retrieval without the rules the "
          f"gap is larger — {num(rag_acc)} and {num(rag_hal)} — but part of it is the "
          f"rules, not the architecture, so it is not the claim we make.")
    doc.p(f"RQ2. The reduction is not bought with abstention: PRAXIS covers "
          f"{num(ns_cov)} of answerable questions against "
          f"{num(T('llm_rag_rules','coverage')[0])} for the rules-in-prompt baseline "
          f"and {num(T('llm_rag','coverage')[0])} for retrieval alone, answering more "
          f"questions and more of them correctly. Extractive RAG is the opposite case, "
          f"kept as the reference point: quoting a retrieved passage gives "
          f"{num(T('rag_extractive','accuracy')[0])} accuracy at "
          f"{num(T('rag_extractive','hallucination_rate')[0])} hallucination and "
          f"{num(T('rag_extractive','coverage')[0])} coverage, because it never commits "
          f"to a decision — the low hallucination rate is silence, not reliability, "
          f"which is the reading the coverage column exists to prevent. Confining the "
          f"model to semantic parsing, with the decision still symbolic, gives the "
          f"strongest configuration: {num(pa_acc, pa_acc_ci)} accuracy at "
          f"{num(pa_hal, pa_hal_ci)} hallucination, {num(pa_cov)} coverage and "
          f"{num(pa_sel)} selective accuracy — of the questions it commits to, none is "
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
              "TABLE II. Decision-level ablations on the test split, 255 questions, "
              "each row disabling one mechanism. Disclosure is the share of the 15 "
              "unauthorized-category questions revealing a record the asker may not "
              "see; the last column is the exact McNemar p-value for correctness "
              "against the full system, with its discordant count. The gate acts on "
              "wording, so Section "
              # Not kept together: pushing all eight rows onto the next page left a
              # third of this one blank, and the heading row now repeats if it breaks.
              "VIII-A tests it instead.")

    # "broadly intact" was wrong by its own table: authorization costs 0.059 accuracy,
    # more than the 0.051 the same paragraph calls real weight. The disclosure point
    # stands on its own and does not need the accuracy claim to be soft.
    doc.p("RQ3. Three-valued reasoning carries real weight: "
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
          "while doing it; an accuracy-only evaluation would miss the second.")

    # Two rows of this table still read as refutations at a glance, and the columns
    # that answer them are now in it. The prose says what the columns mean.
    ka_acc = T("ns_no_kind_agreement", "accuracy")[0]
    (ka_p, ka_n), (ts_p, _) = _sig("ns_no_kind_agreement"), _sig("ns_trust_statements")
    ts_hal = T("ns_trust_statements", "hallucination_rate")[0]
    ts_cov = T("ns_trust_statements", "coverage")[0]
    doc.p(f"Two rows need a second column before they read as refutations. Removing "
          f"the kind-agreement check raises accuracy to {num(ka_acc)}, nominally above "
          f"the full system, but the paired test puts the difference at "
          f"p = {ka_p:.3g} on {ka_n} discordant cases: noise. What the check buys is "
          f"{abs(ns_hal - T('ns_no_kind_agreement','hallucination_rate')[0]):.3f} "
          f"hallucination for "
          f"{abs(ns_cov - T('ns_no_kind_agreement','coverage')[0]):.3f} coverage. "
          f"Letting the asker’s statements override records lowers hallucination to "
          f"{num(ts_hal)}, well below the full system, while coverage falls from "
          f"{num(ns_cov)} to {num(ts_cov)} and accuracy drops significantly "
          f"(p = {ts_p:.3g}) — this paper’s own warning turned on itself, since that "
          f"configuration abstains its way to the better rate. Removing the numeric "
          f"grounding check changes nothing, for the same reason the gate does not: "
          f"both police prose, and the default configuration writes none.")

    doc.h2("A Controlled Test of the Verification Gate")
    # Condensed for the six-page limit. The decision-level/text-level distinction was
    # stated three times across five paragraphs; it is stated once here. Every number
    # survives: the divergence count, both unsupported counts, the discordant pairs and
    # the exact McNemar p that stops us claiming the difference.
    doc.p("Two measurements are at stake, and separating them resolves an apparent "
          "contradiction. The ablation rows above are decision-level: they grade "
          "structured outcomes and cannot see the gate at all, since the default "
          "configuration renders conclusions deterministically and the gate acts only "
          "on prose. The experiment here is text-level. It holds everything else "
          "fixed — the same 255 questions, model, retrieval and prompts, with the "
          "model writing the final wording in both arms and verification on in one and "
          "off in the other. Reaching the switch proved harder than expected, and the "
          "difficulty is itself a result: the guard is layered across the realizer, the "
          "controller’s re-verification and the release check, and a switch "
          "reaching only the last produced byte-identical arms on all 255 cases, "
          "because each remaining layer repaired what the ablated one let through. A "
          "regression test now asserts that it reaches all three.")
    doc.p(f"With the switch complete the arms diverge: the released answer differs on "
          f"{gate['differ']} of {gate['cases']} cases. Answers stating a figure the "
          f"evidence does not support rise from {gate['gated_unsupported']} to "
          f"{gate['ablated_unsupported']}, and that difference is not established "
          f"— the arms differ on only {gate['discordant']} questions in this "
          f"respect, an exact McNemar p of {gate['p_value']:.2g} — so we do not "
          f"claim the count as a measured effect. What the experiment shows is "
          f"qualitative and specific: on one case the gated system answers with the "
          f"recommended resolution and its grounds, while the ablated system abandons "
          f"the conclusion for generic boilerplate carrying a figure found nowhere in "
          f"the evidence; establishing the rate needs a larger prose-bearing split. "
          f"Every decision-level metric is meanwhile identical in both arms, which "
          f"locates what the benchmark can see: the scorer grades the structured "
          f"decision, fixed by the symbolic layer before any model writes a word. The "
          f"gate protects the prose, which this scorer does not grade; Study II "
          f"supplies the claim-level instrument.")
    # Removed for the six-page limit: a paragraph explaining that two pairs of numbers
    # in this paper are coincidences rather than transcription errors - the gate's
    # changed-wording count against the full system's error count, and two mean
    # latencies a millisecond apart. Both were recounted from the records and both
    # hold, but the explanation answers a reader's suspicion rather than stating a
    # result, which makes it a line for a response to reviewers rather than for the
    # paper. The counts and the two distributions are in docs/audit/, and
    # scripts/independent_metrics.py recomputes every one of them.

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
              f"TABLE III. Confirmatory split, {confirm['cases']} questions over five "
              f"unseen companies, written after the system was final and scored once. "
              f"Brackets are 95% bootstrap intervals. Fewer systems ran here: the "
              f"confirmatory run was scoped to the comparisons the paper’s claims rest "
              f"on.", keep_together=True)
    doc.p(f"RQ4. The LLM-free configuration transfers essentially unchanged "
          f"({num(cns_acc)} against {num(ns_acc)} on test). The parser configuration "
          f"does not: accuracy falls from {num(pa_acc)} to {num(cpa_acc)}, "
          f"hallucination rises from {num(pa_hal)} to {num(cpa_hal)} and selective "
          f"accuracy falls from {num(pa_sel)} to {num(cpa_sel)}. The paired tests "
          "narrow with it: the parser is correct where embedding understanding is not "
          "on 40 questions against 2 on the test split (exact McNemar, p = 4e-10) but "
          "7 against 1 on the confirmatory split (p = 0.07). We therefore report the "
          "parser’s advantage as demonstrated on the test split and not established on "
          "unseen worlds.")

    # ---- X. Error Analysis ----
    doc.h1("Error Analysis")
    path = SCALE_DIR / "test.neurosymbolic.jsonl"
    if path.exists():
        cats = category_rates(path)
        # A ten-row table of per-category accuracy, dropped for the six-page limit.
        # The paragraph below already names every category with the errors it
        # contributes, and the category sizes are in Section V, so the table's third
        # column was the only thing it added - recoverable by division, and indicative
        # at best on 15 to 45 cases. The weakest categories are named here instead.
        weakest = ", ".join(f"{c} {a:.3f} on n = {cnt}" for c, cnt, a in cats[:3])
        doc.p(f"Accuracy is uneven across the ten categories (sizes in Section V); "
              f"the weakest three are {weakest}.")
    # Previously this section discussed not_covered and nothing else, which is 15 of
    # 52 errors. Naming where the other 37 come from changes what the reader takes the
    # system's weakness to be: the single largest source is the encoder's reading of a
    # rephrased question, not the rule base's coverage.
    rows_ns = per_case("neurosymbolic")
    from collections import Counter
    err = Counter(r.get("category") for r in rows_ns.values() if not r.get("correct"))
    top = ", ".join(f"{c} {k}" for c, k in
                    ((err[k], k) for k in [k for k, _ in err.most_common()]))
    doc.p(f"The {sum(err.values())} errors are not one failure mode. By category: "
          f"{top}. Two thirds fall outside the failure analysed below. The largest "
          f"source, {err['paraphrased']} of "
          f"{sum(1 for r in rows_ns.values() if r.get('category') == 'paraphrased')} "
          f"paraphrased questions, is the encoder: the question is in scope but its "
          f"wording moves far enough from the goal’s own phrasing that "
          f"nearest-neighbour selection lands on a different goal. The "
          f"{err['recommendation']} recommendation errors are the rule base instead — "
          f"choosing among six remedies needs more of the policy than the rules "
          f"encode, and the system commits to the wrong one rather than abstaining. "
          f"Both are addressable without new architecture; the third is not.")
    # Three paragraphs here stated the mechanism, then its consequence, then the
    # remedy, each opening by restating the one before. One paragraph, same content.
    doc.p("The aggregate also conceals a complete failure. On the fifteen questions no "
          "policy clause covers the system abstained in none: thirteen confident "
          "answers and two clarifications. Asked the warranty period on a product, it "
          "answered that a replacement was not available. Understanding maps an "
          "out-of-scope question onto the nearest in-scope goal and the system answers "
          "that question correctly, citing real clauses and records, so the gate cannot "
          "intervene — every check passes and no unsupported number appears in any of "
          "the fifteen. Verification establishes that a claim follows from evidence, "
          "not that it answers the question asked. An out-of-scope question is not "
          "unsupportable but answerable about something else, and the grounding makes "
          "the irrelevant answer more convincing rather than less. The remedy is a "
          "relevance check independent of the gate, which this configuration cannot "
          "express, since nearest-neighbour selection always returns a goal.")

    # ---- Study II: the from-scratch model ----
    if fb:
        corpus = fb["corpus"]
        # Recomputed from claims.jsonl rather than read from report.json, which
        # counts unverified claims as false. See football_stats.
        s2 = football_stats(FOOTBALL_CLAIMS)
        params = fb["model_parameters"]
        tokens = corpus["tokens"]["train"]

        doc.h1("Study II: Gating a Model Trained from Scratch")
        # Compressed for the six-page limit. The four subsection headings are gone and
        # the training narrative is one paragraph, but nothing a reader needs to judge
        # the result was cut: the sizing that bounds every claim, the four-valued
        # verdicts, the extraction rate, the per-type intervals, and the held-out
        # comparison with its sample sizes, interval and test all remain. What went is
        # restatement - a table that repeated four numbers Table V already carries, and
        # prose that re-described the counts beside it.
        doc.p(f"The first study removes the language model from the decision; this one "
              f"puts it back in the hardest form — a model whose knowledge comes wholly "
              f"from a corpus we control, generating freely, with the symbolic layer "
              f"deciding what reaches the reader. It tests the gate, not the generator. "
              f"The source is {fb.get('source_goals', 3637)} FIFA World Cup goals from "
              f"1930 to 2022 over 1,162 matches. One dataset serves three roles, which "
              f"keeps the comparison honest: the rows are verbalised into a training "
              f"corpus, loaded as a typed knowledge graph, and used to generate "
              f"evaluation prompts, so generator and verifier are grounded in the same "
              f"facts. The split is by match rather than by row, 137 withheld entire, "
              f"and leakage is checked on (fixture, tournament) pairs rather than "
              f"names, since fixtures recur; no evaluated fixture appears in "
              f"training.")
        # This paragraph said the model "memorises the corpus" while the held-out
        # comparison below said it "has not reliably memorised" it. The records support
        # the second: the refuted rate on matches it trained on is 0.458, so most of
        # what it says about its own training data is still wrong. What it does do is
        # overfit, which is not the same thing as retaining the facts.
        doc.p(f"The model is nanoGPT [20], unmodified, at {params/1e6:.1f}M parameters "
              f"(4 layers, 4 heads, width 256, vocabulary {corpus['vocab_size']}), "
              f"trained from random initialization on one consumer GPU; initial loss "
              f"8.454 against ln(4096) = 8.318, minimum-validation checkpoint "
              f"{fb['best_val_loss']:.4f}. The sizing bounds every claim about it: "
              f"{tokens:,} tokens against {params/1e6:.1f}M parameters is "
              f"{tokens/params:.3f} tokens per parameter, two orders of magnitude below "
              f"compute-optimal. It overfits without thereby retaining the facts, no "
              f"configuration avoids that at this scale, and a stronger generator would "
              f"make the test easier rather than more informative.")
        doc.p(f"Generated text is split into sentences, each matched against the "
              f"corpus’s phrasings to yield a typed claim: a goal event, a player’s "
              f"tournament total, or a match total. Rule checks need no lookup — a goal "
              f"in the first half at minute 68 is impossible whatever the records say — "
              f"while graph checks ask whether the goal is on record. Verdicts are "
              f"four-valued: a claim about a player or match the records do not contain "
              f"is UNVERIFIED rather than CONTRADICTED, since the graph is incomplete "
              f"and absence is not refutation. Extraction covers "
              f"{s2['extraction']*100:.1f}% of {s2['sentences']:,} generated sentences, "
              f"{s2['unparseable']} being unparseable; the rest are reported as not "
              f"checkable rather than guessed at. Of the typed claims "
              f"{s2['supported']:,} were supported, {s2['refuted']:,} refuted and "
              f"{s2['unverified']} unverifiable; the "
              f"{fb.get('truncated_fragments_discarded',0)} fragments severed by the "
              f"fixed {fb['sampling']['max_new_tokens']}-token budget are discarded "
              f"rather than counted against the model, one per generation.")

        rows = [["SLM only",
                 f"{s2['checkable']:,}", f"{s2['refuted']:,}", f"{s2['unverified']:,}",
                 num(s2["refuted_rate"]), num(1.0)],
                ["SLM + gate",
                 f"{s2['supported']:,}", "0", "0",
                 num(0.0), num(s2["coverage"])]]
        # Not spanning. A spanning table opens a section, and Word reserved the whole
        # foot of the preceding column for it - 113 points of white space above this
        # table alone. Six columns fit one column once the two row labels are short.
        doc.table(["System", "Released", "Refut.", "Unver.", "Ref. rate", "Cov."],
                  rows, widths=[0.78, 0.52, 0.46, 0.46, 0.60, 0.46], caption=
                  f"TABLE IV. {fb['prompts']} prompts, {s2['checkable']:,} checkable "
                  f"claims. SLM only releases every claim; SLM + gate releases only "
                  f"what the graph confirms. Both read the same generations, so the "
                  f"gate changes only what is released. Refuted and unverifiable are "
                  f"kept apart because the graph is incomplete: a claim it has no "
                  f"record of is not thereby false. The gate withholds both.")

        # Fig. 3 was a two-bar chart carrying four numbers; the table that replaced it
        # restated the two rates and the coverage figure that Table V already gives in
        # its own columns, so it went when the page budget tightened. The two numbers
        # that are not in Table V - the withheld share and its split into refuted and
        # unverifiable - are in the paragraph below.
        if s2["kinds"]:
            # This was a four-column table of three rows. A table earns its space when
            # a reader scans or compares down a column; three rows of one rate each
            # read as well in a sentence and cost a third of the vertical space, which
            # is what the page limit buys with. Every number it held is here, intervals
            # included.
            label = {"goal": "goal event",
                     "player_tournament_total": "player tournament total",
                     "match_total": "match total"}
            parts = [f"{label.get(k, k)} {r:.3f} [{lo:.3f}, {hi:.3f}] (n = {cnt})"
                     for k, cnt, r, u, (lo, hi) in s2["kinds"]]
            doc.p("The gate discriminates by claim type rather than rubber-stamping. "
                  "Refuted-claim rates with 95% bootstrap intervals, unverifiable "
                  "claims counted apart: " + "; ".join(parts) + ". The match-total "
                  "rate rests on the fewest claims, hence its wider interval.")

        # Cut for the six-page limit to what only this section says. The three-way
        # distinction it opened with - measured comparison, trade-off, and the
        # verifier's own correctness - is made again in the conclusion, and the
        # tautology of the gated rate is in the Study II limitation; repeating either
        # here cost eleven lines. What stays is the pair of numbers that carry the
        # result and the evidence that the checks are substantive.
        doc.p(f"The gated rate of 0.000 is near-tautological and is not the finding, "
              f"since the gate releases only what the graph confirms. The informative "
              f"quantities are the base refuted rate of {num(s2['refuted_rate'])} and "
              f"the {num(s2['withheld'])} of checkable claims withholding costs — the "
              f"refuted ones plus a further {num(s2['unverified_rate'])} the graph "
              f"cannot confirm either way. Verification buys soundness with silence, "
              f"and pays for part of that silence with claims that may well be true. "
              f"The checks are substantive nonetheless: "
              f"{NUMWORD.get(s2['rule_only'], s2['rule_only'])} claims were refuted by "
              f"rule alone, with no database consulted.")

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
                  f"of {ho['refuted'] - se['refuted']:+.3f} [{lo:.3f}, {hi:.3f}], "
                  f"Fisher exact p = {s2['transfer_p']:.2f}. The interval excludes any "
                  f"difference larger than about nine points either way, so the model "
                  f"confabulates about its own training data about as readily as about "
                  f"events it never saw: at {tokens/params:.3f} tokens per parameter it "
                  f"has not reliably memorised the corpus. This study therefore "
                  f"measures a generator unreliable everywhere, not one failing only "
                  f"outside its training distribution.")

    # ---- XI. Limitations ----
    doc.h1("Limitations and Threats to Validity")
    # Seven bullets became four. Each bullet carries a paragraph gap, and three of the
    # seven were a sentence apiece; nothing is dropped, but what belongs together is
    # now together.
    doc.bullet("Scope. Results come from generated worlds with oracle gold answers, "
               "not real customer traffic, in a single domain whose governing rules can "
               "be written down; English only, one item per order. Both studies verify "
               "claims against a curated knowledge base, so neither speaks to "
               "open-domain factuality, where no such base exists to consult.")
    doc.bullet("Transfer and authorship. Perfect selective accuracy does not "
               "generalize: the 1.000 figure on the test split falls on the "
               "confirmatory split. The rule base, world generators and oracle were "
               "written by one person and the rules hand-translated from policy text; "
               "an independently authored evaluation, and automatic extraction of "
               "candidate rules for human review, are the next steps.")
    doc.bullet(f"The gate’s contribution is shown at the level of released text and is "
               f"not quantified: the rise in unsupported figures in Section VIII-A "
               f"rests on {gate['discordant']} discordant cases and is not significant "
               f"(p = {gate['p_value']:.2g}). Quantifying it needs a prose-faithfulness "
               f"metric and a larger prose-bearing split. Model capability is a "
               f"separate open question: a 1.5B parser scored well below a 7B one while "
               f"the retrieval baselines barely moved, but the two runs used different "
               f"inference stacks.")
    if fb:
        _s2 = football_stats(FOOTBALL_CLAIMS)
        doc.bullet("Study II. The gated rate of zero refuted claims is true by "
                   "construction: verification is definitionally correct with respect "
                   "to the graph, so the gate’s precision and recall cannot be measured "
                   "against independent ground truth and a graph error would propagate "
                   "silently. Its model is a deliberately weak generator of checkable "
                   "claims, not a usable assistant, and an extractor that silently "
                   f"fails on the {100-_s2['extraction']*100:.1f}% of sentences it does "
                   "not cover is indistinguishable from a model that said nothing "
                   "checkable.")

    doc.h1("Conclusion and Future Work")
    # Condensed for the six-page limit. Three paragraphs here restated the Results,
    # Limitations and Study II sections in turn; a conclusion that re-argues the paper
    # is the cheapest page to buy back. What stays is the headline with the comparison
    # it is actually made against, the three findings that temper it, and the future
    # work - each once.
    doc.p(f"Removing the language model from the decision path, rather than asking it "
          f"to audit itself, reduced hallucination from {num(rules_hal)} to "
          f"{num(ns_hal)} against a model given the same corpus and rules, and from "
          f"{num(rag_hal)} against retrieval alone, while raising coverage from "
          f"{num(T('llm_rag_rules','coverage')[0])} to {num(ns_cov)} — not, therefore, "
          f"an artefact of refusing difficult questions.")
    doc.p(f"Three findings temper this. The comparison that matters is against a model "
          f"given the same rules, where the gain is {rules_gap:+.3f} accuracy "
          f"[{rules_gap_ci[0]:.3f}, {rules_gap_ci[1]:.3f}], not the larger gap against "
          f"retrieval alone. The parser configuration’s selective accuracy of "
          f"{num(pa_sel)} falls to {num(cpa_sel)} on unseen worlds and its advantage "
          f"over embedding understanding is not established there (p = 0.07), so no "
          f"claim of perfect reliability survives the confirmatory split. And the "
          f"LLM-free configuration does not recognise questions its rule base does not "
          f"cover — 0 abstentions on 15 — a failure verification is "
          f"structurally unable to catch, because relevance is not support.")
    doc.p("Future work: a relevance gate for the LLM-free configuration, which needs "
          "goal selection to have a null option it lacks; an independently authored "
          "evaluation; and raising Study II’s coverage without reintroducing "
          "unsupported claims.")

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
        "ACM Comput. Surv., vol. 55, no. 12, pp. 1–38, 2023.",
        "L. Huang et al., “A survey on hallucination in large language models: "
        "principles, taxonomy, challenges, and open questions,” ACM Trans. Inf. "
        "Syst., vol. 43, no. 2, art. 42, pp. 1–55, 2025.",
        "P. Lewis et al., “Retrieval-augmented generation for knowledge-intensive "
        "NLP tasks,” in Proc. Adv. Neural Inf. Process. Syst. (NeurIPS), vol. 33, "
        "pp. 9459–9474, 2020.",
        "A. Asai et al., “Self-RAG: learning to retrieve, generate, and critique "
        "through self-reflection,” in Proc. Int. Conf. Learn. Representations "
        "(ICLR), 2024.",
        "S. Dhuliawala et al., “Chain-of-verification reduces hallucination in "
        "large language models,” in Findings Assoc. Comput. Linguistics: ACL 2024, "
        "pp. 3563–3578, 2024.",
        "P. Manakul et al., “SelfCheckGPT: zero-resource black-box hallucination "
        "detection for generative large language models,” in Proc. Conf. Empir. "
        "Methods Nat. Lang. Process. (EMNLP), pp. 9004–9017, 2023.",
        "S. Min et al., “FActScore: fine-grained atomic evaluation of factual "
        "precision in long form text generation,” in Proc. Conf. Empir. Methods "
        "Nat. Lang. Process. (EMNLP), pp. 12076–12100, 2023.",
        "L. Pan et al., “Logic-LM: empowering large language models with symbolic "
        "solvers for faithful logical reasoning,” in Findings Assoc. Comput. "
        "Linguistics: EMNLP 2023, pp. 3806–3824, 2023.",
        "T. X. Olausson et al., “LINC: a neurosymbolic approach for logical "
        "reasoning by combining language models with first-order logic provers,” in "
        "Proc. Conf. Empir. Methods Nat. Lang. Process. (EMNLP), pp. 5153–5176, "
        "2023.",
        "L. Gao et al., “PAL: program-aided language models,” in Proc. Int. Conf. "
        "Mach. Learn. (ICML), PMLR vol. 202, pp. 10764–10799, 2023.",
        "W. Chen et al., “Program of thoughts prompting: disentangling computation "
        "from reasoning for numerical reasoning tasks,” Trans. Mach. Learn. Res., "
        "2023.",
        "Z. Cheng et al., “Binding language models in symbolic languages,” in Proc. "
        "Int. Conf. Learn. Representations (ICLR), 2023.",
        "A. d’Avila Garcez and L. C. Lamb, “Neurosymbolic AI: the 3rd wave,” Artif. "
        "Intell. Rev., vol. 56, no. 11, pp. 12387–12406, 2023.",
        "H. Kautz, “The third AI summer: AAAI Robert S. Engelmore memorial "
        "lecture,” AI Mag., vol. 43, no. 1, pp. 105–125, 2022.",
        "S. C. Kleene, Introduction to Metamathematics. Amsterdam, The Netherlands: "
        "North-Holland, 1952.",
        "X. Wang et al., “Self-consistency improves chain of thought reasoning in "
        "language models,” in Proc. Int. Conf. Learn. Representations (ICLR), 2023.",
        "S. Xiao et al., “C-Pack: packed resources for general Chinese embeddings,” "
        "in Proc. ACM SIGIR Conf. Res. Develop. Inf. Retrieval, pp. 641–649, 2024.",
        "Qwen Team, “Qwen2.5 technical report,” arXiv:2412.15115, 2024.",
        "Q. McNemar, “Note on the sampling error of the difference between "
        "correlated proportions or percentages,” Psychometrika, vol. 12, no. 2, pp. "
        "153–157, 1947.",
        "A. Karpathy, “nanoGPT,” 2023. [Online]. Available: "
        "github.com/karpathy/nanoGPT",
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

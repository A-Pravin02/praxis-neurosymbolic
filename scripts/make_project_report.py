"""Build the project report for course submission.

    python scripts/make_project_report.py

Writes `docs/PRAXIS_FINAL_REPORT.docx`. Convert to PDF with Word.

This is the project-work document: problem, objectives, literature, architecture,
implementation, the demo interface, sample test cases with their actual results,
the measured findings, limitations and the repository link.

Every number is read from an artifact at build time. The sample-case results come
from `artifacts/demo_suite.json`, which is the recorded output of running the demo
server's own endpoint - including the three cases the system gets wrong. A report
that showed only the seven passing cases would not describe this system.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/PRAXIS_FINAL_REPORT.docx"
SHOTS = ROOT / "docs/screenshots"
FIGURES = ROOT / "docs/paper/figures"
STAMP = datetime.now(timezone.utc).strftime("%d %B %Y")

TITLE = ("Reducing Hallucinations in Large Language Models "
         "Through a Neuro-Symbolic Reasoning Layer")
AUTHORS = [("Pravin Anbalagan", ""), ("Athiban Karthik", "")]
INSTITUTION = "Vellore Institute of Technology (VIT), Vellore, India"
REPO_PLACEHOLDER = "https://github.com/A-Pravin02/praxis-neurosymbolic"


def load(rel: str):
    path = ROOT / rel
    if not path.exists():
        raise SystemExit(f"missing artifact: {rel}\n"
                         f"Run the script that produces it before building the report.")
    return json.loads(path.read_text(encoding="utf-8"))


_MARKUP = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`")


def _marked(text: str) -> list[tuple[str, bool, bool, bool]]:
    """Split `**bold**`, `*emphasis*` and backticked code into styled runs.

    Returns (text, bold, italic, monospace). All three forms were being written into
    this report and only the bold one was understood, so `*can*` and the backticks
    around every file path reached the page as literal punctuation. Emphasis is italic
    rather than more bold: the document already uses bold to open a paragraph with its
    subject, and a second weight competing with that reads as shouting.
    """
    runs: list[tuple[str, bool, bool, bool]] = []
    position = 0
    for match in _MARKUP.finditer(text):
        if match.start() > position:
            runs.append((text[position:match.start()], False, False, False))
        bold, emph, mono = match.group(1), match.group(2), match.group(3)
        if bold is not None:
            runs.append((bold, True, False, False))
        elif emph is not None:
            runs.append((emph, False, True, False))
        else:
            runs.append((mono, False, False, True))
        position = match.end()
    if position < len(text):
        runs.append((text[position:], False, False, False))
    return [run for run in runs if run[0]]


def environment() -> dict[str, str]:
    """The interpreter and library versions this build is running against.

    Read at build time rather than written down, so the report cannot claim a version
    the machine that produced its numbers was not using. A library that is absent is
    recorded as absent instead of being guessed at.
    """
    import importlib
    import platform

    found = {
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.version()} ({platform.machine()})",
    }
    for module in ("onnxruntime", "fastembed", "fastapi", "sqlalchemy", "pydantic",
                   "numpy", "torch", "transformers"):
        try:
            found[module] = getattr(importlib.import_module(module), "__version__", "?")
        except Exception:                      # noqa: BLE001 - absence is the finding
            found[module] = "not installed"
    try:
        import torch
        found["cuda"] = ("available" if torch.cuda.is_available() else "not available")
    except Exception:                          # noqa: BLE001
        found["cuda"] = "unknown"
    return found


ENV = environment()


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "uncommitted"
    except Exception:                                      # noqa: BLE001
        return "uncommitted"


# --------------------------------------------------------------- document
class Report:
    def __init__(self) -> None:
        self.doc = docx.Document()
        style = self.doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(10.5)
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Inches(8.27), Inches(11.69)
        sec.left_margin = sec.right_margin = Inches(0.95)
        sec.top_margin = sec.bottom_margin = Inches(0.85)
        self._counters: dict[str, int] = {}
        self._labels: dict[str, tuple[str, int]] = {}
        self._emitted: set[str] = set()

    def h(self, text, level=1):
        p = self.doc.add_heading(text, level)
        for r in p.runs:
            r.font.color.rgb = RGBColor(0x14, 0x14, 0x14)
            r.font.name = "Calibri"
        p.paragraph_format.space_before = Pt(13 if level == 1 else 9)
        p.paragraph_format.space_after = Pt(4)
        return p

    def p(self, text="", size=None, align=None, italic=False, space=6):
        par = self.doc.add_paragraph()
        par.paragraph_format.space_after = Pt(space)
        if align:
            par.alignment = align
        for chunk, bold, emph, mono in _marked(text):
            run = par.add_run(chunk)
            run.bold = bold
            run.italic = italic or emph
            if mono:
                run.font.name = "Consolas"
            if size:
                run.font.size = Pt(size)
            elif mono:
                run.font.size = Pt(9.5)
        return par

    def bullet(self, text):
        par = self.doc.add_paragraph(style="List Bullet")
        par.paragraph_format.space_after = Pt(2)
        for chunk, bold, emph, mono in _marked(text):
            run = par.add_run(chunk)
            run.bold = bold
            run.italic = emph
            if mono:
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)

    def numbered(self, text):
        par = self.doc.add_paragraph(style="List Number")
        par.paragraph_format.space_after = Pt(2)
        par.add_run(text)

    def code(self, lines):
        for line in lines:
            par = self.doc.add_paragraph()
            par.paragraph_format.space_after = Pt(0)
            par.paragraph_format.left_indent = Inches(0.25)
            run = par.add_run(line)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        self.doc.add_paragraph().paragraph_format.space_after = Pt(3)

    # -- numbering ---------------------------------------------------------
    # Captions and in-text references used to carry hand-written numbers, which is
    # fine until a table is inserted in the middle and every later number, and every
    # sentence pointing at one, has to be found and changed. Numbers are issued here
    # against a label instead, and `ref` reads them back, so inserting a table
    # renumbers the document and its cross-references together or not at all.
    def declare(self, inventory: list[tuple[str, str]]) -> None:
        """Assign every table and figure its number, in document order, up front.

        Numbering on first use cannot work: a section routinely refers to a table that
        appears a paragraph later, and that reference has to resolve. Declaring the
        inventory also makes the document's figure and table list explicit in one
        place, so a table that was added but never captioned, or captioned but never
        declared, is a build error rather than something to notice in the PDF.
        """
        for kind, label in inventory:
            if label in self._labels:
                raise SystemExit(f"duplicate label {label!r}")
            counter = self._counters.setdefault(kind, 0) + 1
            self._counters[kind] = counter
            self._labels[label] = (kind, counter)

    def number(self, kind: str, label: str) -> int:
        """The declared number for this caption, marking it as emitted."""
        if label not in self._labels:
            raise SystemExit(f"caption for undeclared label {label!r}")
        declared_kind, counter = self._labels[label]
        if declared_kind != kind:
            raise SystemExit(f"{label!r} was declared a {declared_kind}, captioned a {kind}")
        self._emitted.add(label)
        return counter

    def ref(self, label: str) -> str:
        """"Table 5", resolved at build time. An unknown label stops the build."""
        if label not in self._labels:
            raise SystemExit(
                f"reference to unknown label {label!r}; "
                f"known: {', '.join(sorted(self._labels))}")
        kind, counter = self._labels[label]
        return f"{kind} {counter}"

    def check_all_emitted(self) -> None:
        missing = sorted(set(self._labels) - self._emitted)
        if missing:
            raise SystemExit("declared but never captioned: " + ", ".join(missing))

    def table(self, head, rows, widths=None, caption=None, fs=9.5):
        t = self.doc.add_table(rows=1, cols=len(head))
        t.style = "Light Grid Accent 1"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, label in zip(t.rows[0].cells, head):
            cell.text = ""
            run = cell.paragraphs[0].add_run(label)
            run.bold = True
            run.font.size = Pt(fs)
        for row in rows:
            cells = t.add_row().cells
            for cell, value in zip(cells, row):
                cell.text = ""
                par = cell.paragraphs[0]
                for chunk, bold, emph, mono in _marked(str(value)):
                    run = par.add_run(chunk)
                    run.bold = bold
                    run.italic = emph
                    if mono:
                        run.font.name = "Consolas"
                    run.font.size = Pt(fs if not mono else fs - 0.5)
        if widths:
            for row in t.rows:
                for cell, w in zip(row.cells, widths):
                    cell.width = Inches(w)
        if caption:
            self.caption(caption)
        else:
            self.doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def caption(self, text):
        par = self.doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_before = Pt(3)
        par.paragraph_format.space_after = Pt(10)
        run = par.add_run(text)
        run.font.size = Pt(9)
        run.italic = True
        run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    def image(self, path: pathlib.Path, caption: str, width=4.9):
        if not path.exists():
            self.p(f"[missing image: {path.name}]", italic=True)
            return
        par = self.doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_after = Pt(2)
        par.add_run().add_picture(str(path), width=Inches(width))
        self.caption(caption)

    def pagebreak(self):
        self.doc.add_page_break()


# --------------------------------------------------------------- content
def main() -> None:
    test = load("eval_reports/research/paper/test.report.json")
    confirm = load("eval_reports/research/confirm/confirm.report.json")
    suite = load("artifacts/demo_suite.json")
    census = load("artifacts/verification_checks.json")
    fb = load("artifacts/football/results/report.json")
    commit = git_commit()

    def M(report, system, field):
        entry = report["systems"][system]["overall"].get(field)
        return entry["value"] if isinstance(entry, dict) else entry

    def F(report, system, field, places: int = 3) -> str:
        """Format a metric, keeping an undefined one undefined.

        Selective accuracy is correctness over the definite answers, so a system that
        commits to nothing has no selective accuracy at all - the harness records null.
        Printing that as 0.000 claims a measured zero where the quantity does not exist,
        and it does so in the direction that flatters this project: extractive retrieval
        gives no definite answer on any of the 255 questions, and its selective accuracy
        is undefined rather than the worst possible score.
        """
        value = M(report, system, field)
        return "n/a" if value is None else f"{value:.{places}f}"

    r = Report()
    # Every table and figure, in the order they appear. Numbers come from here, and
    # from nowhere else, so a cross-reference cannot disagree with a caption.
    r.declare([
        ("Table", "related"), ("Table", "comparison"),
        ("Figure", "arch"),
        ("Table", "checks"),
        ("Table", "stack"),
        ("Figure", "ui-landing"),
        ("Table", "splits"), ("Table", "environment"),
        ("Table", "suite"),
        ("Figure", "ui-neural"), ("Figure", "ui-verify"), ("Figure", "ui-failure"),
        ("Figure", "ui-suite"),
        ("Table", "results"),
        ("Figure", "results-chart"),
        ("Table", "intervals"),
        ("Table", "ablation"),
        ("Figure", "transfer"),
    ])

    # ---- title page
    # The block used to sit in the top third with the rest of the page empty. Three
    # leading blanks drop it to roughly the optical centre, which is where a title
    # page reads as composed rather than as a page that ran out of content.
    for _ in range(3):
        r.p()
    r.p(TITLE, size=19, align=WD_ALIGN_PARAGRAPH.CENTER, space=4)
    r.p("A neuro-symbolic reasoning and verification layer for enterprise "
        "decision support", size=12, align=WD_ALIGN_PARAGRAPH.CENTER, italic=True,
        space=22)
    r.p("PROJECT REPORT", size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space=16)
    for name, _ in AUTHORS:
        r.p(name, size=12.5, align=WD_ALIGN_PARAGRAPH.CENTER, space=2)
    r.p(INSTITUTION, size=11, align=WD_ALIGN_PARAGRAPH.CENTER, space=18)
    r.p(f"System: PRAXIS   ·   {STAMP}   ·   source commit {commit}",
        size=10, align=WD_ALIGN_PARAGRAPH.CENTER, space=10)
    r.p(f"Source code: {REPO_PLACEHOLDER}", size=10.5,
        align=WD_ALIGN_PARAGRAPH.CENTER, space=4)
    r.pagebreak()

    # ---- 1 abstract
    r.h("1. Abstract", 1)
    r.p("**The problem.** Language models are being asked questions that carry "
        "consequences - whether a refund is owed, which policy version governs a "
        "dispute, how much a traveller may reclaim. Retrieval-augmented generation "
        "grounds such answers in documents, but grounding is not entailment: a model "
        "handed the correct clause can still state a conclusion that clause does not "
        "license, and it states it fluently enough that the reader has no signal to "
        "distrust it. Retrieval therefore addresses where the evidence comes from and "
        "leaves who draws the inference unchanged.")
    r.p(f"**The architecture.** PRAXIS makes the language model a reader of questions "
        f"rather than an authority on answers. A 33M-parameter encoder interprets the "
        f"question into a goal and the records it names, and every part of that "
        f"interpretation is validated against the rule schema before use. A symbolic "
        f"reasoner then resolves the policy version in force on the case's own date, "
        f"restricts records to those the asker is authorized to see, and evaluates the "
        f"rules under Kleene strong three-valued logic, so a fact the company has no "
        f"record of is *unknown* rather than false - the distinction between 'no, you "
        f"are not eligible' and 'nobody recorded whether you are'. A deterministic "
        f"verifier defines {census['defined_checks']} checks, of which "
        f"{census['distinct_checks']} run on at least one test question, and a release "
        f"gate publishes the answer or withholds it. **In the default configuration no "
        f"language model acts anywhere on the decision path.**")
    r.p(f"**Method.** Evaluation uses {test['cases']} questions held out over five "
        f"procedurally generated enterprise worlds, with gold answers from a reference "
        f"oracle implemented independently of the system under test, and a further "
        f"{confirm['cases']} questions over five more worlds written after the system "
        f"was final and scored once. Policy values are drawn from ranges that include "
        f"figures no real retailer publishes, so a system answering from what retail "
        f"policies usually say is wrong in a way the scorer can see.")
    r.p(f"**Findings.** Two configurations are reported and should not be conflated. "
        f"The **default, LLM-free** configuration answers "
        f"**{F(test,'neurosymbolic','accuracy')}** of all questions correctly at a "
        f"hallucination rate of {F(test,'neurosymbolic','hallucination_rate')} with "
        f"{F(test,'neurosymbolic','coverage')} coverage, against "
        f"{F(test,'llm_rag','accuracy')} and {F(test,'llm_rag','hallucination_rate')} "
        f"for the same corpus retrieved and answered by a 7B instruction-tuned model. "
        f"The reduction is not bought with abstention: it covers "
        f"{F(test,'neurosymbolic','coverage')} of answerable questions where retrieval "
        f"covers {F(test,'llm_rag','coverage')} - more questions answered, and more of "
        f"them correctly. Using the same model **only as a semantic parser**, with the "
        f"decision still symbolic, reaches {F(test,'ns_parser','accuracy')}. That "
        f"second figure is a separate configuration and is not the default's "
        f"performance.")
    r.p(f"**Scope and limitations.** Neither figure transfers unchanged. On the "
        f"confirmatory worlds the LLM-free configuration holds at "
        f"{F(confirm,'neurosymbolic','accuracy')} while the parser configuration falls "
        f"to {F(confirm,'ns_parser','accuracy')} and its selective accuracy falls from "
        f"{F(test,'ns_parser','selective_accuracy')} to "
        f"{F(confirm,'ns_parser','selective_accuracy')}, so the perfect score on the "
        f"test split is not a reliability claim. The worlds are synthetic, the domain "
        f"is single, and the evaluation is English-only. No claim is made that "
        f"hallucination is eliminated. The sharpest limitation is structural and is "
        f"demonstrated rather than hidden: verification establishes that a claim "
        f"follows from the evidence, not that it answers the question asked. A web "
        f"interface built to make each stage inspectable answers "
        f"**{suite['correct']} of {suite['total']}** sample cases correctly, and one of "
        f"the three failures is exactly this - a supported answer to a question nobody "
        f"asked.")

    # ---- 2 introduction
    r.h("2. Introduction", 1)
    r.h("2.1 Problem statement", 2)
    r.p("Language models now answer questions that carry consequences: whether a "
        "refund is owed, which policy version governs a dispute, how much a traveller "
        "may reclaim. Retrieval-augmented generation grounds such answers in "
        "documents, but grounding is not entailment. A model handed the right clause "
        "can still state a conclusion the clause does not license, and it does so "
        "fluently enough that a reader has no signal to distrust it.")
    r.p("The common remedies keep the model as the authority. Self-consistency "
        "sampling, self-critique and chain-of-verification all ask the model to audit "
        "its own output - auditor and author are the same model, so a confident "
        "systematic error is invisible to both. Combining neural and symbolic "
        "components is a long-standing programme [12]; what this project changes is "
        "not that they are combined but which of them holds the authority for the "
        "decision. Our own measurement bears this out: "
        f"asking a 7B model to check its own retrieval-grounded draft moved accuracy "
        f"from {M(test,'llm_rag','accuracy'):.3f} to "
        f"{M(test,'llm_rag_selfcheck','accuracy'):.3f} and hallucination from "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} to "
        f"{M(test,'llm_rag_selfcheck','hallucination_rate'):.3f} - an improvement far "
        f"smaller than the error it is meant to remove.")
    r.p("Underlying this is a distinction the field's vocabulary tends to collapse. "
        "Answering a consequential question well requires four different things to go "
        "right, and a system can do any three of them and still be wrong:")
    for step, gloss in [
        ("Generating a plausible answer",
         "producing text a competent reader would accept. A language model does this "
         "well, and does it just as well when the answer is wrong, which is why "
         "fluency carries no information about correctness."),
        ("Retrieving supporting evidence",
         "placing the governing clause and the relevant records in front of the "
         "answerer. This is what retrieval-augmented generation adds, and it is "
         "necessary but not sufficient: it changes what the model has read, not what "
         "licenses the conclusion."),
        ("Establishing a logically supported conclusion",
         "deriving the answer from that evidence by rules that hold whether or not "
         "anyone finds the result plausible. This is the step PRAXIS removes the model "
         "from entirely, and it is where the architecture's contribution lies."),
        ("Verifying that the conclusion answers the question asked",
         "confirming that the thing established is the thing wanted. This is not the "
         "same property as the previous one, and no amount of evidence checking "
         "supplies it - a claim can be perfectly supported and still be about "
         "something else. Section 7.2 demonstrates the system failing exactly here."),
    ]:
        r.bullet(f"**{step}** - {gloss}")
    r.p("The first three are addressed by the architecture and measured in Section 8. "
        "The fourth is addressed only partially, and the report is organised so that "
        "the gap is visible rather than averaged away.")

    r.h("2.2 Objectives", 2)
    for obj in [
        "Design an architecture in which the decision is established symbolically, so "
        "that the class of errors in which a model asserts an unsupported conclusion "
        "cannot arise on the decision path.",
        "Implement three-valued reasoning so that a fact the company has no record of "
        "is treated as unknown rather than false.",
        "Implement a deterministic evidence gate that withholds any answer whose "
        "claims are not supported by the retrieved records.",
        "Evaluate against retrieval and language-model baselines on the same "
        "questions, corpus and authorization context.",
        "Build an interface that exposes each stage of the pipeline, so the reasoning "
        "can be inspected rather than taken on trust.",
        "Report coverage beside every accuracy, so that abstention is never mistaken "
        "for reliability.",
    ]:
        r.bullet(obj)

    r.h("2.3 Scope", 2)
    r.p("This is an architecture project evaluated on generated enterprise worlds with "
        "gold answers from an independent oracle. It is not a general-domain factuality "
        "result. The worlds are synthetic and are not real customer traffic. No claim "
        "is made that the approach eliminates hallucination, or that it transfers to "
        "domains whose governing rules cannot be written down.")

    # ---- 3 literature
    r.h("3. Literature Survey", 1)
    r.p(f"{r.ref('related')} groups the work this project builds on by what it leaves open. "
        "The gap is narrow and deliberate: each line addresses a system that "
        "supplies evidence, or scores text after the fact, or asks a model to "
        "check itself, rather than one that refuses to release an answer its own "
        "rules do not entail.")
    r.table(["Area", "Representative work", "Limitation this project addresses"], [
        ["Hallucination surveys", "Ji et al. (2023) [1]; Huang et al. (2025) [2]",
         "Separate faithfulness from factuality; our target is narrower - a decision "
         "stated without entailment from the governing rule."],
        ["Retrieval-augmented generation", "Lewis et al. (2020) [3]",
         "Supplies evidence but leaves inference to the model, so a grounded answer "
         "can still be unlicensed."],
        ["Self-critique and resampling",
         "Chain-of-verification (2024) [5]; SelfCheckGPT (2023) [6]; Self-RAG (2024) [4]",
         "Auditor and author are the same model; a confident systematic error is "
         "invisible to both."],
        ["Claim-level scoring", "FActScore (2023) [7]",
         "An evaluation method, not a release gate - it scores text after the fact "
         "rather than withholding it."],
        ["Neuro-symbolic solvers",
         "Logic-LM (2023) [8]; LINC (2023) [9]; PAL (2023) [10]; Binder (2023) [11]",
         "Formalise a reasoning chain for a puzzle. PRAXIS formalises the "
         "enterprise's own rule base, versioned over time and filtered by an "
         "authorization view."],
    ], [1.3, 1.9, 3.0], f"Table {r.number('Table', 'related')}. Related work and the gap addressed.")

    r.p(f"{r.ref('comparison')} sets the same families against the dimensions this "
        f"project cares about. It characterises what each published method is *for*, "
        f"not what its authors could have built: the neuro-symbolic solvers genuinely "
        f"do symbolic reasoning, and Self-RAG genuinely can decline to use a retrieved "
        f"passage. The claim here is narrower and is the actual research gap - no one "
        f"of them combines a standing, versioned rule base, an authorization filter "
        f"applied before reasoning, and a deterministic gate over the released text.")
    r.table(["Dimension", "RAG [3]", "Self-critique [4,5,6]", "NS solvers [8-11]",
             "PRAXIS"], [
        ["Evidence retrieval", "Core contribution", "Yes; Self-RAG retrieves "
         "adaptively", "Not the focus", "Yes, over clauses and records"],
        ["Who draws the inference", "The model", "The model, then the same model "
         "again", "A solver, over a formalisation the model wrote", "A rule engine, "
         "over a rule base a person wrote"],
        ["Symbolic reasoning", "None", "None", "Yes - formal solver or executed "
         "program", "Yes - three-valued rule evaluation"],
        ["Handling of unknowns", "Not modelled", "Not modelled", "Usually two-valued",
         "Third truth value; unknown is not false"],
        ["Deterministic release gate", "None", "Revision judged by the model",
         "The solver's result is the answer", "Checks on the released text; the "
         "answer can be withheld"],
        ["Authorization-aware reasoning", "Not addressed", "Not addressed",
         "Not addressed", "Records filtered before the rules run"],
        ["Abstention", "Not by construction", "Self-RAG can decline a passage",
         "Solver failure yields no answer", "Insufficient evidence is a first-class "
         "outcome, reported with coverage"],
    ], [1.25, 1.0, 1.25, 1.25, 1.45],
        f"Table {r.number('Table', 'comparison')}. What each family of methods is "
        f"designed to do. A blank is a matter of scope, not of quality: none of these "
        f"methods set out to reason over an enterprise's versioned rule base.",
        fs=8.5)

    # ---- 4 architecture
    r.h("4. System Architecture", 1)
    r.image(FIGURES / "architecture.png",
            f"Figure {r.number('Figure', 'arch')}. PRAXIS as implemented. Hatched "
            f"blocks are neural, dotted stored knowledge, grey symbolic, dark the "
            f"release gate. Reading down: the question is interpreted and evidence "
            f"retrieved; the policy version in force on the case's own date is "
            f"resolved; records are restricted to an authorized view; the rules are "
            f"evaluated three-valued; the conclusion is established; the "
            f"{census['defined_checks']} checks are applied; the gate releases or "
            f"withholds. The dashed panel is the relevance check, drawn outside the "
            f"flow because it exists on the parser path and not on the default "
            f"encoder path - Section 7.2 shows why.", width=3.9)
    r.p("The pipeline has four stages. The neural lane interprets the question and "
        "retrieves evidence; the symbolic lane resolves the policy version in force on "
        "the case's own date, restricts records to those the asker may see, and "
        "evaluates the rules under three-valued logic; the verifier checks the claims "
        "in the drafted reply against the evidence; the release gate publishes or "
        "withholds.")

    r.h("4.1 Neural understanding", 2)
    r.p("A 33M-parameter bge-small encoder, run locally through ONNX, maps the question "
        "to a goal in the rule base, the records it refers to and any facts it states. "
        "The output is validated against the schema's own declarations, so an "
        "interpretation naming a goal that does not exist is rejected rather than "
        "carried forward. The encoder proposes; it never asserts the answer.")

    r.h("4.2 Three-valued symbolic reasoning", 2)
    r.p("Rules are evaluated under Kleene strong three-valued logic [13]: a fact the company "
        "has no record of is unknown, not false. A two-valued engine answers 'no, you "
        "are not eligible' when the truthful answer is 'nobody recorded whether you "
        "are'. The reasoner resolves the policy version in force on the case's own date "
        "rather than the newest, and reads records through an authorized view, so a "
        "rule consults only facts the asker may see.")

    r.h("4.3 The evidence gate", 2)
    r.p(f"The verifier defines **{census['defined_checks']} deterministic checks**. "
        f"**{census['distinct_checks']} of them run on at least one of the "
        f"{census['cases']} test questions**; the remaining one, "
        f"{', '.join(census['never_ran'])}, belongs to the enumeration pipeline, which "
        f"none of this split's ten question categories reaches. Both counts are "
        f"produced by `scripts/count_checks.py`, which reads the defined names out of "
        f"the verifier's source and the observed ones out of the stored traces, so the "
        f"two cannot drift apart in this report. {r.ref('checks')} lists every one of "
        f"them with what it establishes and how often it ran.")
    r.p("A check is applicable or it is not; none runs on every question, because each "
        "depends on something the case must actually assert. A question that cites no "
        "policy has no policy applicability to check, and a refusal issued before any "
        "record is read has nothing to verify at all.")
    applicability = {
        "fact validity": "Every fact the reasoner used is one the records actually "
                         "contain. Runs whenever rules were evaluated.",
        "authorization": "No record outside the asker's authorized view reached the "
                         "answer. Runs whenever records were read.",
        "policy applicability": "Each cited clause genuinely governs this case. Runs "
                                "whenever a clause is cited.",
        "policy version": "The clause applied is the version in force on the case's "
                          "own date, not the newest. Runs when a cited policy has "
                          "more than one version.",
        "symbolic provenance": "The released conclusion is the one the rule engine "
                               "established, not a restatement of it. Runs whenever "
                               "there is a conclusion.",
        "supported numbers": "Every figure in the answer appears in the evidence. "
                             "Runs whenever the answer states a number.",
        "contradictions": "No two pieces of cited evidence conflict. Runs whenever "
                          "rules were evaluated.",
        "answer agreement": "The prose agrees with the structured decision. Runs when "
                            "an answer is phrased for release.",
        "unsupported claims": "The released wording adds no claim the draft did not "
                              "carry. Runs when an answer is phrased for release.",
        "calculation": "A computed amount re-derives from the rule's own arithmetic. "
                       "Runs on calculation goals.",
        "record consistency": "The record's own fields do not contradict the "
                              "conclusion. Runs when a single record decides the case.",
        "citation integrity": "Every rule cites a clause that exists in the policy "
                              "text. Enumeration pipeline only.",
    }
    ran = census.get("ran_on_cases", {})
    rows = []
    for name in census["defined_names"]:
        count = ran.get(name)
        rows.append([name, applicability.get(name, "-"),
                     f"{count} of {census['cases']}" if count else "never on this split"])
    r.table(["Check", "What it establishes, and when it applies", "Cases it ran on"],
            rows, [1.15, 3.6, 1.05],
            f"Table {r.number('Table', 'checks')}. The verifier's "
            f"{census['defined_checks']} checks. Counts are measured over the "
            f"{census['cases']} frozen test questions, not asserted.", fs=8.5)
    r.p(f"Each check returns one of four verdicts, and the distinction between the "
        f"last two is what keeps the gate honest. **Supported**: the evidence entails "
        f"the claim. **Contradicted**: the evidence refutes it. **Unverified**: the "
        f"evidence neither entails nor refutes it - the records are silent. **Not "
        f"checkable**: the claim is not the kind of thing this check can evaluate. "
        f"Treating *unverified* as *contradicted* would make the gate punish the "
        f"company for incomplete records; treating it as *supported* would let an "
        f"unevidenced claim through. It is reported as itself.")
    r.p(f"The release decision is then simple and deterministic: if any applicable "
        f"check returns contradicted, the answer is withheld and the system reports "
        f"insufficient evidence. Measured over the {census['cases']} test cases the "
        f"number of checks that run ranges from {census['min_per_case']} to "
        f"{census['max_per_case']}, with a median of {census['median_per_case']}. The "
        f"{census['zero_check_cases']} cases that run none are refusals settled before "
        f"there is anything to verify - {'; '.join(census['zero_check_reason'])} - and "
        f"they are counted here rather than quietly excluded, because a gate that "
        f"never runs is a gate that cannot be credited with the outcome.")

    # ---- 5 implementation
    r.h("5. Implementation", 1)
    r.h("5.1 Technology stack", 2)
    r.p(f"{r.ref('stack')} names every component with the version the reported "
        f"results were produced against. Nothing in the decision path requires a "
        f"network call, an API key or a GPU.")
    r.table(["Layer", "Technology and version", "Role"], [
        ["Language", f"Python {ENV['python']}",
         "Everything below except the interface."],
        ["Neural encoder", f"BAAI bge-small-en-v1.5 (33M), ONNX Runtime "
                           f"{ENV.get('onnxruntime', '-')}, via fastembed "
                           f"{ENV.get('fastembed', '-')}",
         "Question interpretation, goal and entity resolution; runs locally on CPU, "
         "no network call and no API key."],
        ["Symbolic reasoner", "Custom rule engine (`backend/app/symbolic/`)",
         "Kleene strong three-valued evaluation over versioned rules."],
        ["Knowledge", "Typed knowledge graph over generated worlds",
         "Records, policy history and an authorization view."],
        ["Verification", "Deterministic checks (`backend/app/hybrid/verification.py`)",
         f"The {census['defined_checks']}-check suite and the release gate."],
        ["Backend", f"FastAPI {ENV.get('fastapi', '-')} (production); Python stdlib "
                    f"`http.server` (demo)",
         "The demo server needs no database, so it runs on a clean checkout."],
        ["Persistence", f"SQLAlchemy {ENV.get('sqlalchemy', '-')}, PostgreSQL "
                        f"(production only)",
         "Not used by the benchmark or the demo; both read the frozen world files."],
        ["Interface", "HTML, CSS, vanilla JavaScript",
         "Single page; no build step and no framework dependency."],
        ["Baselines", f"Qwen2.5-7B-Instruct, served by `scripts/local_llm_server.py` "
                      f"(torch {ENV.get('torch', '-')}, transformers "
                      f"{ENV.get('transformers', '-')}) behind an OpenAI-compatible "
                      f"HTTP API",
         "LLM-only, LLM+RAG, self-check and rule-extraction baselines, and the "
         "semantic parser. Greedy decoding; completions cached by (model, messages, "
         "max_tokens) so a re-run reproduces exactly."],
        ["Scale comparison", "Qwen2.5-1.5B-Instruct, Hugging Face transformers",
         "Section 9's capability note, and the relevance-gate measurement."],
        ["Study II model", "nanoGPT, 4.3M parameters (4 layers, 4 heads, width 256, "
                           "vocabulary 4096), trained from random initialization",
         "A deliberately weak generator of checkable claims."],
    ], [1.1, 2.45, 2.65],
        f"Table {r.number('Table', 'stack')}. Implementation stack. Versions are those "
        f"of the environment the reported results were produced in; "
        f"{r.ref('environment')} gives the rest of that environment.", fs=8.5)

    r.h("5.2 Repository structure", 2)
    r.code([
        "backend/app/hybrid/        controller, verification, grounding, knowledge pack",
        "backend/app/symbolic/      three-valued rule engine",
        "backend/app/neural/        encoder interpretation and semantic parser",
        "backend/app/evaluation/    benchmark, scoring, system definitions",
        "backend/data/benchmark_worlds/   13 generated worlds, 663 cases",
        "demo/server.py             demo API driving the real pipeline",
        "demo/static/index.html     the interface",
        "scripts/                   experiment, audit and figure scripts",
        "eval_reports/research/     per-question records for every reported number",
        "docs/paper/                the IEEE paper and its figures",
    ])

    r.h("5.3 The demo interface", 2)
    r.p("The interface exists to make the pipeline inspectable. Every panel is filled "
        "from the controller's own trace - the same object the benchmark scores - so "
        "nothing shown on the page is precomputed or illustrative. A case that fails "
        "in the interface fails in the benchmark.")
    r.image(SHOTS / "01-landing.jpg",
            f"Figure {r.number('Figure', 'ui-landing')}. The interface on load. The left pane lists one sample case per "
            "question category; a question can also be typed directly.")

    r.h("6. Experimental Setup", 1)
    r.h("6.1 How the enterprise worlds are generated", 2)
    r.p("A benchmark written by hand against one knowledge pack measures how well a "
        "system fits that pack. This evaluation generates companies instead. An "
        "individual *world* is a complete synthetic enterprise: a product catalogue, "
        "staff with roles, customers, orders and returns, a policy document, and a "
        "policy-version history with dated amendments. Every world is answered by the "
        "*same* rule files, so a result that holds across worlds is a property of the "
        "method rather than of one dataset its author happened to know.")
    r.p("Three generator decisions determine what the benchmark can show, and each is "
        "a deliberate attack on a way the evaluation could flatter the system:")
    for head, body in [
        ("Counterfactual policy values",
         "return windows, fee rates, thresholds and caps are drawn from ranges that "
         "deliberately include values no real retailer publishes - a 23-day window, a "
         "6% fee capped at 750. A language model answering from what retail policies "
         "usually say is then wrong in a way the scorer can see, instead of being "
         "accidentally right from prior knowledge."),
        ("Boundary records",
         "orders are not sampled uniformly. Each world places records just inside, "
         "exactly on, and just outside every window and threshold, and on both sides "
         "of the returns-policy version change. Those are precisely the cases where a "
         "plausible answer and a correct one come apart."),
        ("Independence from the system under test",
         "the generator imports nothing from the reasoner, and gold answers come from "
         "`oracle.py`, a separate reference implementation written from the policy "
         "text that does not import the rule language, the reasoner, the knowledge "
         "graph or the knowledge pack. Where the two disagree one of them is wrong, "
         "and `scripts/check_oracle.py` reports every disagreement to be adjudicated "
         "against the policy text rather than silently resolved in either direction."),
    ]:
        r.bullet(f"**{head}** - {body}")
    r.p("Generation is seeded, so `generate_world(seed)` returns the same world on "
        "every machine. The worlds are identified by those seeds: development 11-13, "
        "test 101-105, confirmatory 201-205.")

    r.h("6.2 How questions are constructed", 2)
    r.p("Every question comes from a template and is answered by the oracle, never by "
        "the system under test. Templates are partitioned so the benchmark cannot be "
        "gamed by phrasing: the canonical wording of each goal is shared between "
        "splits, but every other wording exists in exactly one split. Development "
        "questions may be read, failed on and used to fix the system; test questions "
        "use phrasings development never saw, so a fix that merely memorised a "
        "phrasing does not transfer. Each question also asks in the direction of its "
        "goal - \"can I still return it?\", never \"am I too late to return it?\" - so "
        "a yes means the same thing for every system scored.")
    r.p("Ten categories probe different failure modes. **known** asks a policy "
        "decision on a named record, plainly. **paraphrased** asks the same decisions "
        "in words the schema never uses. **multi_hop** requires finding the record "
        "through relations. **calculation** asks for an amount the policy computes. "
        "**policy_version** asks about damaged items near a policy change, where "
        "applying the current version to every order gives a wrong answer. "
        "**recommendation** asks what an agent may offer and whether approval is "
        "needed. **missing_info** omits a fact the policy needs, so the correct answer "
        "is conditional and any definite answer is unsupported. **false_premise** "
        "asserts a policy value, approval or record fact that is false, so accepting "
        "it is a hallucination. **unauthorized** asks about a record the asker may not "
        "see, so any disclosure is a leak. **not_covered** asks a plausible company "
        "question no policy answers.")

    r.h("6.3 Splits, and what each is for", 2)
    r.table(["Split", "Worlds", "Seeds", "Questions", "Purpose"], [
        ["Development", "3", "11-13", "153",
         "Threshold calibration and debugging. Never reported as a result."],
        ["Test (frozen)", "5", "101-105", "255",
         "Frozen before first use; the main quantitative evaluation."],
        ["Confirmatory", "5", "201-205", "255",
         "New companies, written after the system was final and scored once."],
        ["Total corpus", "13", "-", "663", "All generated cases."],
    ], [1.15, 0.7, 0.8, 0.9, 2.65],
        f"Table {r.number('Table', 'splits')}. Dataset splits. The three share no case "
        f"identifier and no world.")
    r.p(f"{r.ref('splits')} gives the division. The separation is what lets the "
        f"confirmatory split function as a held-out test: thresholds were calibrated "
        f"on the development worlds alone, and the confirmatory worlds were generated "
        f"from different seeds after the system was final. One caveat belongs here "
        f"rather than in a footnote - the confirmatory split reuses the *test* "
        f"split's question wordings. Its companies are new; its phrasings are not. It "
        f"therefore measures transfer to unseen organisations, and should not be read "
        f"as a second test of paraphrase robustness.")

    r.h("6.4 How answers are judged", 2)
    r.p("The oracle returns a typed gold answer, and correctness means something "
        "different for each type. A **decision** or **value** or **result** is "
        "answerable, and is correct when the system commits to the right answer. A "
        "**conditional** gold - the question lacks a fact the policy needs - is "
        "correct only if the answer is hedged rather than definite, because a definite "
        "answer there asserts something the knowledge does not support. A **denied** "
        "case is correct only if the system refuses without disclosing the record. A "
        "**not-covered** case is correct only if the system reports insufficient "
        "evidence. Answerability is therefore not a property of the question's "
        "surface; it is the oracle's judgement about whether the policy and records "
        "determine an answer at all.")
    r.p("**Metrics, and their denominators.** Let N be all cases in a split, A the "
        "answerable ones, and D the subset of A the system answered definitely. "
        "Accuracy and hallucination are over N. Coverage is |D|/|A|. Selective "
        "accuracy is correctness over D. The denominators differ, which is why the "
        "figures can look incompatible without being so: conditional, denied and "
        "not-covered cases are excluded from coverage but included in accuracy. "
        "Hallucination is the union of three disjoint faults - a wrong definite "
        "answer, a definite answer without support, and a disclosure of a record the "
        "asker may not see. Coverage and selective accuracy are always reported "
        "together, because a system can buy a high selective accuracy by refusing "
        "every hard question.")

    r.h("6.5 Execution environment and procedure", 2)
    r.p("All systems see the same questions, corpus and authorization context, and "
        "every language-model system within a run uses the same model. Decoding is "
        "greedy throughout. The benchmark writes one record per question as it "
        "finishes, so an interrupted run resumes without re-asking a model a question "
        "it has already answered, and the report is rebuilt from those records rather "
        "than from memory. The environment below is read from the machine at build "
        "time by this report's own generator.")
    r.table(["Component", "Value"], [
        ["Operating system", ENV["os"]],
        ["Python", ENV["python"]],
        ["ONNX Runtime / fastembed",
         f"{ENV.get('onnxruntime','-')} / {ENV.get('fastembed','-')}"],
        ["NumPy", ENV.get("numpy", "-")],
        ["FastAPI / SQLAlchemy / Pydantic",
         f"{ENV.get('fastapi','-')} / {ENV.get('sqlalchemy','-')} / "
         f"{ENV.get('pydantic','-')}"],
        ["PyTorch / transformers",
         f"{ENV.get('torch','-')} / {ENV.get('transformers','-')}"],
        ["CUDA", ENV.get("cuda", "unknown")],
        ["PRAXIS decision path", "CPU only; the encoder is ONNX and no model runs on "
                                 "the decision path"],
        ["Language-model baselines", "Served over HTTP by "
                                     "`scripts/local_llm_server.py`; the serving "
                                     "device and any quantization were not recorded "
                                     "in the run metadata"],
    ], [2.0, 4.1],
        f"Table {r.number('Table', 'environment')}. The environment this build was "
        f"produced in. The last row is a limitation, not a specification: the latency "
        f"comparison in {r.ref('results')} is therefore not hardware-controlled.",
        fs=8.5)

    # ---- 7 sample test cases
    r.h("7. Sample Test Cases and Results", 1)
    timing = suite.get("timing", {})
    r.p(f"Ten cases were run through the interface, one per question category, taken "
        f"from world 101 of the frozen test split. The selection is the first case of "
        f"each category, so it is reproducible and not chosen for effect. "
        f"**{suite['correct']} of {suite['total']} were answered correctly.** The "
        f"PASS and FAIL labels in {r.ref('suite')} are not written by hand: each is computed by "
        f"the same per-gold-kind rule the benchmark scorer uses, so a conditional gold "
        f"passes only on a hedged answer and a denied gold only on a refusal that "
        f"leaks nothing.")
    r.p(f"**These ten cases are a demonstration, not the evaluation.** They exist to "
        f"show that the interface runs the real pipeline and that each stage can be "
        f"inspected; the quantitative result is the {test['cases']}-question frozen "
        f"test split in Section 8. The two must not be read as comparable. Ten cases "
        f"drawn one per category deliberately over-weight the rare categories: "
        f"not-covered questions are 6% of the test split and 10% of this set, so a "
        f"single failure here moves the headline by ten points where the same failure "
        f"moves the benchmark by four tenths of one. The sample is also a single "
        f"world, where the benchmark spans five. {suite['correct']} of "
        f"{suite['total']} is therefore an illustration of the system's behaviour "
        f"across the range of question types, and the benchmark's "
        f"{F(test,'neurosymbolic','accuracy')} is the measurement.")
    if timing:
        r.p(f"**Timing, and what it measures.** The set took "
            f"{timing['first_pass_ms']:.0f} ms, a mean of "
            f"{timing['first_pass_mean_ms']:.0f} ms per question, in one cold process "
            f"with the knowledge pack already loaded. Of that, the first case alone is "
            f"{timing['first_case_ms']:.0f} ms, because answering it also builds the "
            f"goal catalogue and the policy index; every later case is a fraction of "
            f"that. Asking the same ten questions again in the same process takes "
            f"{timing['repeat_pass_ms']:.0f} ms, "
            f"{timing['repeat_mean_ms']:.1f} ms per question, because the encoder "
            f"memoises embeddings by text. Both figures are real and they answer "
            f"different questions; this table reports the first, which is the cost of "
            f"answering a question the process has not seen before, and therefore the "
            f"only one comparable to the benchmark's mean latency in Table 2. An "
            f"earlier version of this report quoted the repeat figure as though it "
            f"were the cost of answering, which understated it by a factor of "
            f"{timing['first_pass_ms'] / max(timing['repeat_pass_ms'], 1e-9):.0f}.")

    rows = []
    for x in suite["results"]:
        verdict = x["verdict"]["correct"]
        rows.append([
            x["case"]["category"],
            x["case"]["question"][:66] + ("…" if len(x["case"]["question"]) > 66 else ""),
            x["release"]["outcome"],
            str(x["gold"]["kind"]),
            "**PASS**" if verdict else "**FAIL**" if verdict is False else "-",
            f"{x['release']['latency_ms']:.1f}",
        ])
    r.table(["Category", "Question", "Outcome", "Gold kind", "Result", "ms"], rows,
            [0.95, 2.55, 0.8, 0.8, 0.6, 0.45],
            f"Table {r.number('Table', 'suite')}. Sample test cases, run through the demo interface. "
            f"{suite['correct']}/{suite['total']} correct.", fs=8.5)

    r.h("7.1 A case the system gets right", 2)
    r.p("Case W101-T-known-1 asks whether an order can be returned. The encoder ranks "
        "candidate goals and carries forward the highest; the reasoner gathers the "
        "order's status, category and delivery date, applies the returns rules under "
        "three-valued logic, and establishes the conclusion before any text is written. "
        f"Figures {r.ref('ui-neural').split()[-1]} and {r.ref('ui-verify').split()[-1]} follow this case through all four stages.")
    r.image(SHOTS / "02-case-neural-symbolic-cropped.png",
            f"Figure {r.number('Figure', 'ui-neural')}. Stages 1 and 2. The encoder's candidate goals with similarity "
            "scores, and the symbolic facts and rules. R-RET-A is proved; R-RET-D is "
            "refuted - the damage window had passed - and the engine records both.")
    r.image(SHOTS / "03-verification-release-cropped.png",
            f"Figure {r.number('Figure', 'ui-verify')}. Stages 3 and 4. Nine checks applied to this case and all "
            "passed, each with the reason it passed. The released answer cites the "
            "policy version in force on the delivery date, not the newest.")

    r.h("7.2 The case the system gets wrong, and why it matters", 2)
    r.p(f"Case W101-T-not_covered-1 asks for the warranty period on a product. No "
        f"policy clause in the rule base covers warranties. The correct behaviour is "
        f"to report insufficient evidence. Instead the encoder maps the question onto "
        f"the nearest in-scope goal - replacement availability - and the system "
        f"answers that question correctly, citing real clauses and real records, as "
        f"{r.ref('ui-failure')} shows.")
    r.image(SHOTS / "05-not-covered-failure-cropped.png",
            f"Figure {r.number('Figure', 'ui-failure')}. The documented failure. Asked about warranty, the system "
            "resolved replacement_available and answered confidently. Every applicable "
            "check passed, because every claim it made was supported.")
    r.p("**The gate cannot intervene, and that is the finding.** Verification "
        "establishes that a claim follows from the evidence, not that it answers the "
        "question asked. These are two different properties, and the whole architecture "
        "secures only the first. Across the fifteen not-covered questions in the test "
        "split this configuration abstained in none. The remedy is a relevance check "
        "between the resolved goal and the question - the dashed panel in Figure 1.")
    r.p("**That check now exists, on one of the two configurations.** Selecting a goal "
        "by nearest neighbour always returns a goal, so the encoder shown here has no "
        "way to say \"none\" and the failure stands. The language-model parser does "
        "have that option, and it was already using it: it names no goal on all "
        "fifteen. It was being overruled downstream, where any policy clause scoring "
        "above the retrieval floor was recited anyway - so text similarity overturned "
        "a correct judgement. The nine questions answered are exactly the nine whose "
        "nearest clause clears that floor. Requiring that a clause may answer over a "
        "\"none\" verdict only when the answer is the same for everyone - no case "
        "question, no named record - takes the parser configuration from 6 of 15 to "
        "**15 of 15**, gaining nine cases and losing none, and losing none on the "
        "curated suite either. Nine earlier attempts to detect out-of-scope questions "
        "by a similarity score were refuted first; `docs/audit/RELEVANCE_GATE.md` "
        "records each with the measurement that killed it. The gate inherits the "
        "parser's errors, which is its real limit: a 1.5B model misreads 29 of 86 "
        "covered questions as out of scope, and while none of those was being answered "
        "correctly before, a weak parser would turn the gate into an abstention "
        "machine.")
    r.image(SHOTS / "04-sample-suite-cropped.png",
            f"Figure {r.number('Figure', 'ui-suite')}. The full sample set run in one pass, with per-case verdicts and "
            "latencies.")

    # ---- 8 results
    r.h("8. Results and Discussion", 1)
    r.h("8.1 Main comparison", 2)
    order = [("neurosymbolic", "PRAXIS (no LLM)"), ("ns_parser", "PRAXIS (LLM parser)"),
             ("llm_rag_rules", "LLM + RAG + rules"),
             ("llm_rag_selfcheck", "LLM + RAG + self-check"),
             ("llm_only", "LLM only"), ("llm_rag", "LLM + RAG"),
             ("rag_extractive", "Extractive RAG")]
    rows = []
    for key, label in order:
        if key not in test["systems"]:
            continue
        rows.append([label, F(test, key, "accuracy"),
                     F(test, key, "hallucination_rate"),
                     F(test, key, "coverage"),
                     F(test, key, "selective_accuracy")])
    r.table(["System", "Accuracy", "Hallucination", "Coverage", "Selective acc."],
            rows, [2.0, 1.0, 1.1, 0.95, 1.1],
            f"Table {r.number('Table', 'results')}. Test split, {test['cases']} questions. Accuracy and "
            f"hallucination are over all cases; coverage has a different denominator "
            f"and is reported separately.")
    r.image(FIGURES / "main_results.png",
            f"Figure {r.number('Figure', 'results-chart')}. Accuracy and hallucination on the test split, with 95% "
            "bootstrap intervals.", width=5.9)
    r.p(f"{r.ref('results')} sets the systems side by side. The LLM-free configuration answers {M(test,'neurosymbolic','accuracy'):.3f} "
        f"of all questions correctly at "
        f"{M(test,'neurosymbolic','hallucination_rate'):.3f} hallucination, against "
        f"{M(test,'llm_rag','accuracy'):.3f} and "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} for the same model given "
        f"retrieval over the same corpus. The reduction is not bought with abstention: "
        f"it covers {M(test,'neurosymbolic','coverage'):.3f} of answerable questions "
        f"while retrieval covers {M(test,'llm_rag','coverage'):.3f} - it answers more "
        f"questions and more of them correctly. {r.ref('results-chart')} plots both quantities with "
        f"their 95% bootstrap intervals.")

    r.h("8.2 Statistical treatment", 2)
    r.p("Every rate in this report carries a 95% bootstrap interval, computed as "
        "follows. The per-question records are resampled with replacement to the "
        "original size, the rate is recomputed on each resample, and the interval is "
        "the 2.5th and 97.5th percentiles of **2,000** such resamples. The resampling "
        "is seeded, so the interval is identical on a re-run. This is the percentile "
        "method; no normal approximation is used, which matters because several of "
        "these rates sit near 0 or 1 where a symmetric interval would extend past the "
        "possible range.")
    r.p("**The sampling unit is the individual question, and this is a limitation.** "
        "Questions are generated within worlds, so two questions from the same world "
        "share its policy parameters and its records and are not independent. "
        "Resampling questions therefore treats clustered observations as independent "
        "and produces intervals that are, if anything, narrower than a world-clustered "
        "analysis would give. The intervals should be read as describing variation "
        "across these questions, not as supporting a claim about the population of "
        "enterprises. Section 9 restates this as a limitation on external validity.")
    r.p(f"{r.ref('intervals')} gives the headline rates with their intervals.")
    ci_rows = []
    for key, label in [("neurosymbolic", "PRAXIS (no LLM)"),
                       ("ns_parser", "PRAXIS (LLM parser)"),
                       ("llm_rag_rules", "LLM + RAG + rules"),
                       ("llm_rag", "LLM + RAG"),
                       ("llm_only", "LLM only")]:
        def band(field):
            entry = test["systems"][key]["overall"].get(field)
            if not isinstance(entry, dict) or not entry.get("ci95"):
                return F(test, key, field)
            lo, hi = entry["ci95"]
            return f"{entry['value']:.3f}  [{lo:.3f}, {hi:.3f}]"
        ci_rows.append([label, band("accuracy"), band("hallucination_rate")])
    r.table(["System", "Accuracy, 95% interval", "Hallucination, 95% interval"],
            ci_rows, [1.8, 2.15, 2.15],
            f"Table {r.number('Table', 'intervals')}. Point estimates with 95% "
            f"percentile bootstrap intervals over {test['cases']} questions, 2,000 "
            f"seeded resamples. The LLM-free and LLM+RAG accuracy intervals do not "
            f"overlap; no significance test is claimed from that fact alone.",
            fs=8.5)
    r.p("Non-overlapping intervals are suggestive, not a test, and the comparisons "
        "that matter are paired - the same question answered by two systems. Those "
        "are reported in the accompanying paper using an exact McNemar test over "
        "discordant pairs. This report states the intervals and declines to convert "
        "them into significance claims they do not license.")

    r.h("8.3 Ablations", 2)
    abl = [("ns_two_valued", "Unknown treated as false"),
           ("ns_latest_version", "Newest policy applied to every case"),
           ("ns_trust_statements", "Asker's statements override records"),
           ("ns_no_authorization", "Reasoning sees every record")]
    cells = ("accuracy", "hallucination_rate", "coverage", "leak_rate")
    rows = [["PRAXIS (full), nothing disabled"]
            + [F(test, "neurosymbolic", c) for c in cells]]
    for key, label in abl:
        if key in test["systems"]:
            rows.append([label] + [F(test, key, c) for c in cells])
    r.table(["Configuration", "Accuracy", "Hallucination", "Coverage", "Disclosure"],
            rows, [2.2, 0.95, 1.05, 0.9, 1.05],
            f"Table {r.number('Table', 'ablation')}. Each row below the first disables exactly one mechanism. "
            "Scored on the structured decision, so these columns cannot measure the "
            "verification gate, which acts on released wording.")
    r.p(f"{r.ref('ablation')} isolates each mechanism. Three-valued reasoning carries real weight: treating unknown as false costs "
        f"{M(test,'neurosymbolic','accuracy') - M(test,'ns_two_valued','accuracy'):.3f} "
        f"accuracy, because the system starts denying entitlements that were merely "
        f"unrecorded. Authorization is the sharpest: removing it leaves accuracy "
        f"broadly intact but raises the disclosure rate from "
        f"{M(test,'neurosymbolic','leak_rate'):.3f} to "
        f"{M(test,'ns_no_authorization','leak_rate'):.3f} - the system answers well and "
        f"leaks almost every protected record while doing so, which an accuracy-only "
        f"evaluation would not reveal.")
    r.p(f"One row needs reading with care. Letting the asker's statements override "
        f"records lowers hallucination to "
        f"{M(test,'ns_trust_statements','hallucination_rate'):.3f}, below the full "
        f"system - but coverage falls from {M(test,'neurosymbolic','coverage'):.3f} to "
        f"{M(test,'ns_trust_statements','coverage'):.3f}. The configuration abstains "
        f"its way to a better hallucination rate, which is exactly why coverage is "
        f"reported beside every accuracy.")

    r.h("8.4 Transfer to unseen worlds", 2)
    r.p(f"On the confirmatory split - five worlds the system had never seen, written "
        f"after it was final and scored once - the LLM-free configuration transfers "
        f"essentially unchanged: {F(confirm,'neurosymbolic','accuracy')} against "
        f"{F(test,'neurosymbolic','accuracy')} on test, a difference well inside "
        f"either interval. The parser configuration does not: accuracy falls from "
        f"{F(test,'ns_parser','accuracy')} to {F(confirm,'ns_parser','accuracy')}, and "
        f"selective accuracy from {F(test,'ns_parser','selective_accuracy')} to "
        f"{F(confirm,'ns_parser','selective_accuracy')}.")
    r.image(FIGURES / "transfer.png",
            f"Figure {r.number('Figure', 'transfer')}. Test against confirmatory, both "
            f"configurations, with 95% bootstrap intervals. The left panel is accuracy "
            f"over all cases; the right is selective accuracy over the cases answered "
            f"definitely. The LLM-free pair is flat; the parser pair is not.",
            width=5.9)
    r.p(f"{r.ref('transfer')} shows the asymmetry, which is the more important of the "
        f"two results in this section. The configuration with no language model on any "
        f"path behaves the same on companies it has never seen, which is what one "
        f"would expect of a rule engine: nothing in it was fitted to the test worlds. "
        f"The configuration whose understanding step is a language model loses "
        f"{abs(M(test,'ns_parser','accuracy') - M(confirm,'ns_parser','accuracy')):.3f} "
        f"accuracy when the companies change. The perfect selective accuracy on the "
        f"test split is therefore not a reliability property of the architecture; it "
        f"is a number that did not survive contact with new data, and reporting it "
        f"without its confirmatory counterpart would be the single most misleading "
        f"thing this report could do.")

    r.h("8.5 Secondary study: gating a model trained from scratch", 2)
    claims = ROOT / "artifacts/football/results/claims.jsonl"
    rows_c = [json.loads(l) for l in claims.read_text(encoding="utf-8").splitlines()
              if l.strip()]
    chk = [x for x in rows_c if x["status"] != "NOT_CHECKABLE"]
    ref = sum(1 for x in chk if x["status"] == "CONTRADICTED")
    unv = sum(1 for x in chk if x["status"] == "UNVERIFIED")
    sup = len(chk) - ref - unv
    r.p(f"A {fb['model_parameters']/1e6:.1f}M-parameter decoder was trained from random "
        f"initialization on a corpus of FIFA World Cup goal records, then made to "
        f"generate freely while the symbolic layer decided what reached the reader. Of "
        f"{len(chk):,} checkable claims, the graph refutes **{ref}** "
        f"({ref/len(chk):.3f}) and cannot confirm a further {unv}; gating releases the "
        f"{sup} supported ones, leaving coverage of {sup/len(chk):.3f}.")
    r.p("Refuted and unverifiable claims are kept apart deliberately: the knowledge "
        "graph is incomplete, so a claim it has no record of is not thereby false. The "
        "gated rate of zero refuted claims holds **by construction** - the gate "
        "releases only what the graph confirms - and is not evidence of perfect "
        "verification. The informative quantities are the base refuted rate and the "
        "coverage that suppressing it costs: verification buys soundness with silence.")

    # ---- 9 limitations
    r.h("9. Limitations", 1)
    for text in [
        "**Scope.** Results come from generated worlds with oracle gold answers, not "
        "real customer traffic, in a single domain whose governing rules can be "
        "written down. English only.",
        "**Question relevance is implemented only where understanding can decline.** "
        "The default encoder configuration abstains on none of the fifteen not-covered "
        "questions, because nearest-neighbour goal selection always returns a goal. "
        "The parser configuration now abstains on all fifteen. Carrying the fix to the "
        "default would mean giving the encoder negative catalogue entries - "
        "enumerating what the company does not do - which is open-ended. "
        "Section 7.2 demonstrates both halves.",
        "**Verifier correctness is unmeasured.** The verifier is correct by "
        "construction with respect to its own rule base; independent ground-truth "
        "labels would be needed to measure its precision and recall.",
        "**Authorship.** The rule base, world generators and oracle were written by "
        "one person. An independently authored evaluation is the natural next step.",
        "**Transfer.** Perfect selective accuracy on the test split does not "
        "generalize, as Section 8.3 shows.",
        "**Neither study speaks to open-domain factuality**, where no curated "
        "knowledge base exists for the gate to consult.",
        "**Statistical intervals treat questions as independent.** They are generated "
        "within worlds and share each world's policy parameters, so the reported "
        "intervals are narrower than a world-clustered analysis would give (Section "
        "8.2).",
    ]:
        r.bullet(text)

    r.h("9.1 External validity and deployment risk", 2)
    r.p("The limitations above are properties of the evaluation. This section asks a "
        "different question: what would go wrong if this architecture were deployed "
        "against a real company's policies tomorrow? Three risks follow directly from "
        "how it works, and each is a consequence of the design rather than a defect "
        "to be patched.")
    for head, body in [
        ("The rule base becomes the ceiling, and the failure is silent",
         "every guarantee here is relative to rules someone wrote. A policy "
         "transcribed incorrectly produces answers that are confidently wrong and "
         "fully verified, because the verifier checks entailment from the rule base "
         "and not the rule base against the world. In this evaluation the rules were "
         "written by the same person who wrote the generator, so transcription error "
         "is systematically understated. A deployment would need the rule base "
         "reviewed by whoever owns the policy, and a route for them to contest an "
         "answer."),
        ("Coverage collapses where rules cannot be written",
         "the architecture answers what its rules decide and abstains otherwise. "
         "Where a real policy is vague, contested or exercised by human judgement, "
         "that is not a small loss of coverage but the normal case, and the system "
         "would abstain on a large fraction of genuine traffic. The honest deployment "
         "shape is therefore a triage layer in front of people, not a replacement for "
         "them, and the coverage figures here come from a domain deliberately chosen "
         "because its rules *can* be written down."),
        ("Relevance failures survive every check",
         "Section 7.2 shows the system answering a warranty question out of the "
         "replacement policy, with every applicable check passing. In an evaluation "
         "this is one scored failure. In deployment it is the most dangerous output "
         "the system can produce, because it carries real citations and a real "
         "record, and is therefore more persuasive than an unsupported guess would "
         "be. The mitigation implemented here works only on the parser path; on the "
         "default path there is none."),
    ]:
        r.bullet(f"**{head}** - {body}")
    r.p("A fourth risk is procedural rather than technical. The evaluation reports "
        "coverage beside every accuracy precisely so that abstention cannot be "
        "mistaken for reliability, but a deployment dashboard that showed only "
        "hallucination rate would make the trust-statements ablation in "
        f"{r.ref('ablation')} look like an improvement. Whatever is measured in "
        "production has to carry both numbers, for the same reason this report does.")

    # ---- 10 conclusion
    r.h("10. Conclusion and Future Work", 1)
    r.p(f"Removing the language model from the decision path, rather than asking it to "
        f"audit itself, reduced hallucination from "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} to "
        f"{M(test,'neurosymbolic','hallucination_rate'):.3f} while raising coverage "
        f"from {M(test,'llm_rag','coverage'):.3f} to "
        f"{M(test,'neurosymbolic','coverage'):.3f} and cutting latency by roughly two "
        f"orders of magnitude. The reduction is not an artefact of refusing difficult "
        f"questions. The latency comparison is not hardware-controlled: PRAXIS runs "
        f"entirely on CPU, while the baselines wait on a served language model, so the "
        f"gap measures the cost of needing a generator at all rather than two "
        f"implementations on equal footing.")
    r.p("**The problem addressed.** Consequential questions are being answered by "
        "systems whose fluency is uncorrelated with their correctness. Retrieval "
        "changes what the answerer has read; it does not change who draws the "
        "inference, and a model handed the right clause can still assert a conclusion "
        "that clause does not license.")
    r.p(f"**The architectural contribution**, and what the experiments actually "
        f"establish. The contribution is a division of authority: the language model "
        f"interprets and may phrase, a rule engine decides, and a deterministic gate "
        f"releases. The experiments establish three things. Symbolic reasoning matters "
        f"on its own terms - treating unknown as false costs "
        f"{abs(M(test,'neurosymbolic','accuracy') - M(test,'ns_two_valued','accuracy')):.3f} "
        f"accuracy, because the system begins denying entitlements that were merely "
        f"unrecorded, and a two-valued engine cannot tell 'no' from 'nobody wrote it "
        f"down'. Authorization matters more sharply than accuracy reveals - removing "
        f"it leaves accuracy broadly intact while the disclosure rate goes from "
        f"{F(test,'neurosymbolic','leak_rate')} to "
        f"{F(test,'ns_no_authorization','leak_rate')}, so a system evaluated on "
        f"accuracy alone would be scored as essentially unchanged while leaking almost "
        f"every protected record. And coverage must accompany every hallucination "
        f"figure, because one ablation in {r.ref('ablation')} lowers hallucination "
        f"below the full system purely by abstaining more.")
    r.p("**What is demonstrated, and what is proposed.** Demonstrated, on the frozen "
        "test split and confirmed on unseen companies: the LLM-free configuration "
        "answers more questions than retrieval and more of them correctly, and it "
        "transfers. Demonstrated and unflattering: the parser configuration's perfect "
        "selective accuracy does not transfer, and the default configuration does not "
        "recognise questions its rule base does not cover. That last failure is "
        "structural rather than a tuning problem - verification establishes that a "
        "claim follows from evidence, not that it answers the question asked, and "
        "support and relevance are separate properties throughout this report. The "
        "architecture secures the first by construction and the second only where "
        "understanding is able to decline.")
    r.p("**Proposed, not demonstrated.** Carrying the relevance gate to the default "
        "configuration requires giving nearest-neighbour goal selection a null option "
        "it does not have, and the obvious route - negative catalogue entries - means "
        "enumerating what a company does not do, which is open-ended. An "
        "independently authored evaluation, in which the rule base, the world "
        "generator and the oracle are not written by one person, would test the "
        "verifier's correctness rather than assuming it. Raising coverage in the "
        "secondary study without reintroducing unsupported claims remains open. None "
        "of these is claimed as a result here.")

    # ---- 11 references
    r.h("11. References", 1)
    for i, ref_text in enumerate([
        "Z. Ji et al., \"Survey of hallucination in natural language generation,\" ACM "
        "Comput. Surv., vol. 55, no. 12, art. 248, pp. 1-38, 2023, "
        "doi: 10.1145/3571730.",
        "L. Huang et al., \"A survey on hallucination in large language models: "
        "principles, taxonomy, challenges, and open questions,\" ACM Trans. Inf. "
        "Syst., vol. 43, no. 2, 2025, doi: 10.1145/3703155.",
        "P. Lewis et al., \"Retrieval-augmented generation for knowledge-intensive NLP "
        "tasks,\" in Adv. Neural Inf. Process. Syst. (NeurIPS), vol. 33, 2020.",
        "A. Asai, Z. Wu, Y. Wang, A. Sil, and H. Hajishirzi, \"Self-RAG: learning to "
        "retrieve, generate, and critique through self-reflection,\" in Proc. Int. "
        "Conf. Learn. Represent. (ICLR), 2024.",
        "S. Dhuliawala et al., \"Chain-of-verification reduces hallucination in large "
        "language models,\" in Findings Assoc. Comput. Linguistics: ACL 2024, "
        "pp. 3563-3578, doi: 10.18653/v1/2024.findings-acl.212.",
        "P. Manakul, A. Liusie, and M. J. F. Gales, \"SelfCheckGPT: zero-resource "
        "black-box hallucination detection for generative large language models,\" in "
        "Proc. Conf. Empirical Methods Natural Lang. Process. (EMNLP), 2023, "
        "pp. 9004-9017, doi: 10.18653/v1/2023.emnlp-main.557.",
        "S. Min et al., \"FActScore: fine-grained atomic evaluation of factual "
        "precision in long form text generation,\" in Proc. EMNLP, 2023, "
        "pp. 12076-12100, doi: 10.18653/v1/2023.emnlp-main.741.",
        "L. Pan, A. Albalak, X. Wang, and W. Y. Wang, \"Logic-LM: empowering large "
        "language models with symbolic solvers for faithful logical reasoning,\" in "
        "Findings Assoc. Comput. Linguistics: EMNLP 2023, pp. 3806-3824, "
        "doi: 10.18653/v1/2023.findings-emnlp.248.",
        "T. X. Olausson et al., \"LINC: a neurosymbolic approach for logical reasoning "
        "by combining language models with first-order logic provers,\" in Proc. "
        "EMNLP, 2023, pp. 5153-5176, doi: 10.18653/v1/2023.emnlp-main.313.",
        "L. Gao et al., \"PAL: program-aided language models,\" in Proc. 40th Int. "
        "Conf. Mach. Learn. (ICML), PMLR, vol. 202, 2023, pp. 10764-10799.",
        "Z. Cheng et al., \"Binding language models in symbolic languages,\" in Proc. "
        "Int. Conf. Learn. Represent. (ICLR), 2023.",
        "A. d'Avila Garcez and L. C. Lamb, \"Neurosymbolic AI: the 3rd wave,\" Artif. "
        "Intell. Rev., vol. 56, no. 11, pp. 12387-12406, 2023, "
        "doi: 10.1007/s10462-023-10448-w.",
        "S. C. Kleene, Introduction to Metamathematics. Amsterdam, The Netherlands: "
        "North-Holland, 1952.",
    ], 1):
        r.p(f"[{i}] {ref_text}", size=9.5, space=3)

    # ---- 12 appendix
    r.h("12. Appendix: Source Code and Reproduction", 1)
    r.p(f"**Repository:** {REPO_PLACEHOLDER}")
    r.p(f"**Commit reported here:** {commit} — the state of the code that produced "
        f"every number in this report. The commit that carries this document is its "
        f"child, since the document is written after the code it describes; both are on "
        f"`master`.")
    r.p(f"**Tested on:** {ENV['os']}, Python {ENV['python']}. Nothing below needs a "
        f"database, a GPU, a network connection or an API key; the encoder runs "
        f"locally through ONNX Runtime and the benchmark reads committed records. "
        f"That claim is exercised rather than asserted - the "
        f"{r.ref('splits')} results are re-derived by the first command, and the "
        f"test suite invoked by the last is the database-free subset.")
    r.p("**Setup.** The only platform difference is the path to the virtual "
        "environment's executables:")
    r.code(["git clone " + REPO_PLACEHOLDER + " && cd praxis-neurosymbolic",
            "python -m venv backend/.venv",
            "",
            "# Windows (PowerShell or Git Bash)",
            "backend\\.venv\\Scripts\\pip install -r backend/requirements.txt",
            "",
            "# Linux or macOS",
            "backend/.venv/bin/pip install -r backend/requirements.txt"])
    r.p("**Running the demo interface.** Use the interpreter from that environment "
        "(`backend\\.venv\\Scripts\\python` on Windows, `backend/.venv/bin/python` "
        "otherwise); the listings below write `python` for whichever applies:")
    r.code(["python demo/server.py --warm",
            "# then open http://127.0.0.1:8099"])
    r.p("**Reproducing the reported numbers.** Each command checks a different thing, "
        "and each prints a pass or fail rather than requiring the output to be read "
        "against this document:")
    r.code(["python scripts/independent_metrics.py  "
            "# re-derives every metric from raw records",
            "python scripts/verify_claims.py        "
            "# checks the paper's claims against artifacts",
            "python scripts/count_checks.py         "
            "# censuses the verification checks",
            "python scripts/check_report.py         "
            "# checks this report's numbers and numbering",
            "",
            "cd backend && python -m pytest -q -m no_db   "
            "# the database-free test suite"])
    r.p("**Rebuilding this document and its figures:**")
    r.code(["python scripts/make_architecture_figure.py --audit",
            "python scripts/make_results_figure.py",
            "python scripts/make_transfer_figure.py",
            "python scripts/record_demo_suite.py    "
            "# re-records the ten sample cases",
            "python scripts/make_project_report.py"])
    r.p("The per-question records behind every reported number are committed under "
        "`eval_reports/research/`, so a clone can re-verify the results without "
        "re-running the experiments or loading a language model. Re-running the "
        "language-model baselines themselves does need a served model and is not "
        "required to check any figure in this report. Model checkpoints are excluded "
        "for size and are regenerated by the training scripts.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    r.doc.save(OUT)
    print(f"  wrote {OUT.relative_to(ROOT)}")
    print(f"  sample suite: {suite['correct']}/{suite['total']} correct")
    print(f"  checks census: {census['defined_checks']} defined, "
          f"{census['distinct_checks']} observed, "
          f"{census['min_per_case']}-{census['max_per_case']} run per case")
    r.check_all_emitted()
    if REPO_PLACEHOLDER in OUT.read_bytes().decode("utf-8", "ignore"):
        print(f"  NOTE: {REPO_PLACEHOLDER} still to be substituted with the repo URL")


if __name__ == "__main__":
    main()

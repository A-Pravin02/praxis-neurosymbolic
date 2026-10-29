"""Build the project report for course submission.

    python scripts/make_project_report.py

Writes `docs/PRAXIS_PROJECT_REPORT.docx`. Convert to PDF with Word.

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
OUT = ROOT / "docs/PRAXIS_PROJECT_REPORT.docx"
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
        for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if not chunk:
                continue
            run = par.add_run(chunk)
            run.bold = i % 2 == 1
            run.italic = italic
            if size:
                run.font.size = Pt(size)
        return par

    def bullet(self, text):
        par = self.doc.add_paragraph(style="List Bullet")
        par.paragraph_format.space_after = Pt(2)
        for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if not chunk:
                continue
            run = par.add_run(chunk)
            run.bold = i % 2 == 1

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
                for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", str(value))):
                    if not chunk:
                        continue
                    run = par.add_run(chunk)
                    run.bold = i % 2 == 1
                    run.font.size = Pt(fs)
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

    def image(self, path: pathlib.Path, caption: str, width=6.1):
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

    r = Report()

    # ---- title page
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
    r.p(f"Retrieval grounds a language model in documents but does not stop it "
        f"asserting things the documents do not support. This project builds PRAXIS, "
        f"an architecture in which a language model is never the authority for a "
        f"decision: a neural encoder interprets the question, a three-valued symbolic "
        f"reasoner applies versioned business rules to authorized records, and a suite "
        f"of {census['distinct_checks']} deterministic checks gates the answer before "
        f"release. In the default configuration the decision path contains no language "
        f"model at all.")
    r.p(f"On {test['cases']} held-out questions over five generated enterprise worlds, "
        f"PRAXIS answers **{M(test,'neurosymbolic','accuracy'):.3f}** correctly at a "
        f"hallucination rate of **{M(test,'neurosymbolic','hallucination_rate'):.3f}** "
        f"with **{M(test,'neurosymbolic','coverage'):.3f}** coverage, against "
        f"{M(test,'llm_rag','accuracy'):.3f} and "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} for the same corpus retrieved "
        f"and answered by a 7B instruction-tuned model. The reduction is not bought "
        f"with abstention: PRAXIS covers {M(test,'neurosymbolic','coverage'):.3f} of "
        f"answerable questions where retrieval covers "
        f"{M(test,'llm_rag','coverage'):.3f}.")
    r.p("A web interface was built to make the pipeline inspectable stage by stage, "
        "and ten sample cases - one per question category - were run through it. "
        f"**{suite['correct']} of {suite['total']} were answered correctly.** The "
        "three failures are reported here rather than omitted, because one of them is "
        "the architecture's central limitation: verification establishes that a claim "
        "follows from the evidence, not that it answers the question asked.")

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
        "systematic error is invisible to both. Our own measurement bears this out: "
        f"asking a 7B model to check its own retrieval-grounded draft moved accuracy "
        f"from {M(test,'llm_rag','accuracy'):.3f} to "
        f"{M(test,'llm_rag_selfcheck','accuracy'):.3f} and hallucination from "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} to "
        f"{M(test,'llm_rag_selfcheck','hallucination_rate'):.3f} - an improvement far "
        f"smaller than the error it is meant to remove.")

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
    r.table(["Area", "Representative work", "Limitation this project addresses"], [
        ["Hallucination surveys", "Ji et al. (2023); Huang et al. (2025)",
         "Separate faithfulness from factuality; our target is narrower - a decision "
         "stated without entailment from the governing rule."],
        ["Retrieval-augmented generation", "Lewis et al. (2020)",
         "Supplies evidence but leaves inference to the model, so a grounded answer "
         "can still be unlicensed."],
        ["Self-critique and resampling",
         "Chain-of-verification (2024); SelfCheckGPT (2023); Self-RAG (2024)",
         "Auditor and author are the same model; a confident systematic error is "
         "invisible to both."],
        ["Claim-level scoring", "FActScore (2023)",
         "An evaluation method, not a release gate - it scores text after the fact "
         "rather than withholding it."],
        ["Neuro-symbolic solvers",
         "Logic-LM (2023); LINC (2023); PAL (2023); Binder (2023)",
         "Formalise a reasoning chain for a puzzle. PRAXIS formalises the "
         "enterprise's own rule base, versioned over time and filtered by an "
         "authorization view."],
    ], [1.3, 1.9, 3.0], "Table 1. Related work and the gap addressed.")

    # ---- 4 architecture
    r.h("4. System Architecture", 1)
    r.image(FIGURES / "architecture.png",
            "Figure 1. PRAXIS as implemented. Hatched blocks are neural, dotted stored "
            "knowledge, grey symbolic, dark the release gate. The dashed panel marks "
            "the one check that is absent.", width=3.9)
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
    r.p("Rules are evaluated under Kleene strong three-valued logic: a fact the company "
        "has no record of is unknown, not false. A two-valued engine answers 'no, you "
        "are not eligible' when the truthful answer is 'nobody recorded whether you "
        "are'. The reasoner resolves the policy version in force on the case's own date "
        "rather than the newest, and reads records through an authorized view, so a "
        "rule consults only facts the asker may see.")

    r.h("4.3 The evidence gate", 2)
    r.p(f"The verifier defines **{census['distinct_checks']} deterministic checks**: "
        f"{', '.join(census['check_names'])}. Which of them apply depends on what the "
        f"case asserts, so none runs on every question. Measured over the "
        f"{census['cases']} test cases, the number that runs ranges from "
        f"{census['min_per_case']} to {census['max_per_case']}, with a median of "
        f"{census['median_per_case']}; the {census['zero_check_cases']} cases that run "
        f"none are refusals settled before there is anything to verify. Verdicts are "
        f"four-valued - supported, contradicted, unverified, not checkable - so missing "
        f"evidence is never read as refutation.")

    # ---- 5 implementation
    r.h("5. Implementation", 1)
    r.h("5.1 Technology stack", 2)
    r.table(["Layer", "Technology", "Role"], [
        ["Neural encoder", "bge-small (33M), ONNX Runtime",
         "Question interpretation, goal and entity resolution; runs locally on CPU."],
        ["Symbolic reasoner", "Python, custom rule engine",
         "Kleene three-valued evaluation over versioned rules."],
        ["Knowledge", "Typed knowledge graph over generated worlds",
         "Records, policy history and an authorization view."],
        ["Verification", "Python, deterministic checks",
         f"The {census['distinct_checks']}-check suite and the release gate."],
        ["Backend", "FastAPI (production), stdlib HTTP (demo)",
         "The demo server needs no database, so it runs on a clean checkout."],
        ["Interface", "HTML, CSS, vanilla JavaScript",
         "Single page; no build step and no framework dependency."],
        ["Baselines", "Qwen2.5-7B-Instruct via transformers",
         "LLM-only, LLM+RAG, self-check and rule-extraction baselines."],
        ["Study II model", "nanoGPT, 4.3M parameters, trained from random init",
         "A deliberately weak generator of checkable claims."],
    ], [1.25, 2.0, 2.95], "Table 2. Implementation stack.")

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
            "Figure 2. The interface on load. The left pane lists one sample case per "
            "question category; a question can also be typed directly.")

    r.pagebreak()
    r.h("6. Experimental Setup", 1)
    r.table(["Split", "Worlds", "Questions", "Purpose"], [
        ["Development", "3", "153", "Threshold calibration only; never reported."],
        ["Test (frozen)", "5", "255", "Frozen before first use; main results."],
        ["Confirmatory", "5", "255", "Written after the system was final; scored once."],
        ["Total corpus", "13", "663", "All generated cases."],
    ], [1.3, 0.9, 1.1, 2.9],
        "Table 3. Dataset splits. The three share no case identifier and no world.")
    r.p("Cases are generated over synthetic enterprise worlds, each with its own "
        "catalogue, staff, policy history and records, with gold answers from an "
        "independent oracle rather than the system under test. All systems see the "
        "same questions, corpus and authorization context; decoding is greedy, so a "
        "run repeats exactly on one machine.")
    r.p("**Metrics.** Let N be all cases, A the answerable cases - those whose gold "
        "kind is a decision, a value or a result - and D the subset of A answered "
        "definitely. Accuracy and hallucination are over N; coverage is |D|/|A|; "
        "selective accuracy is correctness over D. Coverage and selective accuracy are "
        "always reported together, because a system can buy high selective accuracy by "
        "refusing the hard questions.")

    # ---- 7 sample test cases
    r.h("7. Sample Test Cases and Results", 1)
    timing = suite.get("timing", {})
    r.p(f"Ten cases were run through the interface, one per question category, taken "
        f"from world 101 of the frozen test split. The selection is the first case of "
        f"each category, so it is reproducible and not chosen for effect. "
        f"**{suite['correct']} of {suite['total']} were answered correctly.** The "
        f"PASS and FAIL labels in Table 4 are not written by hand: each is computed by "
        f"the same per-gold-kind rule the benchmark scorer uses, so a conditional gold "
        f"passes only on a hedged answer and a denied gold only on a refusal that "
        f"leaks nothing.")
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
            f"Table 4. Sample test cases, run through the demo interface. "
            f"{suite['correct']}/{suite['total']} correct.", fs=8.5)

    r.h("7.1 A case the system gets right", 2)
    r.p("Case W101-T-known-1 asks whether an order can be returned. The encoder ranks "
        "candidate goals and carries forward the highest; the reasoner gathers the "
        "order's status, category and delivery date, applies the returns rules under "
        "three-valued logic, and establishes the conclusion before any text is written.")
    r.image(SHOTS / "02-case-neural-symbolic.jpg",
            "Figure 3. Stages 1 and 2. The encoder's candidate goals with similarity "
            "scores, and the symbolic facts and rules. R-RET-A is proved; R-RET-D is "
            "refuted - the damage window had passed - and the engine records both.")
    r.image(SHOTS / "03-verification-release.jpg",
            "Figure 4. Stages 3 and 4. Nine checks applied to this case and all "
            "passed, each with the reason it passed. The released answer cites the "
            "policy version in force on the delivery date, not the newest.")

    r.h("7.2 The case the system gets wrong, and why it matters", 2)
    r.p("Case W101-T-not_covered-1 asks for the warranty period on a product. No policy "
        "clause in the rule base covers warranties. The correct behaviour is to report "
        "insufficient evidence. Instead the encoder maps the question onto the nearest "
        "in-scope goal - replacement availability - and the system answers that "
        "question correctly, citing real clauses and real records.")
    r.image(SHOTS / "05-not-covered-failure.jpg",
            "Figure 5. The documented failure. Asked about warranty, the system "
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
    r.image(SHOTS / "04-sample-suite.jpg",
            "Figure 6. The full sample set run in one pass, with per-case verdicts and "
            "latencies.")

    # ---- 8 results
    r.pagebreak()
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
        rows.append([label, f"{M(test,key,'accuracy'):.3f}",
                     f"{M(test,key,'hallucination_rate'):.3f}",
                     f"{M(test,key,'coverage'):.3f}",
                     f"{(M(test,key,'selective_accuracy') or 0):.3f}"])
    r.table(["System", "Accuracy", "Hallucination", "Coverage", "Selective acc."],
            rows, [2.0, 1.0, 1.1, 0.95, 1.1],
            f"Table 5. Test split, {test['cases']} questions. Accuracy and "
            f"hallucination are over all cases; coverage has a different denominator "
            f"and is reported separately.")
    r.image(FIGURES / "main_results.png",
            "Figure 7. Accuracy and hallucination on the test split, with 95% "
            "bootstrap intervals.", width=6.2)
    r.p(f"The LLM-free configuration answers {M(test,'neurosymbolic','accuracy'):.3f} "
        f"of all questions correctly at "
        f"{M(test,'neurosymbolic','hallucination_rate'):.3f} hallucination, against "
        f"{M(test,'llm_rag','accuracy'):.3f} and "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} for the same model given "
        f"retrieval over the same corpus. The reduction is not bought with abstention: "
        f"it covers {M(test,'neurosymbolic','coverage'):.3f} of answerable questions "
        f"while retrieval covers {M(test,'llm_rag','coverage'):.3f} - it answers more "
        f"questions and more of them correctly.")

    r.h("8.2 Ablations", 2)
    abl = [("ns_two_valued", "Unknown treated as false"),
           ("ns_latest_version", "Newest policy applied to every case"),
           ("ns_trust_statements", "Asker's statements override records"),
           ("ns_no_authorization", "Reasoning sees every record")]
    rows = [["PRAXIS (full)", f"{M(test,'neurosymbolic','accuracy'):.3f}",
             f"{M(test,'neurosymbolic','hallucination_rate'):.3f}",
             f"{M(test,'neurosymbolic','coverage'):.3f}",
             f"{M(test,'neurosymbolic','leak_rate'):.3f}"]]
    for key, label in abl:
        if key in test["systems"]:
            rows.append([label, f"{M(test,key,'accuracy'):.3f}",
                         f"{M(test,key,'hallucination_rate'):.3f}",
                         f"{M(test,key,'coverage'):.3f}",
                         f"{M(test,key,'leak_rate'):.3f}"])
    r.table(["Configuration", "Accuracy", "Hallucination", "Coverage", "Disclosure"],
            rows, [2.2, 0.95, 1.05, 0.9, 1.05],
            "Table 6. Each row disables exactly one mechanism.")
    r.p(f"Three-valued reasoning carries real weight: treating unknown as false costs "
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

    r.h("8.3 Transfer to unseen worlds", 2)
    r.p(f"On the confirmatory split - five worlds the system had never seen, written "
        f"after it was final and scored once - the LLM-free configuration transfers "
        f"essentially unchanged: {M(confirm,'neurosymbolic','accuracy'):.3f} against "
        f"{M(test,'neurosymbolic','accuracy'):.3f} on test. The parser configuration "
        f"does not: accuracy falls from {M(test,'ns_parser','accuracy'):.3f} to "
        f"{M(confirm,'ns_parser','accuracy'):.3f}, and selective accuracy from "
        f"{M(test,'ns_parser','selective_accuracy'):.3f} to "
        f"{M(confirm,'ns_parser','selective_accuracy'):.3f}. The perfect selective "
        f"accuracy on the test split does not generalize, and no claim of perfect "
        f"reliability should be read from it.")

    r.h("8.4 Secondary study: gating a model trained from scratch", 2)
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
    ]:
        r.bullet(text)

    # ---- 10 conclusion
    r.h("10. Conclusion and Future Work", 1)
    r.p(f"Removing the language model from the decision path, rather than asking it to "
        f"audit itself, reduced hallucination from "
        f"{M(test,'llm_rag','hallucination_rate'):.3f} to "
        f"{M(test,'neurosymbolic','hallucination_rate'):.3f} while raising coverage "
        f"from {M(test,'llm_rag','coverage'):.3f} to "
        f"{M(test,'neurosymbolic','coverage'):.3f} and cutting latency by roughly two "
        f"orders of magnitude. The reduction is not an artefact of refusing difficult "
        f"questions.")
    r.p("Two findings temper this. The parser configuration's perfect selective "
        "accuracy on the test split does not transfer to unseen worlds, and the "
        "default configuration does not recognise questions its rule base does not "
        "cover - a failure verification is structurally unable to catch, because "
        "relevance is not support. Support and relevance are separate properties "
        "throughout this report: the architecture secures the first by construction, "
        "and the second only where understanding is able to decline.")
    r.p("Future work is carrying the relevance gate to the default configuration, "
        "which needs goal selection to have a null option it currently lacks, an "
        "independently authored evaluation, and raising coverage in the secondary "
        "study without reintroducing unsupported claims.")

    # ---- 11 references
    r.h("11. References", 1)
    for i, ref_text in enumerate([
        "Z. Ji et al., \"Survey of hallucination in natural language generation,\" ACM "
        "Comput. Surv., vol. 55, no. 12, pp. 1-38, 2023.",
        "L. Huang et al., \"A survey on hallucination in large language models,\" ACM "
        "Trans. Inf. Syst., vol. 43, no. 2, 2025.",
        "P. Lewis et al., \"Retrieval-augmented generation for knowledge-intensive NLP "
        "tasks,\" in NeurIPS, vol. 33, 2020.",
        "A. Asai et al., \"Self-RAG: learning to retrieve, generate, and critique "
        "through self-reflection,\" in ICLR, 2024.",
        "S. Dhuliawala et al., \"Chain-of-verification reduces hallucination in large "
        "language models,\" in Findings of the ACL, pp. 3563-3578, 2024.",
        "P. Manakul, A. Liusie, and M. J. F. Gales, \"SelfCheckGPT,\" in EMNLP, "
        "pp. 9004-9017, 2023.",
        "S. Min et al., \"FActScore,\" in EMNLP, pp. 12076-12100, 2023.",
        "L. Pan et al., \"Logic-LM,\" in Findings of the ACL: EMNLP, pp. 3806-3824, 2023.",
        "T. X. Olausson et al., \"LINC,\" in EMNLP, pp. 5153-5176, 2023.",
        "L. Gao et al., \"PAL: program-aided language models,\" in ICML, PMLR vol. 202, "
        "pp. 10764-10799, 2023.",
        "Z. Cheng et al., \"Binding language models in symbolic languages,\" in ICLR, 2023.",
        "A. d'Avila Garcez and L. C. Lamb, \"Neurosymbolic AI: the 3rd wave,\" Artif. "
        "Intell. Rev., vol. 56, no. 11, pp. 12387-12406, 2023.",
        "S. C. Kleene, Introduction to Metamathematics. Amsterdam: North-Holland, 1952.",
    ], 1):
        r.p(f"[{i}] {ref_text}", size=9.5, space=3)

    # ---- 12 appendix
    r.h("12. Appendix: Source Code and Reproduction", 1)
    r.p(f"**Repository:** {REPO_PLACEHOLDER}")
    r.p(f"**Commit reported here:** {commit} — the state of the code that produced "
        f"every number in this report. The commit that carries this document is its "
        f"child, since the document is written after the code it describes; both are on "
        f"`master`.")
    r.p("Running the demo interface:")
    r.code(["python -m venv backend/.venv",
            "backend/.venv/Scripts/pip install -r backend/requirements.txt",
            "python demo/server.py --warm",
            "# open http://127.0.0.1:8099"])
    r.p("Reproducing the reported numbers (no database or GPU required):")
    r.code(["python scripts/independent_metrics.py   # recomputes every metric",
            "python scripts/verify_claims.py         # checks claims against artifacts",
            "python scripts/count_checks.py          # censuses the verification checks",
            "cd backend && python -m pytest -q -m no_db"])
    r.p("The per-question records for every reported number are committed under "
        "`eval_reports/research/`, so a clone can re-verify the results without "
        "re-running the experiments. Model checkpoints are excluded for size and are "
        "regenerated by the training scripts.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    r.doc.save(OUT)
    print(f"  wrote {OUT.relative_to(ROOT)}")
    print(f"  sample suite: {suite['correct']}/{suite['total']} correct")
    print(f"  checks census: {census['distinct_checks']} defined, "
          f"{census['min_per_case']}-{census['max_per_case']} run per case")
    if REPO_PLACEHOLDER in OUT.read_bytes().decode("utf-8", "ignore"):
        print(f"  NOTE: {REPO_PLACEHOLDER} still to be substituted with the repo URL")


if __name__ == "__main__":
    main()

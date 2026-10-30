# PRAXIS project report — revision changelog

Every change made in this revision, by section, with the evidence for it. The report
is built by `scripts/make_project_report.py`, so each entry points at a change in that
generator rather than at hand-editing of a document.

Base: the 12-page report at commit `34c76f4`. Result: 19 pages, 10 tables, 8 figures,
65 automated checks passing (`scripts/check_report.py`).

---

## Critical corrections

### C1. The architecture diagram said ten checks; the text said eleven; both were wrong

**Found:** `scripts/make_architecture_figure.py` drew the literal string *"ten checks"*
while Section 4.3 said eleven.

**Investigated rather than reconciled.** The verifier's source defines **twelve** check
names. The census over the frozen test split observes **eleven**. The twelfth,
*citation integrity*, is reached only by the enumeration pipeline, and none of the test
split's ten question categories reaches it. So neither of the two numbers already in
the document was right, and picking either would have been wrong.

**Changed:**
- `scripts/count_checks.py` now reads the defined names out of
  `backend/app/hybrid/verification.py` and records `defined_checks`, `defined_names`
  and `never_ran` alongside the observed counts.
- The diagram takes its number from that census instead of a literal, so the two
  cannot drift again.
- Section 4.3 states both numbers, names the check that never runs, and explains why.
- The IEEE paper was corrected the same way (it said "defines 11").
- `scripts/verify_claims.py` now checks the defined count *and* that the paper names
  the check no test question reaches.

### C2. The diagram showed the relevance check as simply absent

It is absent on the default encoder path and present on the parser path. Showing it as
wholly missing understates what is built; showing it as present would overstate it. The
dashed panel now reads *"relevance of goal to question: parser path only, not the
encoder"*, and the captions in both documents say the same.

### C3. Table 8 printed `0.000` for a quantity that does not exist

Extractive retrieval gives no definite answer on any of the 255 questions, so its
selective accuracy is undefined and the harness records null. The builder coerced that
to `0.000` with `or 0` — a measured zero where no measurement exists, in the direction
that flatters this project. It prints `n/a`, and `check_report.py` fails if an
undefined metric is ever rendered as a number.

### C4. Thirteen references, none cited

The survey named works by author and year while the reference list numbered them, so no
citation in the body pointed at any entry. Citations [1]–[11] now appear in the survey
table, [13] at the Kleene logic it refers to, and [12] in the introduction where the
point it supports is made. `check_report.py` fails on an uncited reference or a
dangling citation.

### C5. A template placeholder reached the page

One paragraph lost its `f` prefix in an earlier edit, so the PDF contained the literal
text `{r.ref('ui-failure')}` mid-sentence. Fixed, and `check_report.py` now fails on
any unrendered `{...}` placeholder, on raw `**` or `*` emphasis, and on raw backticks.

---

## Section by section

### Title page
Content unchanged — title, subtitle, both authors, institution, build date, source
commit and repository URL. The block was sitting in the top third of an otherwise empty
page; three leading blanks drop it to roughly the optical centre. No department,
registration number or logo was invented.

### 1. Abstract
Rewritten from three paragraphs to five, covering in order: the problem; why retrieval
is insufficient; the architecture, including the neural/symbolic division, three-valued
reasoning and the deterministic verifier; the experimental method; the quantitative
findings; and the limitations.

**The two configurations are now separated explicitly.** The default LLM-free
configuration's 0.796 is labelled as such, and the parser configuration's 0.945 is
introduced as "a separate configuration and is not the default's performance". The
abstract also now carries the confirmatory figures, so the perfect selective accuracy
cannot be read as a reliability claim.

### 2. Introduction
Added the four-way distinction the architecture turns on, as a bulleted list: generating
a plausible answer, retrieving supporting evidence, establishing a logically supported
conclusion, and verifying that the conclusion answers the question asked. The section
states which three the architecture addresses and which one it does not. Research
objectives preserved verbatim.

### 3. Literature survey
Table 1 preserved. Added **Table 2**, a dimension-by-dimension comparison across RAG,
self-critique, neuro-symbolic solvers and PRAXIS.

Written to be fair rather than favourable: it records that the neuro-symbolic solvers
genuinely do symbolic reasoning and that Self-RAG genuinely can decline a retrieved
passage. Its caption states that a blank is a matter of scope, not quality. The claim
made is only that no one of these methods combines a standing versioned rule base, an
authorization filter applied before reasoning, and a deterministic gate over released
text.

### 4. Architecture (Figure 1)
Figure corrected per C1 and C2 and redrawn. Labels now sit on an opaque patch of each
box's own fill, because the hatching and the dot fill were running through the type.
Audited at 400 px/in with every label at 6.0 pt or larger, and the placement width
checked against the figure's own assumption.

Caption rewritten to walk the reader down the pipeline in order: question, neural
understanding, evidence retrieval, policy-version resolution, authorized view,
three-valued reasoning, established conclusion, the checks, the release gate, release or
abstention.

### 4.3 Evidence gate
Expanded from one paragraph to a section with **Table 3**, which lists all twelve
checks, what each establishes, when it applies, and how many of the 255 test cases it
ran on — counts taken from the census, not asserted. The four verdicts are defined
individually, with the reason unverified is kept distinct from contradicted. The release
rule is stated explicitly, and the seventeen cases that run no check at all are kept in
view rather than excluded.

### 5.1 Technology stack
**Table 4** now carries exact versions, read from the machine at build time by the
report's own generator rather than typed in: Python 3.11.9, ONNX Runtime 1.19.2,
fastembed 0.4.2, FastAPI 0.115.6, SQLAlchemy 2.0.36, PyTorch 2.5.1+cu121, transformers
4.46.3. The encoder is named as BAAI bge-small-en-v1.5. The 7B baseline's serving path
is given correctly for the first time — `scripts/local_llm_server.py`, which wraps torch
and transformers behind an OpenAI-compatible HTTP API with a completion cache — where
the previous table said only "via transformers".

### 5.3 / 7. Screenshots
All five are the real interface; none was replaced or mocked up. The capture tool is
fixed at 800×550 and cannot be raised from here, so resolution was improved the only way
available: `scripts/crop_screenshots.py` removes the repeated sidebar and the title bar
from the four case screenshots, enlarging the panel that carries the argument by 1.25×
at the same printed width. Aspect ratio is preserved by python-docx; nothing is
stretched. The landing screenshot keeps its sidebar, since the two-pane layout is what
it shows.

### 6. Experimental setup
Expanded from half a page to five subsections.

- **6.1** how a world is generated, and the three generator decisions that determine
  what the benchmark can show: counterfactual policy values, boundary records, and
  independence of the oracle from the system under test.
- **6.2** how questions are constructed, the split of template wordings, the
  direction-of-goal convention, and what each of the ten categories probes.
- **6.3** the splits, now with seeds (11–13, 101–105, 201–205). Adds the caveat that
  the confirmatory split reuses the test split's wordings: new companies, not new
  phrasings.
- **6.4** how answers are judged, per gold kind, and the metric denominators.
- **6.5** execution environment and procedure, with **Table 6** recording the operating
  system, interpreter and library versions. The last row records that the serving device
  and any quantization for the language-model baselines were **not** captured in the run
  metadata — a limitation, written as one.

### 7. Sample cases
All ten retained with their actual outcomes; no PASS or FAIL was changed. Added an
explicit statement that these ten are a demonstration and the 255-question frozen split
is the evaluation, with the reason they are not comparable: one case per category
over-weights the rare categories, so a single failure moves this set by ten points and
the benchmark by four tenths of one.

### 7.1 / 7.2 Case studies
Both preserved. The successful case now points at Figures 3 and 4 for the four stages.
The warranty failure is preserved in full and its discussion strengthened: evidence
consistency, question relevance, factual correctness and decision correctness are
separated, and the reason deterministic evidence verification cannot supply relevance is
stated. The relevance gate built on the parser path is reported with its measured result
and with the limit that it inherits the parser's errors.

### 8. Results
Benchmark values unchanged and re-verified against the stored records. Added:

- **8.2 Statistical treatment** — the bootstrap is described concretely: 2,000 seeded
  resamples, percentile method, 2.5th and 97.5th percentiles, no normal approximation.
  **Table 9** gives point estimates with their intervals. The section states plainly
  that the sampling unit is the individual question and that questions within a world
  are not independent, so the intervals are narrower than a world-clustered analysis
  would give. No significance is claimed from non-overlapping intervals.
- **8.4 Transfer** — **Figure 8**, a new test-versus-confirmatory comparison for both
  configurations with their intervals, drawn only from verified values.
- Ablation discussion extended on why accuracy alone is insufficient, using the
  0.000 → 0.867 disclosure change.

### 9. Limitations
All eight preserved. Added the clustering limitation from 8.2, and a new **9.1 External
validity and deployment risk**: the rule base becomes a silent ceiling; coverage
collapses where rules cannot be written; relevance failures survive every check and are
more persuasive than unsupported guesses because they carry real citations. A fourth,
procedural risk notes that a dashboard showing only hallucination rate would read one
ablation as an improvement.

### 10. Conclusion
Rewritten to separate what is demonstrated from what is proposed, and to state why
symbolic reasoning matters, why authorization matters, and why coverage must accompany
every hallucination figure — each tied to a measured ablation rather than asserted.

### 11. References
Verified against publisher records (see `UNRESOLVED_ISSUES.md` for what could not be).
Shortened titles replaced with full ones, author lists corrected where the entry had
collapsed to "et al." unnecessarily, venues given in IEEE form, and DOIs added for every
entry that has one.

### 12. Appendix
Separate Windows and Linux/macOS setup commands; the tested environment named; the
reproduction commands grouped by what each one checks; commands added for rebuilding the
figures and the document. The no-database, no-GPU claim is retained because the commands
that demonstrate it were run on this machine and pass.

---

## Tooling added

| Script | What it does |
|---|---|
| `scripts/check_report.py` | 65 checks on the exported PDF: numbering, citations, provenance, every metric against `eval_reports/`, and the markup guards from C5 |
| `scripts/render_pdf_pages.py` | renders each page to PNG for visual inspection |
| `scripts/make_transfer_figure.py` | the test-versus-confirmatory figure |
| `scripts/crop_screenshots.py` | removes the repeated sidebar from the case screenshots |
| `scripts/record_demo_suite.py` | re-records the ten sample cases with honest cold and warm timings |
| `scripts/export_pdf.ps1` | Word export with an optional page limit (`-MaxPages`) |

The report generator also gained a **label registry**: tables and figures are declared
once in document order and numbered from that declaration, so a cross-reference cannot
disagree with a caption, and a declared item that is never captioned fails the build.

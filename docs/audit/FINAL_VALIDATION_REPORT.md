# PRAXIS project report — final validation

Three independent passes over `docs/PRAXIS_FINAL_REPORT.pdf`: an academic review, a
technical cross-check against the source and artifacts, and a page-by-page inspection of
the rendered PDF. Every automated result below was produced by running the command
named, on this machine, against the delivered document.

```
python scripts/independent_metrics.py   ->  151 values recomputed, 0 mismatched
python scripts/verify_claims.py         ->  26 claims substantiated
python scripts/check_report.py          ->  65 checks pass, 19 pages
cd backend && python -m pytest -q -m no_db  ->  143 passed, 366 deselected
powershell -File scripts/export_pdf.ps1 docs/paper/PRAXIS_IEEE.docx -MaxPages 6  ->  6 pages
```

---

# Review 1 — Academic evaluation

| Dimension | Assessment |
|---|---|
| Research problem | Sharply stated and non-trivial. The distinction between grounding and entailment is made early and carried through the whole document. |
| Novelty and contribution | The contribution is a division of authority, not a new model. Honestly positioned: Table 2 concedes that prior neuro-symbolic work does symbolic reasoning and that Self-RAG can decline. The gap claimed is narrow and defensible. |
| Literature survey | Thirteen references, all now cited in the body. Two tables: one on the gap addressed, one on capability dimensions. See Review 1 weaknesses. |
| Technical depth | Strong. Three-valued reasoning, policy versioning and authorization are each explained and each quantified by an ablation. |
| Architecture | Figure 1 now matches the implementation, including the twelve-check inventory and the path-specific relevance check. |
| Implementation | Versions read from the machine at build time rather than typed. Repository structure given. |
| Experimental methodology | The strongest part. World generation, question construction, oracle independence, split discipline and metric denominators are each explained with the reason the design choice was made. |
| Results | Every value traced to stored records. Two configurations kept apart throughout. |
| Discussion | Ablations are interpreted, including the one that looks like an improvement and is not. |
| Limitations | Eight limitations plus a deployment-risk section. The central failure is demonstrated rather than described. |
| References | IEEE form, DOIs where they exist, nine verified exactly against publisher records. |
| Presentation | 10 tables, 8 figures, consistent numbering, no stranded pages. |

### Remaining weaknesses

1. **Single-author evaluation.** The rule base, the world generator and the oracle were
   written by one person, so the oracle's independence is structural rather than
   social. Stated in Section 9; it is the single largest threat to the results.
2. **The 0.945 headline belongs to a configuration that does not transfer.** The report
   now says so in the abstract, Section 8.4 and the conclusion. A reader skimming only
   the results table could still carry away the wrong number.
3. **Table 2's finer attributions are unverified line by line** (see
   `UNRESOLVED_ISSUES.md` §2).
4. **Length.** 19 pages against a 12–15 guideline, accounted for in
   `UNRESOLVED_ISSUES.md` §8.
5. **Secondary study is a different regime.** A 4.3M model trained from scratch tests
   the gate under a weak generator; it says nothing about how a strong contemporary
   model behaves under the same gate. The report says this.

---

# Review 2 — Technical verification

Every number and technical claim was traced to its source. The method matters: 65 of the
checks are executed by `scripts/check_report.py` against the **exported PDF**, not
against the generator's intentions, so what is verified is what a reader receives.

### Traced and correct

| Claim | Source | Result |
|---|---|---|
| Every value in Tables 8 and 10 | `eval_reports/research/paper/test.report.json` | matches |
| Confirmatory accuracy and selective accuracy | `eval_reports/research/confirm/confirm.report.json` | matches (0.812 / 0.835, 0.889 / 0.909) |
| 95% intervals in Table 9 | the same reports' `ci95` fields | matches |
| Bootstrap description (2,000 resamples, seeded, percentile) | `backend/app/evaluation/research.py:226` and `BOOTSTRAP_SAMPLES` | matches |
| Split sizes and seeds | `backend/app/evaluation/worlds.py:44-53` | 11–13, 101–105, 201–205; 3+5+5 = 13 worlds, 663 cases |
| The twelve checks and their per-case counts | `artifacts/verification_checks.json`, written from the verifier's source and the stored traces | matches |
| Sample-suite score and timings | `artifacts/demo_suite.json` | 7/10; 1462 ms cold, 3.2 ms repeat |
| Library versions | read from the interpreter at build time | matches this machine |
| Source commit | `git cat-file -e` | exists on `master` |
| Repository URL | `git remote get-url origin` | matches |
| Study II model size | `artifacts/football/results/report.json` → 4,262,144 | 4.3M, matches |
| Every reproduction command in the appendix | executed as printed | all pass |

### Defects found and fixed during this pass

- **A measured zero that was never measured.** Table 8 printed `0.000` for extractive
  retrieval's selective accuracy, which is undefined. Now `n/a`.
- **The diagram and the text disagreed on the check count, and both were wrong.**
  Twelve defined, eleven observed; neither figure was in the document before.
- **A template placeholder printed literally** in the body text.
- **Raw markdown reached the page** — eight `*emphasis*` spans and 24 backticks.
- **The stack table misdescribed the 7B's serving path.**
- **Thirteen uncited references.**

### Consistency between the report and the IEEE paper

Both are built from the same artifacts. The paper was corrected in step for the check
inventory and the relevance panel, re-exported at **6 pages**, and re-verified: 26
claims substantiated. The architecture figure is shared, so a correction to it could not
land in one document and not the other.

---

# Review 3 — PDF quality assurance

Pages were rendered to images with `scripts/render_pdf_pages.py` and inspected. Text
extraction alone was not treated as sufficient, and that decision paid for itself twice:
a bullet that extracted as `�` renders correctly, while asterisks that extracted
normally were in fact printing as literal punctuation.

| Check | Result |
|---|---|
| Page count | 19, no blank pages |
| Section numbering | 1–12 sequential, no gaps or duplicates |
| Figure numbering | 1–8, each captioned exactly once |
| Table numbering | 1–10, each captioned exactly once |
| Cross-references | every one resolves; numbers issued from a single declaration |
| Margins and page size | A4, 0.95 in sides, 0.85 in top and bottom, consistent |
| Fonts | Calibri throughout, Consolas for code and file paths; no missing glyphs |
| Overlapping elements | none |
| Orphaned headings | none observed |
| Stranded pages | none; the two near-empty pages in the draft were caused by forced page breaks after full-width figures, and both breaks were removed |
| Figure legibility | architecture diagram audited at 6.0 pt minimum and 400 px/in; the data figures are vector-sourced; screenshots improved by cropping (see `UNRESOLVED_ISSUES.md` §3) |
| Table legibility | dense tables set at 8.5 pt, others at 9.5 pt; no cell overflow |
| Raw markup | none — asterisks, double asterisks, backticks and `{...}` placeholders are all checked for and absent |

### Fixed during this pass

- Two near-empty pages caused by forced section breaks after a figure.
- The transfer figure's legend overlapped a bar, and its value labels collided with the
  error-bar caps. Legend moved above the axes; labels moved above the interval caps.
- The architecture diagram's hatching and dot fill ran through the box labels; labels
  now sit on an opaque patch of each box's own fill.
- Cropped screenshots initially cut the application's title bar mid-word; the bar is now
  cropped away with the sidebar.

---

## Verdict

The document is internally consistent, every reported number traces to a committed
artifact, and the commands it offers a reader were run as printed. The known gaps are
listed in `UNRESOLVED_ISSUES.md` rather than smoothed over — in particular that the
parser configuration's headline does not transfer, that the verifier's correctness is
unmeasured, and that the confidence intervals ignore clustering by world.

The defects fixed in this revision were mostly of one kind: the document asserting
something slightly stronger or tidier than the artifacts supported. That is the failure
mode worth watching for in any further edits.

# Change log — final audit

## Abstract and figures (fifth pass)

| # | Change | Why |
|---|---|---|
| 30 | Abstract no longer says selective accuracy "falls to 0.909" after quoting two figures. It now reads: **accuracy falls to 0.835 and selective accuracy to 0.909**. | The sentence quoted 0.945 accuracy and 1.000 selective accuracy and then said "which falls to 0.909". The nearest antecedent was right, but a reader could take it as 0.945 → 0.909. The confirmatory split's accuracy is **0.835**; 0.909 is its selective accuracy. Both are now named. |
| 31 | **Fig. 1 redrawn.** It was a four-lane horizontal diagram authored at 7.6 in and placed in the paper at 3.1–3.85 in. | Its labels were specified at 6.2 pt in a 7.6 in figure, so on the page they printed at **2.5 pt** — present in the file, unreadable in the paper. This predates this audit; placing it single-column made it worse. Redrawn as a vertical single-column flow at the size it actually occupies, so every `fontsize` is the size it prints at. **Smallest label is now 6.0 pt.** |
| 32 | `make_architecture_figure.py --audit` added. | It reports the smallest printed label, checks each label against the width of its own box, and fails if the width the builder places the figure at does not match the width it was drawn for — the mismatch that caused the defect. |
| 33 | Fig. 1's in-figure legend removed; the shading key moved into the caption. | The caption prints at caption size and already described the shading. The legend was competing with the diagram for a row at 6.2 pt. |
| 34 | Fig. 1's caption now distinguishes the **three-valued** rule engine from the **four-valued** verification statuses. | They are separate layers and the caption conflated them by silence. |
| 35 | SVG emitted beside every figure PNG. | Word rasterises embedded images to about 200 dpi on export (measured: 200, 200 and 226 dpi for Figs. 1–3). The vector sources let the camera-ready substitute scalable art. |


## The accuracy numerator (fourth pass)

Section VI reported accuracy as 241/255 = 0.945 and then explained it with counts
that did not reach 241: **205 answerable + 6 not-covered is 211**. The thirty
conditional and denied cases the numerator includes were named in the breakdown of
N and never carried into the sum.

Recomputed from the 255 per-question records for the parser configuration:

| Gold kind | n | Correct | Rule in the scorer |
|---|---:|---:|---|
| Answerable (decision, value, result) | 210 | **205** | Answered and matching gold. All 205 definite answers correct → selective accuracy 1.000. |
| Conditional | 15 | **15** | Outcome conditional or clarification, **and not definite**. |
| Denied | 15 | **15** | Outcome denied **and no disclosure**. Disclosure rate is measured on this subset. |
| Not-covered | 15 | **6** | Outcome insufficient. Nine answered instead — the Section X failure. |

205 + 15 + 15 + 6 = **241**, matching the 241 records independently marked correct.
**The numerator was right; the explanation was incomplete.** Each group carries a
distinct correctness rule, so none is a free pass — a conditional case fails if the
system commits definitely, and a denied case fails if it leaks.

| # | Change | Why |
|---|---|---|
| 25 | Section VI now prints all four groups and their sum, every term counted from the records at build time. | The sentence stated two of four terms and then the total. The builder now refuses to emit arithmetic that does not close. |
| 26 | Correctness rules for conditional and denied cases stated in Section VI. | The paper defined the rule for not-covered cases only, so a reader could not tell whether the other thirty were scored on any criterion. |
| 27 | `scripts/independent_metrics.py` added; Section VI cites it. | `audit_metrics.py` mirrors `research.summarise`, so an error shared by harness and audit would pass both. The new script implements Section VI's definitions against the raw records instead. **151 values recomputed, 0 mismatched.** |
| 28 | Three checks added to `verify_claims.py` for the worked example. | No existing check would have caught this: every metric check passed throughout, because the metrics were correct and only the prose was wrong. 22 checks now, up from 19. |
| 29 | Fig. 1 changed from page-spanning to single-column. | At 3.4 in inside a 6.5 in row it left half the row empty, costing about twice the column-lines it needed. Single-column at 3.1 in is barely smaller and let Fig. 2 go back to 6.6 in. |

Dataset counts verified against the records: development 153 cases over worlds
11–13, test 255 over 101–105, confirmatory 255 over 201–205; 663 across 13 worlds;
510 evaluation questions. **The three splits share no case id and no world.**


Changes made during the final consistency audit. Each entry states what was wrong and
what evidence settled it. Nothing here was changed on suspicion alone.

## Cross-references, statistics and format (first pass)

| # | Change | Why |
|---|---|---|
| 1 | Broken cross-reference fixed: the conclusion cited "the capability effect reported in Section VIII". | That section was removed earlier (the 1.5B-vs-7B comparison ran across two inference stacks and so was not controlled). Section VIII is Ablation Studies, so the pointer resolved to the wrong content. Now points to Section XII, where the finding survives as a limitation. |
| 2 | Limitations bullet now cites **Section VIII-A** for the gate measurement. | The brief asks that the conclusion and limitations point at the subsection that measured the gate. |
| 3 | "References" moved from `Heading 1` to `Heading 5`. | `Heading 1` auto-numbers, so References printed as a numbered section. `Heading 5` is the style the IEEE template itself uses for References and Acknowledgment. Sections are now I–XIII with References unnumbered. |
| 4 | McNemar definition stated in Section VI. | The paper reported p-values without defining the test. It now states that concordant pairs are discarded and the discordant split is tested two-sided against Binomial(n, 1/2), using the exact tail rather than the chi-square approximation because the counts are small. |
| 5 | Verification-gate paragraph rewritten. | The earlier wording ("the ablations leave the gate's contribution unmeasured") could be read as contradicting the controlled experiment that follows. It now names both measurements: the ablation rows are decision-level and cannot see the gate by construction; the controlled test is text-level and measures what it changes. |
| 6 | Unsupported-figure wording corrected to "from four to six". | An earlier draft said the ablation "releases 2 unsupported figures", implying the gated arm had none. The true counts are 4 and 6. |

## Study II audit (second pass)

The first audit validated the enterprise study and the gate experiment numerically,
but checked Study II only by confirming its files existed. Every number in Study II's
prose was therefore unverified. Six were wrong.

| # | Change | Why |
|---|---|---|
| 7 | **Unverifiable claims are no longer counted as false.** The headline was 0.505; the refuted rate is **0.467**, with a further **0.038** unverifiable. | `report.json` stores `false_released` as every claim that is not `SUPPORTED`, folding the 40 `UNVERIFIED` claims in with the 493 refuted. Section XI-B states the opposite — a claim naming a player or match the graph has no record of is unverified *because* absence is not refutation — and contribution 2 advertises that distinction. The headline contradicted the principle the paper is built on. Study II statistics are now recomputed from `claims.jsonl`, the raw per-claim record, instead of read from the derived file. Coverage is unaffected (0.495): the gate withholds both. |
| 8 | "Twenty-one claims were refuted by rule alone" → **nineteen**. | 19 records carry a `rule_failures` entry, one each. The figure 21 matches nothing in the artifacts. |
| 9 | Extraction coverage 96.9% → **96.8%**. | 1056/1091 = 96.79%. |
| 10 | Status breakdown rewritten. | It read "1,056 yielded a typed claim (523 supported, 493 contradicted, 40 unverified, 35 unparseable)" — but the 35 unparseable sentences are the ones that did *not* yield a typed claim, so the parenthetical did not sum to its own subject. |
| 11 | "the commonest failure relocates a real goal to the wrong tournament" corrected. | Wrong tournament is 98 of 493 refutations. The largest group, 152, is a goal event not on record at the stated minute. Wrong-tournament is the next largest, and is now described as such. |
| 12 | Contribution 5 no longer claims a scale measurement. | It advertised "a scale measurement showing that the architecture converts model capability into accuracy far more effectively than the model alone does". That comparison was cut from the results for running across two inference stacks, and the limitations list it as an open question. The paper claimed and retracted the same result. Contribution 5 is now the error analysis alone; the count stays at six. |

Consequential edits: Table V gained separate **Refuted** and **Unverifiable** columns;
Table VI reports the refuted rate per claim type with unverifiable counted apart;
Fig. 3 stacks the unverifiable share above the refuted one so the bar still sums to
what the gate withholds; the held-out/seen rates become 0.480 and 0.458; and the
abstract, Section XI-D, the limitations bullet and the conclusion follow.

One paragraph was cut to hold six pages: the closing paragraph of Section VIII-A
restated the decision-level/text-level distinction a third time, after the opening
paragraph of that subsection and after "Yet every decision-level metric is identical
in both arms". Nothing in it was unique.

`scripts/verify_claims.py` grew eight checks that read the built `.docx` and compare
every Study II figure against `claims.jsonl`, including one asserting that no
"false" or "refuted" label is ever attached to the withheld share. It now runs 19
checks, up from 11. The 0.505 value still appears, correctly, as the share the gate
withholds — the defect was the label, not the number.

## Ablation-table audit (third pass)

Three of the seven ablation rows were reported without the second column that
explains them, and two of those read as refutations of the paper's own claims.
Every figure needed was already in `test.report.json`.

| # | Change | Why |
|---|---|---|
| 17 | Section VIII gained a paragraph reading the three rows that need a second column. | **Removing the kind-agreement check raised accuracy to 0.804, above the full system's 0.796.** A reviewer reads Table III and concludes a check is harmful. The paired test gives p = 0.625 on 4 discordant cases — noise. What the check buys is 0.012 hallucination for 0.019 coverage, and the paper now says so. |
| 18 | Same paragraph, second row. | **Trusting the asker's statements lowered hallucination to 0.094, well below 0.149.** Coverage falls 0.943 → 0.748 and accuracy drops significantly (p = 1.49e-08): the configuration abstains its way to a better hallucination rate. This is the exact failure the metrics section warns about, demonstrated inside the paper's own ablation table, and it went undiscussed. |
| 19 | Same paragraph, third row. | Removing the numeric grounding check changes nothing (p = 1), for the same reason the verification gate shows no decision-level effect — both police prose and the default configuration writes none. The gate's no-op got a subsection; the adjacent identical no-op got no mention. |
| 20 | Study II now quantifies the discarded fragments. | The paper said 300 trailing fragments were "discarded as a sampling artifact" without stating the share. It is 21.6% of generated sentences — but **0.89 per prompt against a fixed 80-token budget**, which is the evidence that it is one severed tail per generation rather than a selection among claims. That ratio is the defence and it was missing. |

Held at six pages by cutting repetition, not substance: the scope caveat was stated
four times (results preamble, a sentence under Fig. 2, the framing statement, the
limitations) and is now stated twice; the absent-model claim was in the abstract, the
introduction, Fig. 1's caption and Section IV, and is now in the first three; the
switch-reach paragraph was condensed, with the three layers enumerated in
`GATE_EVIDENCE.md` instead. The two page-spanning figures were reduced from 3.85 in
to 3.4 in and 6.8 in to 6.3 in, which recovered the last five lines.

## Earlier in this programme

| # | Change | Why |
|---|---|---|
| 21 | Three wrong author names corrected in `references.bib`: Rizzo→**Raileanu** (CoVe), Ellis→**Levy** (LINC), Yeh→**Yu** (Binder). | Verified against ACL Anthology and the ICLR listing. None reached the PDF, which cites all three with "et al.", but the bibliography attributed work to researchers who did not write it. |
| 22 | `no_verification` extended to reach all three guard layers. | It gated only the release decision. Two successive runs of the controlled ablation produced byte-identical arms, because the realizer's unsupported-figure check and the controller's re-verification each repaired what the ablated layer let through. |
| 23 | `config.py` literal extended with `"local"`. | The documented `LLM_PROVIDER=local` path raised a validation error on import, so the local-model baselines could not be run at all. |
| 24 | `parser_fallback` recorded per case. | 36% of `ns_parser` runs fall back to the embedding interpreter; that was visible only in stderr, so the score could be read as "the model's understanding" when a third of it was not. |

## Verification performed at audit time

- **151 published metrics** recomputed by `scripts/independent_metrics.py`, which
  implements Section VI's definitions against the raw records rather than reusing the
  harness summariser; **0 mismatched**.
- **55 metrics** recomputed by `scripts/audit_metrics.py`, which mirrors the harness;
  **0 mismatched**.
- **17 paired comparisons** recomputed from per-question predictions; **0 disagreed**
  (`docs/audit/STATISTICAL_VALIDATION.md`).
- **22 experimental claims** checked against code, tests and records; all
  substantiated (`scripts/verify_claims.py`).
- **128 database-free tests** pass. The remaining 376 need a PostgreSQL instance and
  are not reported as passing unrun.
- Gate experiment conditions confirmed: identical case ids, worlds and categories in
  both arms, systems differing only by the ablation flag.
- Dataset splits confirmed disjoint in both case ids and worlds.
- Final PDF inspected after the last edit: **15/15 structural checks pass** — 6 pages,
  A4, sections I–XIII, References unnumbered, Tables I–VI, Figs. 1–3, no duplicate
  numbering, no blank page, no leftover template text, every cross-reference resolving.

## Checked and found correct

- The confirmatory split's `neurosymbolic` vs `llm_rag` comparison gives 168 against 16, **identical to the test split**. This looks like copied data and is not: the two splits share no case ids and use different worlds, and both figures reconcile with their own accuracies (confirm 39 + 168 + 16 = 223 correct between them; test 35 + 168 + 16 = 219). A coincidence, verified rather than assumed.
- Section numbering was already sequential before this audit; the defect was that References was numbered at all.

## Reproducing

```
python scripts/audit_metrics.py --tag paper --split test
python scripts/independent_metrics.py
python scripts/verify_claims.py
python scripts/make_audit_reports.py
python scripts/build_paper_docx.py --template docs/paper/templates/ieee_a4_transitional.docx
```

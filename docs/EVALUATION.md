# Evaluation

## What is being compared

Four architectures, one language model, one corpus, one set of questions. Only
the machinery around the model changes, so a difference in the numbers is
attributable to the architecture rather than to a better model or a better prompt.

| Mode | Retrieval | Knowledge + rules | Verification + gate |
|---|---|---|---|
| `llm_only` | — | — | — |
| `llm_rag` | yes | — | — |
| `llm_rag_verify` | yes | — | yes |
| `neurosymbolic` | yes | yes | yes |

## Running it

```bash
python scripts/run_eval.py                      # all four modes, demo suite
python scripts/run_eval.py --modes llm_rag,neurosymbolic
python scripts/run_eval.py --limit 5            # a quick smoke run
```

Reports land in `eval_reports/` as JSON (every case, every answer) and Markdown
(the comparison table). The API equivalent is `POST /api/evaluation/run`, which
also persists runs to `eval_runs` / `eval_case_results` for the tenant.

Evaluation never mutates the workspace: each case runs in a transaction that is
rolled back.

## Metrics

| Metric | Definition |
|---|---|
| Factual accuracy | Cases expecting an answer where the stated value is right and no forbidden value appears |
| Symbolic accuracy | The subset requiring a derivation the rule engine must perform |
| Groundedness | Answers whose asserted numbers all appear in the retrieved evidence |
| Citation accuracy | Answers citing at least one gold document |
| Abstention accuracy | Cases where refusal is correct and the system refused |
| False abstention rate | Answerable cases the system refused anyway |
| Contradiction detection | Conflict cases where the disagreement was surfaced |
| Hallucination rate | Answers asserting a value with no support, or a forbidden value, or answering where refusal was correct |
| Unsupported claim rate | Claims marked weak/unsupported/contradicted, over all claims |
| Forbidden content rate | Answers containing text the case forbids (a leak) |
| Retrieval precision/recall | Cited documents against the gold document set |
| Latency | Mean and p95 wall-clock per case |

Groundedness is judged **by the harness**, identically for every mode. Reading
each mode's own internal grounding report would compare the modes against
different yardsticks, since only the full pipeline runs that check.

## Measured result on the demo suite

23 cases, `LLM_PROVIDER=mock`, `EMBEDDING_PROVIDER=hashing`. Reproduce with
`python scripts/run_eval.py`.

| Metric | llm_only | llm_rag | llm_rag_verify | neurosymbolic | better |
|---|---|---|---|---|---|
| Factual accuracy | 0.000 | 0.769 | 0.769 | **1.000** | higher |
| Symbolic reasoning accuracy | 0.000 | 0.600 | 0.600 | **1.000** | higher |
| Groundedness | 1.000 | 1.000 | 1.000 | 1.000 | higher |
| Citation accuracy | 0.000 | 0.609 | 0.609 | **0.636** | higher |
| Abstention accuracy | 1.000 | 1.000 | 1.000 | 1.000 | higher |
| Contradiction detection | 0.000 | 0.000 | 0.000 | **1.000** | higher |
| Retrieval precision | 0.000 | 0.387 | 0.387 | 0.387 | higher |
| Retrieval recall | 0.000 | 1.000 | 1.000 | 1.000 | higher |
| Hallucination rate | 0.000 | 0.130 | 0.130 | **0.000** | lower |
| Unsupported claim rate | 0.000 | 0.000 | 0.000 | 0.200 | lower |
| False abstention rate | 0.867 | 0.000 | 0.000 | 0.000 | lower |
| Forbidden content rate | 0.000 | 0.000 | 0.000 | 0.000 | lower |
| Mean latency (ms) | 0.0 | 10.7 | 11.8 | 21.5 | lower |
| p95 latency (ms) | 0.0 | 11.0 | 15.0 | 29.0 | lower |

### Reading these honestly

**What the numbers support.** On this corpus, the symbolic layer is what
separates 20 days from 15 days for two users asking the same question with the
same retrieved passages: symbolic accuracy 0.600 to 1.000. Contradiction
detection goes from 0.000 to 1.000, because retrieval alone has no mechanism for
noticing that two documents disagree. Measured hallucination goes from 0.130 to
0.000.

**What they do not support.** Several important caveats:

- **The suite is small (23 cases) and I wrote it against the corpus I also
  wrote.** It demonstrates behaviours; it is not an independent benchmark, and
  the absolute numbers should not be quoted as a general result.
- **`llm_only` shows 0.867 false abstention, not fabrication**, because the
  offline provider has a small glossary and refuses rather than inventing. With a
  cloud model, that row would show *hallucination* instead — the classic failure
  the architecture is designed to catch. Re-run the harness with your provider
  configured to see that comparison; the mock provider makes the architecture's
  contribution visible but understates what it is protecting against.
- **Retrieval precision of 0.387 is not a defect.** Precision is computed against
  the gold document set, and the retriever deliberately returns eight spans for
  the reranker and the verifier to filter. Recall of 1.000 is the number that
  matters at this stage: the right document is always in the candidate set.
- **`unsupported_claim_rate` is *higher* for the full pipeline (0.200).** That is
  the metric working as intended, not a regression: only the full pipeline
  *labels* claims as weak or contradicted. The ablations mark everything
  supported because they never check. A mode that does no verification will
  always look perfect on a metric about verification outcomes.
- **Latency doubles** (10.7 ms to 21.5 ms mean). That is the cost of knowledge
  loading, inference, verification and the grounding check, and it is
  proportionally larger here than it would be in production because the offline
  model responds instantly; with a real model, generation dominates.

## Measured result on the real dataset

278 generated cases over `merged_dataset_body_00.csv` (70 tables, 1.24 M rows),
`LLM_PROVIDER=mock`. Reproduce with:

```bash
python scripts/ingest_merged_dataset.py --csv path/to/merged_dataset_body_00.csv
python scripts/build_dataset_suite.py --csv path/to/merged_dataset_body_00.csv
python scripts/run_eval.py dataset --suite-file eval_suites/dataset_suite.json
```

Unlike the demo suite, the expected values here are **recomputed from the source
rows by arithmetic**, not chosen by whoever wrote the question.

| Metric | llm_only | llm_rag | llm_rag_verify | neurosymbolic | better |
|---|---|---|---|---|---|
| Factual accuracy | 0.000 | 0.384 | 0.359 | **0.793** | higher |
| Citation accuracy | n/a | 0.543 | 0.543 | **0.687** | higher |
| Abstention accuracy | 1.000 | 0.775 | 0.887 | **0.975** | higher |
| Groundedness | 1.000 | 1.000 | 1.000 | 1.000 | higher |
| Retrieval recall | n/a | 1.000 | 1.000 | 1.000 | higher |
| Retrieval precision | n/a | 0.159 | 0.159 | 0.148 | higher |
| Hallucination rate | 0.000 | 0.058 | 0.029 | **0.004** | lower |
| Unsupported claim rate | n/a | 0.000 | 0.251 | 0.043 | lower |
| False abstention rate | 1.000 | 0.369 | 0.495 | **0.187** | lower |
| Mean latency (ms) | 0.2 | 30.5 | 46.8 | 291.1 | lower |

Hallucination fell **0.058 to 0.004, a 93% relative reduction**, while false
abstention *also* fell (0.369 to 0.187). Both moving in the right direction at
once is the claim worth making: refusing more often is a trivial way to suppress
hallucination, and it would have shown up here as a worse false-abstention rate.

`n/a` means no case in the suite exercised that metric; the harness prints `n/a`
rather than 0.000 so an unmeasured metric is never read as a perfect score.

The full account — what the dataset is, why it is ingested as computed facts
rather than embedded rows, and the nine defects that only appeared on real data —
is in [DATASET_FINDINGS.md](DATASET_FINDINGS.md).

## The curated suite: holding ground truth fixed, varying the phrasing

The generated suite has unarguable expected values and five sentence templates.
That combination tests attribute lookup thoroughly and phrasing robustness not at
all, which is the fairest criticism of the benchmark — and templated questions
are exactly where a reviewer will push.

`scripts/build_curated_suite.py` isolates that variable. Each of its **74 cases
reuses a generated case's expected values, gold documents and expected kind, by
id**, and replaces only the question with a sentence a person would type:

| Generated | Curated |
|---|---|
| How many rows does the walmart dataset have? | How big is the walmart table? |
| What is the minimum price in the diamonds dataset? | What's the cheapest diamond in the diamonds data? |
| What is the mean MonthlyCharges in the telco customer churn dataset? | On the telco customer churn data — what do customers pay per month on average? |
| What is the maximum DC POWER in the Plant_1_Generation_Data dataset? | What was peak DC power at Plant_1_Generation_Data? |
| What is the mean net promoter score in the train dataset? | How does the train dataset score on net promoter? |

The rewrites deliberately vary what the templates hold constant: statistic words
(mean / average / typical / "what do X run"), superlatives (maximum / highest /
peak / most / ceiling), whether the word "dataset" appears at all, sentence shape
(question, imperative, possessive, embedded clause), and domain vocabulary
("cheapest diamond" rather than "minimum price").

**Why this is stronger than writing questions from scratch.** A hand-written
suite reintroduces exactly the objection the generated suite removed: the person
writing the questions also picks the answers. Here the answers are still
arithmetic over the source rows, no human chose them, and the only thing that
changed between the two suites is the English. So a score gap between them is
attributable to phrasing and nothing else.

24 of the 74 cases (32%) expect a refusal, split between an attribute the named
table simply lacks and one that exists in a *different* table.

```bash
python scripts/build_curated_suite.py
python scripts/run_eval.py curated --suite-file eval_suites/curated_suite.json
```

`tests/test_curated_suite.py` fails if a curated case's expected value ever drifts
from the generated case it borrows from, if a "rewrite" left the sentence
unchanged, or if the refusal cases fall below a quarter of the suite — the cases a
benchmark written to flatter a system quietly drops first.

## The suite

`backend/app/evaluation/cases.py`. Each case declares what a correct system does:

```python
EvalCase(
    id="sym-leave-junior",
    question="How many annual leave days am I entitled to?",
    user_email="bob@acme.test",
    expected_kind="symbolic",
    expected_values=[15.0], forbidden_values=[20.0, 18.0],
    gold_documents=["Acme Employee Handbook 2026"],
)
```

`expected_kind` is one of `grounded`, `symbolic`, `abstain`, `conflict`,
`denied`, `general`. Four cases exist where **refusal is the correct answer**, and
they are scored as passes when the system refuses — a system that answered them
would score worse, not better.

## Adding cases for your dataset

```python
from app.evaluation.cases import EvalCase, register_suite

register_suite("mydata", [
    EvalCase(id="q1", question="...", user_email="analyst@acme.test",
             expected_kind="grounded", expected_values=[42.0],
             gold_documents=["Some Document"]),
])
```

Then `python scripts/run_eval.py mydata`. Suites can also be loaded from JSON via
`load_suite_from_json`.

Write abstention and conflict cases first. They are the ones that catch a
regression toward confident guessing, and they are the ones a suite written to
make a system look good will quietly omit.

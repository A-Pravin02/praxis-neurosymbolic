# Findings from the real dataset

This records what happened when the platform met `merged_dataset_body_00.csv`
instead of the corpus it was developed against. It is written for the research
write-up, so it states the defects the real data exposed as prominently as the
results.

## What the dataset is

A meta-file with one row per *line* of some underlying file:

```
category,source_file,line_number,content
```

Unpacked, it is **70 datasets across six domains**, 1,237,584 rows, 125.6 M
characters of content:

| Domain | Rows | Share |
|---|---|---|
| Business / HR / Sales | 823,395 | 66.5% |
| Energy & Utilities | 247,821 | 20.0% |
| Economy & Finance | 110,851 | 9.0% |
| Entertainment | 24,283 | 2.0% |
| Agriculture | 21,938 | 1.8% |
| Education | 9,296 | 0.8% |

62 are tabular (Walmart weekly sales, HR promotion, solar plant telemetry,
diamonds, telco churn, house prices…) and 8 are text-oriented (IMDB reviews,
course catalogues).

## The decision that shaped everything else

**This is not a knowledge corpus. It is a collection of ML training tables.**

There is no prose to retrieve and cite. Embedding 1.24 M numeric rows would have
been expensive and useless: retrieving `143,69,217,5.9,0.58` as "evidence" answers
nothing and cites nothing a person can check.

So the adapter extracts what is genuinely *knowable* about a table:

| Artifact | Count | Role |
|---|---|---|
| Dataset cards | 70 | Retrievable prose describing each table — the citable evidence |
| Text corpora | 2 | Sampled excerpts from genuinely textual sources |
| Chunks | 725 | What retrieval searches |
| Computed facts | ~34,700 | Schema, statistics and entity records, each with provenance |

A statistic fact is *computed*, not extracted: `dataset:walmart.weekly_sales_max
= 693099.36` carries "Computed over 423,325 values of Weekly_Sales in
walmart.csv" as its provenance. That is a stronger guarantee than any text
extractor can offer, and it is why this dataset suits the architecture: the
symbolic layer holds values the retrieval layer could never state reliably.

Bounded by design: statistics accumulate incrementally, categorical counters cap
at 200 distinct values, row-level triples are produced only for tables under
2,000 rows and capped at 8,000 per table. Ingestion of the whole file takes about
four minutes and never holds more than a few hundred megabytes.

## Ground truth you cannot argue with

The demonstration suite was written by hand against a corpus also written by
hand — fine for showing behaviour, weak as evidence, because the author of the
questions chose the answers.

Tabular data removes that objection. `scripts/build_dataset_suite.py` recomputes
statistics from the source and emits **278 cases whose expected values come from
arithmetic over the real rows**:

- **198 answerable** — row counts, minima, maxima, means, per table.
- **80 where refusal is correct**, in two flavours:
  - *absent column*: a plausible attribute the table does not have.
  - *cross-dataset*: an attribute that exists in **some** table but not the one
    named. This is the trap plain retrieval falls into — the words match a real
    passage, so an answer appears, about the wrong table.

## Results

278 cases, four architectures, same model, same corpus
(`python scripts/run_eval.py dataset --suite-file eval_suites/dataset_suite.json`):

| Metric | llm_only | llm_rag | llm_rag_verify | **neurosymbolic** | better |
|---|---|---|---|---|---|
| Factual accuracy | 0.000 | 0.384 | 0.359 | **0.793** | higher |
| Citation accuracy | n/a | 0.543 | 0.543 | **0.687** | higher |
| Abstention accuracy | 1.000 | 0.775 | 0.887 | **0.975** | higher |
| Groundedness | 1.000 | 1.000 | 1.000 | 1.000 | higher |
| Retrieval recall | n/a | 1.000 | 1.000 | 1.000 | higher |
| Retrieval precision | n/a | 0.159 | 0.159 | 0.148 | higher |
| **Hallucination rate** | 0.000 | 0.058 | 0.029 | **0.004** | lower |
| Unsupported claim rate | n/a | 0.000 | 0.251 | 0.043 | lower |
| False abstention rate | 1.000 | 0.369 | 0.495 | **0.187** | lower |
| Mean latency (ms) | 0.2 | 30.5 | 46.8 | 291.1 | lower |

The full pipeline is best on every quality axis simultaneously — which is the
result worth having, because the easy way to cut hallucination is to refuse more
often, and that would have shown up as a *higher* false-abstention rate. Instead
both fell: hallucination 0.058 → 0.004 (a 93% relative reduction) while false
abstention fell 0.369 → 0.187.

`llm_only` scores 1.000 on abstention and 0.000 on hallucination because the
offline model refuses everything it has no glossary entry for. Its 1.000
false-abstention rate is the real story of that column. With a cloud model that
row would show fabrication instead — see the caveats in `docs/EVALUATION.md`.

Metrics reading `n/a` had no applicable case in this suite. There are no
contradiction or symbolic-derivation cases here, and reporting 0.000 for
something nothing measured would be misleading.

## Nine defects the real data exposed

Every one survived 294 passing tests against the demo corpus. This is the part of
the exercise with the most to say.

1. **Lexical search ANDed every term.** `plainto_tsquery` turns "how many rows
   does the walmart dataset have" into `'mani' & 'row' & 'walmart' & 'dataset'`;
   because no card contains "many", the lexical arm returned *nothing at all*.
   One incidental word disabled half the retriever. Now an OR query ranked by
   `ts_rank_cd`.

2. **Thousands separators broke numeric matching.** The card printed
   `423,325`; the extractor read it as 423 and 325; the fact held 423325; nothing
   ever matched, and the system abstained on a value it had computed itself.

3. **Rounding broke it again.** The fact kept `34.803915`, the card printed
   `34.8039`. Exact comparison rejects that. Fixed on both sides: statistics are
   stored at the precision the card prints, and agreement now means *one value is
   a correct rounding of the other*.

4. **Facts attached to a document were never loaded.** Only facts attached to a
   retrieved *chunk* were collected, so ingest-time statistics — which attach to
   the document — were invisible to the reasoner.

5. **Orphaned facts on re-ingest.** `Fact.source_document_id` is not a foreign
   key, so deleting a document left its facts behind. Re-ingesting produced two
   generations of the same fact at different precisions, which the contradiction
   detector then correctly reported as sources disagreeing — with itself.

6. **A repeating "id" column is not an identifier.** Keying entity triples on
   `MarketID` produced 174 facts sharing one subject and predicate, manufacturing
   contradictions out of a group label.

7. **Same-named tables collapsed.** `HR Data/train.csv` and `Sales DB/TRAIN.csv`
   both slugged to `train`, silently merging two unrelated tables' facts.

8. **A refusal that reported itself as an answer.** The informative "this dataset
   does not record that" message left `gate_decision` at `ANSWER_WITH_CAVEAT`.
   Every consumer — including the evaluation harness — read a refusal as a
   confident reply. This alone cost 0.65 of abstention accuracy.

9. **Cross-dataset leakage, in three forms.** "The median income in the walmart
   dataset" was answered from a supermarket table; "the Plant_1_Generation_Data
   dataset" matched a `plant_id` column because the *dataset name* leaked into
   *column* matching; "net promoter score" matched `avg_training_score` on the
   single shared word "score". Each is fluent, cited, and about the wrong thing.

## The controls added in response

- **Workspace-aware resolution** (`app/knowledge/resolver.py`). Rather than
  guessing what a question is about from a hand-written vocabulary, the system
  asks the workspace which subjects and predicates actually exist. A predicate
  that does not exist can never be resolved, so no plausible-sounding attribute
  can be invented. This is what makes the platform work on an arbitrary dataset
  without new code.
- **Attribute coverage floor.** A column must account for at least half of what
  was asked about, not merely share one common word with it.
- **Subject confinement.** When a question names a dataset, only that dataset's
  own sources may answer it. If it holds no matching attribute, the system says
  so by name rather than borrowing a similar column from elsewhere.
- **Evidence backfill.** When a computed fact is the answer, its own source
  passage is fetched into the evidence set — not to manufacture support, but so
  verification has the passage the value actually came from. If that passage does
  not contain the value, verification still fails.
- **Cross-source ambiguity.** Competing numbers from different documents for one
  value question are ambiguity, not an answer. The system lists them and asks
  which is meant.

All nine defects have permanent regression tests
(`tests/test_dataset_adapter.py`, plus additions to `tests/test_orchestrator.py`).
The suite is now **312 tests**.

## Honest limits of this result

- **The scope is enterprise QA over a known corpus**, and specifically
  structured factual questions. That is what the 278 cases are and what the
  numbers support. Open-ended natural-language questions are *not* tested here,
  and the result should not be generalised to them.
- **The suite is generated, not curated.** Its questions are templated, so they
  test attribute lookup over tabular statistics well and natural phrasing
  variety poorly.
- **Retrieval precision is 0.148** because the retriever deliberately returns
  eight candidates for the reranker and verifier to filter. Recall of 1.000 is
  the meaningful figure at that stage.
- **Latency is ~6× the RAG-only path** (291 ms vs 30 ms), dominated by
  resolution and knowledge loading. With a real model, generation would dominate
  instead and the ratio would compress.
- **Statistics are computed once, at ingest.** Nothing recomputes them if the
  underlying data changes; the fact carries the row count it was computed over.
- **Medians and percentiles use a sample** of up to 20,000 values per column.
  Minima, maxima, means and counts are exact.
- **The offline model understates the risk.** These numbers measure the
  architecture's contribution, not what a fluent model would do unaided. Re-run
  the harness with a cloud provider before quoting hallucination figures as
  general.

## Postscript: what the curated suite then exposed

The templated questions were the benchmark's weakest point, so
`scripts/build_curated_suite.py` rewrote 74 of the cases in natural phrasing
while reusing their machine-derived expected values by id (see
[EVALUATION.md](EVALUATION.md)). The first run was a bad result, and an
instructive one:

| | Generated | Curated (first run) |
|---|---|---|
| Factual accuracy | 0.657 | **0.280** |
| False abstention | 0.298 | **0.660** |
| Hallucination rate | 0.014 | **0.000** |

Accuracy more than halved on the same ground truth. Crucially it **failed in the
safe direction** — hallucination went to zero and abstention accuracy to 1.000.
The system did not start inventing answers when it stopped understanding the
question; it stopped answering.

Three more defects were behind it, and none was in the resolver, which mapped
"the highest fuel price" to `fuel_price_max` correctly all along.

10. **A computed statistic could not be VERIFIED.** Only inferred facts and user
    attributes had a path to that status, so a value the system had calculated
    from the source rows was capped at SUPPORTED and forfeited the gate's entire
    `0.30 × verified_share` term. It now verifies when the stated figure is the
    fact's value *and* that value appears in the retrieved evidence — the same
    check the precision bug (defect 3) broke.

11. **The relevance waiver was half-applied.** The gate waives its retrieval
    floor for a derived, supported answer, then scored confidence with a formula
    still weighted `0.22 ×` that waived relevance. The answer cleared the floor
    and died on the score. Both phrasings of the walmart fuel-price case
    abstained, so this was suppressing template questions too.

12. **Same-named tables were disambiguated by which one could answer.** "The
    train dataset" matches `HR Data/train.csv` and `Sales DB/TRAIN.csv` equally
    well; the resolver kept the first candidate yielding a matching column, which
    guarantees answering from the one table that *can* answer even when the reader
    meant the other. An exact tie in subject score is now reported as ambiguity,
    naming the candidates. An unequal match still resolves — "promotion" names
    `promotion.csv` better than `employee_promotion.csv`, and preferring the
    closer name is right there.

After the fixes, on the same 278 machine-derived cases:

| Metric | before | after |
|---|---|---|
| Factual accuracy | 0.657 | **0.793** |
| Abstention accuracy | 0.938 | **0.975** |
| Hallucination rate | 0.014 | **0.004** |
| False abstention rate | 0.298 | **0.187** |

Curated factual accuracy went 0.280 → 0.360, and its abstention accuracy is a
perfect 1.000 with hallucination at 0.000. It peaked at 0.460 *before* the
ambiguity fix, which traded some accuracy back for that perfect abstention rate.
That is the right trade: the cases given up are ones whose ground truth assumes
"train" means one particular train table, and declining to guess between two is
the behaviour this architecture exists to have.

**Read the post-fix numbers as in-sample.** They are measured on the suite that
motivated the fixes. The curated suite's own 0.360 remains the honest headline
for phrasing robustness, and a fresh, unseen phrasing set is needed before
treating any of it as a general result.

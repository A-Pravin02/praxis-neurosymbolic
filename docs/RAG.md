# Retrieval

```
source -> parse -> normalize -> chunk -> screen -> embed -> store
                                                      |
                                query -> hybrid search + authorization filter
                                                      |
                                                   rerank -> evidence spans
```

## Parsing

A `Parser` turns bytes into a `ParsedDocument` of heading-delimited sections and,
for tabular sources, structured `records`. Supported today: `.txt`, `.md`,
`.rst`, `.log`, `.pdf`, `.docx`, `.json`, `.jsonl`, `.csv`, `.tsv`.

Parsers know nothing about meaning. They find structure — headings (markdown,
underlined, numbered), pages, tables, columns — and stop there. Meaning is
supplied later by extractors and mappings, which is what makes the pipeline
dataset-agnostic: a new format needs a new `Parser`, not a new pipeline.

## Chunking

Sentence-aware, with list items kept whole because that is where policy values
live ("- Annual leave: 20 days"). Target 220 tokens, 40 tokens of overlap so a
value split across a boundary stays retrievable. Each chunk carries its section
heading and page, so a citation points at something a human can find, and short
sections are prefixed with their heading so the chunk is self-describing.

A single unit longer than twice the target is hard-split on words rather than
being allowed to dominate a chunk.

## Embeddings

The default is a deterministic feature-hashing embedder: word unigrams, word
bigrams and character 4-grams hashed into 384 signed dimensions with sublinear
term weighting, L2-normalised. BLAKE2b, not Python's randomised `hash`, so
vectors are stable across processes and machines.

It needs no model download, no network and no GPU, which is what lets the whole
platform — including the test suite and the benchmark — run offline and
reproducibly. It is a lexical-semantic hybrid, not a sentence transformer: it
captures term overlap and some morphology, not paraphrase. That limitation is
why retrieval is hybrid rather than purely dense.

Set `EMBEDDING_PROVIDER=openai` or `ollama` for real semantic embeddings.
Changing provider or dimension requires re-embedding the corpus
(`scripts/reindex.py`).

## Hybrid retrieval

Two independent searches, merged:

- **Dense.** `embedding <=> query` cosine distance via pgvector, ivfflat index.
- **Lexical.** PostgreSQL full text search over a generated `tsvector` column,
  ranked with `ts_rank_cd`. The column is `GENERATED ALWAYS`, so no application
  code can forget to update it.

Scores combine at `0.55 * vector + 0.45 * lexical` (configurable). Dense search
finds passages that share meaning; lexical search finds the exact policy term the
user typed. Neither alone is adequate on a small policy corpus.

## Authorization is inside the query

Every retrieval runs under three independent controls:

1. PostgreSQL RLS on the session's bound tenant;
2. an explicit `tenant_id ==` predicate in the query;
3. the compiled access-constraint predicate plus the document permission filter.

The third is worth spelling out: `required_permission IS NULL OR
required_permission IN (:held...)`, ANDed with every applicable
`AccessConstraint` compiled to bound SQL. A document the caller may not read is
not filtered out after retrieval — it is never a candidate, so it cannot
influence ranking, evidence relevance or the gate.

## Reranking

Retrieval score alone is a poor ordering, because a retriever always returns
*something*. The reranker adds question-term coverage and rarer-term weighting,
a bonus when the section heading matches, a penalty for very short spans, and a
65% demotion for quarantined spans. Ties break on document and chunk id, so the
ordering is deterministic — the same question gives the same evidence.

## Injection screening

Every chunk is scanned at ingest time for instruction-like content: override
phrasing, role assumption, system-prompt extraction, data exfiltration, policy
bypass, forged chat-template or envelope delimiters. Above 0.6 the chunk is
**quarantined**.

Quarantined does not mean hidden. The passage stays searchable and is shown in
the evidence panel with a warning, because concealing it from a user
investigating their own corpus would be worse. What it loses is influence: it is
demoted in ranking, capped at 40% support weight, never the sole support for a
claim, never extracted into a fact, and never quoted as an answer.

## Adding a dataset

See `docs/DATASET_INGESTION.md`. In short: `scripts/ingest_dataset.py <path>`
walks a directory, dispatches on file type, and — for structured records — maps
columns to predicates through a declarative `RecordMapping`. No schema is
assumed anywhere in the pipeline.

## Limits

- **ivfflat recall.** Approximate search with `lists=100`. Fine for the demo
  corpus; tune `lists`/`probes` or move to HNSW for large collections.
- **Fixed dimension.** 384 is baked into the column type. Changing it is a
  migration plus a re-embed.
- **English text analysis.** The `tsvector` uses the `english` configuration.
- **No cross-encoder.** The reranker is feature-based, not a trained model.
- **Whole documents are the permission unit.** There is no per-section
  authorization within a document.
- **No incremental re-embedding.** Editing a document means re-ingesting it.

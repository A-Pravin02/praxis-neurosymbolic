# Ingesting a dataset

The platform was built before any real dataset existed, so nothing in the
pipeline assumes a schema. Adding a dataset means supplying an adapter — usually
just a mapping file — not changing the architecture.

## The pipeline

```
file -> parse -> normalize -> chunk -> injection screen -> embed -> index
                     |
                     +-> structured records -> RecordMapping -> facts
                     +-> prose             -> extractors     -> facts
                                                             -> rule candidates
```

## First: profile it, do not ingest it

```bash
python scripts/ingest_dataset.py /path/to/data --profile
```

This reads nothing into the database. It reports file counts by type, which files
are record-oriented, what columns they have, and which files could not be parsed.
Decide what the data *is* before deciding what to do with it:

| The data is… | Treat it as… |
|---|---|
| Prose: policies, manuals, contracts | Retrieval corpus + conservative fact extraction |
| Records: CSV/JSON rows about entities | Structured facts via a `RecordMapping` |
| Question/answer pairs with known-good answers | **Evaluation cases**, not training data |
| Worked examples of reasoning | Candidate **rules**, reviewed by a human |
| Labels or ground truth | Evaluation, and possibly future fine-tuning |

A dataset is not automatically training data. The default path is retrieval plus
knowledge extraction plus symbolic reasoning, because that path is auditable and
reversible; fine-tuning is neither.

## Ingesting

```bash
# Prose
python scripts/ingest_dataset.py ./docs --tenant acme --as admin@acme.test \
  --collection policies --sensitivity internal --effective-date 2026-01-01

# Structured records
python scripts/ingest_dataset.py ./records.csv --tenant acme --as admin@acme.test \
  --mapping mapping.json

# Retrieval only, no fact extraction
python scripts/ingest_dataset.py ./corpus --tenant acme --no-facts
```

Also available as `POST /api/documents/upload` (multipart) and
`POST /api/documents/text` (JSON), both requiring `document:write`.

## Mapping structured records

```json
{
  "subject_field": "employee_id",
  "subject_prefix": "employee:",
  "predicate_fields": ["tenure_years", "department", "country"],
  "exclude_fields": ["internal_notes"],
  "rename": {"tenure_years": "years_of_service"},
  "units": {"tenure_years": "years"},
  "sensitivity": "internal",
  "required_permission": null
}
```

- `subject_field` — the column identifying the entity a row is about.
- `subject_prefix` — namespaces subjects so `employee:E-1002` cannot collide with
  a document subject.
- `rename` — maps your column names onto predicates rules already use. This is
  the important one: renaming `tenure_years` to `years_of_service` is what lets
  the existing rule `R12` fire on your data without touching the rule.
- `units` — attaches units so values render as "4 years", not "4".
- `sensitivity` / `required_permission` — the authorization labels every derived
  fact inherits.

Omit `--mapping` and the ingester infers one, taking the first id-like column as
the subject. Fine for exploration; specify a mapping for anything real.

## Extending the vocabulary

Free-text extraction only mints facts for predicates it knows, because predicates
invented from arbitrary noun phrases are the main source of junk facts. To teach
it your domain, add to `PREDICATE_HINTS` and `PREDICATE_LABELS` in
`backend/app/neuro/heuristics.py`:

```python
PREDICATE_HINTS["claim_settlement_days"] = (
    "claim settlement", "settlement window", "claim processing time",
)
PREDICATE_LABELS["claim_settlement_days"] = "claim settlement window"
```

Hints must contain the word that distinguishes the predicate. `"leave allowance"`
was removed from the annual-leave hints during development precisely because it
also matches *sick* leave allowance, and silently produced wrong facts.

For a domain needing more than patterns, register an extractor:

```python
from app.knowledge.extraction import register_extractor
register_extractor("insurance", my_extractor)
```

## Candidate rules

Conditional sentences are recognised and recorded as **candidates**, never
activated:

> "Employees with more than 2 years of service receive 20 days of annual leave."

becomes

```json
{"conditions": [{"predicate": "years_of_service", "op": "gt", "value": 2.0}],
 "conclusion": {"predicate": "annual_leave_days", "value": 20.0, "unit": "days"},
 "confidence": 0.65}
```

Candidates are stored on the document's metadata and reported by the ingester. A
human reviews them and creates real rules through `POST /api/rules`. Automatically
activating a rule mined from one sentence is exactly the confident error this
architecture exists to prevent.

## After ingesting: validate

```bash
# 1. Retrieval finds the right documents
curl -X POST localhost:8000/api/documents/search -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"query": "your domain term", "top_k": 5}'

# 2. Extraction produced sensible facts, and no nonsense ones
curl "localhost:8000/api/facts?limit=100" -H "authorization: Bearer $TOKEN"

# 3. Write evaluation cases, including ones that must abstain
#    backend/app/evaluation/cases.py -> register_suite("mydata", [...])

# 4. Benchmark all four architectures on your data
python scripts/run_eval.py mydata
```

Step 4 is the point of the exercise: it tells you whether the architecture
actually reduces unsupported generation *on your data*, rather than on the demo
corpus. If it does not, that is a finding worth having, and the report will show
which cases fail and how.

## Re-embedding

Changing `EMBEDDING_PROVIDER` or `EMBEDDING_DIMENSION` invalidates stored
vectors. Dimension is part of the column type, so a change needs a migration and
a re-embed. Provider changes need a re-embed only.

## Limits

- Files are ingested one at a time in a single process; a very large corpus wants
  a batching worker.
- Only the listed file types are supported; anything else is skipped with a
  message.
- There is no deduplication across ingests. Re-ingesting the same file creates a
  second document, which will then look like a contradiction with itself.
- Documents are versioned by `effective_date` and `version_label`, but there is
  no supersede-in-place operation: you ingest the new version and the conflict
  detector resolves by date.

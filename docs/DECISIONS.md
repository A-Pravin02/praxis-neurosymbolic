# Architecture Decision Records

Format: context → decision → consequences. Decisions are made autonomously; alternatives noted.

## ADR-001 — Backend: Python 3.11 + FastAPI + SQLAlchemy 2.0 + Pydantic v2
Async-capable, first-class OpenAPI, and the symbolic/RAG code is far cheaper to write in Python.
SQLAlchemy 2.0 typed ORM with Alembic migrations. Consequence: one language across API,
reasoning, ingestion and evaluation.

## ADR-002 — PostgreSQL 16 + pgvector as the only datastore
Rejected a separate vector DB (Qdrant/Chroma). Reason: tenant isolation is the hardest security
requirement here, and putting vectors in the same database lets **one** RLS mechanism protect
both documents and their embeddings. A separate vector store would need a second, independently
auditable isolation implementation. Consequence: recall at very large scale is bounded by
ivfflat; acceptable for MVP, and `VectorStore` is an interface so it can be swapped.

## ADR-003 — Deterministic offline default providers
`LLM_PROVIDER=mock` and `EMBEDDING_PROVIDER=hashing` are the defaults. The whole platform —
including E2E tests, evaluation, and `docker compose up` — runs with **no API key and no network**.
The mock LLM is a deterministic, template + extraction based responder, not a random stub: it
performs intent parsing by rules and composes answers strictly from the approved evidence packet.
Cloud providers (`openai`, `anthropic`) and local (`ollama`) implement the same Protocol.
Consequence: reproducible tests; the architecture's contribution is measurable independently of
model quality. Trade-off: prose quality with the mock provider is plain.

## ADR-004 — Hashing embeddings by default
Word/char n-gram hashing into 384 dims with sublinear TF and L2 norm. Deterministic, dependency
free, no download. Combined with lexical BM25-lite in a hybrid retriever, this gives usable
retrieval on the demo corpus. Real semantic embeddings are a config switch.

## ADR-005 — RLS as the primary isolation control, not the only one
Defence in depth: (1) Postgres RLS with `FORCE`, (2) mandatory `tenant_id` filter in the
repository layer, (3) PDP check before any resource read, (4) permanent tests that fail if the
app role is an owner/superuser/BYPASSRLS. Any one of these failing does not by itself leak data.

## ADR-006 — Constraints compiled, never evaluated as SQL
`AccessConstraint` is a small expression language parsed to an AST (fields whitelisted per
resource type), then compiled to SQLAlchemy `BinaryExpression`s with bound parameters.
Rejected: passing user strings to `text()`. Rejected: Python `eval`.

## ADR-007 — Symbolic engine written in-house (no Prolog/Datalog dependency)
Requirements are: JSON-serializable rules, per-tenant storage, provenance on every derived fact,
contradiction detection, and both forward and backward chaining. A semi-naive forward chainer
with a goal-directed backward prover over a function-free Horn-clause subset covers this in
~600 lines and keeps the trace structures we need for provenance. Consequence: no negation-as-
failure beyond stratified `absent()` guards; documented in SYMBOLIC_REASONING.md.

## ADR-008 — Three-category knowledge typing is enforced by the type system
`FactKind ∈ {DIRECT, INFERRED, MODEL_GENERATED}`. Serialization always includes the kind, the
gate treats them differently, and MODEL_GENERATED facts can never be cited as dataset facts.

## ADR-009 — Frontend: React 18 + TypeScript + Vite, plain CSS
Rejected Next.js: no SSR/SEO requirement here, and Vite gives a much smaller, faster, more
reliable Docker build (static bundle served by nginx) for an authenticated SPA. Rejected Tailwind
to avoid an extra build step; hand-written CSS with design tokens.

## ADR-010 — Streaming over SSE, not WebSockets
One-directional token streaming with plain `EventSource`-style semantics; works through nginx
with `X-Accel-Buffering: no`, simpler auth (bearer via fetch + ReadableStream).

## ADR-011 — Evidence gate is deterministic code, not a prompt
The decision to answer or abstain is made by scored predicates in `verification/gate.py`, never
by asking the model "are you sure?". A model cannot lower its own bar.

## ADR-012 — Prompt-injection defence is structural
Retrieved text is escaped, envelope-fenced, and never placed in the system role. A detector
flags imperative/override patterns; flagged spans are quarantined: excluded from the
instruction path, retained in the evidence path with a warning. Rejected: trying to "instruct
the model to ignore instructions" as the sole control.

## ADR-013 — Argon2id for password hashing
Memory-hard, current OWASP recommendation, `argon2-cffi` is pure-wheel and installs cleanly on
all platforms. Rejected bcrypt (72-byte truncation) and scrypt.

## ADR-014 — Refresh token family rotation with reuse detection
Refresh tokens are stored as SHA-256 hashes with a `family_id`. Using a rotated (already
consumed) token revokes the entire family — the standard OAuth 2.1 replay countermeasure.

## ADR-015 — Ingestion adapters, not an ingestion schema
`ingest_dataset.py <path>` sniffs types and dispatches to `Parser` implementations; structured
records go through a `RecordAdapter` that maps arbitrary columns to Entity/Fact triples via a
declarative mapping file. The future real dataset needs a mapping, not a redesign.

## ADR-016 — Synchronous SQLAlchemy, threadpool-offloaded endpoints
Setting `app.tenant_id` per connection for RLS is materially simpler and less error-prone with a
sync `Session` bound to one connection for the request's lifetime. FastAPI runs sync endpoints in
a threadpool; the workload here is dominated by embedding/reasoning CPU work, not connection
concurrency. SSE streaming uses a sync generator. Consequence: revisit if we need >~200 rps.

## ADR-017 — PyJWT + HS256 for access tokens
Symmetric signing, single service. Access tokens are short-lived (15 min) and carry
`sub, tid, roles, perms, jti, typ`. `typ` is checked so a refresh token can never be used as an
access token. Key rotation supported via `JWT_SECRET` + `JWT_SECRET_PREVIOUS`.

## ADR-018 — `user_lookup` routing table instead of an RLS bypass for login
RLS needs a tenant before it can filter, but login happens before a tenant is known. Rather than
grant the app role `BYPASSRLS` or add a `SECURITY DEFINER` escape hatch (both of which would
become permanent holes), a single global table maps lowercased email → (tenant_id, user_id). It
holds no credentials, no names, no profile data. Login reads it, binds the tenant, then loads the
user under RLS as normal. Consequence: email is globally unique across tenants. A constant-time
dummy verification runs when the email is unknown so responses do not reveal existence.

## ADR-019 — Refresh tokens carry a tenant routing prefix
Token format is `<tenant_hex>.<secret>`. The prefix is an untrusted routing hint used only to
bind an RLS tenant before the hashed-secret lookup; authorisation still comes entirely from the
stored row, and the row's `tenant_id` is re-checked. A forged prefix simply finds nothing.

## ADR-020 — Migrations are explicit DDL, never `metadata.create_all`
The initial migration originally called `Base.metadata.create_all`. That reads the
*current* models, so every later model addition was silently absorbed into revision
0001 — and the second migration then failed on a fresh database with "relation already
exists". Found by the clean-start validation, not by the test suite, which is itself a
lesson. Revision 0001 is now explicit `op.create_table` DDL, and
`test_migrations_match_the_models` diffs the migrated schema against the models so any
future drift fails a test instead of a deployment.

## ADR-021 — Claim relevance and claim support are separate scores
A sentence lifted verbatim from a retrieved document is trivially "supported" by that
document, which says nothing about whether it answers the question. Conflating the two
let an unrelated passage satisfy the evidence gate. `Claim.relevance` (does this answer
the question?) is now computed during selection and `Claim.support_score` (is this
backed by evidence?) during verification, and the gate consults both.

## ADR-022 — An unresolved conflict suppresses both values, not just the derived one
An earlier version suppressed the derived conclusion when sources disagreed but still
let a retrieved sentence stating one of the disputed values through, so the answer read
"The warranty period is 24 months… the sources disagree." A reader takes the first
number as the answer. Any claim stating a disputed value is now marked CONTRADICTED,
and the conflict report *is* the answer.

## ADR-023 — Temporal conflict resolution, with disclosure
When conflicting facts all carry distinct effective dates, the newest supersedes: a 2026
handbook replaces a 2019 one, and refusing to answer because a withdrawn document still
exists would make the system useless. The conflict is still reported, the superseded
value is still shown, and the severity drops to `warning` rather than disappearing.
Without an ordering signal there is no automatic resolution — a confidence difference is
evidence about the extractor, not about the world.

## ADR-024 — A permissive email type instead of `EmailStr`
Pydantic's `EmailStr` delegates to `email-validator`, which rejects reserved test domains
such as `.test` — exactly what a demo corpus and a test suite should use. Address syntax
is not a security control here; authentication is. A constrained string with a shape
check is the right trade-off.

## ADR-025 — Free-text extraction only mints known predicates
Deriving a predicate name from an arbitrary noun phrase produced facts like
`annual_leave_days = 10` from a sentence about *sick* leave, because a hint matched too
loosely. Text extraction now requires a match in `PREDICATE_HINTS` (longest hint wins),
skips sentences carrying qualifiers ("during", "target", "unless"), and treats
conditional sentences as rule candidates rather than facts. A missing fact is recoverable;
a wrong fact is treated as ground truth by the reasoning engine.

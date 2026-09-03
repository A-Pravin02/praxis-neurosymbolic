# Architecture

**Project:** Praxis — a neuro-symbolic, retrieval-augmented, evidence-gated conversational assistant.

**Thesis:** an LLM is a *language* engine, not a *truth* engine. Every factual claim that
reaches the user must be traceable to (a) a retrieved evidence span, (b) a structured fact, or
(c) a deterministic inference over facts and rules. Anything else is either labelled as
unverified model output or refused.

---

## 1. Request pipeline

```
USER
 │
 ▼  HTTP / SSE
WEB CLIENT (React + TS, streaming chat, evidence & reasoning panels)
 │
 ▼
API LAYER (FastAPI routers, Pydantic schemas, request-id middleware)
 │
 ▼
AUTH + TENANT CONTEXT  (JWT access token → Principal{user, tenant, roles, permissions})
 │                      Postgres session var app.tenant_id set per-request → RLS
 ▼
CONVERSATION MANAGER  (history, context-window budget, persistence, titles)
 │
 ▼
NEURAL UNDERSTANDING  (LLMProvider.parse_intent → structured IntentFrame)
 │
 ▼
TASK ROUTER  → DIRECT_RESPONSE | RAG_QUERY | SYMBOLIC_REASONING |
               RAG_PLUS_SYMBOLIC | POLICY_DECISION | CONTRADICTION_ANALYSIS
 │
 ├── RETRIEVAL       (hybrid: pgvector cosine + lexical BM25-lite, RLS-scoped, reranked)
 ├── KNOWLEDGE       (Entity / Fact triples with source, confidence, provenance)
 ├── SYMBOLIC ENGINE (forward chaining, backward goal proof, constraints, contradictions)
 └── POLICY PDP      (deterministic ALLOW / DENY / INDETERMINATE + obligations)
 │
 ▼
VERIFICATION ENGINE (claim ↔ evidence entailment-lite, citation support, consistency)
 │
 ▼
CONFIDENCE / EVIDENCE GATE  (thresholds → ANSWER | ANSWER_WITH_CAVEAT | ABSTAIN | DENY)
 │
 ▼
RESPONSE LLM (presents ONLY gate-approved content; cannot introduce new facts)
 │
 ▼
ANSWER + SOURCES + REASONING TRACE + VERIFICATION STATUS
```

The gate is *before* generation and *after* it: a post-generation grounding check
re-verifies the produced sentences against the approved evidence set and strips or
flags anything unsupported.

---

## 2. Module boundaries

| Package | Responsibility | Must not know about |
|---|---|---|
| `app.api` | HTTP shape only: routing, serialization, status codes | business rules |
| `app.auth` | passwords, tokens, rotation, revocation, sessions | tenants' data model |
| `app.tenancy` | tenant context, RLS session binding, role/permission resolution | LLMs |
| `app.policy` | PDP, constraint AST, safe SQL predicate compiler | HTTP, LLM |
| `app.rag` | parse → normalize → chunk → embed → store → retrieve → rerank | policy internals (receives a filter) |
| `app.knowledge` | Entity/Fact/Claim/Provenance CRUD + extraction from evidence | LLM prompting details |
| `app.symbolic` | rules, chaining, constraints, contradiction, proof trace | HTTP, DB session details (works on Fact DTOs) |
| `app.neuro` | LLMProvider abstraction, intent parsing, prompt assembly | DB |
| `app.verification` | claim/evidence checks, gate decision | generation |
| `app.orchestrator` | wires router → subsystems → gate → response | HTTP |
| `app.evaluation` | harness comparing 4 pipeline modes, metrics, reports | HTTP |

Dependency rule: `api → orchestrator → {neuro, rag, knowledge, symbolic, policy, verification} → db`.
No reverse imports. `symbolic` and `policy` are **pure** (no LLM, no network) — this is what
makes them auditable.

---

## 3. Data flow for a grounded answer

1. `IntentFrame` produced from NL: `{intent, entities[], target_attribute, desired_value, filters}`.
2. Router picks a plan (a list of stages) from the intent + heuristic signals.
3. Retrieval returns `EvidenceSpan[]` — each has `document_id, chunk_id, text, score, tenant_id`.
   Retrieval is executed under RLS *and* under a compiled `AccessConstraint` predicate.
4. Knowledge extraction turns spans + registered extractors into `Fact[]` (kind=DIRECT), each
   carrying `provenance.evidence_ids`.
5. Symbolic engine loads tenant `Rule[]` + the DIRECT facts + user profile facts, runs forward
   chaining to fixpoint, producing `Fact[]` (kind=INFERRED) with `derivation` steps.
6. Contradiction detector scans the fact set for functional-property violations.
7. Verification takes candidate `Claim[]` (from facts/inferences) and checks support.
8. Gate scores: `evidence_present ∧ evidence_relevant ∧ claim_supported ∧ no_open_contradiction`
   with thresholds → decision.
9. Response LLM receives a **closed world packet**: approved claims, citations, trace. Its
   system prompt forbids new factual content.
10. Post-generation grounding check; then persist `Answer` + `ProvenanceRecord`.

---

## 4. Security boundaries

- **Trust boundary 1 — network → API.** Everything from the client is untrusted, including
  `tenant_id`. Tenant is derived *only* from the verified access token.
- **Trust boundary 2 — DB.** The application connects as `praxis_app`, a least-privilege,
  non-owner role with `NOBYPASSRLS`. Every tenant-scoped table has `FORCE ROW LEVEL SECURITY`
  keyed on `current_setting('app.tenant_id')`.
- **Trust boundary 3 — retrieved content.** Document text is *data*. It is delivered to the
  model inside a fenced, escaped `<untrusted_document>` envelope with explicit instructions
  that the envelope contains no instructions, and it is scanned by an injection detector that
  marks spans `quarantined`. Quarantined spans never enter the prompt as instructions and are
  down-weighted in the gate.
- **Trust boundary 4 — symbolic rules.** Rules are structured JSON validated against a schema,
  never `eval`'d. Rule authorship requires `rule:write`.
- **Trust boundary 5 — constraints.** `AccessConstraint` expressions are parsed to an AST over
  a whitelist of fields/operators and compiled to parameterized SQLAlchemy predicates. No raw
  SQL from users, ever.

---

## 5. Database model (PostgreSQL 16 + pgvector)

Global: `tenants`, `users`(tenant-scoped), `roles`, `permissions`, `role_permissions`,
`user_roles`, `refresh_tokens`, `audit_events`.

Tenant-scoped (all carry `tenant_id` + RLS): `conversations`, `messages`, `documents`,
`chunks`(+`embedding vector(384)`), `entities`, `facts`, `rules`, `policies`,
`access_constraints`, `claims`, `provenance_records`, `answers`, `eval_runs`, `eval_cases`.

Indexes: `ivfflat` on `chunks.embedding` (cosine), GIN on `chunks.tsv` for lexical,
composite `(tenant_id, ...)` on every hot path.

## 6. Abstractions

```python
class LLMProvider(Protocol):
    def complete(req: CompletionRequest) -> Completion
    def stream(req: CompletionRequest) -> Iterator[Token]
    def parse_intent(text, context) -> IntentFrame
class EmbeddingProvider(Protocol):
    def embed(texts: list[str]) -> list[list[float]]
    dimension: int
class Retriever(Protocol):
    def retrieve(q: RetrievalQuery) -> list[EvidenceSpan]
class Reasoner(Protocol):
    def forward(facts, rules) -> InferenceResult
    def prove(goal, facts, rules) -> Proof | None
class PolicyDecisionPoint(Protocol):
    def decide(req: AccessRequest) -> Decision
class Verifier(Protocol):
    def verify(claims, evidence, inferences) -> VerificationReport
```

Providers are selected by env (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`) through a registry, so the
default deterministic offline providers can be swapped for cloud ones with no code change.

## 7. Evaluation architecture

`EvalCase{question, expected_kind, expected_answer|expected_abstain, gold_doc_ids, gold_facts}`
is run through four `PipelineMode`s (LLM_ONLY, LLM_RAG, LLM_RAG_VERIFY, NEUROSYMBOLIC) sharing
the same LLM, so differences are attributable to the architecture, not the model. Metrics are
computed from actual run artifacts and written to `eval_reports/`.

# Project 1 — Praxis: RAG + Neuro-Symbolic Pipeline Report

**Central claim:** hallucination is reduced because a language model is never the thing
that decides. It understands the question and it words the answer. Every fact, number and
yes/no in between comes from retrieval or from a rule engine, and an answer that cannot be
supported is withheld rather than produced.

All constants below are read from the code, not from memory. Sources:
`app/hybrid/router.py`, `app/verification/gate.py`, `app/verification/engine.py`,
`app/symbolic/reasoner.py`, `app/hybrid/controller.py`, `app/config.py`.

---

## 1. Input and output

**Input:** a natural-language question, plus the identity of the person asking
(`actor_email`, `actor_roles`). Identity is not decoration — it decides which records may
enter the reasoning at all.

**Output:** an `AssistantAnswer` carrying

| Field | Meaning |
|---|---|
| `answer` | the wording shown to the user |
| `pipeline` | which of A–G handled it, and why |
| `outcome` | answered / insufficient / clarification / denied |
| `citations` | clause ids and record ids the answer rests on |
| `trace` | every stage, with the layer (NEURAL / SYMBOLIC / HYBRID) that ran it |
| `confidence` | a number computed in code, with its factor breakdown |
| `verification` | per-claim status: VERIFIED / SUPPORTED / WEAK / CONTRADICTED |

---

## 2. The pipeline

```
QUERY
  ↓  NEURAL      encode question (BAAI/bge-small-en-v1.5, ONNX, 384-dim, offline)
  ↓  NEURAL      semantic parse into the closed schema (goal, inputs, clause)
  ↓  HYBRID      ROUTER — choose pipeline A–G  ........... §3
  ↓  SYMBOLIC    AUTHORIZATION — admit only permitted records  ... §4
  ↓  SYMBOLIC    GROUNDING — resolve the subject, verify stated facts
  ↓  SYMBOLIC    REASONER — three-valued proof over rules  ....... §5
  ↓  HYBRID      VERIFICATION — check every claim against evidence  §6
  ↓  HYBRID      GATE — answer / caveat / conflict / abstain / deny  §7
  ↓  NEURAL      REALIZER — word the verified conclusion only
FINAL VERIFIED ANSWER
```

The order matters and is a security property: **authorization runs before reasoning**, so
the symbolic engine only ever receives facts the asker is allowed to see. A system that
reasoned first and filtered afterwards would leak through its explanations.

---

## 3. Decision criteria — how a question is routed

Seven outcomes (`Pipeline`, router.py):

| | Pipeline | Used when |
|---|---|---|
| A | Knowledge retrieval | the answer is a policy clause or stored record |
| B | Policy reasoning | a yes/no the rules determine |
| C | Multi-hop reasoning | an explanation following relations to a failed condition |
| D | Calculation | an amount computed by rule arithmetic |
| E | Clarification | cannot be pinned to one goal or one record |
| F | Insufficient knowledge | **is** a company question, nothing covers it |
| G | General conversation | **not** a company question at all |

F and G are deliberately different. F says "your knowledge base does not cover this" —
honest, because guessing would invent policy. G answers as an ordinary assistant, uncited
and unverified, because there is no company policy to invent.

### Thresholds (router.py lines 47–54)

| Constant | Value | Role |
|---|---|---|
| `GOAL_ACCEPT` | 0.60 | goal accepted on similarity alone |
| `GOAL_SUPPORTED` | 0.55 | accepted if stated inputs or mentions corroborate |
| `GOAL_SUGGEST` | 0.45 | only good enough to offer as "did you mean" |
| `RETRIEVAL_ACCEPT` | 0.55 | clause relevant enough to answer an informational question |
| `TIE_WINDOW` | 0.03 | goals this close resolved by rule graph, not by score |
| `KIND_WINDOW` | 0.06 | a goal of the requested kind this close wins |
| `PARSED_CLAUSE_FLOOR` | 0.40 | floor for a clause the parser itself selected |

### Why symbolic rather than "normal LLM"

Not a heuristic — it follows from the **structure of the rule base** for the matched goal:

- goal whose rules **compute a value** → **D, Calculation**
- goal **concluded by rules** → **B or C, symbolic evaluation**
- goal with **no rules** → **A, lookup**
- **no goal matched, question is on-topic** → **F**
- **no goal matched, question is off-topic** → **G, plain LLM**

So the LLM-only path (G) is chosen only when the question has *no connection to the
company at all*. The test (`_off_topic`) is lexical and deliberately strict: it compares
the question's content words against the vocabulary of the knowledge pack itself — policy
text, goal descriptions, schema predicate and entity-type names. One shared content word
keeps the question in the company pipeline. Entity *labels* are excluded on purpose: whether
a customer's name appears in the graph must not change topicality, because topicality is
decided before authorization has admitted anything.

### Two symbolic checks on the neural interpretation

1. **Content-word floor.** A question with no content word cannot select a goal however
   close its embedding. ("How much?" scored 0.6 against a delivery-charge goal.)
2. **Kind agreement.** The goal's kind must match what was asked. "What's my
   reimbursement?" (amount) and "does my trip qualify?" (yes/no) sit hundredths apart in
   embedding space, and answering the wrong one produces a fluent, cited answer to a
   question nobody asked.

Near-ties (within `TIE_WINDOW`) are broken by the **rule graph** — prefer the goal the
question supplies evidence for, then the prerequisite over the goal depending on it —
never by a rounding difference in cosine similarity.

---

## 4. RAG parameters

| Parameter | Value | Note |
|---|---|---|
| `chunk_target_tokens` | 220 | |
| `chunk_overlap_tokens` | 40 | |
| `embedding_dimension` | 384 | |
| `retrieval_top_k` | 24 | first-pass recall |
| `rerank_top_k` | 8 | passed to verification |
| `embedding_provider` | **`hashing`** | ⚠ see below |
| encoder (neural layer) | BAAI/bge-small-en-v1.5, ONNX | offline, ~130 MB |

Retrieval is **hybrid**: pgvector cosine search + Postgres full-text lexical search, fused,
then reranked. Every span is authorization-filtered at the SQL level.

> ⚠ **Known defect.** `embedding_provider` defaults to `hashing` — feature-hashed word and
> character n-grams. It is lexical-semantic, not a sentence transformer, so "can I give
> this back?" cannot reach a chunk about "returning an item". The semantic ONNX encoder
> already exists in `app/neural/encoder.py` and measures 0.851 similarity on that pair
> against 0.453 for an unrelated one. **This is the root cause of weak RAG behaviour and is
> being fixed by routing document embeddings through the same encoder.**

Retrieved documents are treated as **untrusted data** — spans can be quarantined, and
quarantined spans are excluded from the usable evidence count.

---

## 5. The symbolic layer — three-valued logic

Classical rule engines are two-valued: a missing fact counts as false, which is why
rule-based bots say "no" to things they should be able to answer. This reasoner keeps three
outcomes apart (`reasoner.py`):

| Value | Meaning | Consequence |
|---|---|---|
| `satisfied` | the knowledge proves the condition | |
| `violated` | the knowledge refutes it | enables *why-not* explanations |
| `unknown` | nobody stated what it depends on | names the missing fact; enables conditional answers |

Goal results: `proved`, `refuted`, `conditional`.

**Open vs closed world.** A company record is closed — an order with no `delivered_on` was
not delivered. A hypothetical the user describes ("if I spent ₹8,750") is open — anything
unstated is unknown, not false. The reasoner is told which subjects are open.

**Inference confidence** (engine.py): `confidence = min(premise confidences) × rule.confidence`,
so a derived fact can never be more certain than the weakest premise it rests on.

**Contradictions** carry severity. An `error` (unresolved) conflict means *neither* value may
be asserted — stating one and mentioning the disagreement afterwards would let the reader
take the first number as the answer. A conflict resolved by version ordering permits the
newer value, and reports both.

---

## 6. Verification — per claim

Each claim in the drafted answer gets a status:

- **VERIFIED** — derived by the rule engine from verified premises, or from the user's own
  profile record, or a computed statistic whose figure both matches the fact's value *and*
  appears in the retrieved evidence.
- **SUPPORTED** — corroborated by evidence: `support = 0.35 × lexical + 0.65 × numeric`
  (a claim whose numbers do not appear in the span is multiplied by 0.4).
- **WEAK** — derivation premises not fully evidence-backed.
- **CONTRADICTED** — sources disagree, or the value was superseded by a newer source.

`evidence_relevance = 0.65 × best + 0.35 × mean(top)`.

A number in a claim must match a number in the evidence — this is the check that catches a
fluent sentence carrying an invented figure.

---

## 7. The gate — the abstention decision

**Made in ordinary code, never delegated to the model.** A model cannot be trusted to lower
its own bar, and asking it "are you sure?" measures nothing.

Hard stops, in order:
1. unresolved contradiction → `REPORT_CONFLICT`, confidence `min(0.5, 0.2 + 0.1 × verified)`
2. evidence required but none → `ABSTAIN`
3. `evidence_relevance < 0.18` → `ABSTAIN` *(waived when a conclusion was derived for the
   exact subject asked about and its own source passage supports it)*
4. no supported claim → `ABSTAIN`

### Confidence formula (`gate._confidence`)

```
confidence = 0.34·mean_support
           + 0.22·evidence_relevance
           + 0.14·min(1, best_retrieval_score)
           + 0.30·verified_share
           − 0.08·min(unsupported_claims, 4)

if symbolic_conclusion and verified:  confidence = max(confidence, 0.82)
clamped to [0.0, 0.97]
```

`verified_share = verified / supported`. When the relevance floor is waived, the 0.22
relevance weight is **redistributed proportionally** across the other three rather than
dropped, so it stays a weighted average and cannot inflate confidence.

Note the ceiling: **0.97, never 1.0.** The system does not claim certainty.

### Decision thresholds (config.py)

| Setting | Value |
|---|---|
| `evidence_relevance_threshold` | 0.18 |
| `claim_support_threshold` | 0.40 |
| `answer_confidence_threshold` | 0.55 |
| `caveat_confidence_threshold` | 0.40 |

`≥ 0.55` → **ANSWER**; `≥ 0.40` → **ANSWER_WITH_CAVEAT**; below → **ABSTAIN**.
A general-knowledge (G) answer is capped at **0.45** and always labelled, so it can never
be presented as grounded.

### A second confidence, for reasoned answers (`controller._confidence`)

```
score = 0.25·understanding + 0.10·stated_inputs + 0.35·symbolic_determinacy
      + 0.15·fact_support  + 0.15·verification
if the semantic encoder is unavailable:  score ×= 0.8
```

where `symbolic_determinacy` = 1.0 determined · 0.95 default · 0.7 conditional ·
0.3 unknown/absent · 0.2 conflict. **The symbolic term carries the largest weight (0.35)** —
the design states outright that determinacy of the proof matters more than similarity.

---

## 8. Where hallucination is actually prevented

| Failure mode | What stops it |
|---|---|
| Invented number | realizer only words the rule engine's computed value; claim numbers must appear in evidence |
| Fluent answer to the wrong question | kind-agreement check + rule-graph tie-breaking |
| Policy invented for an uncovered topic | pipeline F — says what is missing |
| Company answer to an off-topic question | pipeline G — answers plainly, uncited, capped at 0.45 |
| Asserting one of two conflicting values | unresolved contradiction blocks **both** |
| Stale policy value | version ordering; superseded values marked CONTRADICTED |
| Leaking records via explanations | authorization precedes reasoning |
| Missing fact treated as false | three-valued logic returns `unknown` and names it |

**Claim scope:** hallucinations are *reduced* in enterprise QA. They are not prevented or
solved, and this system never claims they are.

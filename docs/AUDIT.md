# PRAXIS — implementation audit

Audited against the code on branch `master`, not against the documentation.
Every number quoted here comes from a report file in `eval_reports/`, from a
script run during the audit, or from a line of source that is cited.

One proposed fix was refuted by experiment during this audit. That is recorded
in place rather than removed.

---

## 1. The actual architecture

There is no single pipeline. There are **two**, and which one answers a question
is decided by the tenant's slug:

```python
# backend/app/api/routers/chat.py:145
if ns_service.serves_tenant(db, principal):
    turn = ns_service.answer(...)                     # path A
else:
    turn = Orchestrator(db, principal).answer(...)    # path B
```

`serves_tenant` (`backend/app/hybrid/service.py:32`) returns true only when the
tenant's slug equals `settings.knowledge_pack_tenant`. **One workspace gets path
A. Every other workspace gets path B.** They do not share a router, a gate, or a
confidence formula.

### Path A — the neuro-symbolic controller (`app/hybrid/`)

Seven pipelines, chosen by `app/hybrid/router.py`:

| | Pipeline | Chosen when |
|---|---|---|
| A | Knowledge retrieval | the answer is a policy clause or stored record |
| B | Policy reasoning | a yes/no the rules determine |
| C | Multi-hop reasoning | an explanation tracing to a failed condition |
| D | Calculation | an amount computed by rule arithmetic |
| E | Clarification | the question pins to no single goal or record |
| F | Insufficient knowledge | a company question nothing covers |
| G | General conversation | not a company question at all |

The choice uses two neural signals (which goal the question is nearest, what kind
of answer is wanted) and one symbolic one: **the shape of the rule base for that
goal**. A goal whose rules compute a value routes to D; a goal with no rules
routes to A. That is read from the compiled rules, not from the question's
wording. This is a genuine design strength and it is implemented as described.

### Path B — the orchestrator (`app/orchestrator/`)

`router.py` maps an intent frame to a `Plan` of boolean stage flags (`retrieve`,
`extract_knowledge`, `reason`, `check_policy`, `verify`, `require_evidence`).
`pipeline.py::answer()` executes exactly that plan, each stage wrapped in a
`_Timer`:

```
understand → retrieve → knowledge → reason → evidence_backfill
           → verify → gate → generate → grounding_check
```

The mapping from intent to plan is pure code. A model supplies the intent frame
but cannot alter the plan, **so a model cannot talk the system into skipping
verification**. That property holds under inspection.

### What the request pipeline does NOT contain

The audit brief asks about several subsystems. These do not exist in the code,
and no finding below should be read as judging them:

- No tool registry, calculator tool, code execution, or external API calls.
- No text-to-SQL and no LLM-authored database queries.
- No long-term or cross-session memory.
- No multi-model routing; one provider is resolved per process
  (`app/neuro/registry.py`).
- No cost model, and **no aggregation of token usage anywhere**. Providers
  populate `prompt_tokens` / `completion_tokens` (`app/neuro/base.py:57`) and
  nothing reads them.

Arithmetic is not delegated to a tool because it is not delegated to a model
either — it is done by the rule engine's `_arith`
(`app/symbolic/engine.py:90`), which implements add/sub/mul/div/min/max/abs/
floor/ceil over the fact base.

### Separation of concerns

| Concern | Separated? | Evidence |
|---|---|---|
| Retrieval | yes | `app/rag/retriever.py`, independently scored |
| Reasoning | yes | `app/symbolic/engine.py`, no model involved |
| Verification | yes | `app/verification/engine.py`, computed not elicited |
| Decision | yes | `app/verification/gate.py`, ordinary code |
| Authorization | yes, and **before** reasoning | `app/policy/pdp.py` |
| Generation | **partially** | see finding 3 |

Generation is the weak seam. In the shipped evaluation configuration the answer
is produced by templates and the model is absent; in the product the model
phrases the answer. Those are different systems wearing the same name.

---

## 2. Is the symbolic layer real reasoning?

**Yes.** This was the audit's most sceptical question and the code answers it.

`app/symbolic/engine.py` implements:

- **Forward chaining to a fixpoint** (`forward`, line 327): iterates rules until
  no new fact is derived, with `MAX_ITERATIONS` and `MAX_DERIVED_FACTS` guards
  and a `truncated` flag rather than silent termination.
- **Unification with variable binding** (`unify_term`, line 143), including
  consistent rebinding of an already-bound variable.
- **Backward chaining** (`prove`, line 400) with a depth limit and cycle
  detection via a `seen` set, producing a `Proof` tree that flattens to ordered
  steps.
- **Stratification checking** at construction (line 323): a rule set with
  unstratified negation is rejected, not silently mis-evaluated.
- **Provenance propagation** (`_instantiate`, line 373): every derived fact
  carries its premise fact IDs, rule code, and the source document of its first
  premise. Confidence is `min(premise confidences) × rule confidence` — it
  degrades, it cannot be manufactured.
- **Contradiction detection** as a functional-property violation
  (`detect_contradictions`, line 562).

Critically: `MODEL_GENERATED` facts are excluded from contradiction detection and
from resolution (line 567). **A model's output can neither create nor settle a
conflict.** That is the difference between a reasoning component and a decorative
one.

**Can the symbolic layer reject an LLM claim?** Yes, and it is the only thing
that can. `check_grounding` (`app/verification/engine.py`) splits the generated
answer into sentences and checks every number against the union of approved
claims, evidence text, and system-derived provenance. `_post_check`
(`app/orchestrator/pipeline.py:1025`) **discards the whole answer** and abstains
when a number appears that no evidence contains.

The asymmetry is deliberate and worth stating plainly:

- unsupported **number** → answer destroyed, gate forced to ABSTAIN, confidence 0
- unsupported **prose** → warning appended, answer published anyway (line 1047)

---

## 3. Does PRAXIS beat a baseline? — yes, with one caveat that changes the claim

Measured on the 255-case generated test split, all systems on identical cases,
95% bootstrap intervals, exact McNemar for pairs
(`eval_reports/research/paper/test.report.md`):

| Metric | PRAXIS | llm_rag | llm_rag_rules | llm_only |
|---|---|---|---|---|
| Accuracy | **0.796** [0.75, 0.84] | 0.200 | 0.537 | 0.235 |
| Hallucination rate | **0.149** [0.11, 0.19] | 0.302 | 0.247 | 0.427 |
| Selective accuracy | **0.874** | 0.412 | 0.793 | 0.341 |
| Over-abstention | **0.057** | 0.538 | 0.219 | 0.400 |
| Share of stated numbers unsupported | **0.007** | 0.083 | 0.000 | 0.281 |
| Mean latency (ms) | **78** | 5814 | 12468 | 6971 |

PRAXIS vs `llm_rag`: correct-only-PRAXIS 168, correct-only-RAG 16,
**p = 3.8e-33**.

Replicated on a **confirmatory split of 255 cases over companies never used
during development** (`eval_reports/research/confirm/confirm.report.md`):
PRAXIS 0.812 [0.76, 0.86], llm_rag 0.216 [0.16, 0.27]. The headline result
replicates.

### The caveat

The system called `neurosymbolic` in every table above is built with
`realizer="template"` and `understanding="encoder"`
(`app/evaluation/systems.py:114`). It contains **no generative language model in
the answer path at all**. The mock provider is injected, and answers are produced
by templates from the derived facts.

So the honest statement of the result is:

> On the covered question types, replacing the language model with retrieval,
> rules and templates is 0.60 more accurate and 75× faster than using the
> language model. The advantage comes from **not using the model**, not from
> making the model better.

The "neuro" in the winning configuration is a sentence-embedding encoder used for
goal and clause matching (`app/neural/encoder.py:94`, falling back to feature
hashing) — not a generator.

The configurations that do use a model:

- `ns_parser` (model parses the question, templates phrase the answer): 0.945 on
  test — but **0.835 on the confirmatory split**, and the advantage over the
  encoder falls from p = 4.1e-10 to **p = 0.07**. That headline number was
  overfit, and the confirmatory split is what caught it.
- `neurosymbolic_llm` (model phrases the answer): 0.783 on a 60-case subset
  (`eval_reports/research/gate/`).

---

## 4. Component table

| Component | Exists | Correct | Used | Improves reliability | Severity of gap | Evidence |
|---|---|---|---|---|---|---|
| Query understanding | yes | partly | yes | yes | MEDIUM | `neural/semantic_parser.py`; overfit on test split |
| Risk/complexity classifier | **no** | — | — | — | LOW | no such module; routing is intent + rule shape |
| Router (7-way) | yes | partly | yes | yes | **CRITICAL** | `hybrid/router.py`; finding 1 |
| Retrieval (dense + lexical + rerank) | yes | yes | yes | yes | LOW | `rag/retriever.py:115,215` |
| Fact extraction | yes | yes | yes | yes | HIGH | `knowledge/extraction.py` — regex, English, per-domain |
| Symbolic engine | yes | yes | yes | yes | — | section 2 |
| Contradiction detection | yes | yes | yes | yes | LOW | `symbolic/engine.py:562` |
| Claim decomposition | yes | yes | yes | yes | LOW | `verification/engine.py` |
| Claim verification | yes | partly | yes | **unproven** | HIGH | finding 4 |
| Post-generation grounding | yes | yes | yes | yes (numbers only) | MEDIUM | `pipeline.py:1025` |
| Evidence gate / abstention | yes | yes | yes | yes | — | `verification/gate.py` |
| Confidence | yes | **computed, not calibrated** | yes | as an ordering | HIGH | finding 5 |
| Authorization (before reasoning) | yes | yes | yes | **yes, strongly** | — | below |
| Tenant isolation | yes | yes | yes | yes | LOW | RLS + explicit predicate + policy filter |
| Injection defence | yes | yes | yes | yes | MEDIUM | `rag/injection.py` |
| Temporal / version reasoning | yes | yes | yes | yes | LOW | effective-date supersession |
| Token / cost accounting | **no** | — | — | — | MEDIUM | nothing aggregates usage |
| Tool use / calculator / SQL | **no** | — | — | — | n/a | not in the code |
| Memory | **no** | — | — | — | n/a | not implemented |

### Authorization is the strongest measured result in the system

Ablating it (`ns_no_authorization`) on the confirmatory split:

| | PRAXIS | authorization removed |
|---|---|---|
| Disclosure rate on unauthorized questions | **0.000** | 0.867 |
| Accuracy, `unauthorized` category | **0.93** | 0.00 |

Exact McNemar **p = 1.2e-4**. The PDP (`app/policy/pdp.py`) is deny-overrides
with default-deny, tenant boundary checked first and not overridable, and
INDETERMINATE treated as denial. No model participates. **The symbolic engine
only ever receives authorized facts**, which is why removing the check changes
the answer rather than merely the wrapper.

### Tenant isolation

Three independent controls, each sufficient alone
(`app/rag/retriever.py:3`): PostgreSQL row-level security bound to the session's
tenant (`ENABLE` + `FORCE ROW LEVEL SECURITY` on every tenant-scoped table,
migration `0001`), an explicit `tenant_id` predicate in the query itself, and the
policy service's retrieval filter. Tenant identity comes only from the
authenticated `Principal` (`app/tenancy/context.py`) and never from the request
body.

### Injection resistance

Retrieved text is treated as data by construction. `neutralise()`
(`app/rag/injection.py`) is applied to **every** span, flagged or not: control
characters stripped, forged envelope delimiters and chat-template markers
defused, length capped. The 12-pattern detector is a secondary signal that
quarantines; a quarantined span is still shown to the user, is scaled to 0.4
support, and **may never be the sole support for a claim**. Structural defence
first, classifier second — the right order.

---

## 5. The ten things preventing PRAXIS from being reliable

### 1. CRITICAL — the system confidently answers a *different question* when asked something its rules do not cover

**Evidence.** `not_covered` category, test split: **accuracy 0.00, hallucination
0.87**. Replicated on the confirmatory split: 0.13 / 0.80.

Inspecting the 15 rows (`test.neurosymbolic.jsonl`), 13 of 15 were answered
definitively and none were correct:

| Asked | Answered |
|---|---|
| "Do you deliver order X to an address outside India?" | "Yes — your Brass Table Lamp order ships free: the ₹3,469 total meets the ₹1,499 free-delivery threshold." |
| "Can you gift-wrap order X before it ships?" | "No — your Cast Iron Skillet order can't be returned: the order has not been delivered yet." |
| "Is there a student discount on the product?" | "No — your Kashmiri Saffron order doesn't qualify for free delivery…" |

Each answer is internally impeccable: derived by the rule engine, traceable to
premises, numerically grounded, verified. It answers a question nobody asked.
**Every downstream guarantee holds and the output is still wrong**, because they
all measure "is the stated value supported" and none measures "is this the
question that was asked".

**The obvious fix was tested and refuted.** Sweeping `GOAL_ACCEPT` over the full
255-case test split during this audit:

| GOAL_ACCEPT | Accuracy | Hallucination | Over-abstention | `not_covered` accuracy |
|---|---|---|---|---|
| 0.60 *(shipped)* | 0.796 | 0.149 | 0.057 | **0.00** |
| 0.70 | 0.784 | 0.149 | 0.062 | **0.00** |
| 0.75 | 0.749 | 0.149 | 0.105 | **0.00** |
| 0.80 | 0.710 | 0.149 | 0.152 | **0.00** |
| 0.85 | 0.710 | 0.149 | 0.152 | **0.00** |

Raising the floor to 0.85 costs 8.6 points of overall accuracy and fixes **zero**
cases. It is not a threshold problem. "Deliver … order" really is near the
delivery goal; the match is correct and the conclusion is still wrong.

**Root cause.** The pipeline has no *closed-world check on the asked-for
attribute*. `KnowledgeProgram` declares its predicates, but nothing ever asks
"does the attribute this question is about exist in the program at all?"

**Fix.** After goal selection and before reasoning, extract the question's target
attribute and require it to resolve to a declared predicate, or to a synonym
registered for one. If it does not, route to pipeline F (insufficient knowledge)
regardless of how well the goal scored. The signal already exists: `ns_parser`
reaches 0.40 on this category precisely because the model sometimes emits an
attribute that maps to nothing.

**Location.** `app/hybrid/router.py::_choose_goal`, gated on
`app/symbolic/dsl.py::KnowledgeProgram`.

**Test.** `not_covered` accuracy on both the test and confirmatory splits.

**Success metric.** `not_covered` accuracy ≥ 0.70 with over-abstention on the
answerable categories held at ≤ 0.06 and overall accuracy not below 0.78.

**Note for honesty:** on this one category a plain RAG baseline is *better* than
PRAXIS — `llm_rag` scores 0.73, PRAXIS 0.00. It should be reported that way.

### 2. CRITICAL — two pipelines with different guarantees, selected by tenant slug

Every reliability property in this audit was measured on path A. Path B has a
different router, a different gate, and its own confidence formula, and it serves
every tenant that is not the knowledge-pack tenant. No benchmark in
`eval_reports/` covers path B.

**Fix.** Either promote one path and delete the other, or benchmark path B to the
same standard before any tenant is onboarded onto it.

### 3. HIGH — the headline configuration contains no language model

Section 3. This is not a defect in the code; it is a defect in how the result
would be *described* if the qualifier were dropped. Any statement of the form
"PRAXIS makes LLM outputs more reliable" is unsupported by these runs. What is
supported is "on covered questions, rules and templates beat the LLM".

**Fix.** Report `neurosymbolic`, `ns_parser` and `neurosymbolic_llm` as three
distinct systems in every table, and state which one any claim refers to.

### 4. HIGH — verification is a self-consistency check, and one arm of it is inert

`verify()` scores each claim against **the same spans that produced it**. There is
no independent source, no entailment model, no second retrieval. It reliably
catches a number that is absent from the evidence. It cannot catch a wrong
document confidently retrieved, and it cannot catch a correct number attached to
the wrong question — which is finding 1.

Measured: the `no_verification` ablation changed **0 of 255** cases. Re-run in the
one configuration where it could matter — the model phrasing the answer — it
changed **0 of 60** (`eval_reports/research/gate/`). An earlier explanation of
this null was tested during development and turned out to be wrong; the current
reading is that templates emit no ungrounded prose, so the branch never fires.

`no_number_check` also changed 0 of 255 — that path is dead in this
configuration.

**Fix.** Report the verification layer's contribution as *unmeasured in the
default configuration* rather than as a benefit, and design a case set where it
can fire.

### 5. HIGH — confidence is computed but not calibrated

`_confidence()` (`gate.py`) blends four measured signals with fixed weights
(support 0.34, relevance 0.22, retrieval 0.14, verified 0.30) minus 0.08 per
unsupported claim. It is reproducible and inspectable, and no scoring metric reads
it — both good.

But it is displayed to users as a percentage, and it is **not** a probability
(`docs/benchmark/calibration-test.md`):

| | value |
|---|---|
| ECE | **0.192** |
| MCE | 0.650 |
| Brier | 0.203 |
| Top bin (0.9–1.0), 42 cases | claims 0.959, scores **0.786** |

**Fix.** Either stop rendering a percentage and render the four bands the gate
already computes (ANSWER / CAVEAT / CONFLICT / ABSTAIN), or fit an isotonic
calibration on a held-out split and report post-calibration ECE.

### 6. HIGH — fact extraction is hand-written regex, per domain, English only

`app/knowledge/extraction.py` is pattern-based by deliberate design ("a wrong fact
is worse than a missing one"), and that judgement is right. But onboarding a new
domain means writing extractors. The second domain (`backend/data/harbour`)
exposed three assumptions that had been hard-coded for the first. Two domains is
enough to show the architecture generalises; it is not enough to claim it scales.

### 7. MEDIUM — nothing measures the token and cost claim

Efficiency is an objective of the project and there is no instrumentation for it.
Providers report usage; nothing sums it. Latency is measured well (per-stage
`_Timer`, in the reports); cost is not measured at all.

**Fix.** Sum `prompt_tokens` / `completion_tokens` onto the turn, persist them,
and add token columns to the benchmark tables. This is a small change and it would
substantiate the strongest practical argument the system has: 78 ms and zero
tokens against 5,814 ms and a full context window.

### 8. MEDIUM — the whole test suite requires PostgreSQL

437 tests. With Docker down, all 437 **error at setup**, none run — verified
during this audit. A session-scoped autouse `database` fixture runs an Alembic
upgrade before anything. The suite passed in full earlier with the stack up, so
this is a coupling problem, not a correctness one.

**Fix.** Mark the pure-logic tests (symbolic engine, verification, gate,
injection, chunker) so they run without a database. Those are exactly the modules
whose correctness the reliability claims rest on.

### 9. MEDIUM — the rate limiter is in-process

`app/core/ratelimit.py` holds its window in a module-level dict. With more than one
worker or instance, the effective limit multiplies by the worker count. Fine for
the demo; wrong under the deployment configuration in `render.yaml`.

### 10. MEDIUM — the soft categories are soft, and one threshold is unjustified

`paraphrased` 0.62 and `recommendation` 0.60 are where the remaining errors live.
Separately, the threshold sweep justifies `GOAL_ACCEPT` on 21 dev questions (where
one question is worth 0.048, so most of that table is noise) and explicitly **does
not** justify `RETRIEVAL_ACCEPT`, which the shipped report says "is still
hand-set". That admission is in the report and should stay there.

---

## 6. What disappears if the symbolic layer is replaced by LLM + RAG?

Measured, not argued. Every figure is PRAXIS vs `llm_rag` on the same 255 cases.

| Capability | Component | PRAXIS | LLM+RAG | Significance |
|---|---|---|---|---|
| Arithmetic over policy rules | `engine._arith`, pipeline D | **1.00** | 0.04 | — |
| Refusing a false premise | fact base contradicting the question | **0.90** | 0.00 | — |
| Applying the *version in force* | effective-date supersession | **0.88** | 0.52 | ablation p = 0.0039 |
| Withholding unauthorized data | PDP before reasoning | **1.00** | 0.00 | ablation p = 1.2e-4 |
| Knowing a required input is missing | three-valued logic | **1.00** | 0.00 | ablation p = 0.002 |
| Multi-hop with every hop verified | forward chaining + provenance | **1.00** | 0.50 | — |
| Not stating unsupported numbers | grounding check | 0.007 share | 0.083 | — |
| Answering at all | — | coverage 0.943 | 0.462 | — |

Four ablations move the result with p < 0.01, so this is not one component
carrying the system. The three-valued logic result is the cleanest single
demonstration: collapsing "unknown" into "false" takes the `missing_info` category
from 1.00 to 0.00 and nothing else changes.

**And what does not disappear:** on `not_covered`, LLM+RAG scores 0.73 and PRAXIS
scores 0.00. On latency and cost PRAXIS wins by ~75×, but only in the
configuration that does not call a model. Both belong in any honest comparison.

---

## 7. Roadmap

Ordered by what the measurements say, not by a generic phase list.

**Phase 1 — close the not-covered hole.** `hybrid/router.py`, `symbolic/dsl.py`.
Closed-world check on the target attribute. Metric: `not_covered` ≥ 0.70 on both
splits, over-abstention ≤ 0.06, overall accuracy ≥ 0.78. *This is the one change
that most changes what the system can honestly claim.*

**Phase 2 — resolve the two pipelines.** Benchmark path B or retire it. Metric: a
report in `eval_reports/` covering whichever path serves a non-demo tenant.

**Phase 3 — calibrate or replace the confidence number.** Isotonic fit on a
held-out split, or drop the percentage for the four bands. Metric: post-fit
ECE ≤ 0.05, or the percentage gone from the UI.

**Phase 4 — instrument tokens and cost.** `neuro/base.py` → turn → benchmark
tables. Metric: a tokens column in the test-split report.

**Phase 5 — give verification a case set where it can fire.** Metric: an ablation
on the verification layer with a non-zero delta, or a written statement that it is
redundant in the template configuration.

**Phase 6 — split the test suite.** Metric: the symbolic, verification, gate and
injection tests run green with no database.

**Phase 7 — third domain.** Metric: a third pack benchmarked without changes to
`app/` code. That, and only that, substantiates the generality claim.

---

## 8. The ultimate question

> Can PRAXIS demonstrably reduce hallucinations and improve reliability compared
> with a strong LLM + RAG baseline, at acceptable latency and cost?

**Yes, on the question types it covers, and the result replicates on unseen
companies.** Hallucination 0.149 vs 0.302; accuracy 0.796 vs 0.200; p = 3.8e-33;
78 ms vs 5,814 ms. Four separate components are each shown to contribute by
ablation.

**Three qualifications must travel with that sentence:**

1. The winning configuration replaces the language model rather than improving it.
   The claim is about an architecture, not about making an LLM better.
2. On questions outside the rule base the system is **worse than the baseline** —
   0.00 against 0.73 — and it fails confidently, which is the worst way to fail.
   Finding 1 is the fix and it has not been implemented.
3. Two domains, generated cases, one 7B model. The confirmatory split already
   demonstrated that this evaluation can catch an overfit headline number, by
   catching one.

Hallucinations are reduced here, in enterprise question answering, on covered
questions. They are not prevented and not solved.

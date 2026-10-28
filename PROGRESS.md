# PROGRESS

Working log for the PRAXIS research programme. Every number here is either measured on
this machine and linked to its source record, or marked `UNVERIFIED`. Nothing is
estimated and then written down as a result.

Machine of record: Windows 11, Python 3.11.9, 12 CPU cores, NVIDIA RTX 4050 Laptop
(6141 MiB VRAM, driver 592.82), 108 GB free disk, torch 2.5.1+cu121 (CUDA available).

---

## Phase 1 — Repository audit

**Status: complete.** Full findings in [`docs/RESEARCH_AUDIT.md`](docs/RESEARCH_AUDIT.md).

### Repository identification

The task named "the existing PRAXIS AI repository". Three candidates existed on this
machine:

| Path | What it actually is | Verdict |
|---|---|---|
| `Desktop/praxisvit` | Python/FastAPI + React neuro-symbolic decision copilot, 167 `.py` files, three-valued reasoner, claim verification, ablation benchmark, LaTeX manuscript | **This is the repository.** Work proceeds here |
| `Desktop/PRAXIS-AI` | TypeScript monorepo: agent payments, policy engine, audit, threat analyzer. Carries the name, matches none of the research spec | Not used |
| `Desktop/neurosymbolic-kbqa` | Separate smaller KBQA project, 21 `.py` files | Not used |

`praxisvit` was selected on evidence, not on the folder name: it contains the
three-valued rule engine, `SUPPORTED`/`CONTRADICTED`-style claim verification,
`app/hybrid/ablations.py`, frozen benchmark splits, and
`docs/paper/{main.tex,references.bib}` — i.e. the exact component list the
specification asks to extend.

### What already works (verified by running it)

- **Full suite: 440 passed, 0 failed, in 9m32s** against the provisioned PostgreSQL.
  This is the baseline the programme must not regress.
- **Pure-logic core: 64 tests pass** with no database at all
  (`pytest -m no_db` → `64 passed, 376 deselected`), covering the symbolic reasoner
  and the neural understanding layer.
- Three-valued reasoning, fact grounding, a ten-check verification gate, knowledge-gap
  classification, policy versioning and an authorized-view graph are all implemented in
  `backend/app/{symbolic,hybrid,neural}`.
- Four frozen benchmark splits exist and were committed before first use:
  `dev.yaml` (27), `test.yaml` (86), `holdout.yaml` (40), `copilot.yaml` (53).
- An honest research document, `docs/NEURO_SYMBOLIC_ARCHITECTURE.md`, which already
  states its own gaps — including that the LLM baselines were never run.

### The central gap this programme must close

`docs/NEURO_SYMBOLIC_ARCHITECTURE.md` §5 states, in the repository's own words, that
`llm_only`, `llm_rag` and `llm_rag_rules` are *implemented but have never produced a
result*, because no provider key was configured and a prior Hugging Face download ran at
about 24 KB/s. Its conclusion: **"the central comparison with LLM and RAG baselines is
not measured in this repository yet."**

Existing published numbers therefore compare the full system against a *lexical
ablation* and against *extractive RAG* — not against a real language model. That is the
first thing to fix, and it is what makes the headline research question answerable.

### Claim audit of existing numbers

Carried over from `docs/NEURO_SYMBOLIC_ARCHITECTURE.md` with provenance. These are
**prior claims pending re-verification on this machine**, not results of this programme.

| Claim (source) | Status |
|---|---|
| Run 1 frozen test: accuracy 0.744, hallucination 0.116 | Prior run, report file present, **not yet re-reproduced here** |
| Run 3 hold-out: accuracy 0.800 | Prior run, single run, **not yet re-reproduced here** |
| Run 4 copilot frozen: accuracy 0.642 | Prior run, **not yet re-reproduced here** |
| Run 5 copilot post-hoc: accuracy 0.774 | Prior run, author states it is **not** an unseen estimate |
| Runs 2, 5, 6, 7 | Author-disclosed as post-hoc / fix-informed — **not unbiased estimates** |
| `llm_only`, `llm_rag`, `llm_rag_rules` on any split | **NEVER RUN. Unverified.** |
| "Symbolic half is reliable: 41/41 test, 21/22 hold-out correct where goal identified" | Prior run, **not yet re-reproduced here** |
| Business outcomes (handle time, resolution, satisfaction) | **Not measured.** Protocol only |

Credit where due: the repository already discloses its own scorer gap (conditional
out-of-scope answers not counted as hallucinations), its single-author dataset
limitation, and its small-sample caveats. Those disclosures are retained.

### Blockers found, and what was done

| Blocker | Resolution |
|---|---|
| 376 of 440 tests need PostgreSQL + pgvector, supplied by Docker, which the spec forbids | **Resolved, suite green.** Managed PostgreSQL provisioned (user-approved), reproducing `docker/postgres/init.sql`'s two-role model: `praxis_owner` owns the schema, `praxis_app` is `NOBYPASSRLS` (verified programmatically, so the RLS tests still mean something). Reproducible via `scripts/setup_cloud_db.py`. No Docker |
| First instance was 270 ms away (`us-east-1`), making the suite unusable | **Resolved.** Re-provisioned in `ap-southeast-1`: **51 ms**, suite completes in 9m32s |
| 440 errors: `DeadlockDetected` on `TRUNCATE` | **Resolved, not a suite defect.** Two orphaned `pytest` processes from cancelled runs held locks on the shared test database. Killed them; suite passed completely |
| No LLM provider key; hosted baselines unmeasurable | **Decision taken (user):** run open-weights locally. torch 2.5.1+cu121 + transformers 4.46.3 installed, CUDA verified on the RTX 4050 |
| Prior author blocked by 24 KB/s Hugging Face downloads | **Gone.** Measured **2.15 MB/s** today, ~90x faster. The LLM baselines are achievable locally with no API key |

### Known deviations from the specification, and why

- **The spec assumes PRAXIS is general-purpose; it is not.** The repository is a
  *domain-specific enterprise support decision copilot* over a fictional company
  ("Meridian Marketplace"). Phase 2 of the spec asks for ChatGPT-like general-purpose
  conversation. That is a genuine scope extension, not a bug fix, and is tracked as its
  own phase rather than silently folded into the audit.
- **Neon is cloud-hosted**, so "runs entirely on the host" is satisfied only in the sense
  that no container runtime is required. A native-Postgres path will be documented for
  replication without cloud access.

---

## Phase 2 / 4 — from-scratch language model

**Status: implemented and feasibility-verified. Not yet trained.**

Code written:
- `backend/app/slm/model.py` — decoder-only causal Transformer: RMSNorm pre-norm, RoPE,
  SwiGLU, weight tying, GPT-2 scaled residual init. `ModelConfig.__post_init__` refuses
  any `initialized_from` other than `random`, and `parameter_provenance()` reports the
  origin of every parameter from the live module, so "trained from scratch" is
  checkable rather than asserted.
- `backend/app/slm/data.py` — corpus cleaning, hash-based deterministic splits,
  exact + MinHash near-duplicate removal, benchmark-leakage detection, corpus-trained
  byte-level BPE, uint16 token packing.
- `backend/app/slm/train.py` — AMP, gradient accumulation, gradient clipping, cosine
  schedule with warmup, checkpoint + optimizer state + resume, early stopping, time
  budget, and per-step logging of loss, tokens/s and GPU memory.
- `scripts/prepare_slm_data.py` — writes a `manifest.json` with licence, counts and
  SHA-256 of every artifact. **Aborts if a benchmark question appears in the corpus**,
  rather than warning.

Verified:
- Random initialization is correct: initial loss **9.035** against ln(8192) = **9.011**
  with correctly shifted next-token targets.
- A first smoke test showed 6.81, which was traced to the *test* passing unshifted
  targets: with tied embeddings the model trivially predicts its own input token
  (target-logit mean 2.23 vs 0.0001 overall). A test artifact, not a model defect — but
  worth recording, because it is exactly the kind of silent leak that would make an
  untrained model look trained.

Hardware probe (synthetic tokens, probe only — never used for a reported result).
Full data: `artifacts/slm/hardware_probe.json`.

| Preset | Params | Batch | Context | Tokens/s | Peak GB | Verdict |
|---|---|---|---|---|---|---|
| pilot | 13.8M | 16 | 256 | 88,168 | 1.16 | fits |
| **small** | **29.9M** | **16** | **512** | **42,948** | **3.17** | **selected** |
| base | 97.5M | 16 | 512 | 6,367 | 7.26 | rejected |
| base | 97.5M | 24 | 512 | 1,193 | 10.18 | rejected |

**Caveat recorded:** the rejected rows raised no `OutOfMemoryError` — Windows WDDM
spills silently past the 6.44 GB of VRAM. Throughput collapsing from 16,075 to 1,193
tokens/s is the real signal. Peak memory alone is not a feasibility test on Windows.

**Feasibility: confirmed.** 29.9M parameters at ~43,000 tokens/s is ~155M tokens/hour,
so a multi-epoch run over a ~100M-token corpus takes hours. The model will not be
competitive with a pretrained instruction-tuned LLM and no such claim will be made for
it; its value is that its entire training set is known.

### Bugs found in the never-run code path

Both were blocking the LLM baselines, and neither was visible without trying to run them.

1. **`config.py` omitted `"local"` from the `llm_provider` literal.** The provider class
   and the registry entry both existed, and `docs/NEURO_SYMBOLIC_ARCHITECTURE.md`
   instructs `LLM_PROVIDER=local`, but that value raised a pydantic validation error on
   import. The documented local path was unreachable. Fixed.
2. **The `dev` split cannot run the LLM systems.** `_who()` requires `case["user"]` to
   build the asker's identity, and `dev.yaml`'s compact `{q:, goal:, act:}` schema has no
   `user` field — it exists to calibrate similarity thresholds for the symbolic path only.
   Deliberately **not** "fixed": substituting a placeholder identity would silently change
   the experiment. Recorded instead, and smoke tests use `test`, which carries identities.

### Test baseline extended

`backend/tests/test_slm.py`: **28 passed**, all `no_db`. Total suite now **468 tests**.
The two that matter most for the research claim:
- untrained loss must equal ~ln(vocab) — the sharpest available check that no pretrained
  knowledge leaked in, since any real language ability would score materially lower;
- a planted benchmark question must be detected by the leakage scanner.
Also covered: causal masking (a later token must not change an earlier output, or the
model can see its targets), RoPE norm preservation, target shift-by-one, cross-process
hash stability, and checkpoint/resume fidelity.

## Phase 5 / E2-E4 — the central comparison, measured at last

**Status: first result obtained.** This is the comparison the repository said had never
been run. Frozen `copilot` split, 53 cases, all eight systems, single run.

Source records:
- `eval_reports/ns-benchmark-meridian-copilot-20261026T062639Z.json` / `.md`
- `eval_reports/ns-benchmark-meridian-copilot-20261026T062639Z-stats.json`

Base model: **Qwen2.5-1.5B-Instruct**, fp16, local GPU, greedy decoding (deterministic).

| Metric | neurosymbolic | llm_only | llm_cot | llm_rag | llm_rag_rules | llm_rag_selfcheck |
|---|---|---|---|---|---|---|
| Final answer accuracy | **0.774** | 0.094 | 0.094 | 0.113 | 0.264 | 0.075 |
| Decision accuracy | **0.840** | 0.400 | 0.440 | 0.400 | 0.480 | 0.360 |
| Calculation accuracy | **0.833** | 0.000 | 0.000 | 0.000 | 0.417 | 0.000 |
| Policy compliance | **0.925** | 0.547 | 0.528 | 0.585 | 0.660 | 0.547 |
| Hallucination rate | **0.075** | 0.453 | 0.472 | 0.415 | 0.340 | 0.453 |
| Unsupported-answer rate | **0.000** | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 |
| Mean latency (ms) | **86** | 3308 | 2520 | 2648 | 6484 | 4730 |

### Paired statistics (`scripts/analyze_benchmark.py`)

McNemar exact (two-sided) plus a 20,000-resample paired percentile bootstrap over
questions, alpha = 0.05. A result is only called persuasive when the test is significant
**and** the interval excludes zero.

| Baseline | Accuracy diff | 95% CI | p | Hallucination diff | 95% CI | p |
|---|---|---|---|---|---|---|
| llm_only | +0.679 | [+0.528, +0.811] | <0.0001 | -0.377 | [-0.528, -0.226] | 0.00004 |
| llm_cot | +0.679 | [+0.528, +0.811] | <0.0001 | -0.396 | [-0.547, -0.245] | 0.00005 |
| llm_rag | +0.660 | [+0.509, +0.811] | <0.0001 | -0.340 | [-0.491, -0.189] | 0.00028 |
| llm_rag_rules | +0.509 | [+0.340, +0.660] | <0.0001 | -0.264 | [-0.415, -0.132] | 0.00131 |
| llm_rag_selfcheck | +0.698 | [+0.566, +0.830] | <0.0001 | -0.377 | [-0.528, -0.226] | 0.00004 |

All five favour the neuro-symbolic system on accuracy, hallucination and compliance.

### What this does and does not establish

**Does:**
- The prior claim reproduces exactly. `neurosymbolic` scored **0.774** here, the same as
  the earlier post-hoc run 5. The system is deterministic and the earlier fixes hold.
- Against this base model, the architecture's advantage is large and statistically
  persuasive on every primary metric, on paired tests appropriate to a 53-item sample.
- Verification is doing the work, not retrieval. `llm_rag` (0.113) barely beats
  `llm_only` (0.094); adding a basic rule engine (`llm_rag_rules`, 0.264) helps most
  among the baselines, and the full verification stack roughly triples that again.
- Self-checking does **not** help: `llm_rag_selfcheck` (0.075) is no better than
  `llm_only` (0.094) and hallucinates at the same rate (0.453 vs 0.453). Asking a model
  to check itself is not a substitute for an external verifier.
- The architecture is also far cheaper at inference: 86 ms against 2.5-6.5 s.

**Does not:**
- **The base model is only 1.5B parameters.** This result does *not* license the claim
  "PRAXIS beats LLMs". It supports the narrower claim that PRAXIS beats a small local
  instruction-tuned model on this domain. A stronger base model might close much of the
  gap, and that is the single biggest threat to validity. **E6 cross-model is required
  before any general claim**, and a frontier-scale comparison remains unmeasured.
- Single run, n = 53, with 4-9 cases per category. Category-level rates are indicative
  only.
- Domain-specific. Nothing here speaks to open-domain conversation.
- The ablation rows (`neurosymbolic_lexical`, `rag_extractive`) show hallucination
  differences that the analysis correctly reports as **inconclusive**: they score 0.000
  only because they almost never commit to an answer (accuracy 0.038). Their
  near-perfect compliance is an artefact of abstention, which is exactly why coverage
  must be read alongside it.
- `Retrieval accuracy` reported `n/a` for every system here, which is **correct, not a
  defect**: the metric is conditional on a case declaring expected `clauses`, and
  `copilot.yaml` declares none. `test.yaml` declares 8 and `holdout.yaml` 3 — exactly
  the denominators behind the prior reports' "expected clause retrieved in 7 of 8 test
  and 3 of 3 hold-out cases". Retrieval quality must therefore be measured on the test
  and hold-out splits, not this one.

## Correction — what the copilot benchmark actually compared

Found while running E6: `neurosymbolic` scored **identically on every metric** across two
different base models (0.774 / 0.840 / 0.833 / 0.925 / 0.075), latency 86 vs 90 ms.
Cause, in `ns_benchmark.neurosymbolic_system`:

```python
# The deterministic realizer: this measures the reasoning, not a model's phrasing.
controller = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
```

The benchmarked `neurosymbolic` system **hardwires the mock LLM**, and the controller's
real model is reached only from `_general_text` — the off-topic conversation pipeline.
The whole decision path is encoder plus symbolic reasoning, with no language model in it.

**Consequence for the claims.** The copilot result is a fair same-questions comparison,
but it is *architecture versus architecture*. It answers "does a neuro-symbolic system
beat LLM approaches on enterprise policy decisions?" It does **not** answer "does adding
verification improve a given LLM?" Those were conflated in the first write-up and are now
kept apart. The repository had already documented the subtlety: *"The verification gate
exists to police prose the model wrote, and the default configuration writes none, so
removing it changes nothing there."*

## The wrong harness was used first

`scripts/run_ns_benchmark.py` exposes 8 systems. `app/evaluation/systems.py` registers
**25**, driven by `scripts/run_research.py`, which is the stronger instrument:

- **255-case `test` split over 5 generated worlds**, and a second 255-case `confirm`
  held-out set, against the 53 hand-written copilot cases. This reaches the 200-500 range
  the specification asks for and substantially answers the single-author limitation.
- Already reports **coverage, selective accuracy, over-abstention, disclosure rate,
  confidence intervals on every metric, and paired exact McNemar** — the Phase 6 metrics.
- Writes resumable per-case JSONL — the Phase 8 requirement.
- Includes `ns_parser` (the model parses the question; decisions stay symbolic), measured
  at 1678 ms against 226 ms, confirming it genuinely calls the model. **This is the real
  cross-model variant**, and the configuration Track B needs.
- Includes seven ablations (`no_verification`, `two_valued`, `trust_statements`,
  `no_authorization`, `latest_version`, `no_number_check`, `no_kind_agreement`) — E7,
  already implemented.

The copilot numbers are retained as a smaller hand-written result and will not be given
the weight of the 255-case runs.

## Phase 5 / E2-E3 on the 255-case test split — partial

Base model Qwen2.5-1.5B-Instruct. Records in
`eval_reports/research/qwen2.5-1.5b/test.*.jsonl`. Four of six systems complete.

| System | Accuracy | Hallucination | Coverage |
|---|---|---|---|
| neurosymbolic (encoder, no LLM) | **0.796** | **0.149** | **0.943** |
| ns_parser (model parses, symbolic decides) | 0.678 | 0.169 | 0.814 |
| llm_rag | 0.259 | 0.443 | 0.710 |
| llm_only | 0.231 | 0.600 | 0.795 |

Two findings that matter more than the headline gap:

1. **Using the model as parser makes PRAXIS worse, not better**: 0.796 → 0.678, and
   coverage 0.943 → 0.814. A 1.5B model is a *worse* question-parser than the 33M
   bge-small encoder. An honest negative result, and the direct answer to "does adding an
   LLM to the understanding layer help?" — at this scale, no.
2. **PRAXIS is not winning by abstaining.** Coverage 0.943 against llm_rag's 0.710: it
   answers *more* questions *and* more accurately. This is the coverage/selective-accuracy
   check the specification demands, and it passes.

### An instrumentation gap, found and fixed

The log showed 92 occurrences of *"semantic parser named an undeclared goal; using
embeddings"* — on 255 cases that is **36% of `ns_parser` runs falling back to the
embedding interpreter**. So `ns_parser` is partly the encoder system, and reading its
score as "the model's understanding" would overstate the model.

That fallback was recorded in the interpretation but never reached the saved per-case
rows, so it was invisible to analysis and present only in stderr. `parse_failed` does not
capture it — that flag is about unreadable JSON and correctly reads 0 here. Added
`parser_fallback` to `SystemAnswer`, populated it in `systems.py` from the trace, and
recorded it per case in `research.py`. Phase 8 requires routing decisions to be recorded;
this one was not.

## Error analysis — a failure the smaller benchmark hid

From `test.neurosymbolic.jsonl`, 255 cases, accuracy by category:

| Category | n | Accuracy |
|---|---|---|
| **not_covered** | 15 | **0.000** |
| recommendation | 25 | 0.600 |
| paraphrased | 45 | 0.622 |
| known | 30 | 0.833 |
| policy_version | 25 | 0.880 |
| false_premise | 20 | 0.900 |
| multi_hop / calculation / missing_info / unauthorized | 100 | 1.000 |

Failure modes among the 52 incorrect answers: 25 (48%) definite but wrong, 13 (25%)
hallucinated, 12 (23%) abstained when an answer was expected.

### `not_covered`: 0 of 15, and the reason matters

These are questions no policy clause covers; the gold outcome is `insufficient`. The
system abstained **zero** times — 13 `answered`, 2 `clarification`, 13 scored as
hallucinations.

| Question asked | What the system answered |
|---|---|
| "What is the warranty period on the Lumen Tablet?" | "No — a replacement isn't available for your Lumen Tablet order: the item is not eligible for return." |
| "Can I pay for order ORD-84844 in monthly instalments?" | "Yes — your Monsoon Rain Jacket order ships free: the customer is a Meridian Plus member…" |
| "Can you gift-wrap order ORD-85004 before it ships?" | "Yes — your Brass Table Lamp order ships free: the ₹3,469 order total meets the ₹1,499 threshold." |

Understanding maps an out-of-scope question onto the nearest in-scope goal, and the
system then answers *that* question confidently, citing real clauses and real records.

**The verification gate cannot catch this, and it is important to see why.** The
system's own gate released every one of these answers, and the figures they cite are
genuinely evidence-backed: across all 15 cases `unsupported_numbers` is 0 and
`unsupported_values` is empty, measured against the world's actual records. The claim
"your order ships free" really is supported. What fails is upstream — the goal chosen was
never the one asked about.

(The scorer does flag 13 of 15 as `unsupported_definite`, but for `not_covered` that
field is defined as `outcome in ("answered", "conditional")` — it records that the system
answered when it should not have, not that the internal checks failed.)

> **Verification establishes that a claim is supported by evidence. It does not
> establish that the claim answers the question.** Relevance is not a property the
> evidence gate tests.

This qualifies the project's headline claim. `README.md` says *"When any of that fails,
the assistant says it does not know — and that refusal is scored as a success"*; the
architecture doc says *"a question your documents do not cover is still refused"*. On
this split that holds **0 times in 15**. Worse, the grounding makes the wrong answer
*more* convincing, not less — a case where verification makes the output more dangerous
rather than safer.

The repository's git history shows the author met this too: *"eval: five fixes for the
not_covered failure, and all five are wrong."*

The 53-case copilot set did not expose it (it reported `unsupported-answer rate 0.000`
and `adversarial 1.00`), which is the clearest argument for preferring the 255-case
generated harness for anything that goes in the paper.

## Correction — the project HAD run language-model baselines

My Phase 1 audit reported that "no published number compares PRAXIS to a language
model". **That was wrong**, and the record is corrected in `docs/RESEARCH_AUDIT.md` and
`docs/NEURO_SYMBOLIC_ARCHITECTURE.md`.

What is true: the `ns_benchmark` track (`copilot`, `test.yaml`, `holdout.yaml`), which
§5 of the architecture document describes, had never seen a language model. What is false:
extending that to the project. A second, stronger track —
`scripts/run_research.py` over 255 generated-world cases — **had already been run in full
with Qwen2.5-7B-Instruct** under the tag `paper` (dated 3 October), ablations included,
and the manuscript's headline figures come from it.

I missed it because I started from that document's §5 and from `run_ns_benchmark.py`,
which exposes 8 of the 25 registered systems. The lesson for the audit: a stale claim in
a document is not evidence about the repository.

## Independent reproduction of the paper's central number

`neurosymbolic` on the 255-case test split, run today on a different machine, a different
database and a different day:

| | Accuracy | Hallucination | Coverage |
|---|---|---|---|
| `paper` tag, 3 October | 0.796 | 0.149 | 0.943 |
| this run, 26 October | **0.7961** | **0.149** | **0.9429** |

Exact. The system is deterministic and contains no language model on the decision path,
so this is a genuine independent reproduction of the manuscript's central figure.

## New result — parser quality is strongly scale-dependent

The author's run used Qwen2.5-7B-Instruct; mine used Qwen2.5-1.5B-Instruct, same split,
same code. That makes a scale comparison the project did not previously have.

| System | 1.5B (this run) | 7B (`paper` tag) | Δ accuracy |
|---|---|---|---|
| neurosymbolic *(no LLM — control)* | 0.796 | 0.796 | **0.000** |
| ns_parser | 0.678 | **0.945** | **+0.267** |
| llm_rag_rules | 0.412 | 0.537 | +0.125 |
| llm_only | 0.231 | 0.235 | +0.004 |
| llm_rag | 0.259 | 0.200 | **-0.059** |

Reading it:

1. **The bare model barely benefits from 4.7x more parameters** (+0.004). Retrieval
   actually got *worse* (-0.059).
2. **The architecture converts scale into accuracy far more effectively than the model
   alone does** (+0.267). The same capability increase that is nearly worthless to
   `llm_only` is worth 27 accuracy points when the model is confined to parsing and the
   decision stays symbolic.
3. **My earlier claim needs qualifying.** I wrote that "using the model as parser makes
   PRAXIS worse". That holds at 1.5B (0.796 → 0.678) and reverses at 7B
   (0.796 → 0.945). The correct statement is that the parser configuration has a
   capability threshold: below it the model is a worse parser than a 33M encoder, above
   it a much better one.
4. The control behaves exactly as it must: `neurosymbolic` is identical across both,
   because no language model touches its decision path.

**Confound to record:** the two runs used different inference stacks — the `paper` run
went through an OpenAI-compatible endpoint, mine through local `transformers` in fp16
with greedy decoding. Sampling settings and quantization are therefore not perfectly
controlled, and the comparison is between *runs*, not a clean single-variable sweep. It
should be repeated with one stack serving both sizes before it goes in the paper.

## Phases 3 (cross-model), 6-11 — in progress

Nothing to report. No results will be written here until they are measured.

---

## Open blockers requiring intervention

1. **A stale cloud project should be deleted.** `praxis-research` (us-east-1) was created
   before the latency problem was found; the live one is `praxis-research-sg`. Deletion is
   irreversible, so it awaits confirmation.
2. **Frontier-scale comparison needs a hosted key.** The local path covers small
   open-weights models only. Whether the advantage survives against a strong model is
   unmeasured, and on current evidence that is the most important open question.

## Log

- **2026-10-26** — Phase 1 audit complete (`docs/RESEARCH_AUDIT.md`). Repository
  identified on evidence, not folder name. Baseline **440/440 green** on managed
  PostgreSQL with no Docker. Central finding: the LLM baselines central to the research
  question have never been run — and the 24 KB/s download that blocked them is gone
  (2.15 MB/s measured). From-scratch model implemented, random init verified,
  feasibility measured on real hardware; 29.9M at 43k tok/s selected.


---

## Football track — from-scratch nanoGPT + symbolic verification (2026-10-26)

**Status: end-to-end experiment complete and measured.**

Source data: `goal.txt`, 3,637 FIFA World Cup goal records, 1930-2022, men's and
women's tournaments, 1,162 matches, 1,831 players, 85 teams.

### Pipeline

`scripts/prepare_football.py` turns one structured dataset into all three things the
experiment needs, so the generator and the verifier are grounded in the same facts:
a verbalised training corpus, a typed knowledge graph, and an evaluation set.

Design decisions that matter:
- **The split is by match, not by row.** If a match's sibling goals were in training,
  the model could recite a neighbouring fact and look correct. 137 matches are withheld
  whole.
- **The minute/period rule was verified against the data before being used.** All 3,637
  rows satisfy it (first half 1-45, second half 46-90, extra time 91-105 / 106-120). A
  rule the data violates would reject true claims.
- **Leakage is checked on `(fixture, tournament)` pairs, not fixture names.** Fixtures
  recur across tournaments, and the 1934 and 1938 tournaments contained replays, so even
  that pair can name two matches; those 2 were dropped as unanswerable. Final check:
  137 evaluated fixtures, **0 leaked**.

Corpus: 13,249 sentences, 1.30 MB, **292,948 training tokens**, vocab 4,096.

### The model

nanoGPT (vendored verbatim, MIT), `n_layer=4 n_head=4 n_embd=256`, **4.20M parameters**,
trained from random initialization on the local RTX 4050.

- Initial loss **8.454** against ln(4096) = **8.318** — consistent with random init.
- Best **validation loss 0.7742 at step 1500**; training loss continued to 0.2031 by
  step 3000 while validation rose to 0.8614. Textbook overfitting, and expected: at
  **0.070 tokens per parameter** the corpus is roughly 285x too small for this model.
  No available configuration avoids it — 293k tokens cannot support a model of useful
  size. The checkpoint kept is step 1500.

### Result

337 stems, half naming held-out matches. Same generations in both runs below (fixed
seed), so only the extractor differs.

**First run** reported 676 of 1,391 sentences checkable — 51% unparseable, which made
the measurement untrustworthy. Widening the extractor fixed five distinct causes:
a stage regex that rejected digits and so lost every knockout round ("round of 16"); a
dropped "In {match} on {date}," prefix; two kinds of total ("X scored N goals at Y",
"{match} produced N goals"); and 302 trailing fragments cut off by `max_new_tokens`,
which are a sampling artifact rather than a model error and are now discarded instead
of counted against it.

**Checkability rose from 48.6% to 96.9%**, and the hallucination estimate moved with it
— the first figure was biased by excluding the claim types the model handles differently.

| System | Released | False | Hallucination | Coverage |
|---|---|---|---|---|
| `slm_only` | 1,056 | 533 | **0.505** | 1.000 |
| `slm_verified` | 523 | 0 | **0.000** | 0.495 |

1,091 claims produced, **1,056 checkable**, 35 unparseable (genuinely garbled output).
Status counts: SUPPORTED 523, CONTRADICTED 493, UNVERIFIED 40.

**Provenance note.** An earlier execution of the same configuration reached validation
loss 0.7746 and gave 0.493 / 0.507; its per-step log was never saved, so its training
curve could not be drawn. Rather than cite a figure from one run beside tables from
another, the run whose log exists was promoted to canonical (validation loss **0.7742**)
and the experiment re-run against its checkpoint. Every number in the paper now derives
from that single execution. The superseded checkpoint is kept at
`artifacts/football/_superseded/ckpt_original_0.7746.pt`. The two runs differ by 0.0004
in validation loss and about one point in hallucination rate, which is the run-to-run
variation of this setup under nondeterministic GPU kernels.

By claim type, which shows the verifier discriminating rather than rubber-stamping:

| Claim kind | n | False rate |
|---|---|---|
| goal event | 928 | 0.488 |
| player tournament total | 136 | 0.478 |
| match total | 25 | **0.783** |

The model is worst at match totals. Examples the graph caught: *"Germany vs Argentina
produced 2 goals"* (the record says 11 — that is the real 11-0 game of 2007);
*"North Korea vs Denmark produced 5"* (4); and *"Raf Mannion scored 1 goal at the 2018
FIFA Men's World Cup"*, an invented player, correctly UNVERIFIED rather than
CONTRADICTED.

### How to read this honestly

**The 0.000 is near-tautological and must not be reported as the finding.** The gate
releases only claims the graph confirms, so of course none of them is false. The
informative numbers are the other two: a **55.6% base hallucination rate**, and the
**44.4% coverage** that suppressing it costs.

What *is* non-circular:
- **21 claims were caught by rule checks alone, with no lookup** — a team credited with
  a goal in a match it did not play in. Pure logic, no database.
- The graph-based contradictions are specific and plausible-looking, which is what makes
  them dangerous: 79 claims named the **wrong tournament** for a real goal ("Sandra
  Minnert scored in minute 57" — true, but 2003, not 2007); others put a real player in
  a match they never scored in, or at a minute they did not score
  ("Rudi Völler did not score in minute 12; recorded: [6, 23, 35, 38, 70, 75, 81, 90]").

### A finding worth keeping

False-claim rate by prompt source: **held-out 0.517, seen 0.496** — statistically
indistinguishable, and now measured on 1,056 claims rather than 676. The model hallucinates just as much about matches it *was* trained on
as about matches it never saw. At 0.070 tokens per parameter it has not reliably
memorised even the training facts. This is evidence that scale/data starvation, not just
missing knowledge, drives the confabulation.

### Limitations

- 3.1% of sentences (34 of 1,089) remain unparseable, down from 51%. The remainder are
  genuinely garbled ("Stefan Dobay in minute 35."), so the measured rates now describe
  essentially all of the model's checkable output.
- Verification is definitionally correct with respect to the graph, so gate precision
  and recall cannot be measured against an independent ground truth here.
- The model is 4.2M parameters on 293k tokens. It is a deliberately weak generator of
  checkable claims, not a useful football assistant, and no claim is made otherwise.
- One domain, one dataset, single run.

### Artifacts

- `nanogpt/` (vendored, `VENDORED.md`), `nanogpt/config/train_football.py`
- `scripts/prepare_football.py`, `scripts/run_football_experiment.py`
- `backend/app/football/verifier.py`, `backend/tests/test_football_verifier.py` (27 tests)
- `artifacts/football/{manifest.json,knowledge_graph.json,eval.jsonl}`
- `artifacts/football/nanogpt/ckpt.pt`, `artifacts/football/results/{report.json,claims.jsonl}`


---

## Reviewer response (2026-10-27)

### Metric audit — all 56 published values reproduce

`scripts/audit_metrics.py` recomputes every metric from the per-question records.
**First run reported 26 disagreements, and the audit script was the thing that was
wrong.** It assumed "answerable" meant anything not `not_covered` and that "committed"
meant not abstained. The harness uses a whitelist, `ANSWERABLE = ("decision", "value",
"result")`, so `conditional` and `denied` cases are excluded from coverage entirely, and
it keys coverage on `definite` rather than on the negation of `abstained`. Corrected to
the harness definitions, **every one of the 56 values matches.**

The consistency question resolves arithmetically. For `ns_parser` on the test split:
255 cases = 210 answerable + 15 not-covered + 30 conditional/denied; 205 of 210 were
answered definitely and all were correct (selective accuracy 1.000); 6 of 15 not-covered
cases were correctly refused; accuracy = 241/255 = 0.945. The denominators differ.

### Count reconciliation — the figure was right, the paper was not clear

| Split | Worlds | Questions | Purpose |
|---|---|---|---|
| Development | 3 | 153 | calibration only, never reported |
| Test | 5 | 255 | frozen, main results |
| Confirmatory | 5 | 255 | scored once |
| **Evaluation total** | **10** | **510** | test + confirmatory |
| Corpus total | 13 | 663 | all generated cases |

"510 across ten worlds" meant the evaluation splits. Now stated as a table.

### The controlled gate ablation — a negative result worth keeping

The review's central request: the model writes the final wording, the gate is the only
variable. Run on the same 255 questions, same model, retrieval and prompts.
Records in `eval_reports/research/gate-ablation/`.

**Both arms are identical on every metric** (0.796 / 0.149 / 0.943 / 0.874) **and the
answer text is byte-identical on all 255 cases.**

The cause is a defect in the ablation mechanism, not a property of the gate. The
`no_verification` flag governs exactly one decision — `controller.py:555`, whether an
answer whose verification report failed is released anyway. It does **not** disable the
realizer's separate check that the model's phrasing introduced no figure absent from the
established conclusion (`response_generation.py:172`). That check fired about twelve
times *in each arm*, including the arm that was supposed to be running without it.

Consequences, both recorded in the paper:
1. The identical ablation rows in Table II show the **flag is incomplete**, not that the
   checks are inert.
2. Measuring the gate needs either a flag that reaches every guard, or a prose-level
   factuality metric — the scorer grades the structured decision and is blind to the
   wording the gate protects. **The gate's contribution in the enterprise benchmark is
   reported as unmeasured.**

### Also done

- Three findings separated in the paper: reduction in the enterprise benchmark, the
  coverage cost in Study II, and verifier correctness — which neither establishes.
- Metric denominators stated mathematically.
- Reference double-numbering fixed (`[2] [2] ...`), the same template auto-numbering
  clash as the captions.
- Paper reduced to 6 pages; figures redrawn in greyscale with hatch and dash patterns so
  identity never depends on colour. The training-curve figure was dropped for the page
  budget — its numbers are all stated in the text.

### Not done, and why

- **Relevance gate** for the `not_covered` failure: this is implementation work, not a
  reporting fix. Still the single most valuable next change.
- **Independently authored evaluation**: needs a second person to write questions.
- **Reference verification against publisher records**: entries are formatted from known
  venues but DOIs and page numbers were not checked against publisher metadata.


---

## Gate ablation resolved (2026-10-28)

### The switch had three reach points, not one

`no_verification` is documented as "an answer that fails verification is released
anyway". It gated one decision. The guard is layered:

1. `response_generation.py` — the realizer refuses wording introducing a figure the
   conclusion does not support.
2. `controller.py` — the controller re-verifies whatever the realizer released and
   reverts to the deterministic draft on failure.
3. `controller.py` — the release check withholds an answer whose report failed.

The switch originally reached only (3). **Two successive runs produced byte-identical
arms on all 255 cases** — not because the checks do nothing, but because each remaining
layer repaired what the ablated one let through. The `ablations` module's own docstring
predicted precisely this: *"switching it off in only the first would let the verifier
quietly repair the damage and the study would report that version resolution does not
matter."*

All three are now gated. `backend/tests/test_ablation_reach.py` (9 tests) asserts both
directions of the behaviour and, structurally, that the switch is read in at least three
places across both modules — the check that would have caught this without two wasted
runs.

### Result of the fixed experiment

`eval_reports/research/gate-ablation-v3/`, 255 cases, model writing the final wording in
both arms, verification the only variable.

| | Gated | Ablated |
|---|---|---|
| Answer text differs | — | **52 of 255** |
| Releases an unsupported figure | 0 | **2** |
| Accuracy / hallucination / coverage / selective accuracy | 0.796 / 0.149 / 0.943 / 0.874 | identical |

A representative failure: on `W102-T-recommendation-2` the gated system answers with the
recommended resolution and its grounds; the ablated system abandons the conclusion
entirely and emits generic support boilerplate — a numbered list of steps to report the
issue — carrying a figure found nowhere in the evidence.

### What this establishes, and what it does not

The gate demonstrably acts: 52 changed answers and 2 unsupported figures released. But
every decision-level metric is unchanged, because the scorer grades the structured
decision — goal, decision, value, outcome — which the symbolic layer fixes before any
model writes a word. **The gate protects the prose; the benchmark grades the decision.**
Its contribution is therefore measured here at the level of released text, not of
decisions, and quantifying it further needs a prose-faithfulness metric. Study II
supplies the claim-level measurement that is the right instrument.

### Also this session

- Mandated title applied; architecture diagram (SVG + 400 dpi PNG) added as Fig. 1,
  drawn as-implemented with the unimplemented relevance check marked.
- Scope-of-evidence statement placed at the head of Results.
- `docs/EXPERIMENT_MANIFEST.md`: 14 experiments, 102 metrics recomputed from stored
  records, 0 mismatched.
- Paper held to 6 pages. The 1.5B-vs-7B capability section was removed: it ran across
  two inference stacks and so is not a controlled comparison; it survives as one line in
  limitations, framed as an open question.

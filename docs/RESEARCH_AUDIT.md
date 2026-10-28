# Repository audit and implementation roadmap

Phase 1 deliverable. Everything below was established by reading or running this
repository on the target machine on 2026-10-26. Measurements are labelled with how they
were obtained; prior results carried over from earlier work are labelled as such and are
**not** treated as reproduced.

---

## 1. Which repository this is

The brief referred to "the existing PRAXIS AI repository". Three candidates existed:

| Path | Contents | Decision |
|---|---|---|
| `Desktop/praxisvit` | Python/FastAPI + React. 167 `.py` files. Three-valued reasoner, claim verification, adaptive router, ablation harness, four frozen benchmark splits, `docs/paper/main.tex` | **Selected** |
| `Desktop/PRAXIS-AI` | TypeScript monorepo: agent payments, policy engine, audit log, threat analyzer. Carries the name but matches no part of the research brief | Rejected |
| `Desktop/neurosymbolic-kbqa` | Separate, smaller KBQA project, 21 `.py` files | Rejected |

Selection was made on contents, not on the folder name. `praxisvit` contains the exact
component list the brief asks to extend — adaptive routing, claim extraction, symbolic
verification with explicit truth values, bounded revision, abstention, and an evaluation
harness with held-out splits.

## 2. Architecture assessment

The system treats the language model as a *language* engine and never as a *truth*
engine. The model is invoked exactly twice — once to turn a question into a structured
intent, once to phrase a conclusion that has already been established — and everything
between is deterministic, replayable code.

```
question → understand (LLM) → route → {retrieve | rules | clarify}
         → ground facts → verify (10 checks) → evidence gate → present (LLM)
```

| Layer | Location | Assessment |
|---|---|---|
| Neural | `backend/app/neural` | bge-small (33M) ONNX encoder, offline. Works. Identified by the authors as the accuracy bottleneck, and the measurements support that |
| Symbolic | `backend/app/symbolic` | Rule DSL, knowledge graph with authorized views, three-valued reasoner, policy versioning resolved on the case's own date. The strongest part of the system |
| Hybrid | `backend/app/hybrid` | Router, fact grounding, neural-symbolic loop, ten-check verification with a release gate, knowledge-gap classification, ablation switches |
| Evaluation | `backend/app/evaluation` | Six configured systems, rule-based scoring, report generation. Well built |
| Data | `backend/data/meridian` | Fictional company. Policies, rules, graph, four benchmark splits |
| UI | `frontend/` | React, with a reasoning panel exposing status, sources and derivation |

Design quality is high and the separation is real: no module under `backend/app` names a
Meridian concept, so the demonstration company is swappable.

**The architecture is sound and does not need rebuilding.** The work ahead is to measure
it against real language models, extend it to general-purpose conversation, and add the
from-scratch model experiment.

## 3. Verified baseline

| Check | Result | How |
|---|---|---|
| Pure-logic core | **64 passed** | `pytest -m no_db` |
| Full suite | **440 passed, 0 failed** in 9m32s | `pytest` against the provisioned database |
| Model builds, random init correct | initial loss **9.035** vs ln(8192)=**9.011** | shifted-target forward pass |

The full suite passing is the baseline this programme must not regress.

### A trap worth recording

The first full-suite attempt produced 440 *errors* — `DeadlockDetected` on
`TRUNCATE tenants, permissions, user_lookup, audit_events`. The cause was two orphaned
`pytest` processes from earlier cancelled runs still holding locks on the shared test
database, not a defect in the suite. After killing them the suite passed completely.
Anyone running this against a shared database should check for stale sessions first.

## 4. Claim audit

Numerical claims in `docs/NEURO_SYMBOLIC_ARCHITECTURE.md`, with status.

| Claim | Source | Status |
|---|---|---|
| Run 1, frozen test: accuracy 0.744, hallucination 0.116 | `docs/benchmark/run1-frozen-test.md` | Prior run; report file present; **not re-reproduced here** |
| Run 2, test after fixes: accuracy 0.779 | author | **Post-hoc**, author-disclosed as not an unbiased estimate |
| Run 3, hold-out: accuracy 0.800 | `run3-holdout.md` | Prior single run; **not re-reproduced here** |
| Run 4, copilot frozen: accuracy 0.642 | `run4-copilot-frozen.md` | Prior run; **not re-reproduced here** |
| Run 5, copilot after fixes: accuracy 0.774 | author | **Post-hoc**, author-disclosed |
| Runs 6, 7 on versioned pack | author | **Post-hoc**, author-disclosed |
| Lexical ablation collapses to 0.093–0.125 | runs 1, 3 | Prior run; the ablation mechanism is implemented and testable |
| Extractive RAG decides 0 cases correctly | runs 1, 3 | Prior run |
| "Decision correct in 41/41 test and 21/22 hold-out where goal identified" | author | Prior run; the strongest claim in the repository and worth re-verifying |
| `llm_only`, `llm_rag`, `llm_rag_rules` on any split | — | **NEVER RUN. Unverified.** |
| Business outcomes (handle time, resolution, satisfaction) | — | **Not measured.** Protocol only |

The repository already discloses, unprompted: a scorer gap (conditional out-of-scope
answers are not counted as hallucinations, which understates the hold-out rate as 0.025
when it is 0.05 in substance), single-authorship of both the knowledge pack and all
question sets, small per-category samples of 3–10 cases, and an encoder too small to
threshold single-word product links safely. Those disclosures are retained verbatim and
not quietly dropped.

### The central gap

`docs/NEURO_SYMBOLIC_ARCHITECTURE.md` §5 states plainly: *"the central comparison with
LLM and RAG baselines is not measured in this repository yet."* The three LLM baseline
systems are implemented but produced no result, for two recorded reasons — no provider
key was configured, and a Hugging Face download ran at about 24 KB/s.

**Correction (made later the same day).** That statement is scoped to the
`ns_benchmark` track which §5 of that document describes — the `copilot`, `test.yaml` and
`holdout.yaml` splits. It is **not** true of the project as a whole. A second and
stronger evaluation track exists, `app/evaluation/research.py` driven by
`scripts/run_research.py`, over 255 generated-world cases, and it **had already been run
in full with Qwen2.5-7B-Instruct** under the tag `paper`
(`eval_reports/research/paper/`, dated 3 October), including every ablation. The
manuscript's headline figures — 0.945 accuracy at 0.035 unsupported, against 0.796 for
embedding understanding — come from that run.

My initial audit missed it because I started from the document's own §5 and from
`run_ns_benchmark.py`, which exposes 8 of the 25 registered systems. The honest summary
is that the **copilot track** had never seen a language model, not the project.

**Measured on this machine today: Hugging Face throughput is 2.15 MB/s** — about 90×
what blocked the original author. The obstacle is gone, and the comparison is now
achievable locally with no API key.

## 5. Training feasibility

Hardware: NVIDIA RTX 4050 Laptop, **6.44 GB VRAM**, 12 CPU cores, 108 GB free disk,
torch 2.5.1+cu121, CUDA available.

Measured with a synthetic token stream (a hardware probe only, never used for a reported
result). Full data in [`artifacts/slm/hardware_probe.json`](../artifacts/slm/hardware_probe.json).

| Preset | Params | Batch | Context | Tokens/s | Peak GB | Verdict |
|---|---|---|---|---|---|---|
| pilot | 13.8M | 16 | 256 | 88,168 | 1.16 | fits |
| **small** | **29.9M** | **16** | **512** | **42,948** | **3.17** | **selected** |
| small | 29.9M | 24 | 512 | 40,932 | 4.58 | fits |
| base | 97.5M | 8 | 512 | 16,075 | 4.37 | fits, slow |
| base | 97.5M | 16 | 512 | 6,367 | 7.26 | rejected |
| base | 97.5M | 24 | 512 | 1,193 | 10.18 | rejected |

**An important caveat.** The two rejected rows did *not* raise `OutOfMemoryError`.
Windows WDDM silently spills past VRAM into host memory, so the allocator reports
7.26 GB and 10.18 GB on a 6.44 GB card without complaint. The real signal is throughput
collapsing from 16,075 to 1,193 tokens/s. **Peak-memory numbers alone are not a
feasibility test on Windows**; throughput has to be read alongside them.

**Conclusion: training from scratch is feasible.** The selected 29.9M-parameter
configuration sits inside the 10–50M range the brief asks for, uses half the available
VRAM, and sustains ~43,000 tokens/s — roughly 155M tokens per hour, so a multi-epoch run
over a ~100M-token corpus takes hours, not days.

What this model will *not* be: competitive with a pretrained instruction-tuned LLM. At
30M parameters on ~100M tokens it is far below any compute-optimal frontier, and its
world knowledge will be thin. It is a controlled base model whose entire training set is
known — which is its research value, and the only claim that will be made for it.

## 6. Minimum viable experimental plan

Ordered by research value per unit of compute. Everything in tier 1 is now unblocked.

**Tier 1 — closes the central gap (highest priority)**
1. Run `llm_only`, `llm_rag`, `llm_rag_rules` and `neurosymbolic` on the frozen
   `copilot` split with a local open-weights model. Report the first run as the frozen
   result, as the repository instructs.
2. Re-reproduce runs 1 and 3 to confirm the prior numbers still hold.
3. Adaptive versus always-on verification (E5), measuring latency and tokens as well as
   accuracy.

**Tier 2 — generalization and ablation**
4. Cross-model verification (E6): repeat tier 1 on a second model family to test whether
   any benefit is a property of the architecture or of one model.
5. Ablations (E7): disable RAG, symbolic verification, contradiction detection, adaptive
   routing and revision independently.

**Tier 3 — the from-scratch model (secondary)**
6. Prepare the corpus with the leakage check enforced, train the 29.9M configuration,
   report loss, perplexity, throughput and cost honestly.
7. E1 and E8: scratch model alone, then with PRAXIS verification.

**Tier 4 — product surface**
8. General-purpose conversation (the brief's Phase 2), which is a genuine scope
   extension: this system is currently a domain-specific support copilot.
9. UI work for model and system comparison.

Statistical treatment throughout: preserve per-question results, compute paired
differences, report confidence intervals, and describe an improvement that is not
statistically persuasive as inconclusive.

## 7. Deviations from the brief, and why

- **No Docker, as required.** The suite needs PostgreSQL with pgvector and row-level
  security, which the repository normally gets from Docker and which cannot be faked —
  the isolation guarantees under test *are* database behaviour. Resolved with managed
  PostgreSQL, reproducing `docker/postgres/init.sql`'s two-role model exactly
  (`praxis_owner` owns the schema; `praxis_app` is `NOBYPASSRLS`, verified
  programmatically, so the RLS tests still mean something). Script:
  [`scripts/setup_cloud_db.py`](../scripts/setup_cloud_db.py). This satisfies "no
  container runtime" but is cloud-hosted rather than strictly on-host; a native
  PostgreSQL path will be documented for replication without cloud access.
- **Region matters.** The first instance was provisioned in `us-east-1`: 270 ms
  round-trip, which made the suite unusable. Re-provisioned in `ap-southeast-1`: 51 ms,
  and the suite completes in 9m32s.
- **PRAXIS is not currently general-purpose.** The brief's Phase 2 assumes it is. Turning
  a verified domain copilot into an open-domain assistant is a scope extension that also
  weakens what verification can guarantee — outside the knowledge base there is nothing
  to verify against. It is tracked as its own phase so that any factuality improvement
  measured on the knowledge base is never reported as an open-domain claim.

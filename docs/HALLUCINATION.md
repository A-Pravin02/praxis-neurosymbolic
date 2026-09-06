# Hallucination control

## The claim this project makes

Hallucination is *reduced* here, measurably. It is not *eliminated*, and there is
good reason to think no architecture containing a language model can eliminate it
outright — the residual risk moves rather than vanishing (see "Where the residual
risk lives" below). Those are two different claims, and the difference matters:
the first is what this platform demonstrates and what the benchmark measures; the
second is what would be unfalsifiable to assert.

What this platform does is narrower and checkable:

> A factual claim reaches the user only when evidence for it exists, is relevant,
> supports the claim, and is not contradicted. Otherwise the system says it does
> not know. Whether that actually reduces unsupported generation is measured, not
> asserted — see `docs/EVALUATION.md`.

The distinction matters. "We stopped the model hallucinating" is unfalsifiable
marketing. "Unsupported claims are refused by a deterministic gate, and here is
the measured difference against the same model without it" is an engineering
claim you can check, and disprove.

### What the measurement says

On 278 questions over a real 70-table dataset, with machine-derived ground truth
and the same model throughout, hallucination fell from **0.058 (RAG alone) to
0.004 (full pipeline)** — a 93% relative reduction — while *false* abstention fell
too, 0.369 to 0.187. That second number is the one that keeps the first honest:
any system can drive hallucination to zero by refusing everything, and that would
show up here as a worse false-abstention rate rather than a better one. Full
tables and caveats in [EVALUATION.md](EVALUATION.md) and
[DATASET_FINDINGS.md](DATASET_FINDINGS.md).

## Where hallucinations come from

Three different failure modes, which need three different controls:

1. **Fabrication.** The model states something no source says. Controlled by the
   evidence gate and the post-generation grounding check.
2. **Misattribution.** The model states something a source *does* say, but cites
   the wrong one, or presents a retrieved passage as more authoritative than it
   is. Controlled by per-claim citation binding and provenance.
3. **Unwarranted inference.** The model reasons from evidence to a conclusion the
   evidence does not license — the failure that looks most like competence.
   Controlled by moving inference out of the model entirely: rules over facts,
   with a recorded derivation.

## The five controls

### 1. The model is not asked for facts

The response prompt receives a closed packet: verified claims, their citation
markers, the deterministic conclusion, the inference steps, and fenced evidence.
Its instructions are to present that packet, not to answer the question. It has
no retrieval tool, no database access and no way to add a fact.

With the default offline provider this is airtight, because the provider composes
answers only from the packet. With a cloud model it is a strong constraint rather
than a guarantee — which is exactly why the check in step 4 exists.

### 2. The evidence gate is code

`app/verification/gate.py` decides ANSWER, ANSWER_WITH_CAVEAT, REPORT_CONFLICT,
ABSTAIN or DENY from measured quantities:

```
evidence_relevance   term coverage of the question by the retrieved set
mean_support         how well each claim is backed by the spans it cites
verified_share       fraction of claims established by deterministic derivation
retrieval score      the reranked score of the best usable span
unsupported_count    claims that failed verification
contradictions       unresolved conflicts touching this answer
```

The gate never asks the model whether it is confident. A model cannot lower its
own bar, and self-reported confidence measures nothing. Thresholds live in
configuration (`ANSWER_CONFIDENCE_THRESHOLD`, `CLAIM_SUPPORT_THRESHOLD`,
`EVIDENCE_RELEVANCE_THRESHOLD`) so the operating point is an explicit choice.

### 3. Numeric claims must match the evidence exactly

Numbers are where hallucination does real damage: "20 days" and "25 days" are
equally fluent. A claim containing a number is supported only if that number
appears in a cited span. Lexical similarity alone cannot carry a numeric claim.

Dates and identifiers (`2026-01-01`, `R12`, `E-1002`) are stripped before this
comparison — they are labels, not quantities, and treating them as quantities
rejects correct answers.

### 4. The produced text is checked again

After generation, `check_grounding` splits the answer into sentences and verifies
each assertive one against the approved claims, the evidence and the system's own
provenance records. Two outcomes:

- **A number with no source.** The answer is discarded and replaced with an
  abstention. This is the hard stop, and it is audited.
- **Wording that cannot be traced.** The answer is kept, the verification status
  drops to PARTIALLY_VERIFIED, confidence is scaled down, and the user is told.

This is the control that survives swapping in a stronger, more talkative model.

### 5. Conflicts are never quietly resolved

When two sources disagree about the same subject and predicate, the answer is not
a coin flip. If an ordering signal exists — a newer effective date — the newer
value is preferred, the conflict is still reported, and the superseded value is
still shown. If no ordering signal exists, **neither value is asserted**: the
system reports the disagreement and asks for a human decision.

That last point was a bug during development. An early version suppressed the
derived conclusion but still let a retrieved sentence stating one disputed value
through, so the answer read "The warranty period is 24 months… the sources
disagree." A reader takes the first number as the answer. Both sides are now
marked contradicted (`test_neither_side_of_an_unresolved_conflict_may_be_asserted`).

## Abstention is a feature

The system refuses when:

- nothing was retrieved;
- what was retrieved is only weakly related (relevance below threshold);
- no candidate claim reached the support threshold;
- computed confidence is below the caveat threshold;
- the produced text failed the numeric grounding check.

Refusals say why and suggest what would help. The evaluation suite contains cases
where refusal is the *correct* answer and scores them as passes, so a change that
makes the system chattier shows up as a regression rather than an improvement.

The cost is false abstention: refusing something that was answerable. That is
measured too (`false_abstention_rate`), because a system that refuses everything
would otherwise score perfectly on the metrics above.

## Three kinds of knowledge, never mixed

```
DIRECT           asserted by a source; carries document, chunk, extractor, date
INFERRED         derived by the rule engine; carries rules and premise fact ids
MODEL_GENERATED  produced by a language model; never stored, never a premise,
                 never citable as a dataset fact
```

The distinction is in the type, the database column, the API response and the UI
badge. A general-knowledge answer is explicitly labelled as not evidence-backed,
and the confidence shown for it is capped at 0.45.

## What this does not fix

- **The offline provider makes the gate look better than a fluent model would.**
  It cannot fabricate, so `llm_only` in the benchmark shows *false abstention*
  where a cloud model would show fabrication. The architecture's contribution is
  real, but the specific numbers depend on the model; re-run the harness with
  your provider configured.
- **Extraction is conservative and therefore incomplete.** Facts are only mined
  for a known predicate vocabulary. A value the extractor misses is not a fact,
  so no rule can use it — it stays retrievable text.
- **Retrieval can still miss.** If the right passage is not retrieved, the system
  abstains on something it could have answered. That is the safe failure, but it
  is still a failure.
- **The grounding check is lexical.** A paraphrase that changes meaning while
  keeping the words can pass. Numbers, names and citations are checked exactly;
  nuance is not.
- **Nothing here validates the sources themselves.** If a document is wrong, a
  well-grounded answer will be confidently wrong. Provenance tells you *where* a
  claim came from, not whether that source deserved trust.

## Where the residual risk lives

"Eliminated" would mean: no input can ever produce an unsupported assertion. This
architecture does not achieve that, and the reason is worth stating precisely,
because it also says exactly where the remaining risk is and how to shrink it.

The risk does not disappear — it **moves**, out of the model's parameters and into
four places that are at least inspectable:

1. **The presentation step.** The response model receives a closed packet and is
   told to present it. With the offline provider this is airtight, because that
   provider can only recombine the packet. With a cloud model it is a strong
   constraint, not a proof: the model could still paraphrase a value wrongly. The
   post-generation grounding check catches numeric drift and untraceable
   sentences, which is a large fraction of that surface — but it is lexical, so a
   meaning-changing paraphrase that reuses the words can pass.

2. **The sources.** A well-grounded answer from a wrong document is confidently
   wrong. Provenance tells you where a claim came from, never whether that source
   deserved trust. No amount of verification machinery fixes a bad corpus.

3. **Extraction and rules.** A fact mined wrongly, or a rule authored wrongly,
   produces a derivation that is internally valid and externally false. This is
   why extraction only mints known predicates, why qualified sentences are
   skipped, and why mined rule candidates are never activated automatically —
   but a human can still write a wrong rule.

4. **The gate's thresholds.** They are an operating point on a trade-off curve,
   not a truth detector. Raise them and false abstention rises; lower them and
   unsupported answers get through. `docs/EVALUATION.md` measures both sides,
   which is the only honest way to present a threshold.

There is also a theoretical argument that some hallucination rate is irreducible
for a calibrated model over an open domain — a model that never guesses on
genuinely uncertain inputs is not calibrated, and one that is calibrated must
sometimes be wrong. This platform sidesteps rather than refutes that: for the
verified path it does not ask the model to be calibrated at all, it removes the
model from the truth decision. The cost is that the verified path only covers
questions the corpus and rules can answer, and everything outside it is labelled
as unverified rather than answered confidently.

## What the claim covers, and what it does not

The measured result is about **enterprise question answering over a known
corpus**: questions with a determinate answer that some source or rule can
settle. Within that scope the evidence is strong. Outside it, the architecture
has not been tested and should not be sold.

| Claim | Supported? |
|---|---|
| "Reduces hallucination in enterprise QA over a known corpus" | Yes — measured by ablation, 0.058 → 0.004 |
| "Reduces hallucination on structured, factual questions" | Yes — this is what the 278 cases are |
| "Reduces hallucination on open-ended natural-language questions" | **Not tested.** The dataset suite is templated |
| "Prevents LLM hallucination" | **No.** Too broad, and unfalsifiable as stated |

Two specific reasons the narrow framing is the honest one:

1. **The evaluated questions are templated.** They exercise attribute lookup over
   tabular statistics thoroughly and phrasing variety hardly at all. A question
   asking the same thing in an unusual way may fail resolution and abstain — that
   shows up in the false-abstention rate, not the hallucination rate, but it is a
   real limit on generality.
2. **Verification is lexical and numeric, not entailment-based.** A paraphrase
   that changes meaning while preserving the words can pass the support check.
   That gap is exactly where open-ended questions live, which is why the
   structured claim is the defensible one today.

So the title this work supports is *"a neuro-symbolic architecture that reduces
hallucination in enterprise QA"*, not *"a system that prevents LLM
hallucinations."* The first is what the benchmark measures. The second is what a
reviewer would take apart.

**The defensible claim, stated once:**

> For enterprise question answering over a known corpus, unsupported generation is
> reduced by moving fact determination out of the model and gating presentation on
> evidence, and the reduction is measurable by ablation against the same model on
> the same corpus. The residual risk is confined to the
> presentation step, the sources, the extraction and rules, and the chosen
> thresholds — each of which is inspectable and independently improvable.

That is a stronger position than "we solved hallucination", because it survives
someone actually testing it.

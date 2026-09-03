# Verification

Verification answers one question: **does the evidence actually support what we
are about to say?** It runs twice — before generation, over candidate claims, and
after generation, over the produced text.

## Claim kinds

| Origin | Kind | How it is verified |
|---|---|---|
| A sentence lifted from a retrieved span | DIRECT | It *is* the evidence, so support is not in question; **relevance** to the question decides whether it is an answer |
| A fact extracted from a document | DIRECT | Term overlap plus exact numeric agreement with a cited span |
| A fact from the user's own record | DIRECT | Verified by provenance: the workspace record is the source |
| A fact derived by the rule engine | INFERRED | Verified when the derivation is intact and its premises are evidence-backed |
| Anything a model produced | MODEL_GENERATED | Never verified, never citable as a dataset fact |

Separating **relevance** from **support** matters. A sentence copied verbatim
from a document is trivially "supported" by that document — which says nothing
about whether it answers the question. Conflating the two let an unrelated
passage through the gate during development; scoring them separately fixed it.

## Support scoring

For a claim against the candidate spans:

```
lexical = fraction of the claim's content words present in the span
numeric = 1 if every number in the claim appears in the span, else 0
score   = lexical                              when the claim states no number
        = 0.35 * lexical + 0.65 * numeric      when it does
        x 0.4                                   when the span is quarantined
```

A numeric claim citing a span that does not contain its number scores near zero,
however similar the wording. Citations are the spans scoring within 60% of the
best, capped at four.

## Verification statuses

| Status | Meaning |
|---|---|
| `VERIFIED` | Every supported claim is backed; nothing unsupported |
| `PARTIALLY_VERIFIED` | Some claims are supported, some are weak |
| `UNVERIFIED` | Nothing was verified, and the route did not require evidence |
| `CONTRADICTED` | An unresolved conflict touches this answer |
| `INSUFFICIENT_EVIDENCE` | No claim reached the threshold |
| `NOT_APPLICABLE` | An ablation mode with verification disabled |

## The gate

`app/verification/gate.py` maps the report onto a decision:

```
unresolved conflict           -> REPORT_CONFLICT
no evidence (when required)   -> ABSTAIN
relevance < 0.18              -> ABSTAIN
no supported claim            -> ABSTAIN
confidence >= 0.55            -> ANSWER
confidence >= 0.40            -> ANSWER_WITH_CAVEAT
otherwise                     -> ABSTAIN
```

```
confidence = 0.34*mean_support + 0.22*relevance + 0.14*retrieval
           + 0.30*verified_share - 0.08 * min(unsupported_claims, 4)
```

A conclusion derived from verified premises floors confidence at 0.82, because a
replayable derivation is the strongest signal available. Confidence is capped at
0.97: nothing here is certainty.

## Post-generation grounding

```
strip citation markers        [2] is a reference, not a value
extract numbers               dates and identifiers excluded
compare to the haystack       approved claims + evidence + provenance strings
check citation indexes        a marker beyond the citation count is invalid
check term overlap            under 34% shared content words flags the sentence
```

- **A number with no source** discards the whole answer and substitutes an
  abstention. Audited.
- **Untraceable wording** keeps the answer, drops the status to
  PARTIALLY_VERIFIED, scales confidence down and warns the user.

Provenance strings — document titles, effective dates, conflict resolutions — are
part of the haystack, because the answer is allowed to say "Handbook 2026 is
effective 2026-01-01 and supersedes the 2019 edition". Those strings come from
the platform's own records, so checking against them is still checking against
ground truth.

## Limits

- Support is lexical and numeric, not semantic entailment. A paraphrase that
  changes meaning while keeping the words can pass.
- Thresholds were tuned on the demo corpus. Re-tune them for a real dataset; they
  are configuration, not constants.
- The verifier judges support, never truth. A well-supported claim from a wrong
  document is still wrong.

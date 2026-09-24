# Which component should read the question?

Both systems below use the same symbolic reasoner, the same rules, the same
authorization and the same records. They differ in one component: what turns the
question into a goal and its inputs.

  `neurosymbolic`  a sentence encoder ranks the goals by similarity
  `ns_parser`      a 7B model extracts the goal and inputs; it never decides anything

255 paired test cases. Exact McNemar on the paired per-case outcomes.

| Metric | encoder | LLM parser | encoder-only | parser-only | p |
|---|---|---|---|---|---|
| Accuracy | 0.796 | **0.906** | 12 | 40 | 0.000128 |
| Hallucination rate | 0.149 | **0.035** | 29 | 0 | 3.73e-09 |

## By category

| Category | n | encoder | parser | encoder-only | parser-only | p |
|---|---|---|---|---|---|---|
| calculation | 45 | 1.00 | 0.78 | 10 | 0 | 0.00195 |
| false_premise | 20 | 0.90 | 1.00 | 0 | 2 | 0.5 |
| known | 30 | 0.83 | 0.83 | 2 | 2 | 1 |
| missing_info | 15 | 1.00 | 1.00 | 0 | 0 | 1 |
| multi_hop | 20 | 1.00 | 1.00 | 0 | 0 | 1 |
| not_covered | 15 | 0.00 | 0.40 | 0 | 6 | 0.0312 |
| paraphrased | 45 | 0.62 | 1.00 | 0 | 17 | 1.53e-05 |
| policy_version | 25 | 0.88 | 1.00 | 0 | 3 | 0.25 |
| recommendation | 25 | 0.60 | 1.00 | 0 | 10 | 0.00195 |
| unauthorized | 15 | 1.00 | 1.00 | 0 | 0 | 1 |

## What this says

The parser wins overall and it wins cleanly on hallucination: 29 cases the encoder
hallucinated on it does not, and **not one case the other way**.

It also moves the category that the ablation run exposed as the system's worst. On
`not_covered` -- plausible company questions no policy answers -- the encoder scores
0.00 and the parser 0.40 (p=0.031). That is an improvement on the worst result in the
system and still the worst category in it: three out of five such questions are still
answered when they should be refused.

Why the encoder cannot do this at all was measured before the parser was tried. Three
candidate signals were tested for whether they separate `not_covered` from covered
questions, and all three were refuted:

  out-of-vocabulary content words   paraphrases have *more* of them (5.00 vs 3.73)
  top-to-second goal margin         policy_version (0.019) and multi_hop (0.023) sit
                                    at or below not_covered (0.021)
  top policy-clause relevance       multi_hop (0.559) is indistinguishable from
                                    not_covered (0.554)

There is no threshold that separates these questions in the encoder's space, because
"can you gift-wrap this order" and "does this order ship free" are genuinely near each
other there. Detecting the *absence* of coverage needs a component that can name a goal
as absent, which is what the parser does and similarity cannot.

## The regression, which is real

`calculation` goes 1.00 -> 0.78 (p=0.002), and every one of the ten lost cases is the
same question: "What is my yearly holiday allowance in days?".

The encoder routes it to the leave calculation and applies the rule to the asker's
record -- "16 days: you have 4.7 years of service, fewer than 5". The parser routes it
to policy retrieval and recites the clause instead -- "Employees receive 16 days per
year" -- which is not that person's entitlement, and in one case contradicts it.

So the parser drops the binding from "my" to the asker's record on this phrasing. That
is a parsing defect with a known shape, not a property of the approach.

## What follows

Neither component dominates. The encoder is better at binding a question to the asker's
own record; the parser is better at recognising paraphrase, recommendation, policy
version and -- uniquely -- absence of coverage. Nothing here argues for replacing one
with the other, and the honest reading is that the understanding stage should use both
and reconcile them, which has not been built or measured.

Whichever is used, the language model still decides nothing. It names a goal and its
inputs; the rules, the authorization and the evidence gate are unchanged between these
two runs.


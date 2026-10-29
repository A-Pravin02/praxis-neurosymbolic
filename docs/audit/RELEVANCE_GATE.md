# The relevance gate: nine attempts, and why the tenth has to be structural

The system answers out-of-scope questions confidently. On the fifteen questions in
the frozen test split that no policy clause covers, it abstains in **none**. It maps
the question onto the nearest in-scope goal and answers *that* question correctly,
citing real clauses and real records, and every verification check passes — because
every claim it makes is in fact supported. Verification establishes that a claim
follows from the evidence, not that it answers the question asked.

This file records what has been tried, with the measurement that rejected each, so
the tenth attempt does not repeat the first nine.

## The constraint any gate must satisfy

| Set | n | Requirement |
|---|---:|---|
| Generated test, `not_covered` | 15 | should fire |
| Generated test, covered | 240 | must not fire |
| Curated Meridian test | 86 | must not fire — colloquial, real phrasing |

The Meridian set is the one that matters. It is phrased the way people actually
write, and a threshold fitted on the generated worlds does not survive it. Attempt 4
below passed the generated split and fired on twelve covered Meridian questions.

## What has been tried

Attempts 1–5 are from commit `2706a80`; 6–9 were measured for this attempt by
`scripts/relevance_signal.py`, `scripts/relevance_census.py` and
`scripts/relevance_phrase.py`.

| # | Signal | Result |
|---|---|---|
| 1 | Raise the goal-acceptance threshold | A sweep over the whole split costs 8.6 points of accuracy at 0.85 and fixes **0 of 15**. |
| 2 | Count words the knowledge pack has never seen | Inverted: `multi_hop` scores 4.85, `not_covered` 2.67. |
| 3 | Score those unseen words for semantic alienness | `not_covered` lands mid-pack. |
| 4 | Strip the named record, score the remainder against the goal | Separated on generated worlds (0.634 vs 0.727), **fired on 12 covered Meridian questions**. Reverted. |
| 5 | Fixed goal-acceptance floor | Same family as 1. |
| 6 | Standardised margin (z-score of the top goal within the catalogue) | **No separation.** `not_covered` mean z = 1.725, *higher* than `known` 1.606 and `paraphrased` 1.618. Confidence is not the signal. |
| 7 | Derivation check: did any rule prove the goal, or did it fall through to a policy default? | Fires on **88 of 240** covered generated cases and **40 of 86** Meridian. |
| 8 | Phrase alignment: does the goal's defining vocabulary appear in the question? (max over terms) | Overlaps badly. "Can I pick up order X from one of your stores?" scores **1.000** — one incidental word match saturates a max. |
| 9 | Phrase alignment weighted by term distinctiveness (IDF over the catalogue) | Closest yet: gap narrows from −0.157 to **−0.025**. Still overlaps, and Meridian's p05 (0.668) is *below* covered dev's p05 (0.717), so any cut that catches out-of-scope questions fires on real phrasing. |

## Why they all fail the same way

Every one of the nine keeps nearest-neighbour goal selection and adds a scalar test
on top of it. A nearest-neighbour search always returns a neighbour. Nothing in the
pipeline ever asks whether the question is covered at all, so the scalar is being
asked to distinguish "the best of a bad catalogue" from "a good match" — and on this
evidence it cannot, because an out-of-scope question is a perfectly well-formed
question that genuinely resembles some goal.

"What is the warranty period on the Lumen Tablet?" is about a product and asks for a
duration. `replacement_available` is about a product and concerns what a customer
may get. The embedding is dominated by what they share — product, customer
entitlement, interrogative form — not by the word that distinguishes them. That is
measurement 6: the warranty question stands out from the catalogue *more* than a
typical covered question does.

## What the evidence says the fix is

One configuration already scores above zero here: the semantic parser, at 0.40 on
the test split against 0.00 for the encoder. The reason is structural rather than
numerical — **the parser can answer "none"**, because it reads the goal list and is
free to name nothing. The encoder has no such option: its catalogue contains only
goals, so `argmax` is always a goal.

So the tenth attempt should not be another signal. It should give goal selection a
genuine null option. Two ways to do that, neither yet built:

1. **Parser-side.** Make "none" an explicit, required part of the parser's output
   contract, and treat it as a hard abstain verified symbolically — the named goal
   must be one the schema declares. This is the path the 0.40 already demonstrates.
   It needs the language model at interpretation time, which the default
   configuration deliberately avoids.

2. **A declared out-of-scope catalogue.** Give the encoder negative entries so
   `argmax` can land outside the goals. These must come from the policy base or the
   development split, never from the test questions — and note that the development
   split's out-of-scope topics (store pickup, loyalty points, price match) do not
   resemble the test split's (warranty, instalments, gift wrap, international
   delivery, student discount), so a catalogue derived from dev is unlikely to
   transfer. Writing entries that *do* transfer means enumerating what the company
   does not do, which is open-ended.

Neither is a small change, and neither should be attempted without re-running the
Meridian set, which is what caught attempt 4 and would have caught 6 through 9.

## Reproducing these measurements

```
python scripts/relevance_signal.py    # attempts 6, and the similarity family
python scripts/relevance_census.py    # attempt 7, and the understanding factor
python scripts/relevance_phrase.py    # attempts 8 and 9
```

Each prints its own separation statistics on both benchmarks. None of them changes
the system.

# The relevance gate: nine refuted attempts, and the tenth, which works

The system answered out-of-scope questions confidently. On the fifteen questions in
the frozen test split that no policy clause covers, the LLM-free configuration abstains
in **none** and the parser configuration in **six**. It maps the question onto the
nearest in-scope goal and answers *that* question correctly, citing real clauses and
real records, and every verification check passes — because every claim it makes is in
fact supported. Verification establishes that a claim follows from the evidence, not
that it answers the question asked.

**Status: built.** The tenth attempt is implemented and measured. On the test split the
parser configuration now refuses all fifteen, with no case losing a correct answer. The
nine refuted attempts are kept below, with the measurement that killed each, because
they are what establishes that the tenth had to be structural rather than another
threshold.

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

Attempts 1–5 are from commit `2706a80`; 6–9 were measured by
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

## Attempt 10: the parser's null option, and what was actually wrong

One configuration already scored above zero: the semantic parser, at 0.40 on the test
split against 0.00 for the encoder. The reason is structural rather than numerical —
**the parser can answer "none"**, because it reads the goal list and is free to name
nothing. The encoder has no such option: its catalogue contains only goals, so `argmax`
is always a goal.

The diagnosis that followed from that was wrong in one important way, and finding out how
is what fixed the bug. The parser was not scoring 0.40 because it says "none" six times
out of fifteen. It says "none" on **all fifteen**, and was then overruled nine times.

The measurement that establishes this needs no language model. `RETRIEVAL_ACCEPT` is
0.55, and for each of the fifteen questions the nearest policy clause scores:

| | nearest clause | score | parser outcome |
|---|---|---:|---|
| gift-wrap order ORD-85004 | CAN-1, *cancellation* | 0.644 | answered |
| gift-wrap order ORD-42293 | CAN-1, *cancellation* | 0.645 | answered |
| deliver ORD-43037 outside India | DEL-3 | 0.584 | answered |
| instalments on ORD-60202 | SUP-1, *support* | 0.570 | answered |
| warranty on the Lumen Tablet | RET-3 | 0.477 | refused |
| student discount, Denim Jeans | RET-3 | 0.490 | refused |

The nine it answered are exactly the nine whose nearest clause clears the floor; the six
it refused are exactly the six that fall below it. Nothing about the model separates
them. `_parsed_without_goal` ended in an unconditional fallback — if a clause scored
above the retrieval floor, its text was recited — so the parser's judgement was being
overturned by the same similarity measure the nine attempts had already shown cannot
tell a covered question from an uncovered one. The system refused six out-of-scope
questions by luck of a threshold and answered the rest out of the cancellation policy.

So the gate is not a new signal at all. It is a condition on when a clause may answer
over the parser's "none":

> A policy clause may answer a question the parser has ruled every goal out of **only
> when the answer is the same for everyone** — not a case act, and no record named.

Both halves are the rule `_personal_request` already applies to the parse: "inform"
means the answer is the same for everyone, so a question about the asker's own position
is not one. No threshold is introduced, and none is tuned. What survives is the general
question the fallback was added for — "what is the returns policy" asks what the text
says, names no record, and the parser names no clause only because the question is about
the policy as a whole.

The verdict is also now first-class rather than inferred. `Interpretation.out_of_scope`
is set only when the reply was valid JSON and the name given was literally `none`, so it
is never confused with a parse failure or an undeclared name — both of which fall back
to the embedding interpreter instead, and must not be reported as "nothing covers this".

### Measured effect

Test split, 255 cases, `ns_parser`, Qwen2.5-1.5B-Instruct, paired case by case against
the run made before the change (`eval_reports/research/qwen2.5-1.5b` → `relevance-gate`):

| | before | after | |
|---|---:|---:|---|
| not-covered questions correct | 6/15 | **15/15** | |
| accuracy | 0.678 | 0.714 | +0.035 |
| hallucination rate | 0.169 | 0.094 | −0.074 |
| definite answer without support | 0.090 | 0.016 | −0.075 |
| wrong definite answer | 0.078 | 0.078 | unchanged |
| coverage | 0.814 | 0.814 | unchanged |
| over-abstention | 0.186 | 0.186 | unchanged |
| selective accuracy | 0.883 | 0.883 | unchanged |

**Nine cases gained, none lost.** Every case whose correctness changed is a not-covered
one.

Coverage and over-abstention not moving is the part worth understanding, because a gate
that refuses things should cost coverage. It does not here: 47 cases changed outcome from
`answered` to `insufficient`, but the 38 outside the not-covered category were **already
being scored wrong**, and what they were emitting was recited clause text, not a definite
answer. Coverage counts definite answers over answerable questions, so refusing a clause
recitation does not reduce it. Those 38 are the honest cost signal all the same: a
1.5B parser misjudges covered questions as out of scope often, and each such misjudgement
now becomes a refusal instead of a wrong answer dressed in someone else's policy. The
7B the paper's 0.945 was measured with is not on this machine, so the headline figures
are unchanged and this is reported as the 1.5B configuration's result, not as theirs.

### The false-positive test, which is the one that matters

Attempt 4 separated cleanly on the generated worlds and then fired on twelve of the 86
curated Meridian questions, which are phrased the way people actually write. Every one
was a false positive, and the attempt was reverted. Attempts 6 to 9 would have failed
the same way. So this gate was put through it.

The raw count looks alarming. On the 86 Meridian questions — all covered — the 1.5B
parser says "none" on **29**, and the gate refuses **23** of those. Among them are
questions no reasonable reading calls out of scope:

```
REFUSED  Can I return order 12346?
REFUSED  How many days of annual leave do I get?
REFUSED  Do I have to pay shipping on my laptop order?
REFUSED  Why can't I cancel the laptop order?
```

That is a 34% false-"none" rate from the parser, and it is the parser's error, not the
gate's: the gate is faithfully executing a verdict that is wrong. It is worth stating
plainly, because it bounds what this mechanism can be trusted to do — **the gate is only
as good as the model's null judgement.**

But a route change is not yet a cost. The question is whether those 23 were being
answered *correctly* before, and scored with the suite's own scorer
(`ns_benchmark.score_case`), both arms sharing one memoised parse so that the gate is the
only difference:

| | before | after |
|---|---:|---:|
| Meridian correct | 41/86 = 0.477 | 43/86 = 0.500 |
| correct answers lost | | **0** |
| gained | | 2 |

**None of the 23 was correct before.** When the parser misreads a covered question as out
of scope, the answer was already being recited from a clause chosen by similarity, and
that was already scored wrong. Refusing it loses nothing and reports the uncertainty
honestly instead.

So across both benchmarks: **+9 on the generated test split, +2 on Meridian, 0 lost on
either.** This is the first of the ten attempts to survive the Meridian test, and the
reason it does is that it is not a detector. Attempts 4 and 6 to 9 each proposed a new
signal for "is this covered", and were wrong about covered questions. This one adds no
signal; it removes an override that was letting text similarity overrule a judgement
already made.

`scripts/relevance_gate_meridian.py` and `scripts/relevance_gate_meridian_scored.py`
reproduce both halves; `artifacts/relevance_gate_meridian*.json` hold the per-question
records.

## Reproducing these measurements

```
python scripts/relevance_signal.py          # attempt 6, and the similarity family
python scripts/relevance_census.py          # attempt 7, and the understanding factor
python scripts/relevance_phrase.py          # attempts 8 and 9
python scripts/relevance_gate_eval.py       # the gate's mechanism, no model needed
python scripts/relevance_gate_meridian.py   # the false-positive test, needs the parser
```

The first three change nothing and only report separation statistics. The fourth forces
`out_of_scope` on and compares routes with and without the gate. The end-to-end figures
above come from:

```
LLM_PROVIDER=local LOCAL_LLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct \
    python scripts/run_research.py --split test --systems ns_parser --tag relevance-gate
```

The behaviour is pinned by regression tests in
`backend/tests/test_semantic_parser.py`, which need no database.

## What was not done

**A declared out-of-scope catalogue**, which would give the *encoder* a null option and
so carry the fix to the default LLM-free configuration, is still not built. The gate
above works only where there is a parser to say "none"; the LLM-free configuration still
scores 0.000 on not-covered questions. Negative catalogue entries would have to come
from the policy base or the development split, never the test questions — and the
development split's out-of-scope topics (store pickup, loyalty points, price match) do
not resemble the test split's (warranty, instalments, gift wrap, international delivery,
student discount), so a catalogue derived from dev is unlikely to transfer. Writing
entries that do transfer means enumerating what the company does not do, which is
open-ended.

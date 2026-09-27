# Error analysis

Every failure on the Harbour test split, read by hand. Forty questions, thirty-four
right, six wrong, and none of the six asserted anything false — the hallucination rate on
this split is 0.000. That is the shape of the failures worth noting before the detail:
**the system fails by being vague or by answering a neighbouring question, not by
inventing an answer.**

The cases are grouped by what actually went wrong, which is not the same as the category
the question was filed under.

## 1. A stated fact was not recognised (3 of 6)

The largest group. Each question states, in ordinary words, the one fact the records
cannot hold — what kind of condition was treated — and the input detector did not pick it
up, so the reasoner correctly refused to commit and answered conditionally.

| Case | Question | What happened |
|---|---|---|
| H09 | "I **came down with something** and needed my appendix out. Will you settle CLM-5001?" | `condition_kind = illness` not detected; answered "provisionally payable" |
| H31 | "**The condition was there before I took the cover.** Is CLM-5001 payable?" | `condition_kind = pre_existing` not detected; answered provisionally |

H09 and H31 are the same defect seen twice. The schema describes `illness` as "an illness
or condition that began after the cover started" and `pre_existing` as "already present,
diagnosed or being treated before the cover started". "Came down with something" and "was
there before I took the cover" are plain paraphrases of those, and the encoder scored both
below the input threshold.

**This is the honest reading:** the input threshold is tuned on the Meridian pack, and
nothing has been tuned for Harbour. A second domain inherits the first domain's
calibration, and this is where that shows.

**What it is not:** a wrong answer. In both cases the system named the missing fact and
said the answer holds only if that fact is true, which is what three-valued logic is for.
A system that guessed `illness` here would have been right — and would have been guessing.

## 2. The wrong goal was selected (2 of 6)

| Case | Question | Selected | Should have been |
|---|---|---|---|
| H13 | "What will Harbour **hand over** for CLM-5001?" | policy retrieval | `payable_amount` |
| H27 | "Since cosmetic surgery is covered…, CLM-5005 should be paid, right?" | `payable_amount` | `claim_payable` |

H13 went to the policy text and returned a clause about emergency admissions — a wrong
answer to the question asked, though not a false statement about the policy.

H27 is more interesting. The question asserts a false premise (cosmetic surgery is
excluded, not covered). The system routed to `payable_amount` rather than `claim_payable`
and answered *"the claim is not payable, so no amount is due."* **The content is right and
the false premise is rejected**; only the route is wrong. It is scored as a failure
because the benchmark checks the goal and the decision, and it should be: a system that
reaches the right words by the wrong route will not keep doing so.

## 3. A question with no goal behind it (1 of 6)

| Case | Question | What happened |
|---|---|---|
| H20 | "Was that hospital in your network?" | answered `claim_payable` instead |

There is no goal for "is this hospital in the network". `in_network` is an attribute of a
hospital, not a question the pack declares, so the nearest goal won. This is the same
failure the Meridian split records as `not_covered`, in a smaller form: a question the
knowledge can answer but the schema does not expose, and the router reaching for the
nearest thing it does expose.

Unlike the Meridian `not_covered` cases, this one is fixable by the pack rather than by
the architecture: declaring a lookup for a hospital's network status would answer it
properly. It is left unfixed here because fixing a test question after seeing it fail is
how a benchmark stops meaning anything.

## 4. A clarification that is arguably correct (1 of 6)

| Case | Question | What happened |
|---|---|---|
| H10 | "Are you going to **turn down** CLM-5005?" | asked which of two goals was meant |

"Turn down" sits between *will this be refused* (`claim_payable`) and *why was this
refused* (`claim_refused_reason`), and the system asked rather than picked. The expected
answer says `claim_payable`, so it scores as a failure.

I have left the expectation alone. Asking is defensible, and possibly better than
guessing, but the gold was written before the run and changing it afterwards to match the
system's behaviour is exactly the move that makes a benchmark flattering and useless.

## What this changes

Three of the six failures are one defect: input detection tuned on another domain. That
is the first thing to fix for Harbour, and the fix is a threshold sweep on a Harbour
development split, which does not exist yet — the same procedure `scripts/threshold_sweep.py`
already applies to Meridian.

Two are goal selection, which is the failure mode the LLM parser addresses on the Meridian
split (`docs/benchmark/run11-parser-codefix.md`: goal-selection categories go to 1.00).
The parser has not been run on Harbour.

None of the six is a fabricated fact, a leaked record, or a number the evidence does not
carry. Across both domains the pattern holds: where this architecture fails, it fails
towards silence.

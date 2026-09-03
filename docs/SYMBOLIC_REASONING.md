# Symbolic reasoning

## Why a rule engine at all

A language model asked "how many leave days am I entitled to?" will produce a
number. It will usually be a number that appears somewhere in the retrieved text
— which is not the same as the number that follows from *this* employee's record
and *this* policy. The two demo employees prove the point: Alice (4 years) and
Bob (1 year) ask the identical question, and retrieval returns the identical
passages. Only a rule applied to their differing records separates 20 from 15.

That is what the symbolic layer is for. It is also why the layer is deterministic
code with no model in it: a conclusion is only worth overriding generated text if
it can be replayed.

## The rule language

Rules are JSON, validated at write time, never code and never SQL.

```json
{
  "code": "R12",
  "name": "Annual leave after two years of service",
  "body": [
    {"type": "fact", "subject": "?e", "predicate": "years_of_service", "value": "?y"},
    {"type": "comparison", "left": "?y", "op": "gt", "right": {"const": 2}}
  ],
  "head": {"subject": "?e", "predicate": "annual_leave_days",
           "value": {"const": 20}, "unit": "days"}
}
```

which renders as:

```
IF ?e.years_of_service = ?y AND ?y > 2 THEN ?e.annual_leave_days = 20
```

### Grammar

```
term      := {"var": "?name"} | {"const": <scalar>}
           | {"expr": {"op": <arith>, "args": [term, ...]}}
condition := {"type": "fact", "subject": term, "predicate": <name>, "value": term}
           | {"type": "comparison", "left": term, "op": <cmp>, "right": term}
           | {"type": "absent", "subject": term, "predicate": <name>}
head      := {"subject": term, "predicate": <name>, "value": term, "unit": <str>}

cmp   := eq | ne | lt | lte | gt | gte | in | not_in | contains
arith := add | sub | mul | div | min | max | abs | floor | ceil
```

Bare `"?y"` is shorthand for `{"var": "?y"}`; any other bare scalar is a constant.

### What validation rejects

Checked when a rule is written, so the engine never defends itself at inference
time:

- an unknown condition type, comparison operator or arithmetic operator;
- a head variable not bound by a positive body condition (it could otherwise
  derive an unbounded set of facts);
- a comparison or `absent` over a variable that is not yet bound;
- a predicate name outside `[A-Za-z_][A-Za-z0-9_.-]{0,119}`;
- terms nested more than 5 deep, or more than 24 conditions;
- a non-scalar constant;
- an unstratified use of `absent` (below).

## Forward chaining

Semi-naive: iterate the rule set, find every substitution satisfying the body
against the current fact index, add derived facts, repeat until a fixpoint or a
cap (32 iterations, 2000 derived facts).

Termination is structural: arithmetic produces values but no new terms, so the
Herbrand universe is finite. The caps are belt and braces — a rule like
`n < 1000 → n = n + 1` terminates on the cap rather than running forever, and the
result is marked `truncated`.

Every derived fact records:

```
kind          INFERRED
confidence    min(premise confidences) x rule confidence
provenance    rule code, premise fact ids, the rendered rule, source document
```

and every step records the rule, the premises as rendered facts, and the
conclusion. That is the audit trail the UI shows and the verifier trusts.

## Backward chaining

`prove(subject, predicate)` starts from a goal:

1. If a known fact matches, that is the proof.
2. Otherwise find a rule whose head unifies with the goal, and recursively prove
   each body condition.
3. Comparisons are evaluated once their variables are bound; `absent` checks the
   closed fact set.

Depth is capped at 12 and visited goals are tracked, so a cyclic rule set returns
"unprovable" rather than recursing. The returned `Proof` flattens to the same
`InferenceStep` shape as forward chaining, so the trace looks identical whichever
direction produced it.

Forward chaining drives ordinary answers (derive everything, then read off the
value). Backward chaining is used for goal-directed questions and is the natural
fit for "why is this true?".

## Negation as failure

`absent` succeeds when the fact set contains no matching fact — closed-world
reasoning. It is only sound if the rule set is **stratified**: a rule testing
`absent(p)` must not depend, transitively, on a rule that derives `p`. Otherwise
the answer depends on evaluation order.

`check_stratification` runs when the engine is constructed and rejects the whole
rule set if that dependency exists, rather than producing an order-dependent
answer. Adding a rule through the API re-validates the entire set for this reason.

## Contradiction detection

Predicates are functional by default: one subject, one predicate, one value. Two
different values is a contradiction. A small set of predicates (`tag`, `role`,
`keyword`, `category`, `member`, `note`) is declared multi-valued and exempt.

`MODEL_GENERATED` facts never participate — generated text may neither create nor
resolve a contradiction.

When a conflict is found, the engine looks for an **ordering signal**:

- **All conflicting facts carry distinct effective dates.** The newest wins. The
  conflict is downgraded to `warning`, `preferred_value` and
  `superseded_fact_ids` are recorded, and the resolution is stated in prose. The
  superseded value is still shown.
- **No ordering signal.** Severity stays `error`. Neither value may be asserted;
  the answer reports the disagreement.

Confidence differences produce a *suggestion* for a human, never an automatic
choice: an extraction being more confident is not evidence about the world.

## Integrity constraints

Separate from rules: assertions that must hold over the fact set.

```json
{"code": "C01", "predicate": "age", "op": "gte", "value": 18,
 "description": "Employees must be at least 18 years old."}
```

A violation surfaces as a constraint-violation contradiction, which the gate
treats as blocking. This is how "age >= 18" or "annual leave <= 40 days"
invalidates an answer built on impossible data.

## Where facts come from

```
user profile attributes   -> subject "self"          (DIRECT, source: user_attribute)
document extraction       -> subject "policy"        (DIRECT, source: document)
structured record import  -> subject from the mapping (DIRECT, source: record)
rule derivation           -> any subject             (INFERRED, source: rule)
```

The `self` subject is what makes per-user reasoning work without trusting the
user: Alice's `years_of_service` comes from her workspace record, not from
anything she typed, so she cannot claim ten years and get a better answer.

## Limits

- **No existential head variables**, no function symbols that build new terms, no
  disjunction in rule bodies. Horn clauses with bounded arithmetic.
- **`absent` is the only negation**, and only in stratified position.
- **No temporal reasoning beyond effective dates.** `valid_from` / `valid_to` are
  stored but not yet used to answer "what was the policy in March?".
- **No probabilistic inference.** Confidence multiplies along a derivation; it is
  a heuristic for ranking and gating, not a calibrated probability.
- **Rules are per tenant and curated.** Candidate rules mined from prose are
  recorded for review and are never activated automatically — an automatically
  activated rule mined from a sentence is exactly the kind of confident error
  this architecture exists to prevent.

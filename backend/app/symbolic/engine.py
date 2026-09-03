"""The symbolic reasoning engine: forward chaining, backward proof, contradictions.

Design commitments:

* Every derived fact records the rule that produced it and the exact premise facts
  it consumed, so any conclusion can be replayed.
* The engine is pure: it takes facts and rules as values and returns values. It
  never touches the database, the network or a language model, which is what
  makes its output trustworthy enough to override generated text.
* Termination is guaranteed: the Herbrand universe is finite (no function symbols
  that build new terms beyond bounded arithmetic), and there are hard caps on
  iterations and derived facts.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.knowledge.schema import FactDTO, FactKind, Provenance, make_fact, unwrap
from app.symbolic.schema import (
    CompiledRule, Condition, Const, Expr, Head, Term, Var, check_stratification, render_rule,
)

MAX_ITERATIONS = 32
MAX_DERIVED_FACTS = 2000
MAX_PROOF_DEPTH = 12

Binding = dict[str, Any]


# ---------------------------------------------------------------------------
# Fact index
# ---------------------------------------------------------------------------

@dataclass
class FactIndex:
    """Facts keyed for matching, preserving insertion order for determinism."""
    facts: list[FactDTO] = field(default_factory=list)
    _by_predicate: dict[str, list[int]] = field(default_factory=dict)
    _seen: set[tuple[str, str, str]] = field(default_factory=set)

    @classmethod
    def build(cls, facts: Iterable[FactDTO]) -> "FactIndex":
        index = cls()
        for fact in facts:
            index.add(fact)
        return index

    def _signature(self, fact: FactDTO) -> tuple[str, str, str]:
        return (fact.subject, fact.predicate, repr(unwrap(fact.object)))

    def add(self, fact: FactDTO) -> bool:
        signature = self._signature(fact)
        if signature in self._seen:
            return False
        self._seen.add(signature)
        self.facts.append(fact)
        self._by_predicate.setdefault(fact.predicate, []).append(len(self.facts) - 1)
        return True

    def by_predicate(self, predicate: str) -> list[FactDTO]:
        return [self.facts[i] for i in self._by_predicate.get(predicate, ())]

    def has(self, subject: Any, predicate: str) -> bool:
        return any(f.subject == subject for f in self.by_predicate(predicate))

    def __len__(self) -> int:
        return len(self.facts)


# ---------------------------------------------------------------------------
# Term evaluation and unification
# ---------------------------------------------------------------------------

def evaluate_term(term: Term, binding: Binding) -> Any:
    if isinstance(term, Const):
        return term.value
    if isinstance(term, Var):
        if term.name not in binding:
            raise KeyError(term.name)
        return binding[term.name]
    if isinstance(term, Expr):
        args = [evaluate_term(a, binding) for a in term.args]
        return _arith(term.op, args)
    raise TypeError(f"Unknown term type: {term!r}")


def _arith(op: str, args: list[Any]) -> Any:
    numbers = []
    for value in args:
        if isinstance(value, bool):
            numbers.append(float(value))
        elif isinstance(value, (int, float)):
            numbers.append(float(value))
        else:
            try:
                numbers.append(float(value))
            except (TypeError, ValueError):
                return None
    if op == "add":
        return sum(numbers)
    if op == "sub":
        return numbers[0] - sum(numbers[1:])
    if op == "mul":
        result = 1.0
        for n in numbers:
            result *= n
        return result
    if op == "div":
        if len(numbers) < 2 or numbers[1] == 0:
            return None
        return numbers[0] / numbers[1]
    if op == "min":
        return min(numbers)
    if op == "max":
        return max(numbers)
    if op == "abs":
        return abs(numbers[0])
    if op == "floor":
        return float(math.floor(numbers[0]))
    if op == "ceil":
        return float(math.ceil(numbers[0]))
    return None


def _normalise(value: Any) -> Any:
    """Compare 20 and 20.0 as equal, and strings case-insensitively."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            return stripped.lower()
    return value


def unify_term(term: Term, value: Any, binding: Binding) -> Binding | None:
    if isinstance(term, Var):
        if term.name in binding:
            return binding if _normalise(binding[term.name]) == _normalise(value) else None
        extended = dict(binding)
        extended[term.name] = value
        return extended
    try:
        expected = evaluate_term(term, binding)
    except KeyError:
        return None
    return binding if _normalise(expected) == _normalise(value) else None


def compare(op: str, left: Any, right: Any) -> bool:
    ln, rn = _normalise(left), _normalise(right)
    if op == "eq":
        return ln == rn
    if op == "ne":
        return ln != rn
    if op == "in":
        items = right if isinstance(right, (list, tuple, set)) else [right]
        return ln in [_normalise(i) for i in items]
    if op == "not_in":
        items = right if isinstance(right, (list, tuple, set)) else [right]
        return ln not in [_normalise(i) for i in items]
    if op == "contains":
        return isinstance(left, str) and isinstance(right, str) and \
            right.lower() in left.lower()
    if not isinstance(ln, (int, float)) or not isinstance(rn, (int, float)):
        return False
    if op == "lt":
        return ln < rn
    if op == "lte":
        return ln <= rn
    if op == "gt":
        return ln > rn
    if op == "gte":
        return ln >= rn
    return False


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class Match:
    binding: Binding
    premises: list[FactDTO] = field(default_factory=list)


def match_body(body: list[Condition], index: FactIndex,
               binding: Binding | None = None) -> list[Match]:
    """All substitutions satisfying the conjunctive body."""
    matches = [Match(binding=dict(binding or {}), premises=[])]
    for condition in body:
        next_matches: list[Match] = []
        for current in matches:
            next_matches.extend(_match_condition(condition, index, current))
            if len(next_matches) > 5000:
                break
        matches = next_matches
        if not matches:
            return []
    return matches


def _match_condition(condition: Condition, index: FactIndex, current: Match) -> list[Match]:
    if condition.type == "comparison":
        try:
            left = evaluate_term(condition.left, current.binding)
            right = evaluate_term(condition.right, current.binding)
        except KeyError:
            return []
        return [current] if compare(condition.op, left, right) else []

    if condition.type == "absent":
        try:
            subject = evaluate_term(condition.subject, current.binding)
        except KeyError:
            return []
        return [] if index.has(subject, condition.predicate) else [current]

    out: list[Match] = []
    for fact in index.by_predicate(condition.predicate):
        binding = unify_term(condition.subject, fact.subject, current.binding)
        if binding is None:
            continue
        binding = unify_term(condition.value, unwrap(fact.object), binding)
        if binding is None:
            continue
        out.append(Match(binding=binding, premises=current.premises + [fact]))
    return out


# ---------------------------------------------------------------------------
# Forward chaining
# ---------------------------------------------------------------------------

@dataclass
class InferenceStep:
    step: int
    rule_code: str
    rule_name: str
    rule_text: str
    premises: list[str]
    premise_fact_ids: list[str]
    conclusion: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step, "rule_code": self.rule_code, "rule_name": self.rule_name,
            "rule_text": self.rule_text, "premises": self.premises,
            "premise_fact_ids": self.premise_fact_ids, "conclusion": self.conclusion,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class Contradiction:
    subject: str
    predicate: str
    values: list[Any]
    fact_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    severity: str = "error"
    resolution: str = ""
    kind: str = "functional_conflict"
    # Set when an ordering signal (a newer effective date) identifies which value
    # supersedes the others. The conflict is still reported; it is not hidden.
    preferred_value: Any = None
    preferred_fact_id: str = ""
    superseded_fact_ids: list[str] = field(default_factory=list)
    superseded_values: list[Any] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.preferred_fact_id != "" or self.preferred_value is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject, "predicate": self.predicate,
            "values": self.values, "fact_ids": self.fact_ids,
            "evidence": self.evidence, "document_ids": self.document_ids,
            "chunk_ids": self.chunk_ids, "severity": self.severity,
            "resolution": self.resolution, "kind": self.kind,
            "resolved": self.is_resolved, "preferred_value": self.preferred_value,
            "superseded_values": self.superseded_values,
            "superseded_fact_ids": self.superseded_fact_ids,
        }


@dataclass
class InferenceResult:
    facts: list[FactDTO] = field(default_factory=list)          # base + derived
    derived: list[FactDTO] = field(default_factory=list)
    steps: list[InferenceStep] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    rules_considered: list[str] = field(default_factory=list)
    rules_fired: list[str] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False

    def value_of(self, subject: str, predicate: str) -> FactDTO | None:
        candidates = [f for f in self.facts
                      if f.subject == subject and f.predicate == predicate]
        if not candidates:
            return None
        # Prefer a DIRECT fact; then the highest confidence.
        candidates.sort(key=lambda f: (f.kind is not FactKind.DIRECT, -f.confidence))
        return candidates[0]


class SymbolicEngine:
    def __init__(self, rules: list[CompiledRule], *, validate_stratification: bool = True):
        self.rules = sorted(rules, key=lambda r: (-r.priority, r.code))
        if validate_stratification:
            check_stratification(self.rules)

    # -- forward chaining --------------------------------------------------
    def forward(self, facts: list[FactDTO]) -> InferenceResult:
        index = FactIndex.build(facts)
        result = InferenceResult(rules_considered=[r.code for r in self.rules])
        step_number = 0
        fired: set[str] = set()

        for iteration in range(MAX_ITERATIONS):
            result.iterations = iteration + 1
            new_this_round: list[tuple[FactDTO, InferenceStep]] = []
            for rule in self.rules:
                for match in match_body(rule.body, index):
                    derived = self._instantiate(rule, match)
                    if derived is None:
                        continue
                    signature = index._signature(derived)
                    if signature in index._seen:
                        continue
                    step_number += 1
                    step = InferenceStep(
                        step=step_number, rule_code=rule.code, rule_name=rule.name,
                        rule_text=render_rule(rule),
                        premises=[p.render() for p in match.premises],
                        premise_fact_ids=[p.fact_id for p in match.premises if p.fact_id],
                        conclusion=derived.render(), confidence=derived.confidence,
                    )
                    new_this_round.append((derived, step))
                    fired.add(rule.code)
                    if len(result.derived) + len(new_this_round) >= MAX_DERIVED_FACTS:
                        result.truncated = True
                        break
                if result.truncated:
                    break
            if not new_this_round:
                break
            for derived, step in new_this_round:
                if index.add(derived):
                    result.derived.append(derived)
                    result.steps.append(step)
            if result.truncated:
                break

        result.facts = list(index.facts)
        result.rules_fired = sorted(fired)
        result.contradictions = detect_contradictions(result.facts)
        return result

    def _instantiate(self, rule: CompiledRule, match: Match) -> FactDTO | None:
        try:
            subject = evaluate_term(rule.head.subject, match.binding)
            value = evaluate_term(rule.head.value, match.binding)
        except (KeyError, TypeError):
            return None
        if value is None or subject is None:
            return None
        premise_confidence = min((p.confidence for p in match.premises), default=1.0)
        confidence = round(premise_confidence * rule.confidence, 6)
        provenance = Provenance(
            source_kind="rule",
            extractor=rule.code,
            rule_codes=[rule.code],
            premise_fact_ids=[p.fact_id for p in match.premises if p.fact_id],
            evidence_text=render_rule(rule),
            document_id=match.premises[0].provenance.document_id if match.premises else None,
            chunk_id=match.premises[0].provenance.chunk_id if match.premises else None,
            document_title=(match.premises[0].provenance.document_title
                            if match.premises else ""),
        )
        unit = rule.head.unit or _unit_from_premises(match.premises, rule.head.predicate)
        return make_fact(str(subject), rule.head.predicate, value, unit=unit,
                         kind=FactKind.INFERRED, confidence=confidence,
                         provenance=provenance)

    # -- backward chaining -------------------------------------------------
    def prove(self, subject: str, predicate: str, facts: list[FactDTO],
              expected_value: Any = None) -> "Proof | None":
        index = FactIndex.build(facts)
        return self._prove(subject, predicate, expected_value, index, depth=0,
                           seen=set())

    def _prove(self, subject: Any, predicate: str, expected: Any, index: FactIndex,
               depth: int, seen: set[tuple[Any, str]]) -> "Proof | None":
        if depth > MAX_PROOF_DEPTH:
            return None
        key = (subject, predicate)
        if key in seen:
            return None  # cycle
        seen = seen | {key}

        # 1. A known fact settles it.
        for fact in index.by_predicate(predicate):
            if fact.subject != subject:
                continue
            if expected is not None and _normalise(unwrap(fact.object)) != _normalise(expected):
                continue
            return Proof(goal=f"{subject}.{predicate}", value=unwrap(fact.object),
                         fact=fact, subgoals=[], rule_code="",
                         explanation=f"Known fact: {fact.render()}")

        # 2. Otherwise, find a rule whose head can produce it.
        for rule in self.rules:
            if rule.head.predicate != predicate:
                continue
            binding: Binding = {}
            bound = unify_term(rule.head.subject, subject, binding)
            if bound is None:
                continue
            subgoals: list[Proof] = []
            resolved = self._solve_body(rule, bound, index, depth, seen, subgoals)
            if resolved is None:
                continue
            try:
                value = evaluate_term(rule.head.value, resolved)
            except (KeyError, TypeError):
                continue
            if expected is not None and _normalise(value) != _normalise(expected):
                continue
            derived = make_fact(
                str(subject), predicate, value, kind=FactKind.INFERRED,
                confidence=round(rule.confidence * min(
                    (p.fact.confidence for p in subgoals if p.fact), default=1.0), 6),
                provenance=Provenance(source_kind="rule", extractor=rule.code,
                                      rule_codes=[rule.code],
                                      evidence_text=render_rule(rule)),
            )
            return Proof(goal=f"{subject}.{predicate}", value=value, fact=derived,
                         subgoals=subgoals, rule_code=rule.code,
                         explanation=render_rule(rule))
        return None

    def _solve_body(self, rule: CompiledRule, binding: Binding, index: FactIndex,
                    depth: int, seen: set, subgoals: list["Proof"]) -> Binding | None:
        current = dict(binding)
        for condition in rule.body:
            if condition.type == "comparison":
                try:
                    left = evaluate_term(condition.left, current)
                    right = evaluate_term(condition.right, current)
                except KeyError:
                    return None
                if not compare(condition.op, left, right):
                    return None
                continue
            if condition.type == "absent":
                try:
                    subject = evaluate_term(condition.subject, current)
                except KeyError:
                    return None
                if index.has(subject, condition.predicate):
                    return None
                continue

            try:
                subject = evaluate_term(condition.subject, current)
            except KeyError:
                # Unbound subject in a backward proof: try each known subject.
                candidates = index.by_predicate(condition.predicate)
                if not candidates:
                    return None
                subject = candidates[0].subject
                bound = unify_term(condition.subject, subject, current)
                if bound is None:
                    return None
                current = bound

            sub = self._prove(subject, condition.predicate, None, index, depth + 1, seen)
            if sub is None:
                return None
            subgoals.append(sub)
            bound = unify_term(condition.value, sub.value, current)
            if bound is None:
                return None
            current = bound
        return current


def _unit_from_premises(premises: list[FactDTO], predicate: str) -> str:
    """Inherit a unit from a premise that speaks about the same predicate."""
    for fact in premises:
        if fact.predicate == predicate and isinstance(fact.object, dict):
            unit = fact.object.get("unit", "")
            if unit:
                return unit
    return ""


@dataclass
class Proof:
    goal: str
    value: Any
    fact: FactDTO | None
    subgoals: list["Proof"]
    rule_code: str = ""
    explanation: str = ""

    def flatten(self) -> list["Proof"]:
        out: list[Proof] = []
        for sub in self.subgoals:
            out.extend(sub.flatten())
        out.append(self)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal, "value": self.value, "rule_code": self.rule_code,
            "explanation": self.explanation,
            "subgoals": [s.as_dict() for s in self.subgoals],
        }

    def steps(self) -> list[InferenceStep]:
        steps: list[InferenceStep] = []
        for i, node in enumerate(self.flatten(), start=1):
            if not node.rule_code:
                continue
            steps.append(InferenceStep(
                step=i, rule_code=node.rule_code, rule_name=node.rule_code,
                rule_text=node.explanation,
                premises=[s.goal + " = " + str(s.value) for s in node.subgoals],
                premise_fact_ids=[s.fact.fact_id for s in node.subgoals
                                  if s.fact and s.fact.fact_id],
                conclusion=f"{node.goal} = {node.value}",
                confidence=node.fact.confidence if node.fact else 1.0,
            ))
        return steps


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

# Predicates that may legitimately hold several values for one subject.
MULTIVALUED_PREDICATES: set[str] = {
    "tag", "role", "keyword", "category", "member", "note",
}


def detect_contradictions(facts: list[FactDTO], *,
                          multivalued: set[str] | None = None) -> list[Contradiction]:
    """Find functional-property violations: one subject-predicate, two values.

    Only DIRECT and INFERRED facts participate. MODEL_GENERATED content is never
    allowed to create or resolve a contradiction.
    """
    multivalued = multivalued or MULTIVALUED_PREDICATES
    grouped: dict[tuple[str, str], list[FactDTO]] = {}
    for fact in facts:
        if fact.kind is FactKind.MODEL_GENERATED:
            continue
        if fact.predicate in multivalued:
            continue
        grouped.setdefault((fact.subject, fact.predicate), []).append(fact)

    contradictions: list[Contradiction] = []
    for (subject, predicate), group in sorted(grouped.items()):
        values = {}
        for fact in group:
            values.setdefault(_normalise(unwrap(fact.object)), []).append(fact)
        if len(values) < 2:
            continue
        involved = [f for facts_ in values.values() for f in facts_]
        contradiction = Contradiction(
            subject=subject, predicate=predicate,
            values=[unwrap(f.object) for f in involved],
            fact_ids=[f.fact_id for f in involved if f.fact_id],
            evidence=[f.provenance.evidence_text for f in involved
                      if f.provenance.evidence_text][:4],
            document_ids=sorted({str(f.provenance.document_id) for f in involved
                                 if f.provenance.document_id}),
            chunk_ids=sorted({str(f.provenance.chunk_id) for f in involved
                              if f.provenance.chunk_id}),
        )
        _apply_resolution(contradiction, involved)
        contradictions.append(contradiction)
    return contradictions


def _apply_resolution(contradiction: Contradiction, facts: list[FactDTO]) -> None:
    """Record which value supersedes the others, when there is a reason to say so.

    A newer effective date is an ordering signal a document set actually carries:
    a 2026 handbook replaces a 2019 one. When that signal exists the conflict is
    downgraded from `error` to `warning` and the preferred value is recorded - but
    the conflict is still reported to the user, and the superseded value is still
    shown. The system never silently picks a side without saying so.
    """
    dated = [f for f in facts if f.provenance.effective_date]
    distinct_dates = {f.provenance.effective_date for f in dated}
    if len(dated) == len(facts) and len(distinct_dates) > 1:
        newest = max(dated, key=lambda f: f.provenance.effective_date or "")
        contradiction.preferred_value = unwrap(newest.object)
        contradiction.preferred_fact_id = newest.fact_id
        contradiction.superseded_fact_ids = [
            f.fact_id for f in facts if f is not newest and f.fact_id
        ]
        contradiction.superseded_values = [
            unwrap(f.object) for f in facts if f is not newest
        ]
        contradiction.severity = "warning"
        contradiction.kind = "superseded_version"
        contradiction.resolution = (
            f"{newest.provenance.document_title or 'The most recent source'} is "
            f"effective {newest.provenance.effective_date} and supersedes the older "
            f"source(s); its value is {unwrap(newest.object)}."
        )
        return

    confidences = {round(f.confidence, 3) for f in facts}
    if len(confidences) > 1:
        best = max(facts, key=lambda f: f.confidence)
        contradiction.resolution = (
            f"Extraction confidence differs; the strongest extraction gives "
            f"{unwrap(best.object)}. Human review is needed."
        )
        return
    contradiction.resolution = (
        "Sources disagree with no ordering signal. Human review is needed."
    )


# ---------------------------------------------------------------------------
# Integrity constraints
# ---------------------------------------------------------------------------

@dataclass
class ConstraintViolation:
    code: str
    description: str
    subject: str
    predicate: str
    value: Any
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "description": self.description, "subject": self.subject,
            "predicate": self.predicate, "value": self.value, "severity": self.severity,
        }


def check_constraints(facts: list[FactDTO],
                      constraints: list[dict[str, Any]]) -> list[ConstraintViolation]:
    """Evaluate domain constraints such as ``age >= 18`` over the fact set.

    Constraint shape: {"code", "description", "predicate", "op", "value", "severity"}
    """
    violations: list[ConstraintViolation] = []
    for constraint in constraints:
        predicate = constraint.get("predicate")
        op = constraint.get("op", "gte")
        bound = constraint.get("value")
        if not predicate:
            continue
        for fact in facts:
            if fact.predicate != predicate:
                continue
            if not compare(op, unwrap(fact.object), bound):
                violations.append(ConstraintViolation(
                    code=constraint.get("code", "CONSTRAINT"),
                    description=constraint.get(
                        "description", f"{predicate} {op} {bound} must hold"),
                    subject=fact.subject, predicate=predicate,
                    value=unwrap(fact.object),
                    severity=constraint.get("severity", "error"),
                ))
    return violations

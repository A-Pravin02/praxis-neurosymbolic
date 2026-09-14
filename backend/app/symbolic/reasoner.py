"""Three-valued, goal-directed symbolic reasoning.

This is where decisions are made. Nothing in this module reads a question, calls a
language model, or looks at text: it takes a goal such as `return_allowed(ORD-12346)`,
the company knowledge graph, and any facts the user stated, and it proves or refutes
the goal from the rules.

Why three values
----------------
Classical rule engines are two-valued: a condition holds or it does not, and a fact
that is missing counts as false. That is exactly why rule-based chatbots answer "no"
or "out of my reach" to questions they should be able to answer. This reasoner keeps
three outcomes apart:

    satisfied   the knowledge proves the condition
    violated    the knowledge refutes the condition     -> why-not explanations
    unknown     nobody has stated what it depends on    -> missing information,
                                                           conditional answers

So when a rule cannot be decided, the result says *which* fact is missing, and when a
value can be computed except for one input, the result is the value under an explicit
assumption rather than a refusal.

Open and closed world
---------------------
A real company record is a closed world: if an order's record has no `delivered_on`,
it was not delivered. A hypothetical case the user describes ("if I spent ₹8,750") is
an open world: anything not stated is unknown, not false. The reasoner is told which
subjects are open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.symbolic.dsl import KnowledgeProgram, RuleSpec
from app.symbolic.engine import _arith, _normalise, compare
from app.symbolic.knowledge_graph import FactRef, KnowledgeGraph
from app.symbolic.render import format_value, humanize, render_template
from app.symbolic.schema import Const, Expr, Var

SATISFIED, VIOLATED, UNKNOWN = "satisfied", "violated", "unknown"
PROVED, REFUTED, CONDITIONAL = "proved", "refuted", "conditional"

_SYMBOL = {"eq": "=", "ne": "≠", "lt": "<", "lte": "≤", "gt": ">", "gte": "≥",
           "in": "in", "not_in": "not in", "contains": "contains"}
_ARITH_SYMBOL = {"add": "+", "sub": "−", "mul": "×", "div": "÷"}


# ---------------------------------------------------------------------------
# Values nobody has stated
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Unknown:
    """A value the reasoning needs and nobody has provided."""

    predicate: str
    subject: str


@dataclass(frozen=True)
class Symbolic:
    """An arithmetic result with an unknown inside it, kept exact rather than guessed.

    `min(8750, 8000, 9 × distance)` simplifies to `min(8000, 9 × distance)`: the known
    part is computed, the unknown part is named.
    """

    op: str
    args: tuple[Any, ...]

    def unknowns(self) -> list[Unknown]:
        found: list[Unknown] = []
        for arg in self.args:
            if isinstance(arg, Unknown):
                found.append(arg)
            elif isinstance(arg, Symbolic):
                found.extend(arg.unknowns())
        return found

    def render(self, value_type: str, *, graph=None, program=None) -> str:
        def part(arg: Any) -> str:
            if isinstance(arg, (int, float)) and not isinstance(arg, bool):
                return format_value(arg, value_type, graph=graph, program=program)
            if isinstance(arg, Unknown):
                return f"the {humanize(arg.predicate)}"
            if isinstance(arg, Symbolic):
                return arg.render(value_type, graph=graph, program=program)
            return str(arg)

        if self.op in ("min", "max"):
            word = "the lower of" if self.op == "min" else "the higher of"
            items = [part(a) for a in self.args]
            return f"{word} {', '.join(items[:-1])} and {items[-1]}" if len(items) > 1 \
                else items[0]
        if self.op in _ARITH_SYMBOL:
            return f" {_ARITH_SYMBOL[self.op]} ".join(part(a) for a in self.args)
        return f"{self.op}({', '.join(part(a) for a in self.args)})"


def _is_open(value: Any) -> bool:
    return isinstance(value, (Unknown, Symbolic))


def _leaves(value: Any) -> list[Unknown]:
    if isinstance(value, Unknown):
        return [value]
    if isinstance(value, Symbolic):
        return value.unknowns()
    return []


def _clean_number(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, 6)
        return int(rounded) if rounded.is_integer() else rounded
    return value


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class ConditionResult:
    index: int
    expression: str          # the condition with known values substituted
    status: str
    note: str = ""           # the rule's own words for why this condition failed
    missing: list[Unknown] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "condition": self.expression, "status": self.status,
                "note": self.note,
                "missing": [{"predicate": m.predicate, "subject": m.subject}
                            for m in self.missing]}


@dataclass
class RuleOutcome:
    spec: RuleSpec
    status: str
    value: Any
    bindings: dict[str, Any]
    types: dict[str, str]
    conditions: list[ConditionResult]
    because: str = ""
    failed: ConditionResult | None = None
    premises: list["GoalResult"] = field(default_factory=list)

    @property
    def priority(self) -> int:
        return self.spec.rule.priority

    @property
    def assumptions(self) -> list[ConditionResult]:
        return [c for c in self.conditions if c.status == UNKNOWN]

    def missing(self) -> list[Unknown]:
        seen: dict[tuple[str, str], Unknown] = {}
        for condition in self.assumptions:
            for leaf in condition.missing:
                seen.setdefault((leaf.predicate, leaf.subject), leaf)
        for premise in self.premises:
            if premise.status == CONDITIONAL:
                for leaf in premise.missing:
                    seen.setdefault((leaf.predicate, leaf.subject), leaf)
        return list(seen.values())

    def as_dict(self, program: KnowledgeProgram, graph: KnowledgeGraph) -> dict[str, Any]:
        head_type = program.value_type(self.spec.rule.head.predicate)
        return {
            "rule": self.spec.code, "name": self.spec.rule.name, "clauses": self.spec.clauses,
            "status": self.status, "priority": self.priority,
            "value": format_value(self.value, head_type, graph=graph, program=program)
            if self.status != REFUTED else None,
            "because": self.because,
            "failed": self.failed.as_dict() if self.failed else None,
            "conditions": [c.as_dict() for c in self.conditions],
        }


@dataclass
class GoalResult:
    subject: str
    predicate: str
    status: str               # determined | conditional | default | unknown | absent | conflict
    value: Any = None
    source: str = "none"      # record | stated | derived | default | none
    decided_by: RuleOutcome | None = None
    outcomes: list[RuleOutcome] = field(default_factory=list)
    missing: list[Unknown] = field(default_factory=list)
    facts: list[FactRef] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.status in ("determined", "default")

    def refutations(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if o.status == REFUTED]

    def conditionals(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if o.status == CONDITIONAL]

    def rules_used(self) -> list[RuleOutcome]:
        """The decisive rule and every derivation beneath it, deduplicated."""
        found: dict[str, RuleOutcome] = {}

        def walk(outcome: RuleOutcome | None) -> None:
            if outcome is None or outcome.spec.code in found:
                return
            found[outcome.spec.code] = outcome
            for premise in outcome.premises:
                if premise.decided_by is not None and premise.source == "derived":
                    walk(premise.decided_by)
                for conditional in premise.conditionals():
                    walk(conditional)

        walk(self.decided_by)
        for outcome in self.conditionals():
            walk(outcome)
        return list(found.values())


# ---------------------------------------------------------------------------
# The reasoner
# ---------------------------------------------------------------------------

class Reasoner:
    def __init__(self, program: KnowledgeProgram, graph: KnowledgeGraph, *,
                 stated: dict[tuple[str, str], Any] | None = None,
                 open_subjects: set[str] | None = None, max_depth: int = 16) -> None:
        self.program = program
        self.graph = graph
        self.stated = dict(stated or {})
        self.open_subjects = set(open_subjects or ())
        self.max_depth = max_depth
        self._memo: dict[tuple[str, str], GoalResult] = {}
        self._active: set[tuple[str, str]] = set()
        self.touched: dict[tuple[str, str, str], FactRef] = {}

    # -- public -------------------------------------------------------------
    def evaluate(self, subject: str, predicate: str) -> GoalResult:
        key = (str(subject), predicate)
        if key in self._memo:
            return self._memo[key]
        if key in self._active or len(self._active) > self.max_depth:
            return GoalResult(key[0], predicate, UNKNOWN, missing=[Unknown(predicate, key[0])])
        self._active.add(key)
        try:
            result = self._evaluate(*key)
        finally:
            self._active.discard(key)
        self._memo[key] = result
        return result

    # -- evaluation ---------------------------------------------------------
    def _evaluate(self, subject: str, predicate: str) -> GoalResult:
        is_open = subject in self.open_subjects

        recorded = self.graph.values(subject, predicate)
        if recorded:
            return self._fact(subject, predicate, recorded[0], "record")
        if is_open and (subject, predicate) in self.stated:
            return self._fact(subject, predicate, self.stated[(subject, predicate)], "stated")

        specs = self.program.rules_for(predicate)
        if specs:
            outcomes = [o for o in (self._apply(spec, subject) for spec in specs)
                        if o is not None]
            result = self._combine(subject, predicate, outcomes)
            # A closed record can still take a user's statement for something the
            # company never records (why they are returning it), but never for
            # something the rules derive from the record.
            if result.status in ("default", "absent") and (subject, predicate) in self.stated \
                    and self.program.is_input(predicate) and not result.refutations():
                return self._fact(subject, predicate, self.stated[(subject, predicate)],
                                  "stated")
            return result

        if (subject, predicate) in self.stated:
            return self._fact(subject, predicate, self.stated[(subject, predicate)], "stated")
        if self.program.is_input(predicate) or is_open:
            return GoalResult(subject, predicate, UNKNOWN,
                              missing=[Unknown(predicate, subject)])
        return GoalResult(subject, predicate, "absent")

    def _fact(self, subject: str, predicate: str, value: Any, source: str) -> GoalResult:
        ref = FactRef(subject, predicate, value, source)
        self.touched[(subject, predicate, source)] = ref
        return GoalResult(subject, predicate, "determined", value=value, source=source,
                          facts=[ref])

    def _combine(self, subject: str, predicate: str,
                 outcomes: list[RuleOutcome]) -> GoalResult:
        proved = [o for o in outcomes if o.status == PROVED]
        conditional = [o for o in outcomes if o.status == CONDITIONAL]

        if proved:
            top = max(o.priority for o in proved)
            best = [o for o in proved if o.priority == top]
            values = {repr(_normalise(o.value)) for o in best}
            if len(values) > 1:
                return GoalResult(subject, predicate, "conflict", outcomes=outcomes)
            winner = best[0]
            result = GoalResult(subject, predicate, "determined", value=winner.value,
                                source="derived", decided_by=winner, outcomes=outcomes)
            # A higher-priority exception that *might* apply and would change the
            # answer is worth reporting: it is what "unless..." means.
            overriding = [c for c in conditional if c.priority > top
                          and repr(_normalise(c.value)) != repr(_normalise(winner.value))]
            result.missing = _unique([m for c in overriding for m in c.missing()])
            self.touched[(subject, predicate, "derived")] = FactRef(
                subject, predicate, winner.value, "derived", winner.spec.code)
            return result

        if conditional:
            top = max(o.priority for o in conditional)
            lead = next(o for o in conditional if o.priority == top)
            return GoalResult(subject, predicate, CONDITIONAL, value=lead.value,
                              source="derived", decided_by=lead, outcomes=outcomes,
                              missing=_unique([m for c in conditional for m in c.missing()]))

        default = self.program.defaults.get(predicate)
        if default is not None:
            return GoalResult(subject, predicate, "default", value=default.value,
                              source="default", outcomes=outcomes)
        return GoalResult(subject, predicate, "absent", outcomes=outcomes)

    def _apply(self, spec: RuleSpec, subject: str) -> RuleOutcome | None:
        rule = spec.rule
        bindings: dict[str, Any] = {}
        types: dict[str, str] = {}
        head_subject = rule.head.subject
        if isinstance(head_subject, Var):
            bindings[head_subject.name] = subject
        elif isinstance(head_subject, Const) and str(head_subject.value) != subject:
            return None

        conditions: list[ConditionResult] = []
        premises: list[GoalResult] = []
        failed: ConditionResult | None = None

        for index, condition in enumerate(rule.body):
            note_template = spec.otherwise[index] if index < len(spec.otherwise) else ""

            if condition.type == "fact":
                result = self._fact_condition(condition, bindings, types, premises, index)
            elif condition.type == "comparison":
                result = self._comparison(condition, bindings, types, index)
            else:
                result = self._absent(condition, bindings, premises, index)

            if result.status == VIOLATED:
                result.note = render_template(note_template, bindings, types,
                                              graph=self.graph, program=self.program)
                conditions.append(result)
                failed = result
                break
            conditions.append(result)

        if failed is not None:
            return RuleOutcome(spec, REFUTED, None, bindings, types, conditions,
                               failed=failed, premises=premises)

        head_value = _clean_number(self._value(rule.head.value, bindings))
        head_type = self.program.value_type(rule.head.predicate)
        if isinstance(rule.head.value, Var):
            types.setdefault(rule.head.value.name, head_type)
        undecided = any(c.status == UNKNOWN for c in conditions) or \
            any(p.status == CONDITIONAL for p in premises) or _is_open(head_value)
        because = render_template(spec.because, bindings, types, graph=self.graph,
                                  program=self.program)
        return RuleOutcome(spec, CONDITIONAL if undecided else PROVED, head_value, bindings,
                           types, conditions, because=because, premises=premises)

    # -- conditions ---------------------------------------------------------
    def _fact_condition(self, condition, bindings, types, premises, index) -> ConditionResult:
        subject = self._value(condition.subject, bindings)
        predicate = condition.predicate
        text = f"{predicate}({self._show(subject)})"
        if _is_open(subject):
            self._bind_unknown(condition.value, bindings, types, predicate, str(subject))
            return ConditionResult(index, text, UNKNOWN, missing=_leaves(subject))

        premise = self.evaluate(str(subject), predicate)
        premises.append(premise)
        value_type = self.program.value_type(predicate)

        if premise.status in ("determined", "default", CONDITIONAL):
            value = premise.value
            if isinstance(condition.value, Var) and condition.value.name not in bindings:
                bindings[condition.value.name] = value
                types[condition.value.name] = value_type
                status = UNKNOWN if _is_open(value) else SATISFIED
                return ConditionResult(index, f"{text} = {self._show(value, value_type)}",
                                       status, missing=_leaves(value))
            expected = self._value(condition.value, bindings)
            shown = f"{text} = {self._show(value, value_type)}"
            if _is_open(value) or _is_open(expected):
                return ConditionResult(index, shown, UNKNOWN,
                                       missing=_leaves(value) + _leaves(expected))
            ok = _normalise(value) == _normalise(expected)
            return ConditionResult(index, shown + ("" if ok else
                                                   f" (needs {self._show(expected, value_type)})"),
                                   SATISFIED if ok else VIOLATED)

        if premise.status in (UNKNOWN, "conflict"):
            self._bind_unknown(condition.value, bindings, types, predicate, str(subject))
            return ConditionResult(index, f"{text} = ?", UNKNOWN,
                                   missing=premise.missing or [Unknown(predicate, str(subject))])

        # absent in a closed world: the record holds no such fact
        return ConditionResult(index, f"{text} is not recorded", VIOLATED)

    def _bind_unknown(self, value_term, bindings, types, predicate, subject) -> None:
        if isinstance(value_term, Var) and value_term.name not in bindings:
            bindings[value_term.name] = Unknown(predicate, subject)
            types[value_term.name] = self.program.value_type(predicate)

    def _comparison(self, condition, bindings, types, index) -> ConditionResult:
        left = self._value(condition.left, bindings)
        right = self._value(condition.right, bindings)
        value_type = self._term_type(condition.left, types) or self._term_type(
            condition.right, types) or "text"
        text = (f"{self._show(left, value_type)} {_SYMBOL[condition.op]} "
                f"{self._show(right, value_type)}")
        if _is_open(left) or _is_open(right):
            return ConditionResult(index, text, UNKNOWN, missing=_leaves(left) + _leaves(right))
        return ConditionResult(index, text,
                               SATISFIED if compare(condition.op, left, right) else VIOLATED)

    def _absent(self, condition, bindings, premises, index) -> ConditionResult:
        subject = self._value(condition.subject, bindings)
        text = f"not {condition.predicate}({self._show(subject)})"
        if _is_open(subject):
            return ConditionResult(index, text, UNKNOWN, missing=_leaves(subject))
        premise = self.evaluate(str(subject), condition.predicate)
        premises.append(premise)
        if premise.status in (UNKNOWN, CONDITIONAL, "conflict"):
            return ConditionResult(index, text, UNKNOWN,
                                   missing=premise.missing or
                                   [Unknown(condition.predicate, str(subject))])
        truthy = premise.status in ("determined", "default") and \
            premise.value not in (False, None, 0, "false", "no")
        return ConditionResult(index, text, VIOLATED if truthy else SATISFIED)

    # -- terms --------------------------------------------------------------
    def _value(self, term: Any, bindings: dict[str, Any]) -> Any:
        if isinstance(term, Const):
            return term.value
        if isinstance(term, Var):
            return bindings.get(term.name, Unknown(term.name, ""))
        if isinstance(term, Expr):
            args = [self._value(a, bindings) for a in term.args]
            if any(_is_open(a) for a in args):
                if term.op in ("min", "max"):
                    known = [a for a in args if not _is_open(a)]
                    rest = [a for a in args if _is_open(a)]
                    if known:
                        pick = min if term.op == "min" else max
                        known = [_clean_number(pick(float(k) for k in known))]
                    return Symbolic(term.op, tuple(known + rest))
                return Symbolic(term.op, tuple(_clean_number(a) if not _is_open(a) else a
                                               for a in args))
            return _clean_number(_arith(term.op, args))
        return term

    def _term_type(self, term: Any, types: dict[str, str]) -> str:
        if isinstance(term, Var):
            return types.get(term.name, "")
        if isinstance(term, Expr):
            for arg in term.args:
                found = self._term_type(arg, types)
                if found:
                    return found
        return ""

    def _show(self, value: Any, value_type: str = "text") -> str:
        if isinstance(value, Unknown):
            return f"?{value.predicate}"
        return format_value(value, value_type, graph=self.graph, program=self.program)


def _unique(items: list[Unknown]) -> list[Unknown]:
    seen: dict[tuple[str, str], Unknown] = {}
    for item in items:
        seen.setdefault((item.predicate, item.subject), item)
    return list(seen.values())

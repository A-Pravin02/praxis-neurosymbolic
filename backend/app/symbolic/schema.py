"""Rule grammar and validation.

Rules are data, never code. A rule is a Horn clause: a conjunctive body and a
single head. Validation is strict and total — an invalid rule is rejected at
write time, so the engine never has to defend itself against malformed input at
inference time.

Grammar
-------
    term      := {"var": "?name"} | {"const": <scalar>}
               | {"expr": {"op": <arith>, "args": [term, ...]}}
    condition := {"type": "fact", "subject": term, "predicate": <name>, "value": term}
               | {"type": "comparison", "left": term, "op": <cmp>, "right": term}
               | {"type": "absent", "subject": term, "predicate": <name>}
    head      := {"type": "fact", "subject": term, "predicate": <name>, "value": term}

`absent` is negation as failure over the *closed* fact set. It is stratified:
a rule using `absent` may not (transitively) depend on a predicate it derives.
That restriction is checked when the rule set is loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import RuleError

CMP_OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "contains"}
ARITH_OPS = {"add", "sub", "mul", "div", "min", "max", "abs", "floor", "ceil"}
CONDITION_TYPES = {"fact", "comparison", "absent"}

_VAR_RE = re.compile(r"^\?[A-Za-z_][A-Za-z0-9_]*$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,119}$")

MAX_CONDITIONS = 24
MAX_TERM_DEPTH = 5


@dataclass(frozen=True)
class Var:
    name: str

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self.name


@dataclass(frozen=True)
class Const:
    value: Any

    def __repr__(self) -> str:  # pragma: no cover
        return repr(self.value)


@dataclass(frozen=True)
class Expr:
    op: str
    args: tuple[Any, ...]


Term = Var | Const | Expr


@dataclass
class Condition:
    type: str
    subject: Term | None = None
    predicate: str = ""
    value: Term | None = None
    left: Term | None = None
    op: str = ""
    right: Term | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Head:
    subject: Term
    predicate: str
    value: Term
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompiledRule:
    code: str
    body: list[Condition]
    head: Head
    name: str = ""
    description: str = ""
    priority: int = 100
    confidence: float = 1.0
    origin: str = "curated"
    source_document_id: str | None = None

    def variables(self) -> set[str]:
        found: set[str] = set()
        for condition in self.body:
            for term in (condition.subject, condition.value, condition.left, condition.right):
                found |= _term_vars(term)
        found |= _term_vars(self.head.subject) | _term_vars(self.head.value)
        return found


def _term_vars(term: Term | None) -> set[str]:
    if isinstance(term, Var):
        return {term.name}
    if isinstance(term, Expr):
        out: set[str] = set()
        for arg in term.args:
            out |= _term_vars(arg)
        return out
    return set()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_term(raw: Any, *, depth: int = 0) -> Term:
    if depth > MAX_TERM_DEPTH:
        raise RuleError("Rule term is nested too deeply.")
    if isinstance(raw, (str, int, float, bool)) or raw is None:
        # Bare strings starting with '?' are variables; everything else is a constant.
        if isinstance(raw, str) and raw.startswith("?"):
            if not _VAR_RE.match(raw):
                raise RuleError(f"Invalid variable name: {raw!r}")
            return Var(raw)
        return Const(raw)
    if not isinstance(raw, dict):
        raise RuleError("A rule term must be a scalar or an object.")
    if "var" in raw:
        name = raw["var"]
        if not isinstance(name, str) or not _VAR_RE.match(name):
            raise RuleError(f"Invalid variable name: {name!r}")
        return Var(name)
    if "const" in raw:
        value = raw["const"]
        if value is not None and not isinstance(value, (str, int, float, bool, list)):
            raise RuleError("Rule constants must be scalars or lists.")
        return Const(value)
    if "expr" in raw:
        expr = raw["expr"]
        if not isinstance(expr, dict):
            raise RuleError("An 'expr' term must be an object.")
        op = expr.get("op")
        if op not in ARITH_OPS:
            raise RuleError(f"Unsupported arithmetic operator: {op!r}")
        args = expr.get("args")
        if not isinstance(args, list) or not (1 <= len(args) <= 4):
            raise RuleError("An arithmetic expression takes 1 to 4 arguments.")
        return Expr(op=op, args=tuple(parse_term(a, depth=depth + 1) for a in args))
    raise RuleError("A rule term object must have 'var', 'const' or 'expr'.")


def parse_condition(raw: Any) -> Condition:
    if not isinstance(raw, dict):
        raise RuleError("Each rule condition must be an object.")
    ctype = raw.get("type", "fact")
    if ctype not in CONDITION_TYPES:
        raise RuleError(f"Unsupported condition type: {ctype!r}")

    if ctype == "comparison":
        op = raw.get("op")
        if op not in CMP_OPS:
            raise RuleError(f"Unsupported comparison operator: {op!r}")
        if "left" not in raw or "right" not in raw:
            raise RuleError("A comparison needs 'left' and 'right'.")
        return Condition(type=ctype, left=parse_term(raw["left"]), op=op,
                         right=parse_term(raw["right"]), raw=raw)

    predicate = raw.get("predicate")
    if not isinstance(predicate, str) or not _NAME_RE.match(predicate):
        raise RuleError(f"Invalid predicate name: {predicate!r}")
    subject = parse_term(raw.get("subject", "?s"))
    if ctype == "absent":
        return Condition(type=ctype, subject=subject, predicate=predicate, raw=raw)
    return Condition(type="fact", subject=subject, predicate=predicate,
                     value=parse_term(raw.get("value", "?v")), raw=raw)


def parse_head(raw: Any) -> Head:
    if not isinstance(raw, dict):
        raise RuleError("The rule head must be an object.")
    if raw.get("type", "fact") != "fact":
        raise RuleError("Only 'fact' heads are supported.")
    predicate = raw.get("predicate")
    if not isinstance(predicate, str) or not _NAME_RE.match(predicate):
        raise RuleError(f"Invalid head predicate: {predicate!r}")
    if "value" not in raw:
        raise RuleError("The rule head needs a 'value'.")
    return Head(subject=parse_term(raw.get("subject", "?s")), predicate=predicate,
                value=parse_term(raw["value"]), raw=raw)


def compile_rule(code: str, body: Any, head: Any, *, name: str = "",
                 description: str = "", priority: int = 100, confidence: float = 1.0,
                 origin: str = "curated") -> CompiledRule:
    if not isinstance(code, str) or not _NAME_RE.match(code):
        raise RuleError(f"Invalid rule code: {code!r}")
    if not isinstance(body, list) or not body:
        raise RuleError("A rule needs at least one condition.")
    if len(body) > MAX_CONDITIONS:
        raise RuleError(f"A rule may have at most {MAX_CONDITIONS} conditions.")
    conditions = [parse_condition(c) for c in body]
    compiled_head = parse_head(head)
    if not 0.0 <= confidence <= 1.0:
        raise RuleError("Rule confidence must be between 0 and 1.")

    rule = CompiledRule(code=code, body=conditions, head=compiled_head, name=name or code,
                        description=description, priority=int(priority),
                        confidence=float(confidence), origin=origin)

    # Every variable in the head must be bound by a positive body condition,
    # otherwise the rule could derive an unbounded set of facts.
    bound: set[str] = set()
    for condition in conditions:
        if condition.type == "fact":
            bound |= _term_vars(condition.subject) | _term_vars(condition.value)
    unbound = (_term_vars(rule.head.subject) | _term_vars(rule.head.value)) - bound
    if unbound:
        raise RuleError(
            "Head variables must be bound by a positive condition: "
            + ", ".join(sorted(unbound))
        )
    for condition in conditions:
        if condition.type == "comparison":
            free = (_term_vars(condition.left) | _term_vars(condition.right)) - bound
            if free:
                raise RuleError(
                    "Comparison variables must be bound first: " + ", ".join(sorted(free))
                )
        if condition.type == "absent":
            free = _term_vars(condition.subject) - bound
            if free:
                raise RuleError(
                    "Variables under 'absent' must be bound first: " + ", ".join(sorted(free))
                )
    return rule


def check_stratification(rules: list[CompiledRule]) -> None:
    """Reject rule sets where negation-as-failure could loop.

    A rule that tests `absent(p)` must not depend, directly or transitively, on a
    rule whose head derives `p`.
    """
    derived: dict[str, set[str]] = {}
    for rule in rules:
        derived.setdefault(rule.head.predicate, set()).add(rule.code)

    # predicate -> predicates it depends on positively
    depends: dict[str, set[str]] = {}
    for rule in rules:
        deps = depends.setdefault(rule.head.predicate, set())
        for condition in rule.body:
            if condition.type == "fact":
                deps.add(condition.predicate)

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            for nxt in depends.get(current, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    for rule in rules:
        for condition in rule.body:
            if condition.type != "absent":
                continue
            negated = condition.predicate
            if negated in derived and (
                negated == rule.head.predicate
                or rule.head.predicate in reachable(negated)
            ):
                raise RuleError(
                    f"Rule {rule.code} uses 'absent' on predicate '{negated}', which the "
                    f"rule set also derives. This is not stratified."
                )


def render_rule(rule: CompiledRule) -> str:
    """Readable form used in the audit trail and the UI."""
    parts = []
    for condition in rule.body:
        if condition.type == "fact":
            parts.append(f"{_render_term(condition.subject)}.{condition.predicate} = "
                         f"{_render_term(condition.value)}")
        elif condition.type == "comparison":
            symbol = {"eq": "==", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">",
                      "gte": ">=", "in": "in", "not_in": "not in",
                      "contains": "contains"}[condition.op]
            parts.append(f"{_render_term(condition.left)} {symbol} "
                         f"{_render_term(condition.right)}")
        else:
            parts.append(f"no {condition.predicate} for "
                         f"{_render_term(condition.subject)}")
    head = (f"{_render_term(rule.head.subject)}.{rule.head.predicate} = "
            f"{_render_term(rule.head.value)}")
    return f"IF {' AND '.join(parts)} THEN {head}"


def _render_term(term: Term | None) -> str:
    if term is None:
        return "_"
    if isinstance(term, Var):
        return term.name
    if isinstance(term, Const):
        value = term.value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    return f"{term.op}({', '.join(_render_term(a) for a in term.args)})"

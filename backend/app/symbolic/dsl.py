"""A readable rule language that compiles to the validated rule grammar.

Company policy is written as logic, not as JSON and not as Python:

    rule R-RET-A "Change-of-mind return within window" [RET-1, RET-3]
      if   order_status(?o) = "delivered"
      and  days_since_delivery(?o) = ?d
      and  effective_return_window(?o) = ?w
      and  ?d <= ?w        otherwise "it was delivered {d} days ago, past the {w}-day window"
      then return_allowed(?o) = true
      because "it was delivered {d} days ago, within the {w}-day window"
    end

Every rule compiles through `app.symbolic.schema.compile_rule`, so it gets the
same validation as a rule written through the API: head variables must be bound,
comparison variables must be bound first, and negation must be stratified.

The file also declares the *schema* the neural layer reads: which entity types
exist, which predicates are goals a user can ask about, and which predicates are
inputs a user can state in their own words. That is what makes understanding
question-independent. Adding a policy means adding rules and goal declarations
here; no example questions and no code.

    entity    order product category ...
    attribute delivered_on(order) -> date
    input     return_reason(order) -> enum damaged_on_arrival "arrived broken or defective"
    goal      return_allowed(order) decision "whether a bought item can be sent back"
    lookup    order_tracking(order) "where an order is now" : order_status carrier
    default   return_allowed = false

Declarations that make the knowledge an operational product rather than a demo:

    version   returns_policy(order) of returns on delivery_date
              the policy version in force for a case is resolved from its date
    access    customer own: customer order payment return refund
              which entity types each kind of person may reason over
    label     recommended_resolution replacement "offer a replacement of the same product"
    action    replacement_available yes "Create a replacement order; no refund is issued."
    followup  recommended_resolution approval_required
              a goal whose result qualifies another goal's recommended action
    input ... verified
              a statement the company records can confirm but a person cannot establish
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import RuleError
from app.symbolic.schema import CompiledRule, check_stratification, compile_rule

GOAL_KINDS = {"decision", "calculation", "explanation", "enumeration", "recommendation"}
VALUE_TYPES = {"text", "number", "money", "days", "distance", "date", "flag", "enum",
               "entity", "percent", "years"}
ACCESS_SCOPES = {"own", "all"}
FUNCTIONS = {"min", "max", "abs", "floor", "ceil"}
_CMP = {"<=": "lte", ">=": "gte", "<": "lt", ">": "gt", "=": "eq", "!=": "ne",
        "in": "in", "not in": "not_in"}


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

@dataclass
class PredicateType:
    predicate: str
    subject_type: str
    value_type: str


@dataclass
class InputValue:
    """A value a user can assert about their own case, in their own words."""

    predicate: str
    subject_type: str
    value_type: str
    value: Any
    description: str
    # A verified input is something the company records ("a supervisor approved an
    # exception"). A person saying it does not make it true: only the record can.
    verified: bool = False


@dataclass
class VersionBinding:
    """`version returns_policy(order) of returns on delivery_date`.

    The version of a policy that governs a case is the one in force on the case's date,
    not whichever is newest. Rules read versioned parameters through the resolved
    version entity (`returns_policy(?o) = ?v and damage_report_days(?v) = ?lim`), so a
    policy change is a new version node in the knowledge graph, never a rule edit.
    """

    predicate: str
    subject_type: str
    policy: str
    date_predicate: str


@dataclass
class AccessGrant:
    """`access support all: customer order` - what a kind of person may reason over."""

    kind: str             # public | customer | employee | support | admin | ...
    scope: str            # own | all
    entity_types: set[str]


@dataclass
class Goal:
    predicate: str
    subject_type: str
    kind: str
    description: str


@dataclass
class Lookup:
    name: str
    subject_type: str
    description: str
    predicates: list[str]


@dataclass
class Default:
    predicate: str
    value: Any


@dataclass
class Relevance:
    """Which of a person's records a question about a goal plausibly concerns.

    `relevant cancellation_allowed when order_status(?x) in [...]` means: when someone
    with several orders asks whether they can cancel "my order", only the ones not yet
    delivered are candidates. It narrows *which record*, never the answer about it.
    """

    name: str
    conditions: list[dict]


@dataclass
class RecordedOutcome:
    """Where a company record states the outcome a goal derives.

    `recorded refund_approved as refund_status via refund_for_return true "approved"
    false "rejected"` lets verification compare the refund's stored status with the
    decision the rules derive. Agreement raises confidence; disagreement is surfaced,
    because one of the record and the policy is wrong.
    """

    goal: str
    predicate: str
    relation: str
    true_value: str
    false_value: str


@dataclass
class RuleSpec:
    """A compiled rule plus the parts that exist only for explanation."""

    rule: CompiledRule
    clauses: list[str]
    because: str = ""
    # Aligned with rule.body: what to say when that condition is the one that fails.
    otherwise: list[str] = field(default_factory=list)
    # Aligned with rule.body: how to state that condition as a requirement when it is
    # unknown - so a conditional answer says "provided you have receipts" and never
    # asserts an assumption as a fact.
    requires: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def code(self) -> str:
        return self.rule.code


@dataclass
class KnowledgeProgram:
    entity_types: set[str] = field(default_factory=set)
    attributes: dict[str, PredicateType] = field(default_factory=dict)
    inputs: dict[str, list[InputValue]] = field(default_factory=dict)
    goals: dict[str, Goal] = field(default_factory=dict)
    lookups: dict[str, Lookup] = field(default_factory=dict)
    defaults: dict[str, Default] = field(default_factory=dict)
    relevance: dict[str, Relevance] = field(default_factory=dict)
    # goal -> {"yes"|"no"|"value": template} used to phrase the answer
    phrases: dict[str, dict[str, str]] = field(default_factory=dict)
    # goal -> how the company *stores* that outcome, for consistency verification
    recorded: dict[str, "RecordedOutcome"] = field(default_factory=dict)
    versions: dict[str, VersionBinding] = field(default_factory=dict)
    access: list[AccessGrant] = field(default_factory=list)
    # predicate -> {text value: how a person reads it}
    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    # goal -> {"yes"|"no"|"value"|<text value>: what the person should do next}
    actions: dict[str, dict[str, str]] = field(default_factory=dict)
    # goal -> goals whose results qualify its recommended action
    followups: dict[str, list[str]] = field(default_factory=dict)
    rules: list[RuleSpec] = field(default_factory=list)

    # -- queries the rest of the system asks -------------------------------
    def rules_for(self, predicate: str) -> list[RuleSpec]:
        return [r for r in self.rules if r.rule.head.predicate == predicate]

    def derived_predicates(self) -> set[str]:
        return {r.rule.head.predicate for r in self.rules}

    def is_input(self, predicate: str) -> bool:
        return predicate in self.inputs

    def value_type(self, predicate: str) -> str:
        if predicate in self.attributes:
            return self.attributes[predicate].value_type
        if predicate in self.inputs:
            return self.inputs[predicate][0].value_type
        return "text"

    def dependencies(self, predicate: str) -> set[str]:
        """Every predicate a proof of `predicate` could need, transitively."""
        seen: set[str] = set()
        stack = [predicate]
        while stack:
            current = stack.pop()
            for spec in self.rules_for(current):
                for condition in spec.rule.body:
                    if condition.predicate and condition.predicate not in seen:
                        seen.add(condition.predicate)
                        stack.append(condition.predicate)
        return seen

    def input_predicates_for(self, predicate: str) -> set[str]:
        return {p for p in self.dependencies(predicate) | {predicate} if self.is_input(p)}

    def is_verified_input(self, predicate: str) -> bool:
        return any(v.verified for v in self.inputs.get(predicate, []))

    def visible_types(self, kind: str, scope: str) -> set[str]:
        return {t for grant in self.access if grant.kind == kind and grant.scope == scope
                for t in grant.entity_types}

    def label(self, predicate: str, value: Any) -> str | None:
        return self.labels.get(predicate, {}).get(str(value))


# ---------------------------------------------------------------------------
# Expression tokenizer and parser
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d+)?)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<var>\?[A-Za-z_][A-Za-z0-9_]*)
    | (?P<op><=|>=|!=|[<>=+\-*/(),\[\]])
    | (?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)
    )""", re.VERBOSE)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    text = text.rstrip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise RuleError(f"Cannot read rule text near: {text[position:position + 24]!r}")
        position = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        # Negative numbers are unary minus, handled by the parser; the lexer never
        # has to guess whether '-' is a sign or a subtraction.
        tokens.append((kind, value))
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.i = 0

    # -- helpers ----------------------------------------------------------
    def peek(self, offset: int = 0) -> tuple[str, str] | None:
        j = self.i + offset
        return self.tokens[j] if j < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise RuleError("Rule text ended unexpectedly.")
        self.i += 1
        return token

    def expect(self, value: str) -> None:
        token = self.take()
        if token[1] != value:
            raise RuleError(f"Expected {value!r} in rule, found {token[1]!r}.")

    def done(self) -> bool:
        return self.i >= len(self.tokens)

    # -- grammar ----------------------------------------------------------
    def expression(self) -> dict | Any:
        left = self.product()
        while self.peek() and self.peek()[1] in ("+", "-"):
            op = {"+": "add", "-": "sub"}[self.take()[1]]
            left = {"expr": {"op": op, "args": [left, self.product()]}}
        return left

    def product(self) -> dict | Any:
        left = self.unary()
        while self.peek() and self.peek()[1] in ("*", "/"):
            op = {"*": "mul", "/": "div"}[self.take()[1]]
            left = {"expr": {"op": op, "args": [left, self.unary()]}}
        return left

    def unary(self) -> dict | Any:
        if self.peek() and self.peek()[1] == "-":
            self.take()
            return {"expr": {"op": "sub", "args": [{"const": 0}, self.unary()]}}
        return self.atom()

    def atom(self) -> dict | Any:
        kind, value = self.take()
        if kind == "num":
            number = float(value)
            return {"const": int(number) if number.is_integer() and "." not in value
                    else number}
        if kind == "str":
            return {"const": bytes(value[1:-1], "utf-8").decode("unicode_escape")}
        if kind == "var":
            return {"var": value}
        if value == "(":
            inner = self.expression()
            self.expect(")")
            return inner
        if value == "[":
            items: list[Any] = []
            while self.peek() and self.peek()[1] != "]":
                item = self.atom()
                if "const" not in item:
                    raise RuleError("List items in a rule must be constants.")
                items.append(item["const"])
                if self.peek() and self.peek()[1] == ",":
                    self.take()
            self.expect("]")
            return {"const": items}
        if kind == "name":
            if value in ("true", "false"):
                return {"const": value == "true"}
            if value in FUNCTIONS and self.peek() and self.peek()[1] == "(":
                self.take()
                args = [self.expression()]
                while self.peek() and self.peek()[1] == ",":
                    self.take()
                    args.append(self.expression())
                self.expect(")")
                return {"expr": {"op": value, "args": args}}
            # A bare name is an entity or symbol constant, e.g. `meridian`.
            return {"const": value}
        raise RuleError(f"Unexpected {value!r} in rule.")

    def atom_call(self) -> tuple[str, dict | Any]:
        """`predicate(term)`."""
        kind, name = self.take()
        if kind != "name":
            raise RuleError(f"Expected a predicate name, found {name!r}.")
        self.expect("(")
        subject = self.expression()
        self.expect(")")
        return name, subject


def _is_call(parser: _Parser) -> bool:
    first, second = parser.peek(), parser.peek(1)
    return bool(first and second and first[0] == "name" and second[1] == "("
                and first[1] not in FUNCTIONS and first[1] not in ("not",))


def parse_literal(text: str) -> dict:
    """One body condition in raw rule-grammar form."""
    parser = _Parser(text)
    first = parser.peek()
    if first and first == ("name", "not") and parser.peek(1) and parser.peek(2) and \
            parser.peek(2)[1] == "(":
        parser.take()
        predicate, subject = parser.atom_call()
        if not parser.done():
            raise RuleError(f"Unexpected text after negated condition: {text!r}")
        return {"type": "absent", "predicate": predicate, "subject": subject}

    if _is_call(parser):
        predicate, subject = parser.atom_call()
        parser.expect("=")
        value = parser.expression()
        if not parser.done():
            raise RuleError(f"Unexpected text after condition: {text!r}")
        return {"type": "fact", "predicate": predicate, "subject": subject, "value": value}

    left = parser.expression()
    token = parser.take()
    op_text = token[1]
    if op_text == "not" and parser.peek() and parser.peek()[1] == "in":
        parser.take()
        op_text = "not in"
    if op_text not in _CMP:
        raise RuleError(f"Unknown comparison {op_text!r} in {text!r}")
    right = parser.expression()
    if not parser.done():
        raise RuleError(f"Unexpected text after comparison: {text!r}")
    return {"type": "comparison", "left": left, "op": _CMP[op_text], "right": right}


def parse_head(text: str) -> dict:
    parser = _Parser(text)
    predicate, subject = parser.atom_call()
    parser.expect("=")
    value = parser.expression()
    if not parser.done():
        raise RuleError(f"Unexpected text after rule head: {text!r}")
    return {"type": "fact", "predicate": predicate, "subject": subject, "value": value}


# ---------------------------------------------------------------------------
# File parser
# ---------------------------------------------------------------------------

_QUOTED_TAIL = re.compile(
    r'^(?P<body>.*?)\s+(?P<key>otherwise|because|requires)\s+"(?P<text>[^"]*)"\s*$')
_RULE_HEADER = re.compile(
    r'^rule\s+(?P<code>[A-Za-z0-9_.\-]+)\s+"(?P<name>[^"]*)"'
    r'(?:\s*\[(?P<clauses>[^\]]*)\])?(?:\s+priority\s+(?P<priority>-?\d+))?\s*$')
_SIGNATURE = re.compile(r'^(?P<pred>[A-Za-z_][A-Za-z0-9_]*)\((?P<stype>[A-Za-z_]\w*)\)$')


def _signature(text: str) -> tuple[str, str]:
    match = _SIGNATURE.match(text.strip())
    if not match:
        raise RuleError(f"Expected predicate(type), found {text!r}")
    return match.group("pred"), match.group("stype")


def _scalar(text: str) -> Any:
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


def parse_program(text: str, *, source: str = "") -> KnowledgeProgram:
    program = KnowledgeProgram()
    lines = text.splitlines()
    i = 0

    def fail(message: str) -> RuleError:
        return RuleError(f"{source or 'rules'} line {i + 1}: {message}")

    while i < len(lines):
        raw = lines[i].split("#", 1)[0].rstrip() if not lines[i].lstrip().startswith("#") \
            else ""
        line = raw.strip()
        if not line:
            i += 1
            continue
        keyword = line.split(None, 1)[0]
        rest = line[len(keyword):].strip()

        try:
            if keyword == "entity":
                program.entity_types.update(rest.split())
            elif keyword == "attribute":
                sig, _, vtype = rest.partition("->")
                predicate, stype = _signature(sig)
                vtype = vtype.strip()
                if vtype not in VALUE_TYPES and vtype not in program.entity_types:
                    raise fail(f"unknown value type {vtype!r}")
                program.attributes[predicate] = PredicateType(predicate, stype, vtype)
            elif keyword == "input":
                match = re.match(r'^(?P<sig>\S+)\s*->\s*(?P<vtype>\w+)'
                                 r'(?:\s+(?P<value>[^"\s]+))?\s+"(?P<desc>[^"]+)"'
                                 r'(?:\s+(?P<verified>verified))?$', rest)
                if not match:
                    raise fail("expected: input pred(type) -> vtype [value] \"description\" "
                               "[verified]")
                predicate, stype = _signature(match.group("sig"))
                vtype = match.group("vtype")
                value = _scalar(match.group("value")) if match.group("value") else None
                if vtype == "enum" and value is None:
                    raise fail("an enum input needs a value")
                program.inputs.setdefault(predicate, []).append(InputValue(
                    predicate, stype, vtype, value, match.group("desc"),
                    verified=bool(match.group("verified"))))
            elif keyword == "version":
                match = re.match(r'^(?P<sig>\S+)\s+of\s+(?P<policy>[\w\-]+)\s+on\s+'
                                 r'(?P<date>\w+)$', rest)
                if not match:
                    raise fail("expected: version pred(type) of <policy> on <date predicate>")
                predicate, stype = _signature(match.group("sig"))
                program.versions[predicate] = VersionBinding(
                    predicate, stype, match.group("policy"), match.group("date"))
            elif keyword == "access":
                match = re.match(r'^(?P<kind>\w+)(?:\s+(?P<scope>own|all))?\s*:\s*(?P<types>.+)$',
                                 rest)
                if not match:
                    raise fail("expected: access <kind> [own|all]: type type ...")
                kind = match.group("kind")
                scope = "all" if kind == "public" else (match.group("scope") or "own")
                program.access.append(AccessGrant(kind, scope, set(match.group("types").split())))
            elif keyword == "label":
                match = re.match(r'^(\w+)\s+(\S+)\s+"([^"]+)"$', rest)
                if not match:
                    raise fail('expected: label <predicate> <value> "text"')
                program.labels.setdefault(match.group(1), {})[match.group(2)] = match.group(3)
            elif keyword == "action":
                match = re.match(r'^(\w+)\s+(\S+)\s+"([^"]+)"$', rest)
                if not match:
                    raise fail('expected: action <goal> yes|no|value|<value> "text"')
                program.actions.setdefault(match.group(1), {})[match.group(2)] = match.group(3)
            elif keyword == "followup":
                parts = rest.split()
                if len(parts) < 2:
                    raise fail("expected: followup <goal> <goal> [<goal> ...]")
                program.followups.setdefault(parts[0], []).extend(parts[1:])
            elif keyword == "goal":
                match = re.match(r'^(?P<sig>\S+)\s+(?P<kind>\w+)\s+"(?P<desc>[^"]+)"$', rest)
                if not match or match.group("kind") not in GOAL_KINDS:
                    raise fail(f"expected: goal pred(type) {'|'.join(sorted(GOAL_KINDS))} "
                               f"\"description\"")
                predicate, stype = _signature(match.group("sig"))
                program.goals[predicate] = Goal(predicate, stype, match.group("kind"),
                                                match.group("desc"))
            elif keyword == "lookup":
                match = re.match(r'^(?P<sig>\S+)\s+"(?P<desc>[^"]+)"\s*:\s*(?P<preds>.+)$', rest)
                if not match:
                    raise fail('expected: lookup name(type) "description" : pred pred')
                name, stype = _signature(match.group("sig"))
                program.lookups[name] = Lookup(name, stype, match.group("desc"),
                                               match.group("preds").split())
            elif keyword == "relevant":
                name, _, condition = rest.partition(" when ")
                if not condition:
                    raise fail("expected: relevant <goal> when <condition using ?x>")
                relevance = program.relevance.setdefault(name.strip(),
                                                         Relevance(name.strip(), []))
                # `order_status(?x) in [...]` is shorthand for binding the attribute
                # and then comparing it.
                sugar = re.match(r"^(\w+)\((\?x)\)\s*(not in|in|!=|<=|>=|<|>)\s*(.+)$",
                                 condition.strip())
                if sugar:
                    relevance.conditions.append(
                        parse_literal(f"{sugar.group(1)}(?x) = ?_value"))
                    relevance.conditions.append(
                        parse_literal(f"?_value {sugar.group(3)} {sugar.group(4)}"))
                else:
                    relevance.conditions.append(parse_literal(condition.strip()))
            elif keyword == "say":
                match = re.match(r'^(\w+)\s+(yes|no|value)\s+"([^"]+)"$', rest)
                if not match:
                    raise fail('expected: say <goal> yes|no|value "template"')
                program.phrases.setdefault(match.group(1), {})[match.group(2)] = match.group(3)
            elif keyword == "recorded":
                match = re.match(r'^(\w+)\s+as\s+(\w+)\s+via\s+(\w+)\s+true\s+"([^"]+)"\s+'
                                 r'false\s+"([^"]+)"$', rest)
                if not match:
                    raise fail('expected: recorded <goal> as <pred> via <relation> '
                               'true "v" false "v"')
                program.recorded[match.group(1)] = RecordedOutcome(*match.groups())
            elif keyword == "default":
                predicate, _, value = rest.partition("=")
                program.defaults[predicate.strip()] = Default(predicate.strip(),
                                                              _scalar(value))
            elif keyword == "rule":
                i = _parse_rule_block(lines, i, program, source)
                continue
            else:
                raise fail(f"unknown statement {keyword!r}")
        except RuleError as exc:
            if str(exc).startswith(source or "rules"):
                raise
            raise fail(str(exc)) from exc
        i += 1

    check_stratification([spec.rule for spec in program.rules])
    _check_references(program)
    return program


def _parse_rule_block(lines: list[str], start: int, program: KnowledgeProgram,
                      source: str) -> int:
    header = _RULE_HEADER.match(lines[start].strip())
    if not header:
        raise RuleError(f"{source} line {start + 1}: malformed rule header")
    code = header.group("code")
    clauses = [c.strip() for c in (header.group("clauses") or "").split(",") if c.strip()]
    priority = int(header.group("priority") or 100)

    body: list[dict] = []
    otherwise: list[str] = []
    requires: list[str] = []
    head: dict | None = None
    because = ""
    i = start + 1
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if line == "end":
            break
        keyword, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            if keyword in ("if", "and"):
                text, notes = rest, {}
                # A condition may carry `otherwise "..."` and `requires "..."`, in any order.
                while True:
                    tail = _QUOTED_TAIL.match(text)
                    if not tail or tail.group("key") not in ("otherwise", "requires"):
                        break
                    notes[tail.group("key")] = tail.group("text")
                    text = tail.group("body")
                body.append(parse_literal(text))
                otherwise.append(notes.get("otherwise", ""))
                requires.append(notes.get("requires", ""))
            elif keyword == "then":
                head = parse_head(rest)
            elif keyword == "because":
                because = _scalar(rest)
            else:
                raise RuleError(f"unexpected {keyword!r} inside rule")
        except RuleError as exc:
            raise RuleError(f"{source} line {i + 1} ({code}): {exc}") from exc
        i += 1
    else:
        raise RuleError(f"{source}: rule {code} has no 'end'")

    if head is None:
        raise RuleError(f"{source}: rule {code} has no 'then'")
    rule = compile_rule(code, body, head, name=header.group("name"),
                        description=header.group("name"), priority=priority)
    program.rules.append(RuleSpec(rule=rule, clauses=clauses, because=because,
                                  otherwise=otherwise, requires=requires, source=source))
    return i + 1


def _check_references(program: KnowledgeProgram) -> None:
    """Every goal must be provable by some rule, and every rule must cite policy."""
    derived = program.derived_predicates()
    for goal in program.goals.values():
        if goal.predicate not in derived:
            raise RuleError(f"Goal {goal.predicate!r} has no rule that concludes it.")
    for spec in program.rules:
        if not spec.clauses:
            raise RuleError(f"Rule {spec.code} does not cite the policy clause it encodes.")
    codes = [spec.code for spec in program.rules]
    duplicates = {c for c in codes if codes.count(c) > 1}
    if duplicates:
        raise RuleError(f"Duplicate rule codes: {', '.join(sorted(duplicates))}")
    for binding in program.versions.values():
        attribute = program.attributes.get(binding.predicate)
        if attribute is None or attribute.subject_type != binding.subject_type:
            raise RuleError(f"Versioned predicate {binding.predicate!r} must be declared as an "
                            f"attribute of {binding.subject_type}.")
        if binding.date_predicate not in program.attributes:
            raise RuleError(f"Version {binding.predicate!r} is dated by an undeclared predicate "
                            f"{binding.date_predicate!r}.")
    for grant in program.access:
        unknown = grant.entity_types - program.entity_types - {"*"}
        if unknown:
            raise RuleError(f"access {grant.kind} names unknown entity types: "
                            f"{', '.join(sorted(unknown))}")
    known_goals = set(program.goals) | set(program.lookups)
    for goal, followups in program.followups.items():
        for name in [goal, *followups]:
            if name not in known_goals and name not in program.derived_predicates():
                raise RuleError(f"followup names {name!r}, which no rule concludes.")


def load_program(directory: Path) -> KnowledgeProgram:
    """Parse every .rules file in a directory into one program."""
    files = sorted(directory.glob("*.rules"))
    if not files:
        raise RuleError(f"No .rules files in {directory}")
    text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    return parse_program(text, source=",".join(f.name for f in files))

"""NEURAL. A language model as a semantic parser into the knowledge pack's closed schema.

The embedding interpreter (query_understanding.py) matches a question to goal
descriptions by cosine similarity. Similarity cannot say "none of these": every question
has a nearest goal, so "how many loyalty points did I earn?" is read as a question about
delay compensation and answered, fluently and wrongly. Telling a paraphrase from a
different question is what language models are good at, so this interpreter asks one.

What the model is allowed to do is deliberately narrow. It never answers the question
and never sees a record. It chooses, from lists the knowledge pack declares:

    goal          one declared goal or lookup, or "none"
    request       what kind of answer is asked for (decide, calculate, explain, ...)
    clause        for a question about what a policy says, one policy clause id, or none
    stated facts  for each declared input, one of its declared values, or a number

and every choice is validated symbolically before anything uses it: a goal that is not
declared, a value outside an input's enumeration, a clause id that does not exist - each
is discarded, never coerced. Records are still found by the deterministic extractor and
resolved against the knowledge graph, and whether a stated fact is true is still decided
by the records, not by the parser. A reply that is not valid JSON falls back to the
embedding interpreter, so a parsing failure degrades understanding rather than inventing
it.

The neural-symbolic loop works the same way as with the embedding interpreter: when the
reasoner names an input it could not prove without, `detect_inputs` asks the model about
exactly that input, with its declared values, and nothing else.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger, log_event
from app.neural.entity_extraction import extract_mentions, split_clauses
from app.neural.intent_detection import IntentCandidate, SpeechAct
from app.neural.query_understanding import Interpretation, StatedInput

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.query_understanding import QueryUnderstanding
    from app.neural.retrieval import PolicyIndex
    from app.symbolic.dsl import KnowledgeProgram
    from app.symbolic.knowledge_graph import KnowledgeGraph

logger = get_logger("praxis.neural")

REQUESTS = {
    "decide": "whether something is allowed, required or applies (a yes or no)",
    "calculate": "an amount of money or a number of days",
    "explain": "why an outcome happened",
    "resolve": "what should be done or offered, or who should handle it",
    "locate": "the current status of a record",
    "enumerate": "which items qualify",
    "inform": "what a policy or the company says in general, not about one case",
}
PARSED_CONFIDENCE = 0.9

SYSTEM = """\
You translate a question into structured data for a company's decision system. You do
not answer the question.

Choose only from the lists given. If the question asks about something none of the goals
decide - a topic the company's rules do not cover, or ordinary conversation - the goal is
"none". Do not pick the nearest goal when it is a different question: "how many loyalty
points did I earn on my order" is not about delay compensation, and "can I pick my order
up from a store" is not about free delivery.

A fact is stated only when the question itself says it. Asking "can I get a replacement?"
does not state why the item is being returned. A claim that someone approved something is
reported as stated; whether it is true is checked later against the records.

Reply with one JSON object and nothing else."""


def _goal_lines(program: "KnowledgeProgram") -> list[str]:
    lines = [f'- "{name}": {goal.description}' for name, goal in program.goals.items()]
    lines += [f'- "{name}": {lookup.description}' for name, lookup in program.lookups.items()]
    return lines


def _input_lines(program: "KnowledgeProgram") -> list[str]:
    lines = []
    for predicate, values in program.inputs.items():
        kind = values[0].value_type
        if kind in ("enum", "flag"):
            options = "; ".join(f'"{v.value}" = {v.description}' if v.value is not None
                                else f"true/false = {v.description}" for v in values)
            lines.append(f"- {predicate}: {options}")
        else:
            lines.append(f"- {predicate} ({kind}, a number): {values[0].description}")
    return lines


class LLMSemanticParser:
    def __init__(self, program: "KnowledgeProgram", graph: "KnowledgeGraph",
                 policies: "PolicyIndex", llm, fallback: "QueryUnderstanding") -> None:
        self.program = program
        self.graph = graph
        self.policies = policies
        self.llm = llm
        self.fallback = fallback
        self.encoder = fallback.encoder
        self.name = f"llm-parser:{getattr(llm, 'model', getattr(llm, 'name', 'model'))}"
        clauses = "\n".join(f"- {c.id}: {c.title}" for c in policies.clauses)
        self._schema = (
            "Goals:\n" + "\n".join(_goal_lines(program))
            + "\n\nRequest kinds:\n" + "\n".join(f"- {k}: {v}" for k, v in REQUESTS.items())
            + "\n\nPolicy clauses in force:\n" + clauses
            + "\n\nFacts a question may state:\n" + "\n".join(_input_lines(program)))

    # -- the parse ------------------------------------------------------------------------
    def understand(self, question: str) -> Interpretation:
        prompt = (
            f"{self._schema}\n\nQuestion: {question}\n\n"
            'JSON keys: "goal" (a goal name or "none"), "request" (a request kind), '
            '"clause" (for an "inform" request, the clause id that answers it, else null), '
            '"stated" (an object mapping fact names to values the question states; {} if '
            "none).")
        raw = self._ask(prompt, 220)
        parsed = _json(raw)
        if parsed is None:
            log_event(logger, 30, "semantic parser reply was not JSON; using embeddings")
            interpretation = self.fallback.understand(question)
            interpretation.encoder = f"{self.name} (fallback: {self.fallback.encoder.name})"
            return interpretation

        goal = str(parsed.get("goal") or "none")
        request = str(parsed.get("request") or "")
        candidates = self._candidate(goal)
        if request not in REQUESTS:
            request = self.fallback.intents.speech_act(question).label
        interpretation = Interpretation(
            question=question, clauses=split_clauses(question),
            speech_act=SpeechAct(request, PARSED_CONFIDENCE, {request: PARSED_CONFIDENCE}),
            candidates=candidates,
            mentions=extract_mentions(question, self.graph, self.encoder),
            encoder=self.name, semantic=True)
        interpretation.inputs = self._inputs(parsed.get("stated"), question,
                                             interpretation.mentions.quantities)
        interpretation.parse = {"raw": raw[:800], "goal": goal, "request": request,
                                "clause": parsed.get("clause"),
                                "rejected": getattr(self, "_rejected", [])}
        clause = parsed.get("clause")
        interpretation.selected_clause = clause if isinstance(clause, str) and \
            clause in {c.id for c in self.policies.clauses} else None
        interpretation.parsed = True
        return interpretation

    def detect_inputs(self, interpretation: Interpretation, predicates: set[str],
                      threshold: float) -> list[StatedInput]:
        """The loop: ask about exactly the inputs the reasoner could not prove without."""
        wanted = sorted(p for p in predicates if p in self.program.inputs)
        if not wanted:
            return []
        lines = [line for line in _input_lines(self.program)
                 if line.split(":")[0].lstrip("- ").split(" ")[0] in wanted]
        prompt = (
            "Facts a question may state:\n" + "\n".join(lines)
            + f"\n\nQuestion: {interpretation.question}\n\n"
            'Reply with JSON only: {"stated": {fact name: value}} containing only the facts '
            "above that the question itself states. Use {} when it states none of them.")
        parsed = _json(self._ask(prompt, 120)) or {}
        found = self._inputs(parsed.get("stated"), interpretation.question,
                             interpretation.mentions.quantities)
        known = {i.predicate for i in interpretation.inputs}
        return [f for f in found if f.predicate in wanted and f.predicate not in known]

    # -- validation -----------------------------------------------------------------------
    def _candidate(self, goal: str) -> list[IntentCandidate]:
        program = self.program
        if goal in program.goals:
            spec = program.goals[goal]
            return [IntentCandidate("goal", goal, spec.subject_type, spec.kind,
                                    spec.description, PARSED_CONFIDENCE)]
        if goal in program.lookups:
            spec = program.lookups[goal]
            return [IntentCandidate("lookup", goal, spec.subject_type, "lookup",
                                    spec.description, PARSED_CONFIDENCE)]
        return []   # "none", or a name the pack does not declare: never coerced

    def _inputs(self, stated: Any, question: str, quantities=()) -> list[StatedInput]:
        self._rejected = []
        if not isinstance(stated, dict):
            return []
        found: list[StatedInput] = []
        for predicate, raw in stated.items():
            values = self.program.inputs.get(str(predicate))
            if not values or raw is None:
                self._rejected.append({"fact": predicate, "value": raw, "why": "not declared"})
                continue
            kind = values[0].value_type
            value: Any = None
            if kind == "enum":
                allowed = {str(v.value) for v in values}
                value = str(raw) if str(raw) in allowed else None
            elif kind == "flag":
                if isinstance(raw, bool):
                    value = raw
                elif str(raw).lower() in ("true", "yes"):
                    value = True
                elif str(raw).lower() in ("false", "no"):
                    value = False
                declared = {v.value for v in values if v.value is not None}
                if declared and value not in declared:
                    value = None
            else:
                number = _number(raw)
                # A number the question does not contain was not stated: the parser may
                # choose which stated number is which fact, but may not supply one.
                if number is not None and (_occurs(number, question) or any(
                        abs(number - float(q.value)) < 1e-6 for q in quantities)):
                    value = number
            if value is None:
                self._rejected.append({"fact": predicate, "value": raw, "why": "invalid value"})
                continue
            found.append(StatedInput(str(predicate), value, PARSED_CONFIDENCE, question, "llm"))
        return found

    def _ask(self, prompt: str, max_tokens: int) -> str:
        from app.neuro.base import CompletionRequest, Message, Role, Task

        try:
            return self.llm.complete(CompletionRequest(
                task=Task.INTENT, system=SYSTEM, messages=[Message(Role.USER, prompt)],
                max_tokens=max_tokens, temperature=0.0)).text
        except Exception as exc:  # noqa: BLE001 - a provider failure falls back, never guesses
            log_event(logger, 30, "semantic parser provider failed", error=type(exc).__name__)
            return ""


def _json(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def _occurs(number: float, question: str) -> bool:
    written = {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", question)}
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "fortnight": 14, "week": 7, "tomorrow": 1,
             "yesterday": 1}
    lowered = question.lower()
    written |= {float(v) for w, v in words.items() if re.search(rf"\b{w}\b", lowered)}
    return any(abs(number - w) < 1e-6 for w in written)

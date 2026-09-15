"""NEURAL. Natural language in, a structured interpretation out.

    "My package arrived damaged. Can I get a replacement instead of a refund?"
        ->
    speech act   decide
    candidates   replacement_available (0.81), return_allowed (0.74), ...
    mentions     personal, no record id
    inputs       return_reason = damaged_on_arrival  (0.78, "My package arrived damaged")

This is the *only* place natural language becomes structure. Everything it produces is
a hypothesis: the symbolic layer validates record ids against the graph, checks stated
inputs against company records, and decides what the question actually needs.

Inputs are found the same way intents are - by meaning, against descriptions declared
in the rules - so "the kettle came smashed" and "it was broken when it arrived" both
reach `return_reason = damaged_on_arrival` without either phrase being written down
anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.neural.entity_extraction import (
    Mentions, description_facets, extract_mentions, is_negated, is_question_clause,
    negates_concept, split_clauses,
)
from app.neural.intent_detection import IntentCandidate, IntentDetector, SpeechAct

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.encoder import Encoder
    from app.symbolic.dsl import KnowledgeProgram
    from app.symbolic.knowledge_graph import KnowledgeGraph

# Similarity a clause needs before it counts as stating an input. Calibrated on the
# development questions in data/meridian/benchmark/dev.yaml, never on the test set.
INPUT_THRESHOLD = 0.70
# When the symbolic layer asks for one specific input it could not prove without, the
# neural layer looks again for just that input and accepts weaker evidence - it now
# knows what it is looking for. This is the neural-symbolic loop.
TARGETED_INPUT_THRESHOLD = 0.64
# Two values of one input closer than this are ambiguous, and neither is asserted.
INPUT_MARGIN = 0.03
# A claim only a record can confirm is reported back to the person, so it needs clearer
# evidence than an ordinary statement before it is raised at all.
VERIFIED_INPUT_THRESHOLD = 0.75

UNIT_OF_INPUT = {"money": "money", "days": "days", "distance": "distance"}
# Words that make a stated number a claim about what the policy says.
POLICY_CLAIM_CUE = re.compile(
    r"\b(policy|policies|rule|rules|window|limit|cap|threshold|allowance|maximum|"
    r"entitled to|you allow|allows?)\b", re.IGNORECASE)


@dataclass
class StatedInput:
    predicate: str
    value: Any
    confidence: float
    evidence: str
    method: str          # semantic | quantity | semantic-negated

    def as_dict(self) -> dict:
        return {"predicate": self.predicate, "value": self.value,
                "confidence": round(self.confidence, 4), "evidence": self.evidence,
                "method": self.method}


@dataclass
class Interpretation:
    question: str
    clauses: list[str]
    speech_act: SpeechAct
    candidates: list[IntentCandidate]
    mentions: Mentions
    inputs: list[StatedInput] = field(default_factory=list)
    encoder: str = ""
    semantic: bool = False

    @property
    def top(self) -> IntentCandidate | None:
        return self.candidates[0] if self.candidates else None

    def input_value(self, predicate: str) -> StatedInput | None:
        return next((i for i in self.inputs if i.predicate == predicate), None)

    def as_dict(self) -> dict:
        return {
            "question": self.question, "clauses": self.clauses,
            "speech_act": self.speech_act.as_dict(),
            "intent_candidates": [c.as_dict() for c in self.candidates],
            "mentions": self.mentions.as_dict(),
            "stated_inputs": [i.as_dict() for i in self.inputs],
            "encoder": self.encoder, "semantic_understanding": self.semantic,
        }


_RELATIVE = re.compile(r"\b(?:whose|who|which|that|where|because|since)\s+(.{8,})$",
                       re.IGNORECASE)
_QUANTITY_TEXT = re.compile(
    r"(?:₹|rs\.?\s*)?\d[\d,]*(?:\.\d+)?\s*(?:days?|weeks?|months?|km|kms|rupees?)?"
    r"(?:\s+ago)?|\b(?:yesterday|a fortnight ago)\b", re.IGNORECASE)
_THIRD_PARTY = re.compile(r"\b(?:my|our|the|a) (?:customer|client|buyer)'?s?\b", re.IGNORECASE)


def _statement_form(clause: str) -> str:
    """The words that carry the statement: quantities are read separately, and "my
    customer's laptop" states the same thing as "the laptop"."""
    text = _QUANTITY_TEXT.sub(" ", clause)
    text = _THIRD_PARTY.sub("the", text)
    return re.sub(r"\s+", " ", text).strip()


def _embedded_statement(clause: str) -> str:
    match = _RELATIVE.search(clause)
    return _statement_form(match.group(1)) if match else ""


def _clause_around(question: str, quantity) -> str:
    """The clause a quantity was written in."""
    for clause in split_clauses(question):
        if quantity.text.lower() in clause.lower():
            return clause
    return question


class QueryUnderstanding:
    def __init__(self, program: "KnowledgeProgram", graph: "KnowledgeGraph",
                 encoder: "Encoder") -> None:
        self.program = program
        self.graph = graph
        self.encoder = encoder
        self.intents = IntentDetector(program, encoder)

    def understand(self, question: str) -> Interpretation:
        candidates = self.intents.candidates(question)
        interpretation = Interpretation(
            question=question,
            clauses=split_clauses(question),
            speech_act=self.intents.speech_act(question),
            candidates=candidates,
            mentions=extract_mentions(question, self.graph, self.encoder),
            encoder=self.encoder.name,
            semantic=self.encoder.semantic,
        )
        relevant: set[str] = set()
        for candidate in candidates[:3]:
            relevant |= self.program.input_predicates_for(candidate.name)
        # Claims only a record can confirm ("my manager already approved it") are looked
        # for in every question, so an unconfirmed one is reported whatever was asked.
        relevant |= {p for p in self.program.inputs if self.program.is_verified_input(p)}
        interpretation.inputs = self.detect_inputs(interpretation, relevant, INPUT_THRESHOLD)

        # A claim is not the request: in "The manager already approved this. Can I return
        # my earbuds?" the first sentence is about approvals and the question is about a
        # return. Intent is re-read without the clauses that only make a claim.
        claim_text = [i.evidence for i in interpretation.inputs
                      if self.program.is_verified_input(i.predicate)]
        if claim_text:
            remaining = [c for c in interpretation.clauses
                         if not any(_statement_form(c) == e or c == e for e in claim_text)]
            if remaining and len(remaining) < len(interpretation.clauses):
                interpretation.candidates = self.intents.candidates(". ".join(remaining))
                again: set[str] = set()
                for candidate in interpretation.candidates[:3]:
                    again |= self.program.input_predicates_for(candidate.name)
                extra = self.detect_inputs(interpretation, again, INPUT_THRESHOLD)
                known = {i.predicate for i in interpretation.inputs}
                interpretation.inputs += [i for i in extra if i.predicate not in known]
        return interpretation

    # -- inputs -----------------------------------------------------------------
    def detect_inputs(self, interpretation: Interpretation, predicates: set[str],
                      threshold: float) -> list[StatedInput]:
        found: list[StatedInput] = []
        found += self._semantic_inputs(interpretation, predicates, threshold)
        taken = {i.predicate for i in found}
        found += self._quantity_inputs(interpretation, predicates - taken)
        return found

    def _semantic_inputs(self, interpretation: Interpretation, predicates: set[str],
                         threshold: float) -> list[StatedInput]:
        options = [(p, value) for p in sorted(predicates)
                   for value in self.program.inputs.get(p, [])
                   if value.value_type in ("enum", "flag")]
        # Only declarative clauses can *state* something. "Can I return the shoes?"
        # asks about returns; it does not say why the shoes are coming back, and
        # scoring it as a statement read a change-of-mind reason into every question.
        # A question can still carry a description in a relative clause: "which team
        # handles a customer *whose payment was deducted twice*" states the case.
        clauses = [_statement_form(c) for c in interpretation.clauses
                   if not is_question_clause(c)]
        clauses += [d for c in interpretation.clauses if is_question_clause(c)
                    for d in [_embedded_statement(c)] if d]
        clauses = [c for c in dict.fromkeys(clauses) if c]
        if not options or not clauses:
            return []

        # A description is several phrasings of one concept ("broken, cracked,
        # smashed, damaged"). A short clause matches one facet far better than the
        # whole list, so each value scores as its best facet.
        facets: list[tuple[int, str]] = []
        for index, (_, value) in enumerate(options):
            for facet in description_facets(value.description):
                facets.append((index, facet))
        scores = self.encoder.similarity(clauses, [f for _, f in facets])

        per_value: dict[int, tuple[float, int, str]] = {}
        for column, (index, facet) in enumerate(facets):
            row = int(scores[:, column].argmax())
            score = float(scores[row, column])
            if score > per_value.get(index, (-1.0, 0, ""))[0]:
                per_value[index] = (score, row, facet)

        best: dict[str, list[tuple[float, int, str, Any]]] = {}
        for index, (score, row, facet) in per_value.items():
            predicate, value = options[index]
            best.setdefault(predicate, []).append((score, row, facet, value))

        stated: list[StatedInput] = []
        for predicate, ranked in best.items():
            ranked.sort(key=lambda item: -item[0])
            score, row, facet, value = ranked[0]
            required = threshold
            if self.program.is_verified_input(predicate):
                required = max(threshold, VERIFIED_INPUT_THRESHOLD)
            elif len(self.program.inputs.get(predicate, [])) > 3:
                # Choosing one of many categories is a classification, not the confirmation
                # of something the rules already expect; the looser targeted threshold of
                # the neural-symbolic loop would let "the reset email never arrives" pass
                # for an undelivered parcel.
                required = max(threshold, INPUT_THRESHOLD)
            if score < required:
                continue
            if len(ranked) > 1 and score - ranked[1][0] < INPUT_MARGIN:
                continue
            clause = clauses[row]
            if value.value is None:
                # A one-sided flag: the negation in the clause decides its value.
                stated.append(StatedInput(predicate, not is_negated(clause), score, clause,
                                          "semantic"))
            elif not negates_concept(clause, facet):
                # "It did not arrive damaged" negates the concept and states nothing;
                # "I don't need it any more" contains a negation that *is* the
                # concept. Only a negation next to the matched words blocks it.
                stated.append(StatedInput(predicate, value.value, score, clause, "semantic"))
        return stated

    def _quantity_inputs(self, interpretation: Interpretation,
                         predicates: set[str]) -> list[StatedInput]:
        stated: list[StatedInput] = []
        quantities = interpretation.mentions.quantities
        units = sorted({UNIT_OF_INPUT.get(q.kind) for q in quantities} - {None})
        for unit in units:
            inputs = [p for p in sorted(predicates) if self.program.value_type(p) == unit]
            numbers = [q for q in quantities if UNIT_OF_INPUT.get(q.kind) == unit]
            if not inputs or not numbers:
                continue
            # Bind each input to the number whose surrounding clause means the most like
            # the input's description. "My grade cap is ₹15,000 so I'll get the full ₹8,750
            # for my trip" has two amounts; the claim is the one said about the trip, not
            # simply the first one written.
            contexts = [_clause_around(interpretation.question, q) for q in numbers]
            descriptions = [self.program.inputs[p][0].description for p in inputs]
            scores = self.encoder.similarity(contexts, descriptions)
            # "Your policy is 30 days, so can I return it after 20 days?" - a number said
            # about the policy is a claim about the policy, checked later against it, not
            # a fact about the case. It is bound only if nothing else can be.
            for row, context in enumerate(contexts):
                if POLICY_CLAIM_CUE.search(context) and len(numbers) > 1:
                    scores[row, :] -= 1.0
            taken: set[int] = set()
            for column, predicate in sorted(enumerate(inputs),
                                            key=lambda pair: -float(scores[:, pair[0]].max())):
                order = sorted(range(len(numbers)), key=lambda row: -float(scores[row, column]))
                row = next((r for r in order if r not in taken), None)
                if row is None:
                    continue
                taken.add(row)
                confidence = 0.95 if len(numbers) == 1 and len(inputs) == 1 \
                    else float(scores[row, column])
                stated.append(StatedInput(predicate, numbers[row].value, confidence,
                                          numbers[row].text, "quantity"))
        return stated

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

UNIT_OF_INPUT = {"money": "money", "days": "days", "distance": "distance"}


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
        interpretation.inputs = self.detect_inputs(interpretation, relevant, INPUT_THRESHOLD)
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
        clauses = [c for c in interpretation.clauses if not is_question_clause(c)]
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
            if score < threshold:
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
        used: set[int] = set()
        for quantity in interpretation.mentions.quantities:
            matching = [p for p in sorted(predicates)
                        if self.program.value_type(p) == UNIT_OF_INPUT.get(quantity.kind)
                        and p not in {s.predicate for s in stated}]
            if not matching or id(quantity) in used:
                continue
            if len(matching) == 1:
                chosen, confidence = matching[0], 0.95
            else:
                # Several inputs share a unit: bind the number to the one whose meaning
                # is closest to the words around it.
                descriptions = [self.program.inputs[p][0].description for p in matching]
                scores = self.encoder.similarity([interpretation.question], descriptions)[0]
                index = int(scores.argmax())
                chosen, confidence = matching[index], float(scores[index])
            used.add(id(quantity))
            stated.append(StatedInput(chosen, quantity.value, confidence, quantity.text,
                                      "quantity"))
        return stated

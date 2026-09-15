"""NEURAL. Map a question onto what the knowledge base can determine.

There is no intent list here and no example questions. The candidate intents are the
goals and lookups *declared in the rule files*, each with a description written from
the policy side ("whether an item that was bought can be sent back to the seller"). The
question and every description are embedded, and the nearest goals are the candidate
intents. A new policy with a new goal extends what the assistant understands without
touching this module.

Separately, the question's *speech act* - is it asking whether something is allowed,
how much, why, where, which, or simply what a policy says - is classified against a
handful of generic, domain-free descriptions. The router uses both: the goal says what
to prove, the speech act says what kind of answer the person wants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.encoder import Encoder
    from app.symbolic.dsl import KnowledgeProgram

# Generic kinds of request. These describe *how* people ask, in any domain; they do
# not mention returns, orders, leave or anything else the company does.
SPEECH_ACTS: dict[str, str] = {
    "inform": "asks what a policy, rule, document or the company says in general, or asks for "
              "an overview or contact details",
    "decide": "asks whether something is allowed, possible, permitted or eligible in their own "
              "situation, or whether they can do something",
    "calculate": "asks how much money, how many days, or what amount or quantity they will "
                 "get or pay",
    "explain": "asks why a decision was made, why something happened, or why something does "
               "not apply to them",
    "locate": "asks where something is, when it will arrive, or what the current status of "
              "their record is",
    "enumerate": "asks which items, products or options qualify, are available or are included",
    "compare": "asks how two or more things differ or which of them is better",
    "resolve": "reports a problem that happened to them and asks what they should do about it",
}


@dataclass
class IntentCandidate:
    kind: str           # goal | lookup
    name: str
    subject_type: str
    goal_kind: str      # decision | calculation | explanation | enumeration | lookup
    description: str
    score: float

    def as_dict(self) -> dict:
        return {"kind": self.kind, "name": self.name, "subject_type": self.subject_type,
                "goal_kind": self.goal_kind, "score": round(self.score, 4)}


@dataclass
class SpeechAct:
    label: str
    score: float
    scores: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"label": self.label, "score": round(self.score, 4),
                "scores": {k: round(v, 4) for k, v in sorted(self.scores.items(),
                                                             key=lambda kv: -kv[1])}}


class IntentDetector:
    def __init__(self, program: "KnowledgeProgram", encoder: "Encoder") -> None:
        self.program = program
        self.encoder = encoder
        self._catalogue: list[IntentCandidate] = [
            IntentCandidate("goal", g.predicate, g.subject_type, g.kind, g.description, 0.0)
            for g in program.goals.values()
        ] + [
            IntentCandidate("lookup", lk.name, lk.subject_type, "lookup", lk.description, 0.0)
            for lk in program.lookups.values()
        ]

    def candidates(self, question: str, *, top: int = 5) -> list[IntentCandidate]:
        if not self._catalogue:
            return []
        scores = self.encoder.similarity([question], [c.description for c in self._catalogue])[0]
        ranked = sorted(zip(self._catalogue, scores), key=lambda pair: -float(pair[1]))
        return [IntentCandidate(c.kind, c.name, c.subject_type, c.goal_kind, c.description,
                                float(score)) for c, score in ranked[:top]]

    def speech_act(self, question: str) -> SpeechAct:
        """What *kind* of answer is wanted.

        Measured on the development questions, sentence embeddings classified this
        correctly 8 times in 27: they encode what a question is about, and "why was
        my refund rejected" and "how much refund will I get" are about the same
        thing. The kind of answer is carried by the question's *form* - its
        interrogative word and its modal - so form decides, and embeddings are
        consulted only when the form says nothing. The form patterns are grammar,
        not domain vocabulary: none of them mentions an order, a refund or leave.
        """
        labels = list(SPEECH_ACTS)
        scores = self.encoder.similarity([question], [SPEECH_ACTS[k] for k in labels])[0]
        embedded = {label: float(s) for label, s in zip(labels, scores)}
        form = speech_act_from_form(question)
        if form is not None:
            return SpeechAct(form, 1.0, {**embedded, "form": 1.0})
        index = int(np.argmax(scores))
        return SpeechAct(labels[index], float(scores[index]), embedded)


# Interrogative form -> kind of answer. Ordered: "why can't I..." is an explanation
# even though it contains "can I", and "how much can I..." is a calculation.
_FORM_RULES: list[tuple[str, re.Pattern]] = [
    ("compare", re.compile(r"\b(compare|comparison|versus|vs\.?|difference between|"
                           r"which is better|better than)\b", re.I)),
    ("explain", re.compile(r"^\s*(why|how come)\b|\bwhy\b|\bwhat (is|was) the reason\b|"
                           r"\bexplain\b", re.I)),
    ("resolve", re.compile(r"\bwhat (should|do|can|must) (i|we)\s+do\b|\bwhat now\b|"
                           r"\bwhat (can|could|should|may) (i|we) offer\b|\bwhat options\b|"
                           r"\bwhat are (my|our|their|the) options\b|\bwho (should )?handles?\b|"
                           r"\bwhich (team|desk)\b|\bwhere (should|do) (i|we) (route|escalate)\b|"
                           r"\bwhat are my options\b|\bhow (do|can|should) (i|we) "
                           r"(fix|handle|deal)\b", re.I)),
    ("enumerate", re.compile(r"^\s*what can (i|we) (get|buy|order|have)\b", re.I)),
    ("calculate", re.compile(r"\bhow (much|many)\b|\bwhat (amount|sum)\b|"
                             r"\b(will|would|do|does|can) (i|we|they) (get|receive|be paid|be "
                             r"reimbursed)\b|\bget back\b|\bwhat do i get\b", re.I)),
    ("locate", re.compile(r"^\s*(where|when)\b|\bwhere('s| is| are)\b|\bwhen will\b|"
                          r"\bstatus of\b|\btrack(ing)?\b", re.I)),
    ("enumerate", re.compile(r"^\s*which\b|\bwhat (products|items|options|things)\b|"
                             r"^\s*list\b|\bwhat can i (get|buy|order)\b", re.I)),
    ("inform", re.compile(r"^\s*what (is|are|does|do)\s+(the|our|your|a|an)\b|"
                          r"\b(policy|policies|rules?|handbook|guidelines?|procedure)\b.*\?*$|"
                          r"^\s*(how do i|how can i|who (should|do|can) i|tell me about|"
                          r"describe)\b|\b(phone|number|address|email|contact|hours)\b", re.I)),
    ("decide", re.compile(r"^\s*(can|could|may|am|is|are|will|would|do|does|should|shall)"
                          r"\b|\b(allowed|eligible|possible|permitted|entitled|qualify|"
                          r"count)\b|\bis there (any|a) way\b", re.I)),
]


def speech_act_from_form(question: str) -> str | None:
    from app.neural.entity_extraction import split_clauses

    text = question.strip()
    # The request is usually the last clause: "The blender came with a cracked lid,
    # can I exchange it?" is a statement followed by a yes/no question. Reading the
    # form of each clause, last first, finds the actual request; anchored patterns
    # such as "^can" would never match it inside the whole sentence.
    from app.neural.entity_extraction import is_question_clause

    clauses = split_clauses(text)
    # Questions first: in "who handles a customer whose reset email never arrives", the
    # word "email" in the description must not make the request an information lookup.
    questions = [c for c in reversed(clauses) if is_question_clause(c)]
    statements = [c for c in reversed(clauses) if not is_question_clause(c)]
    # Among several questions ("what can I offer, and does it need approval?") the more
    # specific kind of request wins, in the order the rules are listed.
    for label, pattern in _FORM_RULES:
        if any(pattern.search(c) for c in questions):
            return label
    for clause in statements:
        for label, pattern in _FORM_RULES:
            if pattern.search(clause):
                return label
    for label, pattern in _FORM_RULES:
        if pattern.search(text):
            return label
    if "?" not in text and re.search(r"\b(i|my|me|we|our)\b", text, re.I):
        # A statement about oneself with no question: a report of a problem.
        return "resolve"
    return None

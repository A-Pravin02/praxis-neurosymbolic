"""The language-model parser may only choose from the pack's closed schema.

Every test uses a scripted model, so what is tested is the validation around the model:
whatever it replies, nothing undeclared, unstated or malformed reaches reasoning.

Marked `no_db` for the reason conftest gives for the rest of the understanding layer:
it is pure logic over the knowledge pack, and needing Postgres to test it would make
this part of the system unverifiable exactly when that matters.
"""
from __future__ import annotations

import json

import pytest

from app.hybrid.controller import NeuroSymbolicController
from app.hybrid.knowledge_pack import load_pack
from app.neural.semantic_parser import LLMSemanticParser
from app.neuro.base import Completion
from app.neuro.providers.mock import MockLLMProvider

pytestmark = pytest.mark.no_db


class Scripted:
    name = "scripted"
    model = "scripted-1"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, request):
        self.prompts.append(request.messages[-1].content)
        text = self.replies.pop(0) if self.replies else "{}"
        return Completion(text=text, model=self.model, provider=self.name)


def _parser(*replies):
    pack = load_pack(None, "hashing")
    llm = Scripted(*replies)
    return LLMSemanticParser(pack.program, pack.graph, pack.policies, llm, pack.understanding), llm


def test_an_undeclared_goal_is_never_coerced_to_the_nearest_one():
    parser, _ = _parser(json.dumps({"goal": "loyalty_points", "request": "calculate",
                                    "stated": {}}))
    interpretation = parser.understand("How many loyalty points did I earn on order 12345?")
    # Not coerced to any goal by the parser: an undeclared name is a failed parse, and
    # understanding falls back rather than inventing a "loyalty_points" goal.
    assert not interpretation.parsed
    assert "loyalty_points" not in [c.name for c in interpretation.candidates]


def test_a_declared_goal_is_taken_with_its_schema_kind():
    parser, _ = _parser(json.dumps({"goal": "refund_required", "request": "decide",
                                    "stated": {}}))
    interpretation = parser.understand("Is order 22002 refunded automatically?")
    assert [c.name for c in interpretation.candidates] == ["refund_required"]
    assert interpretation.candidates[0].goal_kind == "decision"


def test_an_enumeration_value_outside_the_declared_set_is_rejected():
    parser, _ = _parser(json.dumps({"goal": "return_allowed", "request": "decide",
                                    "stated": {"return_reason": "customer_is_angry"}}))
    interpretation = parser.understand("Can I return order 12346?")
    assert interpretation.inputs == []
    assert interpretation.parse["rejected"][0]["fact"] == "return_reason"


def test_a_number_the_question_never_states_cannot_be_stated_by_the_parser():
    parser, _ = _parser(json.dumps({"goal": "reimbursable_amount", "request": "calculate",
                                    "stated": {"claimed_amount": 8750,
                                               "travel_distance_km": 300}}))
    interpretation = parser.understand("I spent ₹8,750 on a business trip. How much back?")
    stated = {i.predicate: i.value for i in interpretation.inputs}
    assert stated == {"claimed_amount": 8750.0}


def test_a_reply_that_is_not_json_falls_back_to_embedding_understanding():
    parser, _ = _parser("Sure! The goal is probably cancellations.")
    interpretation = parser.understand("Can I cancel order 12345?")
    assert not interpretation.parsed
    assert "fallback" in interpretation.encoder


def test_the_parser_never_sees_a_record():
    parser, llm = _parser(json.dumps({"goal": "cancellation_allowed", "request": "decide",
                                      "stated": {}}))
    parser.understand("Can I cancel order 12345?")
    prompt = llm.prompts[0]
    assert "62999" not in prompt and "Priya" not in prompt and "shipped" not in prompt


def test_no_goal_and_no_clause_is_reported_as_not_covered_not_answered():
    pack = load_pack(None, "hashing")
    llm = Scripted(json.dumps({"goal": "none", "request": "inform", "clause": None,
                               "stated": {}}))
    controller = NeuroSymbolicController(pack, llm=MockLLMProvider())
    controller.understanding = LLMSemanticParser(pack.program, pack.graph, pack.policies, llm,
                                                 pack.understanding)
    answer = controller.answer("Can I pick up order 12345 from one of your stores?",
                               actor_email="priya@meridian.test")
    assert answer.outcome == "insufficient"
    assert answer.decision is None


def test_a_parsed_goal_is_still_decided_by_the_rules_not_the_model():
    pack = load_pack(None, "hashing")
    llm = Scripted(json.dumps({"goal": "cancellation_allowed", "request": "decide",
                               "stated": {}}))
    controller = NeuroSymbolicController(pack, llm=MockLLMProvider())
    controller.understanding = LLMSemanticParser(pack.program, pack.graph, pack.policies, llm,
                                                 pack.understanding)
    answer = controller.answer("Can I cancel order 12345?", actor_email="priya@meridian.test")
    # ORD-12345 has shipped: the rules say no, whatever a model might believe.
    assert answer.outcome == "answered" and answer.decision is False


def test_a_misspelt_goal_name_is_repaired_by_spelling_alone():
    parser, _ = _parser(json.dumps({"goal": "cancelation_allowed", "request": "decide",
                                    "stated": {}}))
    interpretation = parser.understand("Can order 22003 still be cancelled?")
    assert [c.name for c in interpretation.candidates] == ["cancellation_allowed"]


def test_an_invalid_goal_is_a_parse_failure_not_a_judgement_that_nothing_covers_it():
    parser, _ = _parser(json.dumps({"goal": "SUP-3", "request": "resolve", "stated": {}}))
    interpretation = parser.understand("How much goodwill credit can order 12349 get?")
    assert not interpretation.parsed
    assert "fallback" in interpretation.encoder


# ---------------------------------------------------------------------------
# The relevance gate: what the parser's "none" verdict is allowed to be
# overridden by.
#
# Nine attempts to detect out-of-scope questions by a score were refuted
# (docs/audit/RELEVANCE_GATE.md); all nine kept nearest-neighbour goal selection
# and added a scalar, and a nearest-neighbour search always returns a neighbour.
# What works is the parser's own null option. These tests pin the two halves of
# it: the verdict is recorded, and a clause chosen by similarity may no longer
# answer over it when the question is about one person's case.
# ---------------------------------------------------------------------------

def _controller_saying(goal, request="decide", clause=None, encoder="hashing"):
    pack = load_pack(None, encoder)
    llm = Scripted(json.dumps({"goal": goal, "request": request, "clause": clause,
                               "stated": {}}))
    controller = NeuroSymbolicController(pack, llm=MockLLMProvider())
    controller.understanding = LLMSemanticParser(pack.program, pack.graph, pack.policies,
                                                 llm, pack.understanding)
    return controller


def test_the_none_verdict_is_recorded_as_a_judgement_not_an_empty_candidate_list():
    parser, _ = _parser(json.dumps({"goal": "none", "request": "decide", "stated": {}}))
    interpretation = parser.understand("Can you gift-wrap order 12345 before it ships?")
    # Distinct from a failed parse, which falls back to embeddings instead.
    assert interpretation.parsed and interpretation.out_of_scope
    assert interpretation.candidates == []


def test_a_failed_parse_is_not_recorded_as_a_judgement_that_nothing_covers_it():
    parser, _ = _parser("not json at all")
    interpretation = parser.understand("Can you gift-wrap order 12345 before it ships?")
    assert not interpretation.parsed and not interpretation.out_of_scope


def test_a_question_about_one_order_is_refused_not_answered_from_the_nearest_clause():
    """The failure this gate exists for.

    On the test split the unguarded fallback answered "can you gift-wrap order
    ORD-85004 before it ships" out of the *cancellation* clause, which scores 0.64
    against it. No rule decides gift-wrapping, so no clause settles it either.
    """
    controller = _controller_saying("none")
    answer = controller.answer("Can you gift-wrap order 12345 before it ships?",
                               actor_email="priya@meridian.test")
    assert answer.outcome == "insufficient"
    assert answer.decision is None


def test_the_refusal_says_a_relevant_looking_clause_was_declined_on_purpose():
    controller = _controller_saying("none")
    answer = controller.answer("Can you gift-wrap order 12345 before it ships?",
                               actor_email="priya@meridian.test")
    reason = (answer.trace or {}).get("pipeline_reason", "")
    # The reason must not read as "no clause was relevant" when one looked relevant
    # and was refused because the question is about a single case.
    assert "named none of them" in reason and "own case" in reason


def test_a_general_policy_question_is_still_answered_from_the_text():
    """The fallback the gate must not break.

    "What is the returns policy" asks what the text says and names no record. The
    parser names no clause only because the question is about the policy as a whole,
    and refusing here reported a knowledge gap over a corpus that covers the topic.

    The real encoder is needed here, unlike the rest of this file: whether the clause
    clears the retrieval floor is the thing under test, and the lexical fallback scores
    every clause too low to clear it.
    """
    controller = _controller_saying("none", request="inform", encoder="semantic")
    answer = controller.answer("What is the returns policy?",
                               actor_email="priya@meridian.test")
    assert answer.outcome != "insufficient"

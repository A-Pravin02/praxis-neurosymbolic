"""The neuro-symbolic assistant end to end: neural understanding, routing, grounding,
reasoning, verification and explanation.

These tests need the semantic encoder. If it cannot be loaded (no network on first
run, no cache), they are skipped rather than run against the lexical fallback, which is
known not to understand paraphrase and would make every assertion meaningless.
"""
from __future__ import annotations

from functools import lru_cache

import pytest

from app.hybrid.controller import NeuroSymbolicController
from app.hybrid.knowledge_pack import load_pack
from app.neural.entity_extraction import (
    description_facets, extract_quantities, is_question_clause, split_clauses,
)
from app.neural.intent_detection import speech_act_from_form

pytestmark = pytest.mark.no_db


@lru_cache(maxsize=1)
def controller() -> NeuroSymbolicController:
    pack = load_pack(None, "semantic")
    if not pack.encoder.semantic:
        pytest.skip("semantic encoder unavailable; these tests do not run on the lexical fallback")
    return NeuroSymbolicController(pack)


def ask(question: str, user: str, context: dict | None = None):
    return controller().answer(question, actor_email=user, context=context)


# --- neural components that need no model ------------------------------------------

def test_quantities_are_typed_by_unit():
    found = {(q.kind, q.value) for q in extract_quantities(
        "I travelled 450 km, spent ₹4,200 and it was two weeks ago")}
    assert {("distance", 450.0), ("money", 4200.0), ("days", 14.0)} <= found


def test_thousands_separators_do_not_split_a_clause():
    assert split_clauses("What will I get back for a 6,000 rupee trip?") == [
        "What will I get back for a 6,000 rupee trip"]


def test_an_embedded_condition_becomes_its_own_statement():
    """"if they arrived damaged" states the case; it is not part of the question."""
    assert "they arrived damaged" in split_clauses("Can I return the almonds if they arrived damaged?")


def test_questions_and_statements_are_told_apart():
    assert is_question_clause("Can I return the shoes I bought")
    assert not is_question_clause("have all the bills")


def test_speech_act_comes_from_interrogative_form():
    assert speech_act_from_form("Why was my refund rejected?") == "explain"
    assert speech_act_from_form("How much will I be reimbursed?") == "calculate"
    assert speech_act_from_form("Where is my order?") == "locate"
    assert speech_act_from_form("The blender came cracked, can I exchange it?") == "decide"


def test_a_description_splits_into_comparable_facets():
    facets = description_facets("the product arrived broken, cracked, smashed or defective")
    assert "the product arrived cracked" in facets


# --- the specification's demonstration ----------------------------------------------

def test_a_damaged_package_with_several_orders_asks_which_one():
    answer = ask("My package arrived damaged. Can I get a replacement instead of a refund?",
                 "priya@meridian.test")
    assert answer.pipeline == "E" and answer.outcome == "clarification"
    assert {o["id"] for o in answer.clarification["options"]} >= {"ORD-12347"}


def test_the_clarification_is_answered_once_the_order_is_known():
    """The follow-up names the order; the damage stated before is stated again."""
    answer = ask("The Copper Kettle order arrived damaged. Can I get a replacement?",
                 "priya@meridian.test")
    assert answer.goal == "replacement_available"
    assert answer.decision is False
    assert "out of stock" in answer.answer


def test_a_reimbursement_is_calculated_not_generated():
    answer = ask("How much will I be reimbursed if my business trip cost ₹8,750?",
                 "dev@meridian.test")
    assert answer.pipeline == "D"
    assert answer.value == 8000
    assert "₹8,000" in answer.answer


def test_a_different_question_selects_a_different_pipeline():
    shipped = ask("Can I cancel order 12345?", "priya@meridian.test")
    refund = ask("Why was my refund rejected even though I returned the product?",
                 "priya@meridian.test")
    policy = ask("What is the company's leave policy?", "priya@meridian.test")
    assert (shipped.pipeline, refund.pipeline, policy.pipeline) == ("B", "C", "A")


# --- generalisation -------------------------------------------------------------------

def test_a_paraphrase_with_no_shared_words_reaches_the_same_goal():
    answer = ask("I ordered this two weeks ago but changed my mind. Is there any way to send "
                 "it back?", "priya@meridian.test")
    assert answer.goal == "return_allowed"
    assert answer.subject == "ORD-12346", "two weeks ago identifies the order"
    assert answer.decision is True


# --- symbolic behaviour surfaced through the controller -------------------------------

def test_a_multi_hop_rejection_names_the_violated_condition():
    # The benchmark's "why was my earbuds refund turned down?" is a recorded failure: the
    # neural layer reads "refund turned down" as a cancellation refund. This test pins the
    # multi-hop mechanism itself, not that phrasing.
    answer = ask("Why was my refund rejected even though I returned the product?",
                 "priya@meridian.test")
    assert answer.pipeline == "C"
    assert "13 days after delivery" in answer.answer and "10 days" in answer.answer


def test_a_contradicted_statement_is_reported_and_the_record_is_used():
    answer = ask("My shoes order 12346 was delivered 60 days ago, can I still return it?",
                 "priya@meridian.test")
    assert answer.decision is True
    assert "record shows 14" in answer.answer


def test_a_hypothetical_is_answered_per_category():
    answer = ask("Can I return this product after 20 days?", "priya@meridian.test")
    assert answer.outcome == "conditional"
    assert "Electronics" in answer.answer and "10-day" in answer.answer


def test_unknown_inputs_become_stated_requirements_not_assumed_facts():
    answer = ask("Am I eligible for reimbursement if I travelled 450 km and spent ₹4,200?",
                 "dev@meridian.test")
    assert answer.outcome == "conditional"
    assert "receipts are attached" not in answer.answer, \
        "an unstated condition must never be asserted as met"
    assert "you have receipts" in answer.answer


def test_a_question_nothing_covers_is_not_answered():
    answer = ask("What is the capital of France?", "priya@meridian.test")
    assert answer.outcome == "insufficient"
    assert answer.pipeline == "F"


def test_someone_elses_order_is_refused_before_any_reasoning():
    answer = ask("Can I cancel order 22003?", "priya@meridian.test")
    assert answer.outcome == "denied"
    assert "22003" not in answer.answer or "belongs to a different account" in answer.answer


def test_a_support_agent_may_reason_about_any_order():
    answer = controller().answer("Can order 22003 be cancelled?",
                                 actor_email="support@meridian.test",
                                 actor_roles=("support",))
    assert answer.decision is True


def test_a_follow_up_uses_the_record_resolved_earlier():
    first = ask("Where is my laptop?", "priya@meridian.test")
    follow = ask("Can I cancel it?", "priya@meridian.test", context=first.context)
    assert follow.subject == "ORD-12345"
    assert follow.decision is False


# --- the trace -----------------------------------------------------------------------

def test_every_answer_records_why_its_pipeline_was_chosen():
    answer = ask("How much will I be reimbursed if my business trip cost ₹8,750?",
                 "dev@meridian.test")
    trace = answer.trace
    for key in ("neural_interpretation", "entities", "retrieved_knowledge", "symbolic_facts",
                "rules_used", "reasoning_steps", "verification", "selected_pipeline",
                "pipeline_reason", "confidence"):
        assert key in trace, key
    assert trace["pipeline_reason"]
    assert {c["layer"] for c in trace["components"]} >= {"NEURAL", "SYMBOLIC"}
    assert trace["verification"]["passed"] is True


def test_the_answer_figures_all_come_from_the_reasoning():
    answer = ask("How much will I get back if I cancel order 22003?", "sam@meridian.test")
    checks = {c["check"]: c for c in answer.trace["verification"]["checks"]}
    assert checks["answer grounding"]["passed"] is True
    assert answer.value == pytest.approx(3324.05)

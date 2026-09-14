"""The symbolic layer of the enterprise assistant, tested without any language model.

Every assertion here is about a decision the rules make from the knowledge graph.
If one of these fails, the assistant would give a wrong answer no matter how well
the neural layer understood the question.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from app.core.errors import RuleError
from app.symbolic.dsl import load_program, parse_literal, parse_program
from app.symbolic.knowledge_graph import load_graph
from app.symbolic.reasoner import CONDITIONAL, Reasoner, Symbolic

pytestmark = pytest.mark.no_db

PACK = Path(__file__).resolve().parents[1] / "data" / "meridian"


@lru_cache(maxsize=1)
def program():
    return load_program(PACK / "rules")


@lru_cache(maxsize=1)
def graph():
    return load_graph(PACK / "knowledge" / "company.yaml")


def decide(subject, predicate, stated=None, open_subjects=()):
    return Reasoner(program(), graph(), stated=stated or {},
                    open_subjects=set(open_subjects)).evaluate(subject, predicate)


# --- the rule language -------------------------------------------------------

def test_the_rule_language_compiles_through_the_validated_grammar():
    literal = parse_literal("?d <= ?w")
    assert literal == {"type": "comparison", "left": {"var": "?d"}, "op": "lte",
                       "right": {"var": "?w"}}
    fact = parse_literal('order_status(?o) = "delivered"')
    assert fact["type"] == "fact" and fact["predicate"] == "order_status"


def test_arithmetic_respects_precedence():
    fact = parse_literal("x(?o) = ?a - ?b * 2")
    assert fact["value"]["expr"]["op"] == "sub"
    assert fact["value"]["expr"]["args"][1]["expr"]["op"] == "mul"


def test_a_rule_that_cites_no_policy_is_rejected():
    source = '''
goal g(order) decision "a goal"
rule R-X "uncited"
  if order_status(?o) = "placed"
  then g(?o) = true
end
'''
    with pytest.raises(RuleError, match="cite"):
        parse_program(source)


def test_a_head_variable_the_body_never_binds_is_rejected():
    source = '''
goal g(order) decision "a goal"
rule R-X "unbound head" [P-1]
  if order_status(?o) = "placed"
  then g(?o) = ?nowhere
end
'''
    with pytest.raises(RuleError, match="bound"):
        parse_program(source)


def test_a_goal_no_rule_can_prove_is_rejected():
    with pytest.raises(RuleError, match="no rule"):
        parse_program('goal orphan(order) decision "nothing concludes this"\n'
                      'rule R-X "other" [P-1]\n  if a(?o) = 1\n  then b(?o) = 2\nend\n')


def test_the_company_pack_loads_and_every_rule_cites_a_clause():
    prog = program()
    assert len(prog.goals) >= 10
    assert all(spec.clauses for spec in prog.rules)


# --- knowledge graph ---------------------------------------------------------

def test_the_graph_links_records_into_traversable_relations():
    kg = graph()
    route = kg.path("CUST-1001", "electronics")
    predicates = [hop[1] for hop in route]
    assert "~placed_by" in predicates and "product_category" in predicates


def test_ownership_is_found_by_traversal():
    kg = graph()
    assert kg.owner_of("RFD-5001") == "CUST-1001"      # refund -> return -> order -> customer
    assert kg.owner_of("CLM-3001") == "EMP-2001"


# --- decisions -------------------------------------------------------------

def test_a_return_within_the_window_is_allowed():
    result = decide("ORD-12346", "return_allowed")
    assert result.status == "determined" and result.value is True
    assert result.decided_by.spec.code == "R-RET-A"
    assert "14 days" in result.decided_by.because


def test_the_plus_extension_is_what_allows_a_40_day_return():
    result = decide("ORD-12349", "return_allowed")
    assert result.value is True
    assert "45-day" in result.decided_by.because


def test_a_late_return_is_refused_with_the_violated_condition():
    result = decide("ORD-12348", "return_allowed")
    assert result.status == "default" and result.value is False
    notes = [o.failed.note for o in result.refutations()]
    assert any("25 days ago, past the 10-day" in n for n in notes)


def test_a_shipped_order_cannot_be_cancelled():
    result = decide("ORD-12345", "cancellation_allowed")
    assert result.value is False
    assert result.decided_by.spec.code == "R-CAN-3"


def test_an_out_of_stock_item_gets_a_refund_not_a_replacement():
    result = decide("ORD-12347", "replacement_available",
                    stated={("ORD-12347", "return_reason"): "damaged_on_arrival"})
    assert result.value is False
    assert any("out of stock" in o.failed.note for o in result.refutations())


def test_a_rejected_refund_is_explained_by_multi_hop_reasoning():
    """refund -> return -> order -> product -> category -> window."""
    result = decide("RTN-9001", "refund_approved")
    assert result.value is False
    assert "13 days after delivery" in result.refutations()[0].failed.note
    assert "10 days" in result.refutations()[0].failed.note


def test_the_cancellation_refund_is_calculated_not_guessed():
    result = decide("ORD-22003", "cancellation_refund")
    assert result.value == pytest.approx(3324.05)


def test_payment_taken_for_a_failed_order_must_be_refunded():
    assert decide("ORD-22002", "refund_required").value is True


def test_delay_compensation_depends_on_membership():
    result = decide("ORD-22004", "delay_compensation")
    assert result.value == 49


def test_leave_entitlement_follows_years_of_service():
    assert decide("EMP-2001", "annual_leave_days").value == 24
    assert decide("EMP-2002", "senior_leave_eligible").value is False


def test_a_recorded_claim_is_capped_by_grade():
    assert decide("CLM-3001", "reimbursable_amount").value == 8000


# --- three-valued reasoning ------------------------------------------------------

def test_an_unstated_fact_is_unknown_not_false():
    """The property that separates this reasoner from a rule-based FAQ bot."""
    result = decide("case:claim", "reimbursable_amount",
                    stated={("case:claim", "claimant"): "EMP-2001",
                            ("case:claim", "claimed_amount"): 8750},
                    open_subjects={"case:claim"})
    assert result.status == CONDITIONAL
    assert result.value == 8000
    missing = {m.predicate for m in result.missing}
    assert {"travel_mode", "receipts_attached", "travel_distance_km"} <= missing


def test_an_unknown_input_inside_arithmetic_stays_symbolic():
    result = decide("case:claim", "reimbursable_amount",
                    stated={("case:claim", "claimant"): "EMP-2001",
                            ("case:claim", "claimed_amount"): 8750,
                            ("case:claim", "travel_mode"): "own_vehicle"},
                    open_subjects={"case:claim"})
    assert isinstance(result.value, Symbolic)
    assert result.value.op == "min"
    assert 8000 in result.value.args, "the known part must still be computed"


def test_a_user_statement_cannot_override_a_company_record():
    """A closed record is authoritative; the user saying otherwise changes nothing."""
    result = decide("ORD-12345", "cancellation_allowed",
                    stated={("ORD-12345", "order_status"): "placed"})
    assert result.value is False


def test_a_stated_reason_is_accepted_where_the_company_keeps_no_record():
    result = decide("ORD-12347", "return_allowed",
                    stated={("ORD-12347", "return_reason"): "damaged_on_arrival"})
    assert result.value is True
    assert result.decided_by.spec.code == "R-RET-D"

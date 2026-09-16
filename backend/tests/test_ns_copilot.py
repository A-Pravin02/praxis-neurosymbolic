"""The Support Decision Copilot: policy versions, authorization before reasoning, claims
checked against records and policy, knowledge gaps, the verification gate, and knowledge
administration without code changes."""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from app.hybrid import knowledge_store as store
from app.hybrid.controller import NeuroSymbolicController
from app.hybrid.knowledge_pack import default_pack_dir, load_pack
from app.hybrid.verification import Check
from app.symbolic.dsl import load_program
from app.symbolic.knowledge_graph import load_graph_dir
from app.symbolic.reasoner import Reasoner

pytestmark = pytest.mark.no_db
PACK = default_pack_dir()
SUPPORT = ("support@meridian.test", ("support",))


@lru_cache(maxsize=1)
def program_and_graph():
    return load_program(PACK / "rules"), load_graph_dir(PACK / "knowledge")


@lru_cache(maxsize=1)
def controller() -> NeuroSymbolicController:
    pack = load_pack(None, "semantic")
    if not pack.encoder.semantic:
        pytest.skip("semantic encoder unavailable; these tests do not run on the lexical fallback")
    return NeuroSymbolicController(pack)


def ask(question: str, user: str, roles: tuple[str, ...] = ()):
    return controller().answer(question, actor_email=user, actor_roles=roles)


# --- policy versions: symbolic, no model needed ------------------------------------------

def test_the_version_in_force_on_the_delivery_date_governs_the_case():
    program, graph = program_and_graph()
    after = Reasoner(program, graph, stated={("ORD-33001", "return_reason"): "damaged_on_arrival"})
    before = Reasoner(program, graph, stated={("ORD-33002", "return_reason"): "damaged_on_arrival"})
    assert after.evaluate("ORD-33001", "return_allowed").value is True
    assert before.evaluate("ORD-33002", "return_allowed").value is False
    assert after.version_resolutions[("ORD-33001", "returns")]["version"] == "RETURNS-v2"
    assert before.version_resolutions[("ORD-33002", "returns")]["version"] == "RETURNS-v1"


def test_a_naive_engine_that_always_applies_the_newest_version_gets_it_wrong():
    program, graph = program_and_graph()
    # The shoes were delivered on 31 August, 14 days ago: version 1's 7-day damage window
    # governs them, version 2's 15-day window does not.
    stated = {("ORD-12346", "return_reason"): "damaged_on_arrival"}
    correct = Reasoner(program, graph, stated=stated)
    naive = Reasoner(program, graph, stated=stated, latest_versions=True)
    assert correct.evaluate("ORD-12346", "replacement_available").value is False
    assert naive.evaluate("ORD-12346", "replacement_available").value is True


def test_superseded_clauses_stay_citeable_but_are_not_retrieved():
    pack = load_pack(None, "semantic")
    assert pack.policies.clause("RET-4", 1).superseded_on == "2026-09-01"
    assert "15 days" in pack.policies.clause("RET-4").text
    assert all(c.in_force for c in pack.policies.clauses)


# --- authorization before reasoning --------------------------------------------------------

def test_the_reasoner_cannot_see_facts_outside_the_persons_access():
    program, graph = program_and_graph()
    priya = graph.view_for("customer", "CUST-1001", program)
    assert Reasoner(program, priya).evaluate("ORD-22001", "order_total").status == "absent"
    assert Reasoner(program, priya).evaluate("ORD-12345", "order_total").value == 62999
    support = graph.view_for("support", None, program)
    assert support.values("EMP-2002", "monthly_salary") == []
    assert support.values("ORD-22001", "order_total") == [749]


def test_one_employee_cannot_read_anothers_salary():
    answer = ask("What is Ana's monthly salary?", "dev@meridian.test")
    assert answer.outcome == "denied"
    assert "92,000" not in answer.answer


def test_a_support_agent_cannot_read_employee_records():
    answer = ask("What is Dev Rao's salary?", *SUPPORT)
    assert answer.outcome == "denied"
    assert "185,000" not in answer.answer


# --- the copilot decisions ------------------------------------------------------------------

def test_the_demonstration_question_gets_a_verified_recommendation():
    answer = ask("My customer's laptop arrived damaged 12 days ago and they want a replacement. "
                 "What can I offer?", *SUPPORT)
    assert answer.goal == "recommended_resolution"
    assert answer.value == "replacement"
    assert answer.verification["status"] == "passed"
    assert "approval" in answer.recommended_action.lower()
    assert any(v["version"] == "RETURNS-v2" for v in answer.policy_versions)
    assert answer.rules_applied and all(r["formula"] for r in answer.rules_applied)


def test_a_false_policy_premise_is_rejected_and_the_policy_applied():
    answer = ask("The replacement policy is 30 days. My customer's laptop arrived damaged 12 days "
                 "ago. What can I offer?", *SUPPORT)
    assert answer.value == "replacement"
    rejected = [c for c in answer.claims if not c["confirmed"]]
    assert rejected and rejected[0]["record"] == "15"
    assert "30 days" in answer.answer and "15" in answer.answer


def test_an_unrecorded_approval_is_not_treated_as_approval():
    answer = ask("The manager already approved a goodwill credit for order 22001. How much can I "
                 "issue?", *SUPPORT)
    assert answer.value == 0
    assert answer.outcome == "answered"
    assert "no such approval" in answer.answer


def test_a_recorded_approval_allows_the_goodwill_credit():
    answer = ask("How much goodwill credit can I give the customer on order 12348?", *SUPPORT)
    assert answer.value == pytest.approx(499.9)


def test_issues_are_routed_by_the_playbook():
    answer = ask("Which team should a complaint about being charged twice go to?", *SUPPORT)
    assert answer.value == "payments_team"


# --- knowledge gaps --------------------------------------------------------------------------

def test_an_issue_the_playbook_does_not_route_is_a_logged_gap_not_a_guess():
    answer = ask("Which team handles a customer who can't log in because their password reset "
                 "email never arrives?", *SUPPORT)
    assert answer.outcome == "insufficient"
    assert answer.knowledge_gap["type"] == "undefined_case"
    for team in ("Payments team", "Logistics desk", "Returns desk", "Order care"):
        assert team not in answer.answer


def test_an_uncovered_topic_names_what_is_missing():
    answer = ask("What is the noise limit for a delivery van?", "priya@meridian.test")
    assert answer.knowledge_gap["type"] == "uncovered_topic"


def test_general_conversation_is_not_logged_as_a_knowledge_gap():
    # A gap is a prompt to an administrator to add knowledge. Filing "who won the
    # world cup" as one would bury the real gaps under questions no policy can answer.
    answer = ask("Who won the 2018 football world cup?", "priya@meridian.test")
    assert answer.pipeline == "G"
    assert answer.knowledge_gap is None


# --- the verification gate -------------------------------------------------------------------

def test_an_answer_that_fails_verification_is_withheld(monkeypatch):
    from app.hybrid import verification

    def broken(self, reasoner):
        return Check("fact validity", False, "simulated corrupted record")

    monkeypatch.setattr(verification.Verifier, "fact_validity", broken)
    answer = ask("Can order 22003 still be cancelled?", *SUPPORT)
    assert answer.outcome == "needs_review"
    assert answer.decision is None and answer.value is None
    assert answer.knowledge_gap["type"] == "verification_failure"
    assert "can still be cancelled" not in answer.answer


# --- knowledge administration ------------------------------------------------------------------

@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    target = tmp_path / "meridian"
    shutil.copytree(PACK, target, ignore=shutil.ignore_patterns(".history", "REVISION"))
    return target


def test_a_rule_citing_a_clause_that_does_not_exist_is_rejected(pack_copy):
    rules = store.read_file(pack_copy, "rules", "04_support.rules")
    broken = rules.replace("[SUP-4]", "[SUP-99]", 1)
    result = store.validate(pack_copy, {("rules", "04_support.rules"): broken})
    assert not result.ok and "SUP-99" in result.errors[0]
    with pytest.raises(store.KnowledgeChangeError):
        store.apply(pack_copy, {("rules", "04_support.rules"): broken}, author="t")
    assert store.read_file(pack_copy, "rules", "04_support.rules") == rules


def test_a_policy_change_is_a_data_change_and_can_be_rolled_back(pack_copy):
    original = store.read_file(pack_copy, "knowledge", "company.yaml")
    store.upsert_entity(pack_copy, entity_id="RETURNS-v2", entity_type="policy_version",
                        name=None, attributes={"damage_report_days": 20}, author="admin")
    graph = load_graph_dir(pack_copy / "knowledge")
    assert graph.values("RETURNS-v2", "damage_report_days") == [20]
    assert store.read_file(pack_copy, "knowledge", "company.yaml") == original
    snapshot = store.history(pack_copy)[0]["id"]
    store.restore(pack_copy, snapshot, author="admin")
    assert load_graph_dir(pack_copy / "knowledge").values("RETURNS-v2", "damage_report_days") == [15]


def test_an_entity_with_an_undeclared_attribute_is_rejected(pack_copy):
    with pytest.raises(store.KnowledgeChangeError):
        store.upsert_entity(pack_copy, entity_id="CUST-9", entity_type="customer", name="X",
                            attributes={"favourite_colour": "blue"}, author="admin")


def test_paths_outside_the_pack_are_refused(pack_copy):
    with pytest.raises(store.KnowledgeChangeError):
        store.validate(pack_copy, {("rules", "../../evil.rules"): "x"})

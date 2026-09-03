"""Knowledge representation, provenance records, and the evaluation harness."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.knowledge import Answer, Claim as ClaimRow, ProvenanceRecord
from app.db.session import tenant_session
from app.evaluation import harness
from app.evaluation.cases import DEMO_SUITE, EvalCase, get_suite
from app.knowledge import store as knowledge_store
from app.knowledge.schema import (
    FactDTO, FactKind, Provenance, make_fact, render_value, typed_value, unwrap,
)
from app.orchestrator import conversation as convo
from app.orchestrator.pipeline import Orchestrator
from tests.conftest import auth_headers


# --- knowledge representation ----------------------------------------------

@pytest.mark.parametrize("value,expected_type,expected", [
    (20, "number", 20.0),
    (20.5, "number", 20.5),
    ("20", "number", 20.0),
    (True, "bool", True),
    ("yes", "bool", True),
    ("2026-01-01", "date", "2026-01-01"),
    ("engineering", "string", "engineering"),
])
def test_value_typing(value, expected_type, expected):
    typed = typed_value(value)
    assert typed["type"] == expected_type
    assert typed["value"] == expected


def test_units_are_preserved_and_rendered():
    fact = make_fact("policy", "annual_leave_days", 20, unit="days")
    assert fact.object["unit"] == "days"
    assert render_value(fact.object) == "20 days"


def test_the_three_knowledge_kinds_are_distinct():
    assert {k.value for k in FactKind} == {"DIRECT", "INFERRED", "MODEL_GENERATED"}


def test_model_generated_facts_are_never_persisted(tenant_db, alice):
    saved = knowledge_store.save_facts(tenant_db, alice.tenant_id, [
        make_fact("policy", "invented_thing", 1, kind=FactKind.MODEL_GENERATED),
        make_fact("policy", "real_thing", 2, kind=FactKind.DIRECT),
    ])
    assert [row.predicate for row in saved] == ["real_thing"]


def test_fact_round_trip_preserves_provenance(tenant_db, alice):
    original = make_fact(
        "policy", "round_trip_days", 7, unit="days",
        provenance=Provenance(source_kind="document", document_title="Doc A",
                              evidence_text="the round trip days is 7",
                              extractor="attr_is_value", effective_date="2026-01-01"))
    rows = knowledge_store.save_facts(tenant_db, alice.tenant_id, [original])
    restored = knowledge_store.fact_to_dto(rows[0])
    assert restored.subject == original.subject
    assert unwrap(restored.object) == 7.0
    assert restored.provenance.document_title == "Doc A"
    assert restored.provenance.effective_date == "2026-01-01"
    assert restored.provenance.extractor == "attr_is_value"


def test_user_attributes_become_facts_about_self(alice):
    facts = knowledge_store.subject_facts_for_user(alice.user_id, alice.attributes)
    rendered = {f.render() for f in facts}
    assert "self.years_of_service = 4" in rendered
    assert all(f.provenance.source_kind == "user_attribute" for f in facts)


def test_facts_respect_permission_filtering(tenant_db, alice, hr_user):
    restricted = make_fact("policy", "restricted_metric", 1)
    restricted.required_permission = "document:read_hr"
    knowledge_store.save_facts(tenant_db, alice.tenant_id, [restricted])

    employee_view = knowledge_store.load_facts(tenant_db, alice,
                                               predicates=["restricted_metric"])
    hr_view = knowledge_store.load_facts(tenant_db, hr_user,
                                         predicates=["restricted_metric"])
    assert employee_view == []
    assert len(hr_view) == 1


def test_invalid_rules_are_reported_not_silently_dropped(tenant_db, alice):
    from app.db.models.symbolic import Rule

    tenant_db.add(Rule(tenant_id=alice.tenant_id, code="BROKEN_RULE",
                       body_json=[{"type": "nonsense"}],
                       head_json={"predicate": "x", "value": 1}, is_active=True))
    tenant_db.flush()
    compiled, problems = knowledge_store.load_rules(tenant_db, alice.tenant_id)
    assert any("BROKEN_RULE" in p for p in problems)
    assert all(rule.code != "BROKEN_RULE" for rule in compiled)


# --- provenance records -----------------------------------------------------

def test_answer_and_provenance_graph_are_persisted(client, alice):
    headers = auth_headers(client, "alice@acme.test")
    body = client.post("/api/chat", headers=headers, json={
        "message": "How many annual leave days am I entitled to?"}).json()

    with tenant_session(alice.tenant_id, alice.user_id) as db:
        answer = db.scalar(select(Answer).where(
            Answer.message_id == uuid.UUID(body["message_id"])))
        assert answer is not None
        assert answer.route == "RAG_PLUS_SYMBOLIC"
        assert answer.confidence > 0

        claims = db.scalars(select(ClaimRow).where(
            ClaimRow.answer_id == answer.id)).all()
        assert claims

        record = db.scalar(select(ProvenanceRecord).where(
            ProvenanceRecord.answer_id == answer.id))
        assert record is not None
        graph = record.graph_json
        kinds = {node["type"] for node in graph["nodes"]}
        # Answer -> claims -> facts -> rules -> evidence -> source.
        assert {"answer", "claim", "fact", "rule", "evidence", "source"} <= kinds
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert {"asserts", "derived_by", "from_source"} <= edge_types


def test_provenance_graph_links_a_conclusion_to_its_premises(alice):
    with tenant_session(alice.tenant_id, alice.user_id) as db:
        turn = Orchestrator(db, alice).answer(
            "How many annual leave days am I entitled to?")
        graph = convo._provenance_graph(turn)
    premise_edges = [e for e in graph["edges"] if e["type"] == "premise"]
    derived_edges = [e for e in graph["edges"] if e["type"] == "derived_by"]
    assert derived_edges
    assert premise_edges or any(
        node["type"] == "fact" and node.get("kind") == "DIRECT"
        for node in graph["nodes"])


# --- context window ---------------------------------------------------------

def test_history_window_is_bounded(client, alice):
    headers = auth_headers(client, "alice@acme.test")
    conversation_id = client.post("/api/conversations", headers=headers,
                                  json={"title": "Long"}).json()["id"]
    for i in range(6):
        client.post("/api/chat", headers=headers, json={
            "conversation_id": conversation_id,
            "message": f"Question number {i} about the notice period?"})
    with tenant_session(alice.tenant_id, alice.user_id) as db:
        window = convo.history_window(db, alice, uuid.UUID(conversation_id))
    from app.config import settings
    assert len(window) <= settings.max_context_messages
    assert sum(len(m["content"]) for m in window) <= settings.max_context_chars


# --- evaluation harness -----------------------------------------------------

def test_demo_suite_is_well_formed():
    kinds = {"grounded", "symbolic", "abstain", "conflict", "denied", "general"}
    assert len(DEMO_SUITE) >= 20
    assert {c.expected_kind for c in DEMO_SUITE} <= kinds
    assert len({c.id for c in DEMO_SUITE}) == len(DEMO_SUITE)
    # The suite must contain cases where refusal is the correct answer.
    assert sum(1 for c in DEMO_SUITE if c.expected_kind == "abstain") >= 4


def test_harness_judges_a_correct_symbolic_answer():
    case = next(c for c in DEMO_SUITE if c.id == "sym-leave-senior")
    outcome = harness.run_case(case, "neurosymbolic")
    assert outcome.correct
    assert not outcome.hallucinated
    assert outcome.citation_correct


def test_harness_judges_a_required_abstention():
    case = next(c for c in DEMO_SUITE if c.id == "abstain-submarine")
    outcome = harness.run_case(case, "neurosymbolic")
    assert outcome.abstained
    assert outcome.correct


def test_harness_detects_a_wrong_answer():
    """A deliberately mis-specified case must be judged incorrect."""
    case = EvalCase(id="synthetic-wrong", question="How many annual leave days am I "
                                                   "entitled to?",
                    user_email="alice@acme.test", expected_kind="symbolic",
                    expected_values=[99.0], forbidden_values=[20.0])
    outcome = harness.run_case(case, "neurosymbolic")
    assert not outcome.correct


def test_harness_does_not_mutate_the_workspace(alice):
    from app.db.models.chat import Message

    with tenant_session(alice.tenant_id, alice.user_id) as db:
        before = len(db.scalars(select(Message)).all())
    harness.run_case(next(c for c in DEMO_SUITE if c.id == "rag-sick-leave"),
                     "neurosymbolic")
    with tenant_session(alice.tenant_id, alice.user_id) as db:
        after = len(db.scalars(select(Message)).all())
    assert before == after


def test_full_suite_shows_the_architecture_helping():
    report = harness.run_suite("demo", ["llm_rag", "neurosymbolic"])
    rag = report.metrics["llm_rag"]
    full = report.metrics["neurosymbolic"]

    assert full.cases == rag.cases == len(DEMO_SUITE)
    # These are the claims the project makes; if a change breaks one, this fails.
    assert full.symbolic_accuracy > rag.symbolic_accuracy
    assert full.contradiction_detection_rate > rag.contradiction_detection_rate
    assert full.hallucination_rate <= rag.hallucination_rate
    assert full.factual_accuracy >= rag.factual_accuracy
    assert full.forbidden_content_rate == 0.0


def test_report_rendering_and_writing(tmp_path):
    report = harness.run_suite("demo", ["neurosymbolic"], limit=3)
    markdown = harness.render_markdown(report)
    assert "Evaluation report" in markdown
    assert "Hallucination rate" in markdown
    paths = harness.write_report(report, tmp_path)
    assert (tmp_path / "latest.md").exists()
    assert paths["json"].endswith(".json")


def test_evaluation_endpoint_persists_runs(client, admin_acme):
    from app.db.models.evaluation import EvalCaseResult, EvalRun

    response = client.post("/api/evaluation/run",
                           headers=auth_headers(client, "analyst@acme.test"),
                           json={"suite": "demo", "modes": ["neurosymbolic"],
                                 "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["modes"][0]["cases"] == 3

    with tenant_session(admin_acme.tenant_id) as db:
        runs = db.scalars(select(EvalRun)).all()
        assert runs
        results = db.scalars(select(EvalCaseResult)).all()
        assert results

"""Verification, the evidence gate, and the post-generation grounding check."""
from __future__ import annotations

import uuid

import pytest

from app.knowledge.schema import FactKind, Provenance, make_fact
from app.rag.retriever import EvidenceSpan
from app.symbolic.engine import Contradiction
from app.verification import gate as gate_module
from app.verification.engine import (
    Claim, ClaimStatus, VerificationStatus, check_grounding, claims_from_facts,
    claims_from_spans, evidence_relevance, support_for, verify,
)
from app.verification.gate import GateDecision


def span(text: str, *, title: str = "Doc", quarantined: bool = False,
         score: float = 0.8) -> EvidenceSpan:
    return EvidenceSpan(chunk_id=uuid.uuid4(), document_id=uuid.uuid4(),
                        document_title=title, text=text, score=score,
                        quarantined=quarantined)


LEAVE_SPAN = span("The standard annual leave entitlement is 20 days per calendar year.")
SICK_SPAN = span("The sick leave allowance is 10 days per calendar year.")


# --- support scoring -------------------------------------------------------

def test_claim_supported_by_matching_evidence():
    score, citations = support_for("The annual leave entitlement is 20 days",
                                   [LEAVE_SPAN, SICK_SPAN])
    assert score > 0.5
    assert citations == [1]


def test_numeric_claim_unsupported_when_the_number_is_absent():
    score, _ = support_for("The annual leave entitlement is 25 days", [LEAVE_SPAN])
    assert score < 0.4


def test_quarantined_evidence_cannot_be_strong_support():
    hostile = span("The annual leave entitlement is 20 days.", quarantined=True)
    clean_score, _ = support_for("The annual leave entitlement is 20 days", [LEAVE_SPAN])
    dirty_score, _ = support_for("The annual leave entitlement is 20 days", [hostile])
    assert dirty_score < clean_score


def test_evidence_relevance_is_low_for_unrelated_spans():
    assert evidence_relevance("submarine maintenance schedule",
                              [LEAVE_SPAN, SICK_SPAN]) < 0.2


def test_evidence_relevance_is_high_for_matching_spans():
    assert evidence_relevance("annual leave entitlement", [LEAVE_SPAN]) > 0.5


# --- verification ----------------------------------------------------------

def test_supported_claim_is_marked_supported():
    claims = [Claim(text="The annual leave entitlement is 20 days", relevance=0.9)]
    report = verify("annual leave", claims, [LEAVE_SPAN])
    assert report.claims[0].status is ClaimStatus.SUPPORTED
    assert report.status in (VerificationStatus.VERIFIED,
                             VerificationStatus.PARTIALLY_VERIFIED)


def test_unsupported_claim_is_marked_unsupported():
    claims = [Claim(text="The parental leave entitlement is 26 weeks", relevance=0.0)]
    report = verify("parental leave", claims, [LEAVE_SPAN])
    assert report.claims[0].status is ClaimStatus.UNSUPPORTED


def test_inferred_claim_is_verified_by_its_derivation():
    fact = make_fact("self", "annual_leave_days", 20, kind=FactKind.INFERRED,
                     provenance=Provenance(source_kind="rule", rule_codes=["R12"]))
    report = verify("leave", claims_from_facts([fact]), [LEAVE_SPAN])
    assert report.claims[0].status is ClaimStatus.VERIFIED


def test_profile_fact_is_verified_without_document_evidence():
    fact = make_fact("self", "years_of_service", 4,
                     provenance=Provenance(source_kind="user_attribute"))
    report = verify("service", claims_from_facts([fact]), [])
    assert report.claims[0].status is ClaimStatus.VERIFIED


def test_unresolved_conflict_marks_claims_contradicted():
    fact = make_fact("policy", "annual_leave_days", 20)
    conflict = Contradiction(subject="policy", predicate="annual_leave_days",
                             values=[20, 18])
    report = verify("leave", claims_from_facts([fact]), [LEAVE_SPAN],
                    contradictions=[conflict])
    assert report.claims[0].status is ClaimStatus.CONTRADICTED
    assert report.status is VerificationStatus.CONTRADICTED


def test_resolved_conflict_does_not_contradict_the_surviving_value():
    fact = make_fact("policy", "annual_leave_days", 20)
    conflict = Contradiction(subject="policy", predicate="annual_leave_days",
                             values=[20, 18], severity="warning",
                             preferred_value=20, superseded_values=[18])
    report = verify("leave", claims_from_facts([fact]), [LEAVE_SPAN],
                    contradictions=[conflict])
    assert report.claims[0].status is not ClaimStatus.CONTRADICTED
    assert report.status is not VerificationStatus.CONTRADICTED


def test_superseded_value_in_a_claim_is_contradicted():
    conflict = Contradiction(subject="policy", predicate="annual_leave_days",
                             values=[20, 18], severity="warning",
                             preferred_value=20, superseded_values=[18])
    claims = [Claim(text="The annual leave entitlement is 18 days", relevance=0.9)]
    report = verify("leave", claims, [span("annual leave entitlement is 18 days")],
                    contradictions=[conflict])
    assert report.claims[0].status is ClaimStatus.CONTRADICTED


def test_span_claims_skip_headings():
    heading_span = span("Acme Employee Handbook 2026")
    assert claims_from_spans("employee handbook", [heading_span]) == []


def test_span_claims_prefer_the_sentence_that_answers_the_question():
    overview = span("The Praxis Router X1 is a dual-band office router for teams.")
    answer = span("The return window is 30 days from delivery.")
    claims = claims_from_spans("What is the return window for the Praxis Router X1?",
                               [overview, answer],
                               focus_terms=["return window", "return period"])
    assert claims and "return window is 30 days" in claims[0].text


# --- the gate --------------------------------------------------------------

def test_gate_abstains_without_evidence():
    result = gate_module.evaluate(verify("q", [], []), [], require_evidence=True)
    assert result.decision is GateDecision.ABSTAIN
    assert result.confidence == 0.0


def test_gate_abstains_on_irrelevant_evidence():
    claims = [Claim(text="The sick leave allowance is 10 days", relevance=0.9)]
    report = verify("submarine maintenance", claims, [SICK_SPAN])
    result = gate_module.evaluate(report, [SICK_SPAN], require_evidence=True)
    assert result.decision is GateDecision.ABSTAIN


def test_gate_answers_on_verified_symbolic_conclusion():
    fact = make_fact("self", "annual_leave_days", 20, kind=FactKind.INFERRED,
                     provenance=Provenance(source_kind="rule", rule_codes=["R12"]))
    report = verify("annual leave entitlement", claims_from_facts([fact]), [LEAVE_SPAN])
    result = gate_module.evaluate(report, [LEAVE_SPAN], symbolic_conclusion=True)
    assert result.decision is GateDecision.ANSWER
    assert result.confidence >= 0.8


def test_gate_reports_an_unresolved_conflict_instead_of_answering():
    conflict = Contradiction(subject="policy", predicate="warranty_months",
                             values=[24, 36])
    report = verify("warranty", [Claim(text="The warranty period is 24 months",
                                       relevance=0.9)],
                    [span("The warranty period is 24 months from purchase.")],
                    contradictions=[conflict])
    result = gate_module.evaluate(report, [LEAVE_SPAN], contradictions=[conflict])
    assert result.decision is GateDecision.REPORT_CONFLICT


def test_gate_never_exceeds_its_confidence_ceiling():
    fact = make_fact("self", "annual_leave_days", 20, kind=FactKind.INFERRED,
                     provenance=Provenance(source_kind="rule", rule_codes=["R12"]))
    report = verify("annual leave", claims_from_facts([fact]), [LEAVE_SPAN])
    result = gate_module.evaluate(report, [LEAVE_SPAN], symbolic_conclusion=True)
    assert result.confidence <= 0.97


def test_gate_thresholds_are_configurable():
    claims = [Claim(text="The sick leave allowance is 10 days", relevance=0.9)]
    report = verify("sick leave allowance", claims, [SICK_SPAN])
    strict = gate_module.evaluate(report, [SICK_SPAN], answer_threshold=0.99,
                                  caveat_threshold=0.98)
    assert strict.decision is GateDecision.ABSTAIN
    lenient = gate_module.evaluate(report, [SICK_SPAN], answer_threshold=0.1,
                                   caveat_threshold=0.05)
    assert lenient.decision is GateDecision.ANSWER


# --- post-generation grounding ---------------------------------------------

def test_grounded_answer_passes():
    claims = [Claim(text="The annual leave entitlement is 20 days",
                    status=ClaimStatus.SUPPORTED)]
    report = check_grounding("The annual leave entitlement is 20 days. [1]",
                             claims, [LEAVE_SPAN])
    assert report.grounded


def test_invented_number_is_caught():
    claims = [Claim(text="The annual leave entitlement is 20 days",
                    status=ClaimStatus.SUPPORTED)]
    report = check_grounding("The annual leave entitlement is 27 days.",
                             claims, [LEAVE_SPAN])
    assert not report.grounded
    assert 27.0 in report.unsupported_numbers


def test_invented_citation_index_is_caught():
    claims = [Claim(text="The annual leave entitlement is 20 days",
                    status=ClaimStatus.SUPPORTED)]
    report = check_grounding("The annual leave entitlement is 20 days. [7]",
                             claims, [LEAVE_SPAN])
    assert not report.grounded
    assert report.invalid_citations == [7]


def test_citation_markers_are_not_treated_as_values():
    claims = [Claim(text="The annual leave entitlement is 20 days",
                    status=ClaimStatus.SUPPORTED)]
    report = check_grounding("The annual leave entitlement is 20 days. [1]",
                             claims, [LEAVE_SPAN])
    assert report.unsupported_numbers == []


def test_provenance_context_may_be_mentioned():
    dated = span("The annual leave entitlement is 20 days.", title="Handbook 2026")
    dated.effective_date = "2026-01-01"
    claims = [Claim(text="The annual leave entitlement is 20 days",
                    status=ClaimStatus.SUPPORTED)]
    report = check_grounding(
        "Handbook 2026 is effective 2026-01-01 and supersedes the 2019 edition; "
        "the entitlement is 20 days. [1]",
        claims, [dated], extra_context=["Handbook 2026 supersedes Handbook 2019"])
    assert report.grounded


def test_neither_side_of_an_unresolved_conflict_may_be_asserted():
    conflict = Contradiction(subject="policy", predicate="warranty_months",
                             values=[24.0, 36.0])
    claims = [
        Claim(text="The warranty period is 24 months from purchase", relevance=0.9),
        Claim(text="The warranty period is 36 months", relevance=0.9),
    ]
    report = verify("warranty", claims, [span("The warranty period is 24 months.")],
                    contradictions=[conflict])
    assert all(c.status is ClaimStatus.CONTRADICTED for c in report.claims)
    assert report.supported_claims == []

"""Reliability lab flags: stated as disagreements with the rules, never as proven errors."""
from __future__ import annotations

from types import SimpleNamespace

from app.hybrid import compare


def _praxis(outcome="answered", decision=None, value=None):
    return SimpleNamespace(outcome=outcome, decision=decision, value=value)


def _baseline(text="", outcome="answered", decision=None, value=None):
    return {"text": text, "outcome": outcome, "decision": decision, "value": value}


def test_a_decision_the_rules_contradict_is_flagged():
    flags = compare._flags(_baseline(decision=True), _praxis(decision=False), set())
    assert [f["kind"] for f in flags] == ["contradicts_rules"]


def test_an_amount_the_rules_compute_differently_is_flagged():
    flags = compare._flags(_baseline(value=500.0), _praxis(value=250.0), set())
    assert flags and flags[0]["kind"] == "contradicts_rules"


def test_a_definite_answer_where_the_rules_found_none_is_flagged():
    flags = compare._flags(_baseline(decision=True), _praxis(outcome="denied"), set())
    assert flags[0]["kind"] == "unsupported_answer"
    assert "may not see" in flags[0]["detail"]


def test_a_number_nothing_supports_is_flagged_and_supported_ones_are_not():
    flags = compare._flags(_baseline(text="Returns are accepted within 30 days, fee 49."),
                           _praxis(decision=True), {49.0})
    assert [f["kind"] for f in flags] == ["unsupported_number"]
    assert "30" in flags[0]["detail"] and "49" not in flags[0]["detail"]


def test_agreement_with_the_rules_raises_no_flag():
    assert compare._flags(_baseline(text="Yes.", decision=True), _praxis(decision=True), set()) == []


def test_the_comparison_is_unavailable_without_a_real_model(monkeypatch):
    monkeypatch.setattr(compare.settings, "llm_provider", "mock")
    assert compare.available() is False

"""Symbolic engine: rule validation, chaining, provenance, contradictions."""
from __future__ import annotations

import pytest

from app.core.errors import RuleError
from app.knowledge.schema import FactKind, Provenance, make_fact, unwrap
from app.symbolic.engine import (
    SymbolicEngine, check_constraints, compare, detect_contradictions, evaluate_term,
)
from app.symbolic.schema import (
    check_stratification, compile_rule, render_rule,
)


def rule(code, body, head, **kwargs):
    return compile_rule(code, body, head, **kwargs)


LEAVE_RULE = {
    "code": "R12",
    "body": [
        {"type": "fact", "subject": "?e", "predicate": "years_of_service", "value": "?y"},
        {"type": "comparison", "left": "?y", "op": "gt", "right": {"const": 2}},
    ],
    "head": {"subject": "?e", "predicate": "annual_leave_days",
             "value": {"const": 20}, "unit": "days"},
}


# --- rule validation --------------------------------------------------------

def test_compiles_and_renders_a_rule():
    compiled = rule(**LEAVE_RULE)
    assert render_rule(compiled) == (
        "IF ?e.years_of_service = ?y AND ?y > 2 THEN ?e.annual_leave_days = 20")


@pytest.mark.parametrize("bad_head", [
    {"subject": "?e", "predicate": "x", "value": "?unbound"},   # unbound head variable
    {"subject": "?e", "predicate": "bad name!", "value": 1},     # invalid predicate
    {"subject": "?e", "predicate": "x"},                         # no value
])
def test_invalid_heads_rejected(bad_head):
    with pytest.raises(RuleError):
        rule("BAD", LEAVE_RULE["body"], bad_head)


@pytest.mark.parametrize("bad_body", [
    [],                                                                   # empty
    [{"type": "eval", "code": "__import__('os').system('rm -rf /')"}],    # not a type
    [{"type": "comparison", "left": "?x", "op": "gt", "right": 1}],       # unbound var
    [{"type": "fact", "subject": "?e", "predicate": "y", "value": {"expr":
      {"op": "system", "args": ["?e"]}}}],                                # unknown op
])
def test_invalid_bodies_rejected(bad_body):
    with pytest.raises(RuleError):
        rule("BAD", bad_body, LEAVE_RULE["head"])


def test_rules_are_data_not_code():
    """A rule cannot smuggle executable content through any field."""
    with pytest.raises(RuleError):
        rule("EVIL", [{"type": "fact", "subject": "?e", "predicate": "p",
                       "value": {"const": {"__class__": "os.system"}}}],
             LEAVE_RULE["head"])


def test_unstratified_negation_is_rejected():
    rules = [
        rule("N1", [{"type": "fact", "subject": "?e", "predicate": "a", "value": "?v"},
                    {"type": "absent", "subject": "?e", "predicate": "b"}],
             {"subject": "?e", "predicate": "b", "value": {"const": 1}}),
    ]
    with pytest.raises(RuleError):
        check_stratification(rules)


def test_stratified_negation_is_accepted():
    rules = [
        rule("N1", [{"type": "fact", "subject": "?e", "predicate": "a", "value": "?v"},
                    {"type": "absent", "subject": "?e", "predicate": "b"}],
             {"subject": "?e", "predicate": "c", "value": {"const": 1}}),
    ]
    check_stratification(rules)


# --- forward chaining -------------------------------------------------------

def test_forward_chaining_derives_a_fact():
    engine = SymbolicEngine([rule(**LEAVE_RULE)])
    result = engine.forward([make_fact("employee_1", "years_of_service", 4)])
    derived = result.value_of("employee_1", "annual_leave_days")
    assert derived is not None
    assert unwrap(derived.object) == 20.0
    assert derived.kind is FactKind.INFERRED
    assert derived.object["unit"] == "days"


def test_rule_does_not_fire_when_condition_fails():
    engine = SymbolicEngine([rule(**LEAVE_RULE)])
    result = engine.forward([make_fact("employee_1", "years_of_service", 1)])
    assert result.value_of("employee_1", "annual_leave_days") is None
    assert result.derived == []


def test_multi_step_inference_chains():
    r1 = rule(**LEAVE_RULE)
    r2 = rule("R14",
              [{"type": "fact", "subject": "?e", "predicate": "annual_leave_days",
                "value": "?d"}],
              {"subject": "?e", "predicate": "carryover_days",
               "value": {"expr": {"op": "min", "args": ["?d", {"const": 5}]}}})
    result = SymbolicEngine([r1, r2]).forward(
        [make_fact("employee_1", "years_of_service", 6)])
    assert unwrap(result.value_of("employee_1", "carryover_days").object) == 5.0
    assert [s.rule_code for s in result.steps] == ["R12", "R14"]


def test_derived_facts_carry_provenance():
    engine = SymbolicEngine([rule(**LEAVE_RULE)])
    premise = make_fact("employee_1", "years_of_service", 4)
    premise.fact_id = "fact-1"
    result = engine.forward([premise])
    derived = result.derived[0]
    assert derived.provenance.rule_codes == ["R12"]
    assert derived.provenance.premise_fact_ids == ["fact-1"]
    step = result.steps[0]
    assert step.premises == ["employee_1.years_of_service = 4"]
    assert step.conclusion.startswith("employee_1.annual_leave_days = 20")


def test_inference_terminates_on_a_self_feeding_rule():
    r = rule("LOOP",
             [{"type": "fact", "subject": "?e", "predicate": "n", "value": "?v"},
              {"type": "comparison", "left": "?v", "op": "lt", "right": {"const": 1000}}],
             {"subject": "?e", "predicate": "n",
              "value": {"expr": {"op": "add", "args": ["?v", {"const": 1}]}}})
    result = SymbolicEngine([r]).forward([make_fact("x", "n", 0)])
    assert result.iterations <= 32
    assert len(result.derived) < 2000 or result.truncated


def test_rules_are_applied_per_subject():
    engine = SymbolicEngine([rule(**LEAVE_RULE)])
    result = engine.forward([
        make_fact("employee_1", "years_of_service", 4),
        make_fact("employee_2", "years_of_service", 1),
    ])
    assert result.value_of("employee_1", "annual_leave_days") is not None
    assert result.value_of("employee_2", "annual_leave_days") is None


def test_absent_condition_behaves_as_negation_as_failure():
    r = rule("N",
             [{"type": "fact", "subject": "?e", "predicate": "a", "value": "?v"},
              {"type": "absent", "subject": "?e", "predicate": "override"}],
             {"subject": "?e", "predicate": "result", "value": {"const": 1}})
    engine = SymbolicEngine([r])
    assert engine.forward([make_fact("x", "a", 1)]).value_of("x", "result") is not None
    with_override = engine.forward([make_fact("x", "a", 1),
                                    make_fact("x", "override", True)])
    assert with_override.value_of("x", "result") is None


# --- backward chaining ------------------------------------------------------

def test_backward_proof_finds_the_derivation():
    r1 = rule(**LEAVE_RULE)
    r2 = rule("R14",
              [{"type": "fact", "subject": "?e", "predicate": "annual_leave_days",
                "value": "?d"}],
              {"subject": "?e", "predicate": "carryover_days",
               "value": {"expr": {"op": "min", "args": ["?d", {"const": 5}]}}})
    proof = SymbolicEngine([r1, r2]).prove(
        "employee_1", "carryover_days", [make_fact("employee_1", "years_of_service", 4)])
    assert proof is not None and proof.value == 5.0
    assert [s.rule_code for s in proof.steps()] == ["R12", "R14"]


def test_backward_proof_returns_none_when_unprovable():
    proof = SymbolicEngine([rule(**LEAVE_RULE)]).prove(
        "employee_1", "annual_leave_days", [make_fact("employee_1", "years_of_service", 1)])
    assert proof is None


def test_backward_proof_of_a_known_fact_cites_the_fact():
    proof = SymbolicEngine([]).prove(
        "employee_1", "years_of_service", [make_fact("employee_1", "years_of_service", 4)])
    assert proof is not None and proof.rule_code == ""
    assert "Known fact" in proof.explanation


# --- contradictions ---------------------------------------------------------

def test_detects_a_functional_conflict():
    conflicts = detect_contradictions([
        make_fact("employee_1", "salary", 50000),
        make_fact("employee_1", "salary", 60000),
    ])
    assert len(conflicts) == 1
    assert sorted(conflicts[0].values) == [50000.0, 60000.0]
    assert conflicts[0].severity == "error"
    assert not conflicts[0].is_resolved


def test_no_conflict_for_identical_values():
    assert detect_contradictions([
        make_fact("employee_1", "salary", 50000),
        make_fact("employee_1", "salary", 50000.0),
    ]) == []


def test_no_conflict_across_different_subjects():
    assert detect_contradictions([
        make_fact("employee_1", "salary", 50000),
        make_fact("employee_2", "salary", 60000),
    ]) == []


def test_effective_dates_resolve_a_version_conflict():
    conflicts = detect_contradictions([
        make_fact("policy", "annual_leave_days", 18,
                  provenance=Provenance(effective_date="2019-01-01",
                                        document_title="Handbook 2019")),
        make_fact("policy", "annual_leave_days", 20,
                  provenance=Provenance(effective_date="2026-01-01",
                                        document_title="Handbook 2026")),
    ])
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.is_resolved
    assert conflict.severity == "warning"
    assert conflict.preferred_value == 20.0
    assert conflict.superseded_values == [18.0]
    # The superseded value is still reported, not hidden.
    assert 18.0 in conflict.values


def test_model_generated_facts_never_create_a_contradiction():
    assert detect_contradictions([
        make_fact("policy", "annual_leave_days", 20),
        make_fact("policy", "annual_leave_days", 99, kind=FactKind.MODEL_GENERATED),
    ]) == []


def test_multivalued_predicates_do_not_conflict():
    assert detect_contradictions([
        make_fact("employee_1", "role", "engineer"),
        make_fact("employee_1", "role", "reviewer"),
    ]) == []


# --- integrity constraints --------------------------------------------------

def test_constraint_violation_detected():
    violations = check_constraints(
        [make_fact("employee_1", "age", 16)],
        [{"code": "C01", "predicate": "age", "op": "gte", "value": 18,
          "description": "Employees must be at least 18."}])
    assert len(violations) == 1
    assert violations[0].code == "C01"


def test_constraint_satisfied_produces_no_violation():
    assert check_constraints(
        [make_fact("employee_1", "age", 30)],
        [{"code": "C01", "predicate": "age", "op": "gte", "value": 18}]) == []


# --- primitives -------------------------------------------------------------

@pytest.mark.parametrize("op,left,right,expected", [
    ("eq", 20, 20.0, True), ("eq", "Yes", "yes", True), ("ne", 1, 2, True),
    ("lt", 1, 2, True), ("lte", 2, 2, True), ("gt", 3, 2, True), ("gte", 2, 3, False),
    ("in", "a", ["a", "b"], True), ("not_in", "c", ["a", "b"], True),
    ("contains", "annual leave", "leave", True),
    ("gt", "text", 2, False),
])
def test_compare(op, left, right, expected):
    assert compare(op, left, right) is expected


def test_arithmetic_division_by_zero_is_undefined_not_an_exception():
    from app.symbolic.schema import parse_term

    term = parse_term({"expr": {"op": "div", "args": [{"const": 1}, {"const": 0}]}})
    assert evaluate_term(term, {}) is None

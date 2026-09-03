"""Policy decision point and the access-constraint compiler."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import AuthorizationError, ConstraintCompileError
from app.db.models.rag import Document
from app.policy import constraints
from app.policy import service as policy_service
from app.policy.constraints import MissingContext
from app.policy.pdp import (
    AccessRequest, Decision, PolicyDecisionPoint, PolicyView, Resource,
)
from app.tenancy.context import Principal
from tests.conftest import auth_headers


def _principal(tenant: uuid.UUID | None = None, roles=("employee",),
               permissions=("document:read",), **attributes) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), tenant_id=tenant or uuid.uuid4(),
        email="subject@example.test", roles=frozenset(roles),
        permissions=frozenset(permissions), attributes=dict(attributes),
    )


# --- constraint language ---------------------------------------------------

def test_parses_a_valid_expression():
    node = constraints.validate(
        {"op": "and", "args": [
            {"op": "eq", "field": "tenant_id", "ref": "subject.tenant_id"},
            {"op": "in", "field": "sensitivity", "value": ["public", "internal"]},
        ]}, "document")
    assert node.op == "and"
    assert "tenant_id == subject.tenant_id" in constraints.describe(node)


@pytest.mark.parametrize("expression", [
    {"op": "eq", "field": "password_hash", "value": "x"},        # field not whitelisted
    {"op": "eq", "field": "tenant_id"},                          # no value or ref
    {"op": "eq", "field": "tenant_id", "value": "a", "ref": "subject.tenant_id"},
    {"op": "drop", "field": "tenant_id", "value": "x"},          # unknown operator
    {"op": "in", "field": "sensitivity", "value": []},           # empty membership
    {"op": "eq", "field": "tenant_id", "ref": "os.environ"},     # unknown context root
    {"op": "and", "args": []},                                   # empty conjunction
    "tenant_id = 1",                                             # not an object
    {"op": "eq", "field": "collection", "value": {"nested": 1}},  # non-scalar literal
])
def test_invalid_expressions_are_rejected(expression):
    with pytest.raises(ConstraintCompileError):
        constraints.validate(expression, "document")


def test_deeply_nested_expression_is_rejected():
    expression = {"op": "true"}
    for _ in range(12):
        expression = {"op": "not", "args": [expression]}
    with pytest.raises(ConstraintCompileError):
        constraints.validate(expression, "document")


def test_sql_injection_attempt_in_a_literal_stays_a_literal(tenant_db, alice):
    node = constraints.validate(
        {"op": "eq", "field": "collection",
         "value": "'; DROP TABLE documents; --"}, "document")
    predicate = constraints.compile_sql(node, policy_service.DOCUMENT_COLUMNS, {})
    rows = tenant_db.scalars(select(Document).where(predicate)).all()
    assert rows == []
    # The table is still there.
    assert tenant_db.scalars(select(Document)).all()


def test_like_wildcards_in_a_literal_are_escaped(tenant_db):
    node = constraints.validate(
        {"op": "contains", "field": "title", "value": "%"}, "document")
    predicate = constraints.compile_sql(node, policy_service.DOCUMENT_COLUMNS, {})
    # A literal percent matches no title, rather than matching everything.
    assert tenant_db.scalars(select(Document).where(predicate)).all() == []


def test_evaluate_in_memory():
    node = constraints.validate(
        {"op": "gte", "field": "amount", "value": 1000}, "salary_record")
    assert constraints.evaluate(node, {"amount": 2000}, {}) is True
    assert constraints.evaluate(node, {"amount": 500}, {}) is False


def test_missing_context_raises_rather_than_defaulting_to_true():
    node = constraints.validate(
        {"op": "eq", "field": "tenant_id", "ref": "subject.department"}, "document")
    with pytest.raises(MissingContext):
        constraints.evaluate(node, {"tenant_id": "x"}, {"subject": {}, "env": {}})


def test_comparison_against_null_is_not_permitted():
    node = constraints.validate(
        {"op": "gte", "field": "amount", "value": 10}, "salary_record")
    assert constraints.evaluate(node, {"amount": None}, {}) is False


# --- PDP -------------------------------------------------------------------

def test_default_deny_when_no_policy_matches():
    pdp = PolicyDecisionPoint([])
    subject = _principal()
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=subject.tenant_id)))
    assert decision.decision is Decision.DENY
    assert decision.matched_policy is None


def test_allow_when_permission_matches():
    subject = _principal(permissions=("document:read",))
    pdp = PolicyDecisionPoint([
        PolicyView(code="A1", effect="ALLOW", subject_permissions=["document:read"],
                   actions=["READ"], resource_types=["document"]),
    ])
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=subject.tenant_id)))
    assert decision.decision is Decision.ALLOW
    assert decision.matched_policy == "A1"


def test_deny_overrides_allow_regardless_of_priority():
    subject = _principal(permissions=("document:read",))
    pdp = PolicyDecisionPoint([
        PolicyView(code="ALLOW_ALL", effect="ALLOW", priority=999,
                   subject_permissions=["document:read"], actions=["READ"],
                   resource_types=["document"]),
        PolicyView(code="DENY_SECRET", effect="DENY", priority=1,
                   actions=["READ"], resource_types=["document"],
                   condition={"op": "eq", "field": "sensitivity",
                              "value": "hr_confidential"}),
    ])
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=subject.tenant_id,
                          attributes={"sensitivity": "hr_confidential"})))
    assert decision.decision is Decision.DENY
    assert decision.matched_policy == "DENY_SECRET"


def test_tenant_boundary_is_not_overridable_by_policy():
    subject = _principal()
    pdp = PolicyDecisionPoint([
        PolicyView(code="ALLOW_EVERYTHING", effect="ALLOW", priority=10_000,
                   actions=["*"], resource_types=["*"]),
    ])
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=uuid.uuid4())))
    assert decision.decision is Decision.DENY
    assert decision.matched_policy == "builtin:tenant_boundary"


def test_unevaluable_condition_is_indeterminate_not_allow():
    subject = _principal()
    pdp = PolicyDecisionPoint([
        PolicyView(code="NEEDS_CONTEXT", effect="ALLOW",
                   subject_permissions=["document:read"], actions=["READ"],
                   resource_types=["document"],
                   condition={"op": "eq", "field": "collection",
                              "ref": "subject.home_collection"}),
    ])
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=subject.tenant_id,
                          attributes={"collection": "hr"})))
    assert decision.decision is Decision.INDETERMINATE
    assert not decision.allowed


def test_malformed_stored_condition_does_not_grant_access():
    subject = _principal()
    pdp = PolicyDecisionPoint([
        PolicyView(code="BROKEN", effect="ALLOW",
                   subject_permissions=["document:read"], actions=["READ"],
                   resource_types=["document"],
                   condition={"op": "no_such_op", "field": "collection", "value": 1}),
    ])
    decision = pdp.decide(AccessRequest(
        subject=subject, action="READ",
        resource=Resource(resource_type="document", tenant_id=subject.tenant_id)))
    assert decision.decision is not Decision.ALLOW


# --- seeded policy set -----------------------------------------------------

def test_employee_denied_salary_record(tenant_db, alice):
    decision = policy_service.decide(
        tenant_db, alice, "READ",
        Resource(resource_type="salary_record", tenant_id=alice.tenant_id,
                 attributes={"tenant_id": str(alice.tenant_id)}))
    assert decision.decision is Decision.DENY


def test_hr_allowed_salary_record(tenant_db, hr_user):
    decision = policy_service.decide(
        tenant_db, hr_user, "READ",
        Resource(resource_type="salary_record", tenant_id=hr_user.tenant_id,
                 attributes={"tenant_id": str(hr_user.tenant_id)}))
    assert decision.decision is Decision.ALLOW
    assert decision.matched_policy == "P030"


def test_enforce_raises_on_denial(tenant_db, alice):
    with pytest.raises(AuthorizationError):
        policy_service.enforce(
            tenant_db, alice, "READ",
            Resource(resource_type="salary_record", tenant_id=alice.tenant_id,
                     attributes={"tenant_id": str(alice.tenant_id)}))


def test_retrieval_filter_excludes_restricted_documents(tenant_db, alice, hr_user):
    from app.db.models.rag import Chunk

    employee_rows = tenant_db.execute(
        select(Document.title).join(Chunk, Chunk.document_id == Document.id)
        .where(policy_service.retrieval_filter(tenant_db, alice)).distinct()
    ).scalars().all()
    hr_rows = tenant_db.execute(
        select(Document.title).join(Chunk, Chunk.document_id == Document.id)
        .where(policy_service.retrieval_filter(tenant_db, hr_user)).distinct()
    ).scalars().all()
    assert "Acme Salary Bands 2026 (HR confidential)" not in employee_rows
    assert "Acme Salary Bands 2026 (HR confidential)" in hr_rows


def test_constraint_that_fails_to_compile_fails_closed(tenant_db, alice, monkeypatch):
    def explode(*args, **kwargs):
        raise ConstraintCompileError("boom")

    monkeypatch.setattr(policy_service.constraints, "validate", explode)
    predicate = policy_service.compiled_filter(tenant_db, alice, "document")
    assert tenant_db.scalars(select(Document).where(predicate)).all() == []


# --- API -------------------------------------------------------------------

def test_policy_evaluate_endpoint(client):
    response = client.post(
        "/api/policies/evaluate", headers=auth_headers(client, "alice@acme.test"),
        json={"action": "READ", "resource_type": "salary_record"})
    assert response.status_code == 200
    assert response.json()["decision"] == "DENY"


def test_constraint_validation_endpoint_rejects_unknown_field(client):
    response = client.post(
        "/api/policies/constraints/validate",
        headers=auth_headers(client, "admin@acme.test"),
        json={"resource_type": "document",
              "expression": {"op": "eq", "field": "password_hash", "value": "x"}})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_constraint"


def test_policy_listing_requires_permission(client):
    assert client.get("/api/policies",
                      headers=auth_headers(client, "alice@acme.test")).status_code == 403
    assert client.get("/api/policies",
                      headers=auth_headers(client, "admin@acme.test")).status_code == 200

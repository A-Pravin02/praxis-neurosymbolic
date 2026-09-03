"""Loads policy data and turns it into decisions and query filters."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, or_, select, true
from sqlalchemy.orm import Session

from app.core import audit
from app.core.errors import AuthorizationError
from app.core.logging import get_logger, log_event
from app.db.models.knowledge import Fact
from app.db.models.policy import AccessConstraint, Policy
from app.db.models.rag import Chunk, Document
from app.policy import constraints
from app.policy.pdp import (
    AccessRequest, Decision, PolicyDecision, PolicyDecisionPoint, PolicyView, Resource,
)
from app.tenancy.context import Principal

logger = get_logger("praxis.policy")

# Column maps used when compiling a constraint into a query predicate.
DOCUMENT_COLUMNS: dict[str, Any] = {
    "tenant_id": Document.tenant_id, "collection": Document.collection,
    "sensitivity": Document.sensitivity, "required_permission": Document.required_permission,
    "uploaded_by": Document.uploaded_by, "status": Document.status,
    "media_type": Document.media_type, "effective_date": Document.effective_date,
    "version_label": Document.version_label, "title": Document.title,
}
CHUNK_COLUMNS: dict[str, Any] = {
    "tenant_id": Chunk.tenant_id, "document_id": Chunk.document_id,
    "injection_flagged": Chunk.injection_flagged, "section": Chunk.section,
}
FACT_COLUMNS: dict[str, Any] = {
    "tenant_id": Fact.tenant_id, "subject": Fact.subject, "predicate": Fact.predicate,
    "kind": Fact.kind, "sensitivity": Fact.sensitivity,
    "required_permission": Fact.required_permission, "is_active": Fact.is_active,
}
COLUMN_MAPS = {"document": DOCUMENT_COLUMNS, "chunk": CHUNK_COLUMNS, "fact": FACT_COLUMNS}


def load_policies(session: Session, tenant_id: uuid.UUID) -> list[PolicyView]:
    rows = session.scalars(
        select(Policy).where(Policy.tenant_id == tenant_id, Policy.is_active.is_(True))
    ).all()
    return [
        PolicyView(
            code=r.code, effect=r.effect, priority=r.priority, name=r.name,
            subject_roles=list(r.subject_roles or []),
            subject_permissions=list(r.subject_permissions or []),
            actions=list(r.actions or []), resource_types=list(r.resource_types or []),
            condition=r.condition_json, obligations=list(r.obligations or []),
        )
        for r in rows
    ]


def get_pdp(session: Session, principal: Principal) -> PolicyDecisionPoint:
    return PolicyDecisionPoint(load_policies(session, principal.tenant_id))


def decide(session: Session, principal: Principal, action: str, resource: Resource,
           context: dict[str, Any] | None = None) -> PolicyDecision:
    pdp = get_pdp(session, principal)
    decision = pdp.decide(AccessRequest(subject=principal, action=action,
                                        resource=resource, context=context or {}))
    if decision.decision is not Decision.ALLOW:
        audit.record(
            session, audit.POLICY_DENY, tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id, resource_type=resource.resource_type,
            resource_id=resource.resource_id, outcome="denied",
            detail={"action": action, "decision": decision.decision.value,
                    "reason": decision.reason, "policy": decision.matched_policy},
        )
    return decision


def enforce(session: Session, principal: Principal, action: str, resource: Resource,
            context: dict[str, Any] | None = None) -> PolicyDecision:
    """Decide and raise on anything that is not an explicit ALLOW."""
    decision = decide(session, principal, action, resource, context)
    if decision.decision is not Decision.ALLOW:
        raise AuthorizationError(decision.reason, extra={
            "decision": decision.decision.value,
            "policy": decision.matched_policy or "",
        })
    return decision


# ---------------------------------------------------------------------------
# Query-level filtering (the "compiled constraint" path)
# ---------------------------------------------------------------------------

def load_constraints(session: Session, principal: Principal,
                     resource_type: str) -> list[AccessConstraint]:
    rows = session.scalars(
        select(AccessConstraint).where(
            AccessConstraint.tenant_id == principal.tenant_id,
            AccessConstraint.resource_type == resource_type,
            AccessConstraint.is_active.is_(True),
        )
    ).all()
    applicable = []
    for row in rows:
        roles = set(row.applies_to_roles or [])
        if not roles or (roles & set(principal.roles)):
            applicable.append(row)
    return applicable


def compiled_filter(session: Session, principal: Principal, resource_type: str,
                    *, describe_into: list[str] | None = None) -> ColumnElement[bool]:
    """AND together every applicable access constraint for a resource type.

    The result is a parameterised SQLAlchemy predicate. A constraint that cannot
    be compiled fails closed: the whole query is reduced to "no rows" rather than
    silently dropping the restriction.
    """
    columns = COLUMN_MAPS.get(resource_type)
    if columns is None:
        return true()
    context = {
        "subject": {
            "user_id": str(principal.user_id), "tenant_id": str(principal.tenant_id),
            "email": principal.email, "roles": sorted(principal.roles),
            "permissions": sorted(principal.permissions),
            **(principal.attributes or {}),
        },
        "env": {},
    }
    predicates: list[ColumnElement[bool]] = []
    for row in load_constraints(session, principal, resource_type):
        try:
            node = constraints.validate(row.expression_json, resource_type)
            predicates.append(constraints.compile_sql(node, columns, context))
            if describe_into is not None:
                describe_into.append(f"{row.code}: {constraints.describe(node)}")
        except Exception as exc:
            log_event(logger, 40, "access constraint failed to compile; failing closed",
                      constraint=row.code, resource_type=resource_type,
                      error=type(exc).__name__)
            from sqlalchemy import false
            return false()
    if not predicates:
        return true()
    return and_(*predicates)


def permission_filter(principal: Principal, resource_type: str) -> ColumnElement[bool]:
    """Documents/facts may require a permission; enforce it in the query itself."""
    if resource_type == "document":
        column = Document.required_permission
    elif resource_type == "fact":
        column = Fact.required_permission
    else:
        return true()
    held = sorted(principal.permissions)
    if not held:
        return column.is_(None)
    return or_(column.is_(None), column.in_(held))


def retrieval_filter(session: Session, principal: Principal,
                     describe_into: list[str] | None = None) -> ColumnElement[bool]:
    """The complete authorization predicate applied to every retrieval query.

    Layered on top of (never instead of) PostgreSQL row level security.
    """
    return and_(
        Document.tenant_id == principal.tenant_id,
        Chunk.tenant_id == principal.tenant_id,
        permission_filter(principal, "document"),
        compiled_filter(session, principal, "document", describe_into=describe_into),
        compiled_filter(session, principal, "chunk", describe_into=describe_into),
    )


def fact_filter(session: Session, principal: Principal) -> ColumnElement[bool]:
    return and_(
        Fact.tenant_id == principal.tenant_id,
        Fact.is_active.is_(True),
        permission_filter(principal, "fact"),
        compiled_filter(session, principal, "fact"),
    )

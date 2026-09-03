from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.api.schemas import (
    AuditEventOut, ConstraintValidateRequest, FactCreate, FactRecordOut,
    PolicyEvaluateRequest, PolicyDecisionOut, PolicyOut, RuleCreate, RuleOut,
)
from app.core import audit
from app.core.deps import CurrentPrincipal, DbSession, require_permissions
from app.core.errors import NotFoundError
from app.db.models.knowledge import Fact
from app.db.models.policy import Policy
from app.db.models.symbolic import Rule
from app.db.models.tenancy import AuditEvent, User
from app.knowledge import store as knowledge_store
from app.knowledge.schema import FactKind, typed_value, unwrap
from app.policy import constraints as constraint_lang
from app.policy import service as policy_service
from app.policy.pdp import Resource
from app.symbolic.engine import SymbolicEngine
from app.tenancy.permissions import (
    AUDIT_READ, FACT_READ, FACT_WRITE, POLICY_READ, RULE_READ, RULE_WRITE,
)

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

@router.get("/facts", response_model=list[FactRecordOut],
            dependencies=[Depends(require_permissions(FACT_READ))])
def list_facts(db: DbSession, principal: CurrentPrincipal,
               subject: str | None = Query(None, max_length=200),
               predicate: str | None = Query(None, max_length=120),
               limit: int = Query(200, ge=1, le=1000)) -> list[FactRecordOut]:
    stmt = select(Fact).where(policy_service.fact_filter(db, principal))
    if subject:
        stmt = stmt.where(Fact.subject == subject)
    if predicate:
        stmt = stmt.where(Fact.predicate == predicate)
    rows = db.scalars(stmt.order_by(Fact.created_at.desc()).limit(limit)).all()
    return [_fact_out(r) for r in rows]


@router.post("/facts", response_model=FactRecordOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permissions(FACT_WRITE))])
def create_fact(payload: FactCreate, db: DbSession,
                principal: CurrentPrincipal) -> FactRecordOut:
    row = Fact(
        tenant_id=principal.tenant_id, subject=payload.subject,
        predicate=payload.predicate, object_value=typed_value(payload.object),
        kind=FactKind.DIRECT.value, confidence=payload.confidence,
        valid_from=payload.valid_from, valid_to=payload.valid_to,
        source_kind="user_assertion", extractor="api",
        evidence_text=f"Asserted via API by {principal.email}",
        sensitivity=payload.sensitivity, required_permission=payload.required_permission,
    )
    db.add(row)
    db.flush()
    audit.record(db, "fact.write", tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="fact",
                 resource_id=str(row.id),
                 detail={"subject": row.subject, "predicate": row.predicate})
    return _fact_out(row)


def _fact_out(row: Fact) -> FactRecordOut:
    return FactRecordOut(
        id=row.id, subject=row.subject, predicate=row.predicate,
        object=unwrap(row.object_value), kind=row.kind, confidence=row.confidence,
        source_document_id=row.source_document_id, evidence_text=row.evidence_text,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@router.get("/rules", response_model=list[RuleOut],
            dependencies=[Depends(require_permissions(RULE_READ))])
def list_rules(db: DbSession, principal: CurrentPrincipal) -> list[RuleOut]:
    rows = db.scalars(
        select(Rule).where(Rule.tenant_id == principal.tenant_id)
        .order_by(Rule.priority.desc(), Rule.code)
    ).all()
    return [_rule_out(r) for r in rows]


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permissions(RULE_WRITE))])
def create_rule(payload: RuleCreate, db: DbSession,
                principal: CurrentPrincipal) -> RuleOut:
    # save_rule compiles first: a malformed or unsafe rule never reaches the table.
    row = knowledge_store.save_rule(
        db, principal.tenant_id, code=payload.code, body=payload.body,
        head=payload.head, name=payload.name, description=payload.description,
        priority=payload.priority, confidence=payload.confidence, origin="api",
    )
    # Re-validate the whole set: a new rule must not break stratification.
    compiled, problems = knowledge_store.load_rules(db, principal.tenant_id)
    SymbolicEngine(compiled)
    audit.record(db, audit.RULE_WRITE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="rule",
                 resource_id=row.code, detail={"invalid_rules": problems})
    return _rule_out(row)


@router.delete("/rules/{code}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permissions(RULE_WRITE))])
def deactivate_rule(code: str, db: DbSession, principal: CurrentPrincipal) -> Response:
    row = db.scalar(select(Rule).where(Rule.tenant_id == principal.tenant_id,
                                       Rule.code == code))
    if row is None:
        raise NotFoundError()
    row.is_active = False
    db.flush()
    audit.record(db, audit.RULE_WRITE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="rule",
                 resource_id=code, detail={"deactivated": True})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _rule_out(row: Rule) -> RuleOut:
    return RuleOut(
        id=row.id, code=row.code, name=row.name, description=row.description,
        body=row.body_json, head=row.head_json, priority=row.priority,
        confidence=row.confidence, is_active=row.is_active, origin=row.origin,
    )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

@router.get("/policies", response_model=list[PolicyOut],
            dependencies=[Depends(require_permissions(POLICY_READ))])
def list_policies(db: DbSession, principal: CurrentPrincipal) -> list[PolicyOut]:
    rows = db.scalars(
        select(Policy).where(Policy.tenant_id == principal.tenant_id)
        .order_by(Policy.priority.desc(), Policy.code)
    ).all()
    return [
        PolicyOut(
            id=r.id, code=r.code, name=r.name, effect=r.effect, priority=r.priority,
            subject_roles=r.subject_roles or [],
            subject_permissions=r.subject_permissions or [],
            actions=r.actions or [], resource_types=r.resource_types or [],
            condition=r.condition_json, is_active=r.is_active,
        )
        for r in rows
    ]


@router.post("/policies/evaluate", response_model=PolicyDecisionOut)
def evaluate_policy(payload: PolicyEvaluateRequest, db: DbSession,
                    principal: CurrentPrincipal) -> PolicyDecisionOut:
    """Evaluate a decision for the caller, or - with user:read - for another user.

    Evaluating on behalf of someone else never grants access; it only reports what
    the PDP would decide, which is how an administrator audits a policy set.
    """
    subject = principal
    if payload.subject_user_id and payload.subject_user_id != principal.user_id:
        from app.auth.service import resolve_principal
        from app.tenancy.permissions import USER_READ

        if USER_READ not in principal.permissions:
            from app.core.errors import AuthorizationError
            raise AuthorizationError()
        user = db.get(User, payload.subject_user_id)
        if user is None or user.tenant_id != principal.tenant_id:
            raise NotFoundError()
        subject = resolve_principal(db, user)

    decision = policy_service.decide(
        db, subject, payload.action.upper(),
        Resource(resource_type=payload.resource_type,
                 tenant_id=principal.tenant_id,
                 attributes={**payload.resource_attributes,
                             "tenant_id": str(principal.tenant_id)}),
    )
    return PolicyDecisionOut(
        decision=decision.decision.value, reason=decision.reason,
        matched_policy=decision.matched_policy, obligations=decision.obligations,
    )


@router.post("/policies/constraints/validate",
             dependencies=[Depends(require_permissions(POLICY_READ))])
def validate_constraint(payload: ConstraintValidateRequest,
                        principal: CurrentPrincipal) -> dict:
    """Parse an access-constraint expression without executing it."""
    node = constraint_lang.validate(payload.expression, payload.resource_type)
    return {"valid": True, "resource_type": payload.resource_type,
            "normalized": node.to_json(), "description": constraint_lang.describe(node)}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@router.get("/audit", response_model=list[AuditEventOut],
            dependencies=[Depends(require_permissions(AUDIT_READ))])
def list_audit(db: DbSession, principal: CurrentPrincipal,
               limit: int = Query(100, ge=1, le=500),
               action: str | None = Query(None, max_length=96)) -> list[AuditEventOut]:
    stmt = select(AuditEvent).where(AuditEvent.tenant_id == principal.tenant_id)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    rows = db.scalars(stmt.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [
        AuditEventOut(
            id=r.id, created_at=r.created_at, action=r.action, outcome=r.outcome,
            actor_user_id=r.actor_user_id, resource_type=r.resource_type,
            resource_id=r.resource_id, request_id=r.request_id, detail=r.detail or {},
        )
        for r in rows
    ]

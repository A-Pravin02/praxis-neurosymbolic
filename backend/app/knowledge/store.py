"""Persistence for facts, entities and rules, and conversion to/from DTOs."""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import RuleError
from app.db.models.knowledge import Entity, Fact
from app.db.models.symbolic import ConstraintRule, Rule
from app.knowledge import resolver
from app.knowledge.schema import FactDTO, FactKind, Provenance, typed_value
from app.policy import service as policy_service
from app.symbolic.schema import CompiledRule, compile_rule
from app.tenancy.context import Principal


def fact_to_dto(row: Fact) -> FactDTO:
    derivation = row.derivation or {}
    return FactDTO(
        subject=row.subject,
        predicate=row.predicate,
        object=row.object_value,
        kind=FactKind(row.kind),
        confidence=row.confidence,
        fact_id=str(row.id),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        sensitivity=row.sensitivity,
        required_permission=row.required_permission,
        provenance=Provenance(
            source_kind=row.source_kind,
            document_id=row.source_document_id,
            chunk_id=row.source_chunk_id,
            document_title=(row.metadata_json or {}).get("document_title", ""),
            evidence_text=row.evidence_text or "",
            extractor=row.extractor or "",
            rule_codes=list(derivation.get("rule_codes", [])),
            premise_fact_ids=list(derivation.get("premise_fact_ids", [])),
            effective_date=(row.metadata_json or {}).get("effective_date"),
        ),
    )


def dto_to_row(dto: FactDTO, tenant_id: uuid.UUID) -> Fact:
    return Fact(
        id=uuid.UUID(dto.fact_id) if dto.fact_id else uuid.uuid4(),
        tenant_id=tenant_id,
        subject=dto.subject,
        predicate=dto.predicate,
        object_value=dto.object,
        kind=dto.kind.value,
        confidence=dto.confidence,
        valid_from=dto.valid_from,
        valid_to=dto.valid_to,
        source_document_id=dto.provenance.document_id,
        source_chunk_id=dto.provenance.chunk_id,
        source_kind=dto.provenance.source_kind,
        extractor=dto.provenance.extractor,
        evidence_text=dto.provenance.evidence_text,
        derivation={
            "rule_codes": dto.provenance.rule_codes,
            "premise_fact_ids": dto.provenance.premise_fact_ids,
        },
        sensitivity=dto.sensitivity,
        required_permission=dto.required_permission,
        metadata_json={
            "document_title": dto.provenance.document_title,
            "effective_date": dto.provenance.effective_date,
        },
    )


def save_facts(session: Session, tenant_id: uuid.UUID, facts: Iterable[FactDTO],
               *, replace_from_document: uuid.UUID | None = None) -> list[Fact]:
    """Persist DIRECT/INFERRED facts. MODEL_GENERATED facts are never stored."""
    if replace_from_document is not None:
        for stale in session.scalars(
            select(Fact).where(Fact.tenant_id == tenant_id,
                               Fact.source_document_id == replace_from_document)
        ).all():
            session.delete(stale)
        session.flush()

    rows: list[Fact] = []
    seen: set[tuple[str, str, str]] = set()
    for dto in facts:
        if dto.kind is FactKind.MODEL_GENERATED:
            continue
        signature = (dto.subject, dto.predicate, repr(dto.object.get("value")))
        if signature in seen:
            continue
        seen.add(signature)
        row = dto_to_row(dto, tenant_id)
        session.add(row)
        rows.append(row)
        dto.fact_id = str(row.id)
    session.flush()
    if rows or replace_from_document is not None:
        resolver.invalidate(tenant_id)
    return rows


def load_facts(session: Session, principal: Principal, *, subjects: list[str] | None = None,
               predicates: list[str] | None = None, limit: int = 2000) -> list[FactDTO]:
    """Load facts the principal is permitted to see (policy + RLS)."""
    stmt = select(Fact).where(policy_service.fact_filter(session, principal))
    if subjects:
        stmt = stmt.where(Fact.subject.in_(subjects))
    if predicates:
        stmt = stmt.where(Fact.predicate.in_(predicates))
    stmt = stmt.order_by(Fact.created_at).limit(limit)
    return [fact_to_dto(row) for row in session.scalars(stmt).all()]


def upsert_entity(session: Session, tenant_id: uuid.UUID, key: str, *, label: str = "",
                  entity_type: str = "thing", attributes: dict | None = None) -> Entity:
    row = session.scalar(
        select(Entity).where(Entity.tenant_id == tenant_id, Entity.key == key)
    )
    if row is None:
        row = Entity(tenant_id=tenant_id, key=key, label=label or key,
                     entity_type=entity_type, attributes=attributes or {})
        session.add(row)
        session.flush()
        return row
    if label:
        row.label = label
    if entity_type and entity_type != "thing":
        row.entity_type = entity_type
    if attributes:
        merged = dict(row.attributes or {})
        merged.update(attributes)
        row.attributes = merged
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def rule_to_compiled(row: Rule) -> CompiledRule:
    return compile_rule(
        row.code, row.body_json, row.head_json, name=row.name,
        description=row.description, priority=row.priority, confidence=row.confidence,
        origin=row.origin,
    )


def load_rules(session: Session, tenant_id: uuid.UUID) -> tuple[list[CompiledRule], list[str]]:
    """Compile every active rule. A rule that fails to compile is skipped and
    reported rather than silently dropped or crashing the request."""
    rows = session.scalars(
        select(Rule).where(Rule.tenant_id == tenant_id, Rule.is_active.is_(True))
        .order_by(Rule.priority.desc(), Rule.code)
    ).all()
    compiled: list[CompiledRule] = []
    problems: list[str] = []
    for row in rows:
        try:
            compiled.append(rule_to_compiled(row))
        except RuleError as exc:
            problems.append(f"{row.code}: {exc.detail}")
    return compiled, problems


def save_rule(session: Session, tenant_id: uuid.UUID, *, code: str, body: list,
              head: dict, name: str = "", description: str = "", priority: int = 100,
              confidence: float = 1.0, origin: str = "curated",
              source_document_id: uuid.UUID | None = None,
              source_chunk_id: uuid.UUID | None = None) -> Rule:
    # Compile first: an invalid rule never reaches the database.
    compile_rule(code, body, head, name=name, description=description,
                 priority=priority, confidence=confidence, origin=origin)
    row = session.scalar(select(Rule).where(Rule.tenant_id == tenant_id, Rule.code == code))
    if row is None:
        row = Rule(tenant_id=tenant_id, code=code)
        session.add(row)
    row.name = name or code
    row.description = description
    row.body_json = body
    row.head_json = head
    row.priority = priority
    row.confidence = confidence
    row.origin = origin
    row.is_active = True
    row.source_document_id = source_document_id
    row.source_chunk_id = source_chunk_id
    session.flush()
    return row


def load_constraint_rules(session: Session, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(ConstraintRule).where(ConstraintRule.tenant_id == tenant_id,
                                     ConstraintRule.is_active.is_(True))
    ).all()
    return [
        {"code": r.code, "description": r.description, "severity": r.severity,
         **(r.expression_json or {})}
        for r in rows
    ]


def subject_facts_for_user(user_id: uuid.UUID, attributes: dict[str, Any],
                           email: str = "") -> list[FactDTO]:
    """Turn a user's stored attributes into facts about the subject 'self'.

    These are DIRECT facts sourced from the user record, which is why an
    eligibility question can be answered about the caller without the caller
    supplying (and therefore being able to forge) the values.
    """
    facts: list[FactDTO] = []
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        facts.append(FactDTO(
            subject="self", predicate=str(key), object=typed_value(value),
            kind=FactKind.DIRECT, confidence=1.0,
            provenance=Provenance(source_kind="user_attribute",
                                  evidence_text=f"user profile: {key}={value}",
                                  extractor="user_attributes"),
            fact_id=f"user:{user_id}:{key}",
        ))
    return facts

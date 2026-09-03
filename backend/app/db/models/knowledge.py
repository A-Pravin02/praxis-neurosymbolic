from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Entity(Base, UUIDPkMixin, TimestampMixin):
    """A named thing facts can be asserted about."""
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_entities_tenant_key"),
        Index("ix_entities_tenant_type", "tenant_id", "entity_type"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), default="thing", nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Fact(Base, UUIDPkMixin, TimestampMixin):
    """A subject-predicate-object triple with provenance and an explicit kind.

    kind is one of DIRECT (asserted by a source), INFERRED (derived by the
    symbolic engine) or MODEL_GENERATED (produced by an LLM, never citable as a
    dataset fact). These are never silently mixed.
    """
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_tenant_subject", "tenant_id", "subject"),
        Index("ix_facts_tenant_predicate", "tenant_id", "predicate"),
        Index("ix_facts_tenant_kind", "tenant_id", "kind"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    predicate: Mapped[str] = mapped_column(String(120), nullable=False)
    object_value: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {"type":..,"value":..}
    kind: Mapped[str] = mapped_column(String(24), default="DIRECT", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    valid_from: Mapped[str | None] = mapped_column(String(32))
    valid_to: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Provenance
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(String(32), default="document", nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    derivation: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Visibility: facts derived from a restricted document inherit its requirement.
    required_permission: Mapped[str | None] = mapped_column(String(96))
    sensitivity: Mapped[str] = mapped_column(String(48), default="internal", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Claim(Base, UUIDPkMixin, TimestampMixin):
    """An assertion the system considered presenting to a user."""
    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_tenant_answer", "tenant_id", "answer_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_kind: Mapped[str] = mapped_column(String(24), default="DIRECT", nullable=False)
    support_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unverified", nullable=False)
    supporting_fact_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    supporting_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Answer(Base, UUIDPkMixin, TimestampMixin):
    """The auditable record of one assistant turn."""
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_tenant_message", "tenant_id", "message_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    route: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gate_decision: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pipeline_mode: Mapped[str] = mapped_column(String(32), default="neurosymbolic",
                                               nullable=False)
    latency_ms: Mapped[int] = mapped_column(default=0, nullable=False)
    trace_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ProvenanceRecord(Base, UUIDPkMixin, TimestampMixin):
    """Answer → claims → facts → rules → evidence → source, stored for replay."""
    __tablename__ = "provenance_records"
    __table_args__ = (Index("ix_provenance_tenant_answer", "tenant_id", "answer_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    answer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    graph_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

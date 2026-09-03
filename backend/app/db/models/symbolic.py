from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Rule(Base, UUIDPkMixin, TimestampMixin):
    """A stored Horn-style rule.

    body_json and head_json are validated JSON structures - never code and
    never SQL. See app/symbolic/schema.py for the grammar.
    """
    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_rules_tenant_code"),
        Index("ix_rules_tenant_active", "tenant_id", "is_active"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    head_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(String(32), default="curated", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ConstraintRule(Base, UUIDPkMixin, TimestampMixin):
    """A domain integrity constraint evaluated over facts."""
    __tablename__ = "constraint_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_constraint_rules_tenant_code"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expression_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(24), default="error", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

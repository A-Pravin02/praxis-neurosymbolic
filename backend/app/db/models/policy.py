from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Policy(Base, UUIDPkMixin, TimestampMixin):
    """A PDP rule: subjects x actions x resource types -> effect."""
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_policies_tenant_code"),
        Index("ix_policies_tenant_active", "tenant_id", "is_active"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    subject_roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    subject_permissions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    actions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    resource_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    condition_json: Mapped[dict | None] = mapped_column(JSONB)
    obligations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AccessConstraint(Base, UUIDPkMixin, TimestampMixin):
    """A reusable row-level constraint attached to roles for a resource type."""
    __tablename__ = "access_constraints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_access_constraints_tenant_code"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    applies_to_roles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expression_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

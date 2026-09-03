from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Tenant(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_email_lookup", "email"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Subject attributes usable by the PDP and the symbolic engine (e.g. years_of_service).
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="users")
    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles", back_populates="users", lazy="selectin"
    )


class Role(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(400), default="", nullable=False)

    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(
        secondary="role_permissions", back_populates="roles", lazy="selectin"
    )


class Permission(Base, UUIDPkMixin, TimestampMixin):
    """Global catalogue of permission verbs (not tenant scoped)."""
    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(400), default="", nullable=False)

    roles: Mapped[list[Role]] = relationship(
        secondary="role_permissions", back_populates="permissions"
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class RefreshToken(Base, UUIDPkMixin, TimestampMixin):
    """Hashed refresh tokens with family-based rotation and replay detection."""
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    client_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    @property
    def is_active(self) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        return (
            self.revoked_at is None
            and self.consumed_at is None
            and self.expires_at > now
        )


class AuditEvent(Base, UUIDPkMixin):
    """Append-only audit trail for security-sensitive actions."""
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), nullable=False
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    resource_id: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    client_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class UserLookup(Base, TimestampMixin):
    """Global email -> (tenant, user) routing table used ONLY by login.

    RLS cannot be evaluated before we know the tenant, and we refuse to grant the
    application a row-security bypass. Instead this table holds the minimum needed
    to route a login attempt: a lowercased email and the ids it belongs to. It
    contains no credentials and no profile data. See ADR-018.
    """
    __tablename__ = "user_lookup"

    email_ci: Mapped[str] = mapped_column(String(320), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class TenantLookup(Base, TimestampMixin):
    """Global slug -> tenant routing table, used before a tenant context exists.

    Same rationale as UserLookup (ADR-018): registration and workspace joining
    happen before row level security has a tenant to filter on, and granting the
    application a row-security bypass to solve that would be a permanent hole.
    This table holds a slug, an id and an active flag - no tenant data.
    """
    __tablename__ = "tenant_lookup"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

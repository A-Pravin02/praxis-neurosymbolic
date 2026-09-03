"""Authentication service: registration, login, refresh rotation, logout."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import passwords, tokens
from app.core import audit
from app.core.errors import ConflictError, InvalidCredentialsError, TokenError, ValidationError
from app.core.logging import get_logger
from app.db.models.tenancy import Permission, RefreshToken, Role, Tenant, User, UserLookup, UserRole
from app.db.session import SessionLocal, bind_tenant
from app.tenancy.context import Principal
from app.tenancy.permissions import ALL_PERMISSIONS, DEFAULT_ROLE, ROLE_TEMPLATES

logger = get_logger("praxis.auth")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}$")
MAX_FAILED_LOGINS = 8
LOCKOUT_SECONDS = 900


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:60] or "workspace"


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"


@dataclass
class AuthResult:
    principal: Principal
    tokens: TokenBundle
    tenant_slug: str
    display_name: str


# ---------------------------------------------------------------------------
# Tenant / role provisioning
# ---------------------------------------------------------------------------

def ensure_permission_catalogue(session: Session) -> dict[str, Permission]:
    """Read the global permission catalogue.

    The application role has no write access to this table (see migration 0001);
    it is provisioned by app.db.provision using the owner connection. Reading it
    here keeps role provisioning honest: a permission that was never declared
    cannot be granted.
    """
    return {p.name: p for p in session.scalars(select(Permission)).all()}


def provision_tenant_roles(session: Session, tenant_id: uuid.UUID) -> dict[str, Role]:
    """Create the standard role set for a tenant. Idempotent."""
    catalogue = ensure_permission_catalogue(session)
    existing = {
        r.name: r
        for r in session.scalars(select(Role).where(Role.tenant_id == tenant_id)).all()
    }
    for role_name, perms in ROLE_TEMPLATES.items():
        role = existing.get(role_name)
        if role is None:
            role = Role(tenant_id=tenant_id, name=role_name, description=f"{role_name} role")
            session.add(role)
            session.flush()
            existing[role_name] = role
        have = {p.name for p in role.permissions}
        for perm_name in perms:
            if perm_name not in have and perm_name in catalogue:
                role.permissions.append(catalogue[perm_name])
    session.flush()
    return existing


def resolve_principal(session: Session, user: User) -> Principal:
    roles = {r.name for r in user.roles}
    perms: set[str] = set()
    for r in user.roles:
        perms.update(p.name for p in r.permissions)
    return Principal(
        user_id=user.id, tenant_id=user.tenant_id, email=user.email,
        roles=frozenset(roles), permissions=frozenset(perms),
        attributes=dict(user.attributes or {}),
    )


def _lookup_tenant_unscoped(session: Session, slug: str) -> dict | None:
    """Exact-slug tenant lookup used before a tenant context exists.

    Only id/slug/is_active are returned and only an exact match is possible, so
    this cannot be used to enumerate or read tenant data.
    """
    row = session.execute(
        text("SELECT id, slug, is_active FROM tenants WHERE slug = :slug"),
        {"slug": slug},
    ).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(
    *, email: str, password: str, display_name: str = "",
    tenant_slug: str | None = None, tenant_name: str | None = None,
    client_ip: str = "",
) -> AuthResult:
    email_ci = normalize_email(email)
    if not email_ci or "@" not in email_ci or len(email_ci) > 320:
        raise ValidationError("A valid email address is required.")
    passwords.validate_password_strength(password)

    session = SessionLocal()
    try:
        bind_tenant(session, None)
        # Email uniqueness is global (see ADR-018).
        if session.get(UserLookup, email_ci) is not None:
            raise ConflictError("An account with that email already exists.")

        if tenant_slug:
            slug = tenant_slug.strip().lower()
            if not _SLUG_RE.match(slug):
                raise ValidationError("Invalid workspace identifier.")
            tenant = _lookup_tenant_unscoped(session, slug)
            if tenant is None or not tenant["is_active"]:
                # Do not reveal whether the workspace exists.
                raise ValidationError("Registration is not available for that workspace.")
            tenant_id = tenant["id"]
            bind_tenant(session, tenant_id)
            role_names = [DEFAULT_ROLE]
            created_tenant = False
        else:
            name = (tenant_name or display_name or email_ci.split("@")[0]).strip()
            slug = slugify(tenant_name or name)
            if _lookup_tenant_unscoped(session, slug) is not None:
                slug = f"{slug}-{uuid.uuid4().hex[:6]}"
            tenant_id = uuid.uuid4()
            bind_tenant(session, tenant_id)
            session.add(Tenant(id=tenant_id, slug=slug, name=name or slug))
            session.flush()
            role_names = ["admin"]
            created_tenant = True

        provision_tenant_roles(session, tenant_id)

        user = User(
            id=uuid.uuid4(), tenant_id=tenant_id, email=email_ci,
            display_name=(display_name or email_ci.split("@")[0])[:200],
            password_hash=passwords.hash_password(password),
            attributes={},
        )
        session.add(user)
        session.flush()

        roles = session.scalars(
            select(Role).where(Role.tenant_id == tenant_id, Role.name.in_(role_names))
        ).all()
        for role in roles:
            session.add(UserRole(user_id=user.id, role_id=role.id, tenant_id=tenant_id))
        session.add(UserLookup(email_ci=email_ci, tenant_id=tenant_id, user_id=user.id))
        session.flush()
        session.refresh(user)

        principal = resolve_principal(session, user)
        bundle = _issue_tokens(session, user, principal, client_ip=client_ip, user_agent="")
        audit.record(session, audit.REGISTER, tenant_id=tenant_id, actor_user_id=user.id,
                     resource_type="user", resource_id=str(user.id), client_ip=client_ip,
                     detail={"created_tenant": created_tenant})
        session.commit()
        return AuthResult(principal=principal, tokens=bundle, tenant_slug=slug,
                          display_name=user.display_name)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("An account with that email already exists.") from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(*, email: str, password: str, client_ip: str = "",
          user_agent: str = "") -> AuthResult:
    email_ci = normalize_email(email)
    session = SessionLocal()
    try:
        bind_tenant(session, None)
        lookup = session.get(UserLookup, email_ci)
        if lookup is None:
            passwords.dummy_verify()
            audit.record(session, audit.LOGIN_FAILURE, outcome="failure",
                         client_ip=client_ip, detail={"reason": "unknown_subject"})
            session.commit()
            raise InvalidCredentialsError()

        bind_tenant(session, lookup.tenant_id, lookup.user_id)
        user = session.get(User, lookup.user_id)
        if user is None or not user.is_active:
            passwords.dummy_verify()
            audit.record(session, audit.LOGIN_FAILURE, tenant_id=lookup.tenant_id,
                         outcome="failure", client_ip=client_ip,
                         detail={"reason": "inactive"})
            session.commit()
            raise InvalidCredentialsError()

        if user.locked_until and user.locked_until > _now():
            passwords.dummy_verify()
            audit.record(session, audit.LOGIN_FAILURE, tenant_id=user.tenant_id,
                         actor_user_id=user.id, outcome="failure", client_ip=client_ip,
                         detail={"reason": "locked"})
            session.commit()
            # Same message: a locked account must not be distinguishable.
            raise InvalidCredentialsError()

        if not passwords.verify_password(password, user.password_hash):
            user.failed_login_count += 1
            if user.failed_login_count >= MAX_FAILED_LOGINS:
                user.locked_until = _now() + dt.timedelta(seconds=LOCKOUT_SECONDS)
                user.failed_login_count = 0
            audit.record(session, audit.LOGIN_FAILURE, tenant_id=user.tenant_id,
                         actor_user_id=user.id, outcome="failure", client_ip=client_ip,
                         detail={"reason": "bad_password"})
            session.commit()
            raise InvalidCredentialsError()

        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = _now()
        session.flush()

        principal = resolve_principal(session, user)
        bundle = _issue_tokens(session, user, principal, client_ip=client_ip,
                               user_agent=user_agent)
        tenant = session.get(Tenant, user.tenant_id)
        audit.record(session, audit.LOGIN_SUCCESS, tenant_id=user.tenant_id,
                     actor_user_id=user.id, client_ip=client_ip)
        session.commit()
        return AuthResult(principal=principal, tokens=bundle,
                          tenant_slug=tenant.slug if tenant else "",
                          display_name=user.display_name)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _issue_tokens(session: Session, user: User, principal: Principal, *,
                  client_ip: str, user_agent: str,
                  family_id: uuid.UUID | None = None) -> TokenBundle:
    access, ttl = tokens.create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, email=user.email,
        roles=sorted(principal.roles), permissions=sorted(principal.permissions),
    )
    material = tokens.issue_refresh_token(user.tenant_id, family_id=family_id)
    session.add(RefreshToken(
        user_id=user.id, tenant_id=user.tenant_id, family_id=material.family_id,
        token_hash=material.token_hash, expires_at=material.expires_at,
        user_agent=(user_agent or "")[:300], client_ip=(client_ip or "")[:64],
    ))
    session.flush()
    return TokenBundle(access_token=access, refresh_token=material.token, expires_in=ttl)


# ---------------------------------------------------------------------------
# Refresh with rotation + replay detection
# ---------------------------------------------------------------------------

def refresh(*, refresh_token: str, client_ip: str = "",
            user_agent: str = "") -> AuthResult:
    tenant_hint, token_hash = tokens.split_refresh_token(refresh_token)
    session = SessionLocal()
    try:
        bind_tenant(session, tenant_hint)
        row = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if row is None or row.tenant_id != tenant_hint:
            audit.record(session, audit.LOGIN_FAILURE, tenant_id=tenant_hint,
                         outcome="failure", client_ip=client_ip,
                         detail={"reason": "refresh_not_found"})
            session.commit()
            raise TokenError()

        if row.revoked_at is not None or row.consumed_at is not None:
            # Replay of an already-rotated token: burn the whole family.
            revoke_family(session, row.tenant_id, row.family_id, reason="replay_detected")
            audit.record(session, audit.REFRESH_REPLAY, tenant_id=row.tenant_id,
                         actor_user_id=row.user_id, outcome="failure",
                         client_ip=client_ip, detail={"family_id": str(row.family_id)})
            session.commit()
            raise TokenError()

        if row.expires_at <= _now():
            row.revoked_at = _now()
            row.revoked_reason = "expired"
            session.commit()
            raise TokenError()

        user = session.get(User, row.user_id)
        if user is None or not user.is_active:
            revoke_family(session, row.tenant_id, row.family_id, reason="user_inactive")
            session.commit()
            raise TokenError()

        row.consumed_at = _now()
        session.flush()

        principal = resolve_principal(session, user)
        bundle = _issue_tokens(session, user, principal, client_ip=client_ip,
                               user_agent=user_agent, family_id=row.family_id)
        tenant = session.get(Tenant, user.tenant_id)
        audit.record(session, audit.REFRESH_ROTATED, tenant_id=user.tenant_id,
                     actor_user_id=user.id, client_ip=client_ip,
                     detail={"family_id": str(row.family_id)})
        session.commit()
        return AuthResult(principal=principal, tokens=bundle,
                          tenant_slug=tenant.slug if tenant else "",
                          display_name=user.display_name)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def revoke_family(session: Session, tenant_id: uuid.UUID, family_id: uuid.UUID,
                  reason: str) -> int:
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).all()
    for r in rows:
        r.revoked_at = _now()
        r.revoked_reason = reason[:64]
    session.flush()
    return len(rows)


def logout(*, refresh_token: str, principal: Principal | None = None,
           client_ip: str = "") -> None:
    """Revoke the presented token's family. Idempotent; never leaks validity."""
    try:
        tenant_hint, token_hash = tokens.split_refresh_token(refresh_token)
    except TokenError:
        return
    session = SessionLocal()
    try:
        bind_tenant(session, tenant_hint)
        row = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        if row is not None:
            if principal is not None and row.user_id != principal.user_id:
                # Presented token belongs to someone else: revoke nothing.
                session.commit()
                return
            revoke_family(session, row.tenant_id, row.family_id, reason="logout")
            audit.record(session, audit.LOGOUT, tenant_id=row.tenant_id,
                         actor_user_id=row.user_id, client_ip=client_ip)
        session.commit()
    finally:
        session.close()


def revoke_all_sessions(session: Session, user: User, reason: str = "revoke_all") -> int:
    rows = session.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).all()
    for r in rows:
        r.revoked_at = _now()
        r.revoked_reason = reason[:64]
    session.flush()
    return len(rows)


def list_sessions(session: Session, user_id: uuid.UUID) -> list[RefreshToken]:
    return list(session.scalars(
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.created_at.desc())
        .limit(50)
    ).all())

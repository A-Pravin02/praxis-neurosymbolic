"""FastAPI dependencies: DB session, authenticated principal, permission guards.

The tenant is derived exclusively from the verified access token. A client-supplied
tenant id is never trusted; if one is present in a header it is only compared, never
used as the source of truth.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.auth import tokens
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.logging import tenant_id_var, user_id_var
from app.db.models.tenancy import User
from app.db.session import SessionLocal, bind_tenant
from app.tenancy.context import Principal


def client_ip(request: Request) -> str:
    # Trust the socket peer. X-Forwarded-For is only honoured when the deployment
    # explicitly places a trusted proxy in front (see docs/DEPLOYMENT.md).
    return request.client.host if request.client else ""


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError()
    return token.strip()


def get_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    claims = tokens.decode_access_token(_bearer(authorization))
    principal = Principal(
        user_id=claims.sub,
        tenant_id=claims.tenant_id,
        email=claims.email,
        roles=frozenset(claims.roles),
        permissions=frozenset(claims.permissions),
    )
    user_id_var.set(str(principal.user_id))
    tenant_id_var.set(str(principal.tenant_id))
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_db(principal: CurrentPrincipal) -> Iterator[Session]:
    """A session already bound to the caller's tenant for row level security."""
    session = SessionLocal()
    try:
        bind_tenant(session, principal.tenant_id, principal.user_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(principal: CurrentPrincipal, db: DbSession) -> User:
    user = db.get(User, principal.user_id)
    if user is None or not user.is_active or user.tenant_id != principal.tenant_id:
        raise AuthenticationError()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permissions(*required: str):
    """Coarse route guard. Fine-grained decisions still go through the PDP."""

    def _guard(principal: CurrentPrincipal) -> Principal:
        missing = [p for p in required if p not in principal.permissions]
        if missing:
            raise AuthorizationError()
        return principal

    return _guard


def require_any_permission(*any_of: str):
    def _guard(principal: CurrentPrincipal) -> Principal:
        if not any(p in principal.permissions for p in any_of):
            raise AuthorizationError()
        return principal

    return _guard

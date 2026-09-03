from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.schemas import (
    AuthOut, LoginRequest, PrincipalOut, RefreshRequest, RegisterRequest, SessionOut, TokenOut,
)
from app.auth import service as auth_service
from app.config import settings
from app.core import ratelimit
from app.core.deps import CurrentPrincipal, CurrentUser, DbSession, client_ip
from app.db.models.tenancy import Tenant

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_auth_out(result: auth_service.AuthResult) -> AuthOut:
    p = result.principal
    return AuthOut(
        user=PrincipalOut(
            user_id=p.user_id, tenant_id=p.tenant_id, tenant_slug=result.tenant_slug,
            email=p.email, display_name=result.display_name,
            roles=sorted(p.roles), permissions=sorted(p.permissions),
        ),
        tokens=TokenOut(
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            expires_in=result.tokens.expires_in,
        ),
    )


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request) -> AuthOut:
    ip = client_ip(request)
    ratelimit.check("register", ip, settings.rate_limit_register_per_minute)
    result = auth_service.register(
        email=str(payload.email), password=payload.password,
        display_name=payload.display_name, tenant_slug=payload.tenant_slug,
        tenant_name=payload.tenant_name, client_ip=ip,
    )
    return _to_auth_out(result)


@router.post("/login", response_model=AuthOut)
def login(payload: LoginRequest, request: Request) -> AuthOut:
    ip = client_ip(request)
    ratelimit.check("login_ip", ip, settings.rate_limit_login_per_minute)
    # A second bucket keyed on the subject slows credential stuffing that rotates IPs.
    ratelimit.check("login_email", str(payload.email).lower(),
                    settings.rate_limit_login_per_minute)
    result = auth_service.login(
        email=str(payload.email), password=payload.password, client_ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    return _to_auth_out(result)


@router.post("/refresh", response_model=AuthOut)
def refresh(payload: RefreshRequest, request: Request) -> AuthOut:
    ip = client_ip(request)
    ratelimit.check("refresh", ip, settings.rate_limit_default_per_minute)
    result = auth_service.refresh(
        refresh_token=payload.refresh_token, client_ip=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    return _to_auth_out(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, request: Request,
           principal: CurrentPrincipal) -> Response:
    auth_service.logout(refresh_token=payload.refresh_token, principal=principal,
                        client_ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=PrincipalOut)
def me(principal: CurrentPrincipal, db: DbSession, user: CurrentUser) -> PrincipalOut:
    tenant = db.get(Tenant, principal.tenant_id)
    resolved = auth_service.resolve_principal(db, user)
    return PrincipalOut(
        user_id=user.id, tenant_id=user.tenant_id,
        tenant_slug=tenant.slug if tenant else "", email=user.email,
        display_name=user.display_name, roles=sorted(resolved.roles),
        permissions=sorted(resolved.permissions),
    )


@router.get("/sessions", response_model=list[SessionOut])
def sessions(db: DbSession, user: CurrentUser) -> list[SessionOut]:
    return [
        SessionOut(
            id=s.id, created_at=s.created_at, expires_at=s.expires_at,
            user_agent=s.user_agent, client_ip=s.client_ip, active=s.is_active,
            revoked_reason=s.revoked_reason,
        )
        for s in auth_service.list_sessions(db, user.id)
    ]


@router.post("/sessions/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
def revoke_all(db: DbSession, user: CurrentUser) -> Response:
    auth_service.revoke_all_sessions(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

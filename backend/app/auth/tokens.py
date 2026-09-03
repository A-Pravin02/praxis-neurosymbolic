"""Access (JWT) and refresh (opaque, hashed, rotating) token handling."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field

import jwt

from app.config import settings
from app.core.errors import TokenError

ACCESS_TYP = "access"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class AccessClaims:
    sub: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    jti: str = ""
    exp: int = 0


def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str,
    roles: list[str], permissions: list[str], ttl_seconds: int | None = None,
) -> tuple[str, int]:
    ttl = ttl_seconds if ttl_seconds is not None else settings.access_token_ttl_seconds
    now = _now()
    payload = {
        "typ": ACCESS_TYP,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "email": email,
        "roles": sorted(roles),
        "perms": sorted(permissions),
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl)).timestamp()),
        "iss": settings.app_name,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, ttl


def decode_access_token(token: str) -> AccessClaims:
    secrets_to_try = [settings.jwt_secret]
    if settings.jwt_secret_previous:
        secrets_to_try.append(settings.jwt_secret_previous)

    last_error: Exception | None = None
    for key in secrets_to_try:
        try:
            payload = jwt.decode(
                token, key, algorithms=[settings.jwt_algorithm],
                issuer=settings.app_name,
                options={"require": ["exp", "iat", "sub", "typ"]},
            )
            break
        except jwt.PyJWTError as exc:  # try the next key
            last_error = exc
    else:
        raise TokenError() from last_error

    # A refresh or any other token type must never authenticate a request.
    if payload.get("typ") != ACCESS_TYP:
        raise TokenError()
    try:
        return AccessClaims(
            sub=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            email=payload.get("email", ""),
            roles=tuple(payload.get("roles", ())),
            permissions=tuple(payload.get("perms", ())),
            jti=payload.get("jti", ""),
            exp=int(payload.get("exp", 0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError() from exc


# --------------------------------------------------------------------------
# Refresh tokens
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RefreshMaterial:
    token: str          # given to the client: "<tenant_hex>.<secret>"
    token_hash: str     # stored
    family_id: uuid.UUID
    expires_at: dt.datetime


def hash_refresh_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_refresh_token(tenant_id: uuid.UUID, family_id: uuid.UUID | None = None,
                        ttl_seconds: int | None = None) -> RefreshMaterial:
    secret = secrets.token_urlsafe(48)
    ttl = ttl_seconds if ttl_seconds is not None else settings.refresh_token_ttl_seconds
    return RefreshMaterial(
        token=f"{tenant_id.hex}.{secret}",
        token_hash=hash_refresh_secret(secret),
        family_id=family_id or uuid.uuid4(),
        expires_at=_now() + dt.timedelta(seconds=ttl),
    )


def split_refresh_token(token: str) -> tuple[uuid.UUID, str]:
    """Parse the routing hint. The prefix is UNTRUSTED: it only selects an RLS
    tenant for the lookup. Authorisation comes from the stored row."""
    if not token or "." not in token:
        raise TokenError()
    prefix, _, secret = token.partition(".")
    if not secret:
        raise TokenError()
    try:
        tenant_id = uuid.UUID(hex=prefix)
    except (ValueError, AttributeError) as exc:
        raise TokenError() from exc
    return tenant_id, hash_refresh_secret(secret)

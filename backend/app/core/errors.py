"""Typed application errors mapped to safe HTTP responses.

Rule: error bodies never disclose whether a resource exists, whether an email is
registered, or any internal detail. The `code` is stable for clients; `detail` is
a short, non-revealing sentence.
"""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    detail = "The request could not be processed."

    def __init__(self, detail: str | None = None, *, code: str | None = None,
                 extra: dict | None = None) -> None:
        self.detail = detail or self.detail
        self.code = code or self.code
        self.extra = extra or {}
        super().__init__(self.detail)


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    detail = "The request payload is invalid."


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    detail = "Authentication is required."


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    # Deliberately identical for unknown-email and wrong-password.
    detail = "Invalid email or password."


class TokenError(AuthenticationError):
    code = "invalid_token"
    detail = "The credential is invalid or has expired."


class AuthorizationError(AppError):
    status_code = 403
    code = "forbidden"
    detail = "You do not have permission to perform this action."


class NotFoundError(AppError):
    # 404 is returned for both "absent" and "not yours" so cross-tenant probing
    # cannot distinguish the two (IDOR hardening).
    status_code = 404
    code = "not_found"
    detail = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    detail = "The request conflicts with the current state."


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    detail = "Too many requests. Please slow down."


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"
    detail = "The uploaded content is too large."


class UnsupportedMediaError(AppError):
    status_code = 415
    code = "unsupported_media_type"
    detail = "That file type is not supported."


class ProviderError(AppError):
    status_code = 502
    code = "provider_error"
    detail = "An upstream provider failed."


class ConstraintCompileError(AppError):
    status_code = 400
    code = "invalid_constraint"
    detail = "The access constraint expression is invalid."


class RuleError(AppError):
    status_code = 400
    code = "invalid_rule"
    detail = "The rule definition is invalid."

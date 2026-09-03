"""Password hashing (Argon2id) and policy checks."""
from __future__ import annotations

import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.config import settings
from app.core.errors import ValidationError

_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A real hash of a random value. Verified against when the account does not exist,
# so that "unknown email" and "wrong password" take comparable time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))

_COMMON = {
    "password", "password1", "12345678", "qwertyuiop", "letmein123",
    "changeme123", "adminadmin", "welcome123", "iloveyou1",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify() -> None:
    """Burn comparable CPU when the subject does not exist."""
    try:
        _hasher.verify(_DUMMY_HASH, "not-the-password")
    except Exception:
        pass


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password_strength(password: str) -> None:
    if len(password) < settings.password_min_length:
        raise ValidationError(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if len(password) > 256:
        raise ValidationError("Password must be at most 256 characters.")
    if password.lower() in _COMMON:
        raise ValidationError("That password is too common. Choose another.")
    classes = sum(
        bool(p.search(password))
        for p in (re.compile(r"[a-z]"), re.compile(r"[A-Z]"),
                  re.compile(r"\d"), re.compile(r"[^\w\s]"))
    )
    if classes < 3:
        raise ValidationError(
            "Password must combine at least three of: lowercase, uppercase, digits, symbols."
        )

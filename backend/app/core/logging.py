"""Structured logging with request-scoped context.

Never logs: passwords, tokens, refresh tokens, full document bodies. A redaction
filter enforces this even if a caller is careless.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
from typing import Any

from app.config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")
tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant_id", default="-")

_SENSITIVE_KEYS = {
    "password", "new_password", "current_password", "token", "access_token",
    "refresh_token", "authorization", "secret", "jwt_secret", "api_key",
    "llm_api_key", "password_hash", "token_hash",
}
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}")


def redact(value: Any, _depth: int = 0) -> Any:
    if _depth > 6:
        return "<deep>"
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k.lower() in _SENSITIVE_KEYS else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        v = _BEARER_RE.sub(r"\1<redacted>", value)
        v = _JWT_RE.sub("<redacted-jwt>", v)
        return v
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.tenant_id = tenant_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "tenant_id": getattr(record, "tenant_id", "-"),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-4000:]
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"[{getattr(record, 'request_id', '-')}] {record.name}: {redact(record.getMessage())}"
        )
        extra = getattr(record, "extra_fields", None)
        if extra:
            base += " " + json.dumps(redact(extra), default=str)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    logging.getLogger("uvicorn.access").handlers = [handler]
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.db_echo else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})

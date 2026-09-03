"""Append-only audit trail for security-sensitive actions."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event, request_id_var
from app.db.models.tenancy import AuditEvent

logger = get_logger("praxis.audit")

# Actions worth an immutable record.
LOGIN_SUCCESS = "auth.login.success"
LOGIN_FAILURE = "auth.login.failure"
LOGOUT = "auth.logout"
REGISTER = "auth.register"
REFRESH_ROTATED = "auth.refresh.rotated"
REFRESH_REPLAY = "auth.refresh.replay_detected"
TOKEN_REVOKED = "auth.token.revoked"
POLICY_DENY = "policy.deny"
POLICY_ALLOW = "policy.allow"
DOCUMENT_UPLOAD = "document.upload"
DOCUMENT_DELETE = "document.delete"
RULE_WRITE = "rule.write"
INJECTION_DETECTED = "security.prompt_injection.detected"
CROSS_TENANT_ATTEMPT = "security.cross_tenant.attempt"
ANSWER_ABSTAINED = "answer.abstained"
EVAL_RUN = "eval.run"


def record(
    session: Session,
    action: str,
    *,
    tenant_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    resource_type: str = "",
    resource_id: str = "",
    outcome: str = "success",
    client_ip: str = "",
    detail: dict | None = None,
    flush: bool = True,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        outcome=outcome,
        request_id=request_id_var.get(),
        client_ip=client_ip,
        detail=detail or {},
    )
    session.add(event)
    if flush:
        session.flush()
    log_event(
        logger, 20, f"audit {action}",
        action=action, outcome=outcome, resource_type=resource_type,
        resource_id=str(resource_id or ""),
    )
    return event

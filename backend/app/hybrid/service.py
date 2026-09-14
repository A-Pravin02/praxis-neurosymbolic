"""HYBRID. Serve the neuro-symbolic assistant through the existing chat API.

A tenant whose slug matches the configured knowledge pack is answered by the
neuro-symbolic controller; every other tenant keeps the document-RAG pipeline. Both
return the same envelope, so conversations, persistence, auditing and the web client
work unchanged, and the neuro-symbolic answer adds its own `neurosymbolic` block with
the pipeline, the explanation, the sources and the full reasoning trace.
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models.chat import Message
from app.db.models.tenancy import Tenant
from app.orchestrator.pipeline import AssistantTurn, StageTiming
from app.tenancy.context import Principal

_GATE = {"answered": "ANSWER", "conditional": "ANSWER_WITH_CAVEAT",
         "clarification": "ABSTAIN", "insufficient": "ABSTAIN", "denied": "DENY"}
_SLUGS: dict[uuid.UUID, str | None] = {}


def serves_tenant(session: Session, principal: Principal) -> bool:
    # Read through the request's own tenant-bound session: the application role never
    # needs elevated access to learn which workspace it is serving.
    if principal.tenant_id not in _SLUGS:
        _SLUGS[principal.tenant_id] = session.scalar(
            select(Tenant.slug).where(Tenant.id == principal.tenant_id))
    return _SLUGS[principal.tenant_id] == settings.knowledge_pack_tenant


def previous_context(session: Session, principal: Principal,
                     conversation_id: uuid.UUID) -> dict[str, Any]:
    """What the conversation has already resolved, so "can I cancel it?" can follow on."""
    trace = session.scalar(
        select(Message.trace_json)
        .where(Message.tenant_id == principal.tenant_id,
               Message.conversation_id == conversation_id,
               Message.role == "assistant")
        .order_by(Message.sequence.desc()).limit(1))
    return ((trace or {}).get("neurosymbolic") or {}).get("context") or {}


def answer(principal: Principal, question: str, context: dict[str, Any]) -> AssistantTurn:
    from app.hybrid.controller import NeuroSymbolicController

    result = _controller().answer(question, actor_email=principal.email,
                                  actor_roles=tuple(sorted(principal.roles)), context=context)
    verification = result.trace.get("verification") or {}
    status = ("INSUFFICIENT_EVIDENCE" if result.outcome == "insufficient"
              else "NOT_APPLICABLE" if result.outcome in ("clarification", "denied")
              else "VERIFIED" if verification.get("passed") else "UNVERIFIED")
    turn = AssistantTurn(
        answer=result.answer,
        route=f"NEUROSYMBOLIC_{result.pipeline}",
        gate_decision=_GATE.get(result.outcome, "ABSTAIN"),
        verification_status=status,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        pipeline_mode="neurosymbolic",
        stages=[StageTiming(name=c["stage"], ms=0, detail={"layer": c["layer"]})
                for c in result.trace.get("components", [])],
    )
    turn.neurosymbolic = {
        "pipeline": result.pipeline, "pipeline_label": result.pipeline_label,
        "outcome": result.outcome, "confidence": result.confidence,
        "explanation": result.explanation, "sources": result.sources,
        "clarification": result.clarification, "goal": result.goal,
        "context": result.context, "trace": result.trace,
    }
    return turn


@lru_cache(maxsize=1)
def _controller():
    from app.hybrid.controller import NeuroSymbolicController

    return NeuroSymbolicController()

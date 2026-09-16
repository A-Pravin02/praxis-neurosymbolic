"""HYBRID. Serve the neuro-symbolic assistant through the existing chat API.

A tenant whose slug matches the configured knowledge pack is answered by the
neuro-symbolic controller; every other tenant keeps the document-RAG pipeline. Both
return the same envelope, so conversations, persistence, auditing and the web client
work unchanged, and the neuro-symbolic answer adds its own `neurosymbolic` block with
the pipeline, the explanation, the sources and the full reasoning trace.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models.chat import Message
from app.db.models.tenancy import Tenant
from app.orchestrator.pipeline import AssistantTurn, StageTiming
from app.tenancy.context import Principal

_GATE = {"answered": "ANSWER", "conditional": "ANSWER_WITH_CAVEAT",
         "clarification": "ABSTAIN", "insufficient": "ABSTAIN", "denied": "DENY",
         "needs_review": "ABSTAIN",
         # General conversation is answered, but never as an evidence-backed answer:
         # the caveat is the point, and every consumer reads it from here.
         "general": "ANSWER_WITH_CAVEAT"}
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
    status = ("UNVERIFIED" if result.outcome == "general"
              else "INSUFFICIENT_EVIDENCE" if result.outcome == "insufficient"
              else "NOT_APPLICABLE" if result.outcome in ("clarification", "denied")
              else "NEEDS_REVIEW" if result.outcome == "needs_review"
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
        "context": result.context,
        "decision_label": result.decision_label, "decision_kind": result.decision_kind,
        "recommended_action": result.recommended_action,
        "verification": result.verification, "knowledge_gap": result.knowledge_gap,
        "policy_versions": result.policy_versions, "rules_applied": result.rules_applied,
        "facts": result.facts, "claims": result.claims,
        # The audit trace holds only facts this person is authorized to see; the client
        # shows it to support and administrative roles.
        "trace": result.trace,
        "audience": "staff" if {"support", "admin", "analyst"} & set(principal.roles)
        else "self",
    }
    return turn


_CACHE: dict[str, Any] = {"revision": None, "controller": None}


def _controller():
    """The controller for the current knowledge revision.

    An administrator's change writes a new revision marker; the next request after it
    loads the changed pack, so every worker converges on the same knowledge.
    """
    from app.hybrid import knowledge_store
    from app.hybrid.controller import NeuroSymbolicController
    from app.hybrid.knowledge_pack import default_pack_dir, load_pack

    current = knowledge_store.revision(default_pack_dir())
    if _CACHE["controller"] is None or _CACHE["revision"] != current:
        load_pack.cache_clear()
        mode = settings.understanding
        if mode == "auto":
            mode = "encoder" if settings.llm_provider == "mock" else "llm"
        _CACHE["controller"] = NeuroSymbolicController(understanding=mode)
        _CACHE["revision"] = current
    return _CACHE["controller"]

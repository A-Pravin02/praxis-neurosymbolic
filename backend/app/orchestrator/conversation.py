"""Conversation manager: persistence, history windowing, titles."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import NotFoundError
from app.db.models.chat import Conversation, Message
from app.db.models.knowledge import Answer, Claim, ProvenanceRecord
from app.neuro.heuristics import title_from
from app.orchestrator.pipeline import AssistantTurn
from app.tenancy.context import Principal


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def create_conversation(session: Session, principal: Principal,
                        title: str | None = None) -> Conversation:
    conversation = Conversation(
        tenant_id=principal.tenant_id, user_id=principal.user_id,
        title=(title or "New conversation")[:300],
    )
    session.add(conversation)
    session.flush()
    return conversation


def get_conversation(session: Session, principal: Principal,
                     conversation_id: uuid.UUID) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    # 404 for "not yours" as well as "absent", so ids cannot be probed.
    if (conversation is None or conversation.tenant_id != principal.tenant_id
            or conversation.user_id != principal.user_id):
        raise NotFoundError()
    return conversation


def list_conversations(session: Session, principal: Principal,
                       limit: int = 100) -> list[tuple[Conversation, int]]:
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("n"))
        .where(Message.tenant_id == principal.tenant_id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = session.execute(
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(Conversation.tenant_id == principal.tenant_id,
               Conversation.user_id == principal.user_id,
               Conversation.is_archived.is_(False))
        .order_by(func.coalesce(Conversation.last_message_at,
                                Conversation.created_at).desc())
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows]


def delete_conversation(session: Session, principal: Principal,
                        conversation_id: uuid.UUID) -> None:
    conversation = get_conversation(session, principal, conversation_id)
    session.delete(conversation)
    session.flush()


def rename_conversation(session: Session, principal: Principal,
                        conversation_id: uuid.UUID, title: str) -> Conversation:
    conversation = get_conversation(session, principal, conversation_id)
    conversation.title = title[:300]
    session.flush()
    return conversation


def list_messages(session: Session, principal: Principal,
                  conversation_id: uuid.UUID) -> list[Message]:
    get_conversation(session, principal, conversation_id)
    return list(session.scalars(
        select(Message)
        .where(Message.tenant_id == principal.tenant_id,
               Message.conversation_id == conversation_id)
        .order_by(Message.sequence)
    ).all())


def history_window(session: Session, principal: Principal,
                   conversation_id: uuid.UUID) -> list[dict[str, str]]:
    """The recent turns that fit the context budget, oldest first.

    Budgeting is by characters rather than provider-specific tokens so the same
    window is produced regardless of which model is configured.
    """
    messages = list(session.scalars(
        select(Message)
        .where(Message.tenant_id == principal.tenant_id,
               Message.conversation_id == conversation_id,
               Message.role.in_(("user", "assistant")),
               Message.status == "complete")
        .order_by(Message.sequence.desc())
        .limit(settings.max_context_messages)
    ).all())

    window: list[dict[str, str]] = []
    budget = settings.max_context_chars
    for message in messages:
        content = message.content or ""
        if len(content) > budget:
            break
        budget -= len(content)
        window.append({"role": message.role, "content": content})
    window.reverse()
    return window


def next_sequence(session: Session, conversation_id: uuid.UUID) -> int:
    current = session.scalar(
        select(func.coalesce(func.max(Message.sequence), -1))
        .where(Message.conversation_id == conversation_id)
    )
    return int(current) + 1


def append_message(session: Session, principal: Principal, conversation: Conversation,
                   *, role: str, content: str, status: str = "complete",
                   turn: AssistantTurn | None = None) -> Message:
    message = Message(
        tenant_id=principal.tenant_id, conversation_id=conversation.id,
        user_id=principal.user_id if role == "user" else None,
        sequence=next_sequence(session, conversation.id),
        role=role, content=content, status=status,
    )
    if turn is not None:
        message.route = turn.route
        message.verification_status = turn.verification_status
        message.confidence = turn.confidence
        message.latency_ms = turn.latency_ms
        message.trace_json = turn.trace()
    session.add(message)
    conversation.last_message_at = _now()
    session.flush()
    return message


def ensure_title(session: Session, conversation: Conversation, question: str) -> None:
    if conversation.title in ("", "New conversation"):
        conversation.title = title_from(question)
        session.flush()


def record_answer(session: Session, principal: Principal, conversation: Conversation,
                  message: Message, question: str, turn: AssistantTurn) -> Answer:
    """Persist the auditable record of this turn: answer, claims, provenance."""
    answer = Answer(
        tenant_id=principal.tenant_id, user_id=principal.user_id,
        conversation_id=conversation.id, message_id=message.id,
        question=question[:8000], answer_text=turn.answer,
        route=turn.route, gate_decision=turn.gate_decision,
        verification_status=turn.verification_status, confidence=turn.confidence,
        pipeline_mode=turn.pipeline_mode, latency_ms=turn.latency_ms,
        trace_json=turn.trace(),
    )
    session.add(answer)
    session.flush()

    for claim in turn.claims:
        session.add(Claim(
            tenant_id=principal.tenant_id, answer_id=answer.id,
            text=claim.get("text", "")[:4000], claim_kind=claim.get("kind", "DIRECT"),
            support_score=float(claim.get("support_score", 0.0)),
            status=claim.get("status", "unverified"),
            supporting_fact_ids=claim.get("fact_ids", []),
            supporting_chunk_ids=[
                c["chunk_id"] for c in turn.citations
                if c["index"] in claim.get("citation_indexes", [])
            ],
        ))

    session.add(ProvenanceRecord(
        tenant_id=principal.tenant_id, answer_id=answer.id,
        graph_json=_provenance_graph(turn),
    ))
    session.flush()
    return answer


def _provenance_graph(turn: AssistantTurn) -> dict[str, Any]:
    """Answer -> claims -> facts -> rules -> evidence -> source, as a graph."""
    nodes: list[dict[str, Any]] = [{"id": "answer", "type": "answer",
                                    "label": turn.answer[:200]}]
    edges: list[dict[str, str]] = []

    for index, claim in enumerate(turn.claims):
        node_id = f"claim:{index}"
        nodes.append({"id": node_id, "type": "claim", "label": claim.get("text", "")[:200],
                      "status": claim.get("status"), "kind": claim.get("kind")})
        edges.append({"from": "answer", "to": node_id, "type": "asserts"})
        for fact_id in claim.get("fact_ids", []):
            edges.append({"from": node_id, "to": f"fact:{fact_id}", "type": "supported_by"})
        for citation_index in claim.get("citation_indexes", []):
            edges.append({"from": node_id, "to": f"evidence:{citation_index}",
                          "type": "cites"})

    for fact in turn.reasoning.get("facts", []):
        fact_id = fact.get("id") or f"{fact['subject']}|{fact['predicate']}"
        nodes.append({
            "id": f"fact:{fact_id}", "type": "fact", "kind": fact.get("kind"),
            "label": f"{fact['subject']}.{fact['predicate']}",
            "value": fact.get("object", {}).get("value"),
        })
        provenance = fact.get("provenance", {})
        if provenance.get("chunk_id"):
            edges.append({"from": f"fact:{fact_id}",
                          "to": f"chunk:{provenance['chunk_id']}", "type": "extracted_from"})
        for rule_code in provenance.get("rule_codes", []):
            edges.append({"from": f"fact:{fact_id}", "to": f"rule:{rule_code}",
                          "type": "derived_by"})
        for premise in provenance.get("premise_fact_ids", []):
            edges.append({"from": f"fact:{fact_id}", "to": f"fact:{premise}",
                          "type": "premise"})

    for step in turn.reasoning.get("inferences", []):
        nodes.append({"id": f"rule:{step['rule_code']}", "type": "rule",
                      "label": step.get("rule_text", step["rule_code"])})

    for citation in turn.citations:
        nodes.append({
            "id": f"evidence:{citation['index']}", "type": "evidence",
            "label": citation["document_title"], "chunk_id": citation["chunk_id"],
            "quarantined": citation.get("quarantined", False),
        })
        nodes.append({"id": f"chunk:{citation['chunk_id']}", "type": "chunk",
                      "label": citation["snippet"][:160]})
        edges.append({"from": f"evidence:{citation['index']}",
                      "to": f"source:{citation['document_id']}", "type": "from_source"})
        nodes.append({"id": f"source:{citation['document_id']}", "type": "source",
                      "label": citation["document_title"]})

    seen: set[str] = set()
    unique_nodes = []
    for node in nodes:
        if node["id"] in seen:
            continue
        seen.add(node["id"])
        unique_nodes.append(node)
    return {"nodes": unique_nodes, "edges": edges}

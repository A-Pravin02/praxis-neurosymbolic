from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.schemas import (
    AssistantTurnOut, ChatRequest, ConversationCreate, ConversationOut, ConversationUpdate,
    MessageOut,
)
from app.config import settings
from app.core import ratelimit
from app.core.deps import CurrentPrincipal, DbSession, client_ip, require_permissions
from app.core.errors import AuthorizationError, NotFoundError
from app.db.session import tenant_session
from app.orchestrator import conversation as convo
from app.orchestrator.pipeline import AssistantTurn, Orchestrator
from app.tenancy.permissions import (
    CHAT_USE, CONVERSATION_READ, CONVERSATION_WRITE, EVAL_RUN,
)

router = APIRouter(tags=["chat"])


def _conversation_out(row, message_count: int = 0) -> ConversationOut:
    return ConversationOut(
        id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at,
        last_message_at=row.last_message_at, is_archived=row.is_archived,
        message_count=message_count,
    )


def _message_out(row) -> MessageOut:
    return MessageOut(
        id=row.id, role=row.role, content=row.content, sequence=row.sequence,
        created_at=row.created_at, status=row.status, route=row.route,
        verification_status=row.verification_status, confidence=row.confidence,
        trace=row.trace_json or {},
    )


def _turn_out(turn: AssistantTurn, *, conversation_id: uuid.UUID | None = None,
              message_id: uuid.UUID | None = None) -> AssistantTurnOut:
    return AssistantTurnOut(
        message_id=message_id, conversation_id=conversation_id, answer=turn.answer,
        route=turn.route, gate_decision=turn.gate_decision,
        verification_status=turn.verification_status, confidence=turn.confidence,
        citations=turn.citations, claims=turn.claims, reasoning=turn.reasoning,
        policy_decision=turn.policy_decision, warnings=turn.warnings,
        stages=[s.as_dict() for s in turn.stages], latency_ms=turn.latency_ms,
        pipeline_mode=turn.pipeline_mode,
    )


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

@router.get("/conversations", response_model=list[ConversationOut],
            dependencies=[Depends(require_permissions(CONVERSATION_READ))])
def list_conversations(db: DbSession, principal: CurrentPrincipal) -> list[ConversationOut]:
    return [_conversation_out(row, count)
            for row, count in convo.list_conversations(db, principal)]


@router.post("/conversations", response_model=ConversationOut,
             status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permissions(CONVERSATION_WRITE))])
def create_conversation(payload: ConversationCreate, db: DbSession,
                        principal: CurrentPrincipal) -> ConversationOut:
    return _conversation_out(convo.create_conversation(db, principal, payload.title))


@router.get("/conversations/{conversation_id}", response_model=ConversationOut,
            dependencies=[Depends(require_permissions(CONVERSATION_READ))])
def get_conversation(conversation_id: uuid.UUID, db: DbSession,
                     principal: CurrentPrincipal) -> ConversationOut:
    return _conversation_out(convo.get_conversation(db, principal, conversation_id))


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut,
              dependencies=[Depends(require_permissions(CONVERSATION_WRITE))])
def update_conversation(conversation_id: uuid.UUID, payload: ConversationUpdate,
                        db: DbSession, principal: CurrentPrincipal) -> ConversationOut:
    row = convo.get_conversation(db, principal, conversation_id)
    if payload.title is not None:
        row.title = payload.title[:300]
    if payload.is_archived is not None:
        row.is_archived = payload.is_archived
    db.flush()
    return _conversation_out(row)


@router.delete("/conversations/{conversation_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permissions(CONVERSATION_WRITE))])
def delete_conversation(conversation_id: uuid.UUID, db: DbSession,
                        principal: CurrentPrincipal) -> Response:
    convo.delete_conversation(db, principal, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/conversations/{conversation_id}/messages",
            response_model=list[MessageOut],
            dependencies=[Depends(require_permissions(CONVERSATION_READ))])
def list_messages(conversation_id: uuid.UUID, db: DbSession,
                  principal: CurrentPrincipal) -> list[MessageOut]:
    return [_message_out(m) for m in convo.list_messages(db, principal, conversation_id)]


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _guard_pipeline_mode(principal, payload: ChatRequest) -> None:
    """Ablation modes disable verification, so they are not for ordinary users.

    `llm_only` and `llm_rag` exist to benchmark the architecture against itself.
    They answer without the evidence gate, which is exactly the behaviour this
    platform exists to prevent, so selecting one requires the same permission as
    running the evaluation harness.
    """
    if payload.pipeline_mode != "neurosymbolic" and EVAL_RUN not in principal.permissions:
        raise AuthorizationError(
            "Comparing pipeline modes requires the evaluation permission. "
            "The default pipeline is the verified one."
        )


def _run_turn(db, principal, payload: ChatRequest, *, persist: bool = True):
    """Shared body for the blocking and streaming endpoints."""
    conversation = (
        convo.get_conversation(db, principal, payload.conversation_id)
        if payload.conversation_id else convo.create_conversation(db, principal)
    )
    convo.ensure_title(db, conversation, payload.message)
    history = convo.history_window(db, principal, conversation.id)
    if persist:
        convo.append_message(db, principal, conversation, role="user",
                             content=payload.message)
    turn = Orchestrator(db, principal).answer(
        payload.message, history=history, pipeline_mode=payload.pipeline_mode)
    message = None
    if persist:
        message = convo.append_message(db, principal, conversation, role="assistant",
                                       content=turn.answer, turn=turn)
        convo.record_answer(db, principal, conversation, message, payload.message, turn)
    return conversation, message, turn


@router.post("/chat", response_model=AssistantTurnOut,
             dependencies=[Depends(require_permissions(CHAT_USE))])
def chat(payload: ChatRequest, request: Request, db: DbSession,
         principal: CurrentPrincipal) -> AssistantTurnOut:
    ratelimit.check("chat", str(principal.user_id), settings.rate_limit_chat_per_minute)
    _guard_pipeline_mode(principal, payload)
    conversation, message, turn = _run_turn(db, principal, payload)
    return _turn_out(turn, conversation_id=conversation.id,
                     message_id=message.id if message else None)


@router.post("/chat/stream", dependencies=[Depends(require_permissions(CHAT_USE))])
def chat_stream(payload: ChatRequest, request: Request,
                principal: CurrentPrincipal) -> StreamingResponse:
    """Server-sent events: token deltas, then the full verified envelope.

    The pipeline runs to completion *before* any token is emitted. Streaming a
    partially-verified answer would defeat the gate, so what streams is the
    already-approved text, chunked for the user's benefit.
    """
    ratelimit.check("chat", str(principal.user_id), settings.rate_limit_chat_per_minute)
    _guard_pipeline_mode(principal, payload)

    def event_stream() -> Iterator[str]:
        try:
            with tenant_session(principal.tenant_id, principal.user_id) as db:
                conversation, message, turn = _run_turn(db, principal, payload)
                payload_out = _turn_out(
                    turn, conversation_id=conversation.id,
                    message_id=message.id if message else None,
                ).model_dump(mode="json")

            yield _sse("start", {"conversation_id": str(conversation.id),
                                 "route": turn.route,
                                 "gate_decision": turn.gate_decision})
            for chunk in _chunk_text(turn.answer):
                yield _sse("delta", {"text": chunk})
            yield _sse("done", payload_out)
        except Exception as exc:  # surfaced to the client as a terminal event
            from app.core.errors import AppError
            detail = (exc.detail if isinstance(exc, AppError)
                      else "The assistant could not complete this request.")
            code = exc.code if isinstance(exc, AppError) else "internal_error"
            yield _sse("error", {"code": code, "message": detail})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _chunk_text(text: str, size: int = 24) -> Iterator[str]:
    buffer: list[str] = []
    count = 0
    for token in text.split(" "):
        buffer.append(token)
        count += len(token) + 1
        if count >= size:
            yield " ".join(buffer) + " "
            buffer, count = [], 0
    if buffer:
        yield " ".join(buffer)

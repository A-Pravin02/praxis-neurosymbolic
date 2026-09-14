"""Pydantic request/response models for the HTTP API."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator
from typing_extensions import Annotated as _Annotated

# A pragmatic email type. `EmailStr` from pydantic delegates to email-validator,
# which rejects reserved test domains such as `.test` - exactly what a demo and a
# test suite should be using. Address syntax is not a security control here:
# authentication is, so a permissive shape check is the right trade-off.
Email = _Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=3,
                      max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"),
]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=200)
    tenant_slug: str | None = Field(default=None, max_length=64)
    tenant_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=512)


class PrincipalOut(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_slug: str
    email: str
    display_name: str
    roles: list[str]
    permissions: list[str]


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class AuthOut(BaseModel):
    user: PrincipalOut
    tokens: TokenOut


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: dt.datetime
    expires_at: dt.datetime
    user_agent: str
    client_ip: str
    active: bool
    revoked_reason: str | None = None


# ---------------------------------------------------------------------------
# Conversations & chat
# ---------------------------------------------------------------------------

class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    is_archived: bool | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: dt.datetime
    updated_at: dt.datetime
    last_message_at: dt.datetime | None
    is_archived: bool
    message_count: int = 0


class CitationOut(BaseModel):
    index: int
    document_id: uuid.UUID | None = None
    document_title: str = ""
    chunk_id: uuid.UUID | None = None
    section: str = ""
    page: int | None = None
    snippet: str = ""
    score: float = 0.0
    quarantined: bool = False


class ClaimOut(BaseModel):
    text: str
    kind: str
    status: str
    support_score: float
    relevance: float = 0.0
    citation_indexes: list[int] = []
    fact_ids: list[str] = []


class ProvenanceOut(BaseModel):
    source_kind: str = "document"
    document_id: str | None = None
    chunk_id: str | None = None
    document_title: str = ""
    evidence_text: str = ""
    extractor: str = ""
    rule_codes: list[str] = []
    premise_fact_ids: list[str] = []
    effective_date: str | None = None


class FactOut(BaseModel):
    id: str | None = None
    subject: str
    predicate: str
    object: Any
    kind: str
    confidence: float
    provenance: ProvenanceOut = ProvenanceOut()
    valid_from: str | None = None
    valid_to: str | None = None
    citation_index: int | None = None


class InferenceStepOut(BaseModel):
    step: int
    rule_code: str
    rule_name: str = ""
    rule_text: str = ""
    premises: list[str] = []
    premise_fact_ids: list[str] = []
    conclusion: str
    # A readable rendering of the conclusion, for prose and for the UI.
    sentence: str = ""
    premise_sentences: list[str] = []
    confidence: float = 1.0


class ContradictionOut(BaseModel):
    subject: str
    predicate: str
    values: list[Any]
    fact_ids: list[str] = []
    document_ids: list[str] = []
    chunk_ids: list[str] = []
    citation_indexes: list[int] = []
    severity: str = "error"
    kind: str = "functional_conflict"
    resolution: str = ""
    # True when an ordering signal (a newer effective date) identifies which value
    # supersedes the others. The conflict is reported either way.
    resolved: bool = False
    preferred_value: Any = None
    superseded_values: list[Any] = []
    # True when this conflict actually blocks the answer, rather than sitting
    # elsewhere in the knowledge base.
    blocking: bool = False


class PolicyDecisionOut(BaseModel):
    decision: str
    reason: str = ""
    matched_policy: str | None = None
    obligations: list[str] = []


class ReasoningTraceOut(BaseModel):
    goal: str = ""
    facts: list[FactOut] = []
    inferences: list[InferenceStepOut] = []
    rules_considered: list[str] = []
    rules_fired: list[str] = []
    contradictions: list[ContradictionOut] = []
    conclusion: str = ""
    iterations: int = 0


class StageTimingOut(BaseModel):
    name: str
    ms: int
    detail: dict[str, Any] = {}


class AssistantTurnOut(BaseModel):
    message_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    answer: str
    route: str
    gate_decision: str
    verification_status: str
    confidence: float
    citations: list[CitationOut] = []
    claims: list[ClaimOut] = []
    reasoning: ReasoningTraceOut = ReasoningTraceOut()
    policy_decision: PolicyDecisionOut | None = None
    warnings: list[str] = []
    stages: list[StageTimingOut] = []
    latency_ms: int = 0
    pipeline_mode: str = "neurosymbolic"
    # Present when a knowledge-pack workspace answered: pipeline, explanation, sources,
    # clarification options and the full reasoning trace.
    neurosymbolic: dict | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sequence: int
    created_at: dt.datetime
    status: str = "complete"
    route: str | None = None
    verification_status: str | None = None
    confidence: float | None = None
    trace: dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: uuid.UUID | None = None
    pipeline_mode: Literal[
        "llm_only", "llm_rag", "llm_rag_verify", "neurosymbolic"
    ] = "neurosymbolic"

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    media_type: str
    collection: str
    sensitivity: str
    required_permission: str | None
    status: str
    byte_size: int
    chunk_count: int = 0
    effective_date: str | None = None
    version_label: str = ""
    created_at: dt.datetime
    error: str | None = None
    injection_flagged_chunks: int = 0


class DocumentIngestTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=400)
    text: str = Field(min_length=1, max_length=2_000_000)
    collection: str = Field(default="default", max_length=96)
    sensitivity: str = Field(default="internal", max_length=48)
    required_permission: str | None = Field(default=None, max_length=96)
    effective_date: str | None = Field(default=None, max_length=32)
    version_label: str = Field(default="", max_length=64)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=8, ge=1, le=50)
    collection: str | None = None


class SearchHitOut(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    text: str
    score: float
    vector_score: float = 0.0
    lexical_score: float = 0.0
    section: str = ""
    page: int | None = None
    quarantined: bool = False


# ---------------------------------------------------------------------------
# Knowledge / rules / policy
# ---------------------------------------------------------------------------

class FactCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=120)
    object: Any
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    valid_from: str | None = None
    valid_to: str | None = None
    sensitivity: str = "internal"
    required_permission: str | None = None


class FactRecordOut(BaseModel):
    id: uuid.UUID
    subject: str
    predicate: str
    object: Any
    kind: str
    confidence: float
    source_document_id: uuid.UUID | None
    evidence_text: str
    created_at: dt.datetime


class RuleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=300)
    description: str = ""
    body: list[dict[str, Any]]
    head: dict[str, Any]
    priority: int = 100
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RuleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str
    body: list[dict[str, Any]]
    head: dict[str, Any]
    priority: int
    confidence: float
    is_active: bool
    origin: str


class PolicyOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    effect: str
    priority: int
    subject_roles: list[str]
    subject_permissions: list[str]
    actions: list[str]
    resource_types: list[str]
    condition: dict[str, Any] | None
    is_active: bool


class PolicyEvaluateRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    resource_type: str = Field(min_length=1, max_length=64)
    resource_attributes: dict[str, Any] = {}
    subject_user_id: uuid.UUID | None = None


class ConstraintValidateRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    expression: dict[str, Any]


# ---------------------------------------------------------------------------
# Evaluation & audit
# ---------------------------------------------------------------------------

class EvalRunRequest(BaseModel):
    suite: str = Field(default="demo", max_length=96)
    modes: list[Literal["llm_only", "llm_rag", "llm_rag_verify", "neurosymbolic"]] = [
        "llm_only", "llm_rag", "llm_rag_verify", "neurosymbolic"
    ]
    limit: int | None = Field(default=None, ge=1, le=500)


class EvalModeMetricsOut(BaseModel):
    mode: str
    cases: int
    # None means the suite contained no case of that kind, which is different
    # from a measured zero.
    factual_accuracy: float | None = None
    groundedness: float | None = None
    citation_accuracy: float | None = None
    unsupported_claim_rate: float | None = None
    hallucination_rate: float | None = None
    contradiction_rate: float | None = None
    symbolic_accuracy: float | None = None
    abstention_accuracy: float | None = None
    retrieval_precision: float | None = None
    retrieval_recall: float | None = None
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0


class EvalRunOut(BaseModel):
    suite: str
    generated_at: dt.datetime
    llm_provider: str
    llm_model: str
    modes: list[EvalModeMetricsOut]
    report_path: str | None = None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    created_at: dt.datetime
    action: str
    outcome: str
    actor_user_id: uuid.UUID | None
    resource_type: str
    resource_id: str
    request_id: str
    detail: dict[str, Any]


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    llm_provider: str
    embedding_provider: str

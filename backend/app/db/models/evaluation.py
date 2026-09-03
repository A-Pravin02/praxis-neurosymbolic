from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin


class EvalRun(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    suite: Mapped[str] = mapped_column(String(96), default="demo", nullable=False)
    mode: Mapped[str] = mapped_column(String(48), nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    llm_model: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class EvalCaseResult(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "eval_case_results"
    __table_args__ = (Index("ix_eval_case_results_run", "run_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(String(96), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expected_kind: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hallucinated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    abstained: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    citation_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unsupported_claims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_claims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_precision: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    retrieval_recall: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

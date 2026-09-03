from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean, Computed, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db.base import Base, TimestampMixin, UUIDPkMixin


class Document(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_tenant_status", "tenant_id", "status"),
        Index("ix_documents_tenant_collection", "tenant_id", "collection"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(1024), default="", nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), default="text/plain", nullable=False)
    collection: Mapped[str] = mapped_column(String(96), default="default", nullable=False)
    # Classification used by the PDP / access constraints (e.g. "public", "hr_confidential")
    sensitivity: Mapped[str] = mapped_column(String(48), default="internal", nullable=False)
    required_permission: Mapped[str | None] = mapped_column(String(96))
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    # Effective/valid time for the document's assertions, used by contradiction resolution.
    effective_date: Mapped[str | None] = mapped_column(String(32))
    version_label: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base, UUIDPkMixin, TimestampMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        Index("ix_chunks_tenant", "tenant_id"),
        Index("ix_chunks_document", "document_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    section: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dimension))
    # Generated in the database so lexical search always matches the stored text.
    tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(text,''))", persisted=True),
        nullable=True,
    )
    # Prompt-injection screening result, computed at ingest time.
    injection_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    injection_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    injection_patterns: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")

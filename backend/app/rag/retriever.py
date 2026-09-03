"""Hybrid retrieval: dense vectors + lexical full text, reranked.

Every query is filtered by three independent controls:

1. PostgreSQL row level security (the session's bound tenant),
2. an explicit ``tenant_id`` predicate in the query itself,
3. the compiled access-constraint predicate from the policy layer.

Any one of them failing does not by itself leak a row.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import get_logger, log_event
from app.db.models.rag import Chunk, Document
from app.neuro.heuristics import coverage, normalize, weighted_overlap
from app.policy import service as policy_service
from app.rag.embeddings import get_embedder
from app.tenancy.context import Principal

logger = get_logger("praxis.rag")


@dataclass
class EvidenceSpan:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    text: str
    score: float = 0.0
    vector_score: float = 0.0
    lexical_score: float = 0.0
    rerank_score: float = 0.0
    section: str = ""
    page: int | None = None
    collection: str = "default"
    sensitivity: str = "internal"
    effective_date: str | None = None
    version_label: str = ""
    quarantined: bool = False
    injection_patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id), "document_id": str(self.document_id),
            "document_title": self.document_title, "text": self.text,
            "score": round(self.score, 4), "section": self.section, "page": self.page,
            "quarantined": self.quarantined,
        }


@dataclass
class RetrievalQuery:
    text: str
    top_k: int = 0
    collection: str | None = None
    include_quarantined: bool = True
    candidate_multiplier: int = 3


@dataclass
class RetrievalResult:
    spans: list[EvidenceSpan]
    candidates_considered: int = 0
    filters_applied: list[str] = field(default_factory=list)
    query_text: str = ""


class HybridRetriever:
    def __init__(self, session: Session, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        q = normalize(query.text)
        if not q:
            return RetrievalResult(spans=[], query_text=q)

        top_k = query.top_k or settings.rerank_top_k
        pool = max(settings.retrieval_top_k, top_k * query.candidate_multiplier)

        described: list[str] = []
        auth_filter = policy_service.retrieval_filter(
            self.session, self.principal, describe_into=described
        )

        vector_hits = self._vector_search(q, pool, auth_filter, query.collection)
        lexical_hits = self._lexical_search(q, pool, auth_filter, query.collection)

        merged: dict[uuid.UUID, EvidenceSpan] = {}
        for span in vector_hits:
            merged[span.chunk_id] = span
        for span in lexical_hits:
            existing = merged.get(span.chunk_id)
            if existing is None:
                merged[span.chunk_id] = span
            else:
                existing.lexical_score = max(existing.lexical_score, span.lexical_score)

        spans = list(merged.values())
        for span in spans:
            span.score = (
                settings.hybrid_vector_weight * span.vector_score
                + settings.hybrid_lexical_weight * span.lexical_score
            )

        spans = rerank(q, spans)
        if not query.include_quarantined:
            spans = [s for s in spans if not s.quarantined]
        selected = spans[:top_k]

        log_event(logger, 20, "retrieval",
                  candidates=len(merged), returned=len(selected),
                  quarantined=sum(1 for s in selected if s.quarantined),
                  filters=len(described))
        return RetrievalResult(spans=selected, candidates_considered=len(merged),
                               filters_applied=described, query_text=q)

    # -- internals ---------------------------------------------------------

    def _base_columns(self):
        return (
            Chunk.id, Chunk.document_id, Chunk.text, Chunk.section, Chunk.page,
            Chunk.injection_flagged, Chunk.injection_patterns,
            Document.title, Document.collection, Document.sensitivity,
            Document.effective_date, Document.version_label,
        )

    def _vector_search(self, query_text: str, limit: int, auth_filter,
                       collection: str | None) -> list[EvidenceSpan]:
        embedder = get_embedder()
        vector = embedder.embed_one(query_text)
        distance = Chunk.embedding.cosine_distance(vector).label("distance")
        stmt = (
            select(*self._base_columns(), distance)
            .join(Document, Document.id == Chunk.document_id)
            .where(auth_filter, Chunk.embedding.isnot(None),
                   Document.status == "ready")
            .order_by(distance)
            .limit(limit)
        )
        if collection:
            stmt = stmt.where(Document.collection == collection)
        rows = self.session.execute(stmt).all()
        spans = []
        for row in rows:
            similarity = max(0.0, 1.0 - float(row.distance))
            spans.append(self._to_span(row, vector_score=similarity))
        return spans

    def _lexical_search(self, query_text: str, limit: int, auth_filter,
                        collection: str | None) -> list[EvidenceSpan]:
        tsquery = func.plainto_tsquery("english", query_text)
        tsv = text("chunks.tsv")
        rank = func.ts_rank_cd(tsv, tsquery).cast(Float).label("rank")
        stmt = (
            select(*self._base_columns(), rank)
            .join(Document, Document.id == Chunk.document_id)
            .where(auth_filter, Document.status == "ready", tsv.bool_op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
        if collection:
            stmt = stmt.where(Document.collection == collection)
        rows = self.session.execute(stmt).all()
        if not rows:
            return []
        top = max(float(r.rank) for r in rows) or 1.0
        return [self._to_span(r, lexical_score=min(1.0, float(r.rank) / top)) for r in rows]

    @staticmethod
    def _to_span(row, *, vector_score: float = 0.0,
                 lexical_score: float = 0.0) -> EvidenceSpan:
        return EvidenceSpan(
            chunk_id=row.id, document_id=row.document_id, document_title=row.title,
            text=row.text, vector_score=vector_score, lexical_score=lexical_score,
            section=row.section or "", page=row.page, collection=row.collection,
            sensitivity=row.sensitivity, effective_date=row.effective_date,
            version_label=row.version_label or "",
            quarantined=bool(row.injection_flagged),
            injection_patterns=list(row.injection_patterns or []),
        )


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

def rerank(query: str, spans: list[EvidenceSpan]) -> list[EvidenceSpan]:
    """Cross-feature rerank: retrieval score + query term coverage + section match.

    Quarantined spans are demoted so a poisoned document cannot dominate the
    evidence set purely by being lexically close to the question.
    """
    for span in spans:
        cover = coverage(query, span.text)
        overlap = weighted_overlap(query, span.text)
        heading_bonus = 0.08 if span.section and coverage(query, span.section) > 0.5 else 0.0
        length_penalty = 0.05 if len(span.text) < 80 else 0.0
        score = (
            0.50 * span.score
            + 0.28 * cover
            + 0.22 * overlap
            + heading_bonus
            - length_penalty
        )
        if span.quarantined:
            score *= 0.35
        span.rerank_score = round(score, 6)
    spans.sort(key=lambda s: (-s.rerank_score, str(s.document_id), s.chunk_id.hex))
    for span in spans:
        span.score = span.rerank_score
    return spans

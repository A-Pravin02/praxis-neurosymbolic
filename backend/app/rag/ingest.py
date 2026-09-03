"""Ingestion pipeline: source -> parse -> normalize -> chunk -> screen -> embed
-> store -> extract knowledge.

The pipeline is dataset-agnostic. Nothing here knows what the content *means*;
domain meaning is supplied by a `RecordMapping` (for structured data) or by the
registered text extractors. A new dataset needs an adapter, not a redesign.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit
from app.core.errors import PayloadTooLargeError, ValidationError
from app.core.logging import get_logger, log_event
from app.db.models.rag import Chunk, Document
from app.knowledge import store as knowledge_store
from app.knowledge.extraction import (
    ExtractionResult, RecordMapping, extract_from_records, extract_from_text,
)
from app.rag import injection
from app.rag.chunker import chunk_document
from app.rag.embeddings import get_embedder
from app.rag.parsers import ParsedDocument, parse_bytes
from app.tenancy.context import Principal

logger = get_logger("praxis.ingest")


@dataclass
class IngestOptions:
    collection: str = "default"
    sensitivity: str = "internal"
    required_permission: str | None = None
    effective_date: str | None = None
    version_label: str = ""
    extract_facts: bool = True
    fact_subject: str = "policy"
    record_mapping: RecordMapping | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    promote_rule_candidates: bool = False


@dataclass
class IngestReport:
    document_id: uuid.UUID
    title: str
    chunks: int = 0
    facts: int = 0
    rule_candidates: int = 0
    rules_created: int = 0
    quarantined_chunks: int = 0
    entities: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id), "title": self.title,
            "chunks": self.chunks, "facts": self.facts,
            "rule_candidates": self.rule_candidates, "rules_created": self.rules_created,
            "quarantined_chunks": self.quarantined_chunks, "entities": self.entities,
            "warnings": self.warnings,
        }


def ingest_bytes(session: Session, principal: Principal, data: bytes, *, filename: str,
                 media_type: str = "", title: str | None = None,
                 options: IngestOptions | None = None) -> IngestReport:
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"Documents are limited to {settings.max_upload_bytes // (1024 * 1024)} MB."
        )
    parsed = parse_bytes(data, filename=filename, media_type=media_type)
    if title:
        parsed.title = title
    return ingest_parsed(session, principal, parsed, raw_size=len(data),
                         checksum=hashlib.sha256(data).hexdigest(),
                         source_uri=filename, options=options)


def ingest_text(session: Session, principal: Principal, text: str, *, title: str,
                options: IngestOptions | None = None,
                source_uri: str = "") -> IngestReport:
    if not text.strip():
        raise ValidationError("The document is empty.")
    parsed = parse_bytes(text.encode("utf-8"), filename=f"{title}.md",
                         media_type="text/markdown")
    parsed.title = title
    return ingest_parsed(session, principal, parsed, raw_size=len(text.encode("utf-8")),
                         checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                         source_uri=source_uri, options=options)


def ingest_parsed(session: Session, principal: Principal, parsed: ParsedDocument, *,
                  raw_size: int, checksum: str, source_uri: str = "",
                  options: IngestOptions | None = None) -> IngestReport:
    options = options or IngestOptions()

    document = Document(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        uploaded_by=principal.user_id,
        title=(parsed.title or "Untitled")[:400],
        source_uri=source_uri[:1024],
        media_type=parsed.media_type,
        collection=options.collection,
        sensitivity=options.sensitivity,
        required_permission=options.required_permission,
        checksum=checksum,
        byte_size=raw_size,
        status="processing",
        effective_date=options.effective_date,
        version_label=options.version_label,
        metadata_json={**parsed.metadata, **options.metadata},
    )
    session.add(document)
    session.flush()

    report = IngestReport(document_id=document.id, title=document.title)

    try:
        chunks = chunk_document(parsed.sections)
        if not chunks:
            document.status = "empty"
            document.error = "No extractable text."
            session.flush()
            report.warnings.append("No extractable text was found in this document.")
            return report

        embedder = get_embedder()
        vectors = embedder.embed([c.text for c in chunks])

        chunk_rows: list[Chunk] = []
        for chunk, vector in zip(chunks, vectors):
            verdict = injection.scan(chunk.text)
            if verdict.flagged:
                report.quarantined_chunks += 1
            row = Chunk(
                id=uuid.uuid4(), tenant_id=principal.tenant_id, document_id=document.id,
                ordinal=chunk.ordinal, text=chunk.text,
                token_estimate=chunk.token_estimate, section=chunk.section[:300],
                page=chunk.page, embedding=vector,
                injection_flagged=verdict.flagged, injection_score=verdict.score,
                injection_patterns=verdict.patterns,
                metadata_json={"excerpts": verdict.excerpts} if verdict.flagged else {},
            )
            session.add(row)
            chunk_rows.append(row)
        session.flush()
        report.chunks = len(chunk_rows)

        if report.quarantined_chunks:
            audit.record(
                session, audit.INJECTION_DETECTED, tenant_id=principal.tenant_id,
                actor_user_id=principal.user_id, resource_type="document",
                resource_id=str(document.id), outcome="quarantined",
                detail={"chunks": report.quarantined_chunks, "title": document.title},
            )
            report.warnings.append(
                f"{report.quarantined_chunks} passage(s) contain instruction-like text and "
                f"were quarantined. They remain searchable as evidence but cannot direct "
                f"the assistant."
            )

        if options.extract_facts:
            extraction = _extract(parsed, chunk_rows, document, options)
            saved = knowledge_store.save_facts(
                session, principal.tenant_id, extraction.facts,
                replace_from_document=document.id,
            )
            report.facts = len(saved)
            report.rule_candidates = len(extraction.rule_candidates)
            entities = {f.subject for f in extraction.facts}
            for key in sorted(entities):
                knowledge_store.upsert_entity(
                    session, principal.tenant_id, key,
                    entity_type="policy" if key == "policy" else "record",
                )
            report.entities = len(entities)
            if extraction.rule_candidates:
                document.metadata_json = {
                    **(document.metadata_json or {}),
                    "rule_candidates": [c.as_dict() for c in extraction.rule_candidates][:50],
                }
                report.warnings.append(
                    f"{len(extraction.rule_candidates)} conditional statement(s) were found "
                    f"and recorded as rule candidates for review. Candidate rules are not "
                    f"activated automatically."
                )

        document.status = "ready"
        session.flush()
        audit.record(session, audit.DOCUMENT_UPLOAD, tenant_id=principal.tenant_id,
                     actor_user_id=principal.user_id, resource_type="document",
                     resource_id=str(document.id),
                     detail={"chunks": report.chunks, "facts": report.facts,
                             "collection": options.collection,
                             "sensitivity": options.sensitivity})
        log_event(logger, 20, "document ingested", document_id=str(document.id),
                  chunks=report.chunks, facts=report.facts,
                  quarantined=report.quarantined_chunks)
    except Exception as exc:
        document.status = "failed"
        document.error = f"{type(exc).__name__}"
        session.flush()
        logger.exception("ingestion failed")
        raise
    return report


def _extract(parsed: ParsedDocument, chunk_rows: list[Chunk], document: Document,
             options: IngestOptions) -> ExtractionResult:
    combined = ExtractionResult()

    if parsed.records:
        mapping = options.record_mapping or RecordMapping.infer(parsed.records)
        if mapping is not None:
            mapping.sensitivity = options.sensitivity
            mapping.required_permission = options.required_permission
            record_result = extract_from_records(
                parsed.records, mapping, document_id=document.id,
                document_title=document.title, effective_date=options.effective_date,
            )
            combined.facts.extend(record_result.facts)
            combined.skipped_sentences += record_result.skipped_sentences

    for row in chunk_rows:
        # A quarantined passage is not allowed to become a fact: text that is
        # trying to issue instructions is not a trustworthy assertion of value.
        if row.injection_flagged:
            continue
        text_result = extract_from_text(
            row.text, subject=options.fact_subject, document_id=document.id,
            chunk_id=row.id, document_title=document.title,
            effective_date=options.effective_date, sensitivity=options.sensitivity,
            required_permission=options.required_permission,
        )
        combined.facts.extend(text_result.facts)
        combined.rule_candidates.extend(text_result.rule_candidates)
        combined.skipped_sentences += text_result.skipped_sentences
    return combined


def delete_document(session: Session, principal: Principal,
                    document_id: uuid.UUID) -> None:
    from app.db.models.knowledge import Fact

    document = session.get(Document, document_id)
    if document is None or document.tenant_id != principal.tenant_id:
        from app.core.errors import NotFoundError
        raise NotFoundError()
    for fact in session.scalars(
        select(Fact).where(Fact.tenant_id == principal.tenant_id,
                           Fact.source_document_id == document_id)
    ).all():
        session.delete(fact)
    session.delete(document)
    session.flush()
    audit.record(session, audit.DOCUMENT_DELETE, tenant_id=principal.tenant_id,
                 actor_user_id=principal.user_id, resource_type="document",
                 resource_id=str(document_id))


def document_chunk_counts(session: Session, tenant_id: uuid.UUID,
                          document_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not document_ids:
        return {}
    rows = session.execute(
        select(Chunk.document_id, func.count(Chunk.id),
               func.count(Chunk.id).filter(Chunk.injection_flagged.is_(True)))
        .where(Chunk.tenant_id == tenant_id, Chunk.document_id.in_(document_ids))
        .group_by(Chunk.document_id)
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}

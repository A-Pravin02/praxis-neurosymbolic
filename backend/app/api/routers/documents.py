from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from app.api.schemas import (
    DocumentIngestTextRequest, DocumentOut, SearchHitOut, SearchRequest,
)
from app.config import settings
from app.core.deps import CurrentPrincipal, DbSession, require_permissions
from app.core.errors import NotFoundError, PayloadTooLargeError, ValidationError
from app.db.models.rag import Document
from app.policy.pdp import Resource
from app.policy import service as policy_service
from app.rag import ingest as ingest_service
from app.rag.retriever import HybridRetriever, RetrievalQuery
from app.tenancy.permissions import DOCUMENT_DELETE, DOCUMENT_READ, DOCUMENT_WRITE

router = APIRouter(prefix="/documents", tags=["documents"])

# Sensitivity labels a caller may assign at upload time. An arbitrary string here
# would let an uploader invent a class that no policy covers.
ALLOWED_SENSITIVITIES = {"public", "internal", "confidential", "hr_confidential"}


def _document_out(row: Document, chunks: int = 0, flagged: int = 0) -> DocumentOut:
    return DocumentOut(
        id=row.id, title=row.title, media_type=row.media_type, collection=row.collection,
        sensitivity=row.sensitivity, required_permission=row.required_permission,
        status=row.status, byte_size=row.byte_size, chunk_count=chunks,
        effective_date=row.effective_date, version_label=row.version_label,
        created_at=row.created_at, error=row.error, injection_flagged_chunks=flagged,
    )


@router.get("", response_model=list[DocumentOut],
            dependencies=[Depends(require_permissions(DOCUMENT_READ))])
def list_documents(db: DbSession, principal: CurrentPrincipal) -> list[DocumentOut]:
    from sqlalchemy import select

    rows = db.scalars(
        select(Document)
        .where(Document.tenant_id == principal.tenant_id,
               policy_service.permission_filter(principal, "document"),
               policy_service.compiled_filter(db, principal, "document"))
        .order_by(Document.created_at.desc())
        .limit(500)
    ).all()
    counts = ingest_service.document_chunk_counts(db, principal.tenant_id,
                                                  [r.id for r in rows])
    return [_document_out(r, *counts.get(r.id, (0, 0))) for r in rows]


@router.post("/text", response_model=DocumentOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permissions(DOCUMENT_WRITE))])
def ingest_text(payload: DocumentIngestTextRequest, db: DbSession,
                principal: CurrentPrincipal) -> DocumentOut:
    _check_sensitivity(payload.sensitivity, payload.required_permission, principal)
    report = ingest_service.ingest_text(
        db, principal, payload.text, title=payload.title,
        options=ingest_service.IngestOptions(
            collection=payload.collection, sensitivity=payload.sensitivity,
            required_permission=payload.required_permission,
            effective_date=payload.effective_date, version_label=payload.version_label,
        ),
    )
    row = db.get(Document, report.document_id)
    return _document_out(row, report.chunks, report.quarantined_chunks)


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_permissions(DOCUMENT_WRITE))])
def upload_document(
    db: DbSession, principal: CurrentPrincipal,
    file: UploadFile = File(...),
    collection: str = Form("default"),
    sensitivity: str = Form("internal"),
    required_permission: str | None = Form(None),
    effective_date: str | None = Form(None),
    version_label: str = Form(""),
    title: str | None = Form(None),
) -> DocumentOut:
    _check_sensitivity(sensitivity, required_permission, principal)
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"Documents are limited to {settings.max_upload_bytes // (1024 * 1024)} MB."
        )
    if not data:
        raise ValidationError("The uploaded file is empty.")
    report = ingest_service.ingest_bytes(
        db, principal, data, filename=file.filename or "upload",
        media_type=file.content_type or "", title=title,
        options=ingest_service.IngestOptions(
            collection=collection[:96], sensitivity=sensitivity,
            required_permission=required_permission,
            effective_date=effective_date, version_label=version_label[:64],
        ),
    )
    row = db.get(Document, report.document_id)
    return _document_out(row, report.chunks, report.quarantined_chunks)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_permissions(DOCUMENT_DELETE))])
def delete_document(document_id: uuid.UUID, db: DbSession,
                    principal: CurrentPrincipal) -> Response:
    ingest_service.delete_document(db, principal, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=list[SearchHitOut],
             dependencies=[Depends(require_permissions(DOCUMENT_READ))])
def search(payload: SearchRequest, db: DbSession,
           principal: CurrentPrincipal) -> list[SearchHitOut]:
    result = HybridRetriever(db, principal).retrieve(
        RetrievalQuery(text=payload.query, top_k=payload.top_k,
                       collection=payload.collection)
    )
    return [
        SearchHitOut(
            chunk_id=s.chunk_id, document_id=s.document_id,
            document_title=s.document_title, text=s.text, score=s.score,
            vector_score=s.vector_score, lexical_score=s.lexical_score,
            section=s.section, page=s.page, quarantined=s.quarantined,
        )
        for s in result.spans
    ]


def _check_sensitivity(sensitivity: str, required_permission: str | None,
                       principal) -> None:
    if sensitivity not in ALLOWED_SENSITIVITIES:
        raise ValidationError(
            "Unknown sensitivity label. Allowed: "
            + ", ".join(sorted(ALLOWED_SENSITIVITIES)) + "."
        )
    if required_permission and required_permission not in principal.permissions:
        # An uploader must not be able to lock a document behind a permission they
        # do not hold themselves - that is a route to hiding data from its owner.
        raise ValidationError(
            "You can only restrict a document to a permission you hold."
        )

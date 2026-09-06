#!/usr/bin/env python
"""Ingest a merged line-per-row dataset into its own workspace.

    python scripts/ingest_merged_dataset.py merged_dataset_body_00.csv

Creates (idempotently) a `research` workspace, then for each underlying source
file writes a dataset card as a retrievable document and stores the computed
schema, statistics and — for small tables — row-level facts.

    --tenant SLUG        workspace to ingest into (default: research)
    --limit N            only read the first N rows of the merged file
    --categories A,B     restrict to these domains
    --no-entity-facts    statistics only, no row-level triples
    --dry-run            report what would be written, write nothing
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import uuid
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.auth import passwords  # noqa: E402
from app.auth.service import provision_tenant_roles, resolve_principal  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models.rag import Document  # noqa: E402
from app.db.models.tenancy import (  # noqa: E402
    Role, Tenant, TenantLookup, User, UserLookup, UserRole,
)
from app.db.provision import ensure_permission_catalogue  # noqa: E402
from app.db.session import SessionLocal, bind_tenant  # noqa: E402
from app.ingestion.merged_dataset import (  # noqa: E402
    dataset_card, entity_facts, iter_source_files, prose_document, read_merged,
    schema_and_statistic_facts,
)
from app.knowledge import store as knowledge_store  # noqa: E402
from app.knowledge.schema import FactDTO, FactKind, Provenance, typed_value  # noqa: E402
from app.rag.ingest import (  # noqa: E402
    IngestOptions, delete_document, ingest_text,
)
from app.seed.demo import DEMO_PASSWORD  # noqa: E402

RESEARCH_USERS = [
    ("admin@research.test", "Research Admin", ["admin"]),
    ("analyst@research.test", "Research Analyst", ["analyst"]),
    ("viewer@research.test", "Research Viewer", ["employee"]),
]


def ensure_workspace(slug: str, name: str):
    """Create the workspace and its users if they are not already there."""
    ensure_permission_catalogue()
    session = SessionLocal()
    try:
        bind_tenant(session, None)
        routing = session.get(TenantLookup, slug)
        if routing is None:
            tenant_id = uuid.uuid4()
            bind_tenant(session, tenant_id)
            session.add(Tenant(id=tenant_id, slug=slug, name=name))
            session.add(TenantLookup(slug=slug, tenant_id=tenant_id, is_active=True))
            session.flush()
        else:
            tenant_id = routing.tenant_id
            bind_tenant(session, tenant_id)
        provision_tenant_roles(session, tenant_id)

        for email, display, role_names in RESEARCH_USERS:
            user = session.scalar(select(User).where(User.tenant_id == tenant_id,
                                                     User.email == email))
            if user is None:
                user = User(id=uuid.uuid4(), tenant_id=tenant_id, email=email,
                            display_name=display,
                            password_hash=passwords.hash_password(DEMO_PASSWORD),
                            attributes={})
                session.add(user)
                session.flush()
                session.add(UserLookup(email_ci=email, tenant_id=tenant_id,
                                       user_id=user.id))
                roles = session.scalars(
                    select(Role).where(Role.tenant_id == tenant_id,
                                       Role.name.in_(role_names))).all()
                for role in roles:
                    session.add(UserRole(user_id=user.id, role_id=role.id,
                                         tenant_id=tenant_id))
                session.flush()

        admin = session.scalar(select(User).where(User.tenant_id == tenant_id,
                                                  User.email == RESEARCH_USERS[0][0]))
        session.refresh(admin)
        principal = resolve_principal(session, admin)
        session.commit()
        return principal
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def drafts_to_facts(drafts, *, document_id, document_title, kind=FactKind.DIRECT):
    """Turn adapter triples into fact DTOs with computation provenance."""
    return [
        FactDTO(
            subject=draft.subject, predicate=draft.predicate,
            object=typed_value(draft.value, draft.unit), kind=kind, confidence=1.0,
            provenance=Provenance(
                source_kind="record", document_id=document_id,
                document_title=document_title, evidence_text=draft.evidence,
                extractor=draft.extractor,
            ),
        )
        for draft in drafts
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--tenant", default="research")
    parser.add_argument("--tenant-name", default="Research Workspace")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--categories", default=None)
    parser.add_argument("--no-entity-facts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary-out", type=pathlib.Path,
                        default=pathlib.Path("dataset_ingestion_summary.json"))
    args = parser.parse_args()

    configure_logging()
    if not args.path.exists():
        raise SystemExit(f"{args.path} does not exist.")

    categories = ({c.strip() for c in args.categories.split(",")}
                  if args.categories else None)

    print(f"reading {args.path} ...")
    started = time.perf_counter()
    files = read_merged(args.path, limit=args.limit, categories=categories)
    elapsed = time.perf_counter() - started
    sources = list(iter_source_files(files))
    print(f"read {len(sources)} source files in {elapsed:.1f}s")

    if args.dry_run:
        print(json.dumps([s.summary() for s in sources], indent=2)[:6000])
        return

    principal = ensure_workspace(args.tenant, args.tenant_name)
    print(f"workspace {args.tenant} ready (tenant {principal.tenant_id})")

    totals = Counter()
    per_file = []

    for index, source in enumerate(sources, start=1):
        session = SessionLocal()
        try:
            bind_tenant(session, principal.tenant_id, principal.user_id)
            title = f"Dataset card: {pathlib.PurePath(source.source_file).name}"
            existing = session.scalar(select(Document).where(
                Document.tenant_id == principal.tenant_id, Document.title == title))
            if existing is not None:
                # Through the service, so the document's facts go with it.
                # `Fact.source_document_id` is not a foreign key, so deleting the
                # row directly would leave orphaned facts behind - and on re-ingest
                # those become a contradiction against their own replacements.
                delete_document(session, principal, existing.id)
                session.flush()

            report = ingest_text(
                session, principal, dataset_card(source), title=title,
                source_uri=f"merged://{source.category}/{source.source_file}",
                options=IngestOptions(
                    collection=source.category.split("/")[0].strip().lower()[:96] or "data",
                    sensitivity="internal",
                    extract_facts=False,          # facts come from computation, not prose
                    metadata={"source_file": source.source_file,
                              "domain": source.category,
                              "rows": source.rows,
                              "columns": len(source.header)},
                ),
            )

            drafts = schema_and_statistic_facts(source)
            if not args.no_entity_facts:
                drafts += entity_facts(source)
            facts = drafts_to_facts(drafts, document_id=report.document_id,
                                    document_title=title)
            saved = knowledge_store.save_facts(
                session, principal.tenant_id, facts,
                replace_from_document=report.document_id)
            knowledge_store.upsert_entity(
                session, principal.tenant_id, f"dataset:{source.slug}",
                label=pathlib.PurePath(source.source_file).name, entity_type="dataset",
                attributes={"domain": source.category, "rows": source.rows})

            prose_chunks = 0
            if source.is_prose and source.prose_samples:
                prose_title = f"Text corpus: {pathlib.PurePath(source.source_file).name}"
                stale = session.scalar(select(Document).where(
                    Document.tenant_id == principal.tenant_id,
                    Document.title == prose_title))
                if stale is not None:
                    delete_document(session, principal, stale.id)
                    session.flush()
                prose_report = ingest_text(
                    session, principal, prose_document(source), title=prose_title,
                    source_uri=f"merged://{source.category}/{source.source_file}#text",
                    options=IngestOptions(
                        collection="text-corpora", sensitivity="internal",
                        extract_facts=False,
                        metadata={"source_file": source.source_file,
                                  "domain": source.category}),
                )
                prose_chunks = prose_report.chunks
                totals["prose_documents"] += 1

            session.commit()

            totals["documents"] += 1 + (1 if prose_chunks else 0)
            totals["chunks"] += report.chunks + prose_chunks
            totals["facts"] += len(saved)
            totals["rows_described"] += source.rows
            per_file.append({
                "source_file": source.source_file, "domain": source.category,
                "slug": source.slug, "rows": source.rows,
                "columns": len(source.header), "facts": len(saved),
                "chunks": report.chunks + prose_chunks,
            })
            print(f"  [{index:>2}/{len(sources)}] {source.source_file[:46]:<48} "
                  f"rows={source.rows:>7,}  facts={len(saved):>6,}  "
                  f"chunks={report.chunks + prose_chunks:>3}")
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            totals["failed"] += 1
            print(f"  [{index:>2}/{len(sources)}] {source.source_file}: "
                  f"FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        finally:
            session.close()

    summary = {"tenant": args.tenant, "totals": dict(totals), "files": per_file}
    args.summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + json.dumps(dict(totals), indent=2))
    print(f"summary written: {args.summary_out}")


if __name__ == "__main__":
    main()

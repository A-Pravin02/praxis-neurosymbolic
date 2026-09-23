#!/usr/bin/env python
"""Ingest the demonstration tenant's policy documents into the RAG store.

    python scripts/ingest_demo_policies.py --tenant meridian

The seeded workspaces carry users, rules and facts but no documents, so retrieval has
nothing to retrieve until this runs. It reads the same policy files the knowledge pack
serves and puts them through the real ingestion path - parse, chunk, embed, store - so
what the demo retrieves is a genuinely ingested document rather than a fixture written
straight into the table.

Idempotent: a document whose title is already present for the tenant is skipped, so
re-running after a failure does not duplicate the corpus.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _data_root() -> pathlib.Path:
    """Where the policy files live, in the repository or inside the container.

    The repository keeps them under backend/data; the image copies them to /app/data.
    Checking both lets one script serve `python scripts/...` and `docker compose exec`
    without a second copy of it drifting out of step.
    """
    for candidate in (ROOT / "backend" / "data", ROOT / "data",
                      pathlib.Path("/app/data")):
        if candidate.is_dir():
            return candidate
    raise SystemExit("cannot find the data directory")

from sqlalchemy import select  # noqa: E402

from app.core.logging import configure_logging  # noqa: E402
from app.db.models.rag import Document  # noqa: E402
from app.db.models.tenancy import TenantLookup  # noqa: E402
from app.db.session import SessionLocal, bind_tenant  # noqa: E402
from app.rag.ingest import ingest_text  # noqa: E402
from app.tenancy.context import Principal  # noqa: E402

DATA = _data_root()


def documents_for(tenant: str) -> list[pathlib.Path]:
    directory = DATA / tenant / "policies"
    if not directory.is_dir():
        raise SystemExit(f"no policy directory for tenant {tenant!r} at {directory}")
    return sorted(directory.glob("*.md"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="meridian")
    parser.add_argument("--email", default="", help="ingest as this user (default: first admin)")
    args = parser.parse_args()
    configure_logging()

    files = documents_for(args.tenant)
    print(f"{len(files)} policy file(s) for {args.tenant}")

    with SessionLocal() as session:
        lookup = session.execute(
            select(TenantLookup).where(TenantLookup.slug == args.tenant)).scalar_one_or_none()
        if lookup is None:
            raise SystemExit(f"tenant {args.tenant!r} does not exist; run the demo seed first")
        bind_tenant(session, lookup.tenant_id, None)

        principal = _principal(session, lookup, args.email)
        existing = {title for (title,) in session.execute(
            select(Document.title).where(Document.tenant_id == lookup.tenant_id))}

        ingested = skipped = 0
        for path in files:
            title = path.stem.replace("_", " ").title()
            if title in existing:
                print(f"  skip   {title} (already present)")
                skipped += 1
                continue
            # The row-level-security context is set with set_config(..., true), which is
            # transaction-local, so committing each document clears it. Re-binding before
            # every document keeps each one inside a properly scoped transaction instead
            # of letting the second insert arrive with an empty app.user_id.
            bind_tenant(session, lookup.tenant_id, principal.user_id)
            report = ingest_text(session, principal, path.read_text(encoding="utf-8"),
                                 title=title, source_uri=f"file://{path.name}")
            session.commit()
            print(f"  ingest {title}: {report.chunks} chunk(s)")
            ingested += 1

    print(f"\ningested {ingested}, skipped {skipped}")
    return 0


def _principal(session, lookup, email: str) -> Principal:
    """An ingesting identity that really exists in the tenant.

    Ingestion is authorization-aware, so it needs a principal carrying real permissions.
    Those come from `resolve_principal`, the same function the API uses after a login, so
    this script cannot grant itself rights a logged-in user would not have - it has to
    pick a seeded user who already holds document:write.
    """
    from app.auth.service import resolve_principal
    from app.db.models.tenancy import User
    from app.tenancy.permissions import DOCUMENT_WRITE

    query = select(User).where(User.tenant_id == lookup.tenant_id)
    if email:
        query = query.where(User.email == email)
    for user in session.execute(query.order_by(User.created_at)).scalars():
        principal = resolve_principal(session, user)
        if principal.has_permission(DOCUMENT_WRITE):
            print(f"ingesting as {user.email} (roles: {', '.join(sorted(principal.roles))})")
            return principal
    raise SystemExit(
        f"no user in {lookup.slug!r} holds {DOCUMENT_WRITE}; run the demo seed first"
        + (f" (looked only at {email})" if email else ""))


if __name__ == "__main__":
    raise SystemExit(main())

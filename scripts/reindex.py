#!/usr/bin/env python
"""Re-embed a tenant's chunks and rebuild the vector index.

Needed after changing EMBEDDING_PROVIDER, and worth running after a bulk
ingestion because ivfflat builds a better index once there is data.

    python scripts/reindex.py --tenant acme
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select, text  # noqa: E402

from app.core.logging import configure_logging  # noqa: E402
from app.db.models.rag import Chunk  # noqa: E402
from app.db.models.tenancy import TenantLookup  # noqa: E402
from app.db.session import SessionLocal, admin_engine, bind_tenant  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402

BATCH = 200


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="acme")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="also REINDEX the ivfflat index (needs the owner role)")
    args = parser.parse_args()
    configure_logging()

    session = SessionLocal()
    try:
        bind_tenant(session, None)
        routing = session.get(TenantLookup, args.tenant)
        if routing is None:
            raise SystemExit(f"No workspace with slug {args.tenant!r}.")
        bind_tenant(session, routing.tenant_id)

        embedder = get_embedder()
        chunks = session.scalars(select(Chunk).order_by(Chunk.created_at)).all()
        print(f"re-embedding {len(chunks)} chunks with {embedder.name}/{embedder.model}")

        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            for chunk, vector in zip(batch, embedder.embed([c.text for c in batch])):
                chunk.embedding = vector
            session.flush()
            print(f"  {min(start + BATCH, len(chunks))}/{len(chunks)}")
        session.commit()
    finally:
        session.close()

    if args.rebuild_index:
        with admin_engine.connect() as conn:
            conn.execute(text("REINDEX INDEX ix_chunks_embedding_cos"))
            conn.commit()
        print("vector index rebuilt")


if __name__ == "__main__":
    main()

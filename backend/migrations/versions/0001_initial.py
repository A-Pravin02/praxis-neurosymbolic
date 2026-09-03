"""initial schema, pgvector, lexical index, row level security

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

from app.config import settings
from app.db.models import TENANT_SCOPED_TABLES, Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "praxis_app"


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    Base.metadata.create_all(bind=bind)

    # --- lexical search column + indexes on chunks -------------------------
    op.execute(
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text,''))) STORED"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_text_trgm ON chunks "
        "USING gin (text gin_trgm_ops)"
    )
    # ivfflat needs data to build well; lists=100 is fine for the demo corpus and
    # is rebuilt by scripts/reindex.py after bulk ingestion.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_chunks_embedding_cos ON chunks "
        f"USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # --- row level security -------------------------------------------------
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE also applies the policy to the table owner, so a mistake in role
        # configuration cannot silently disable isolation.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    # tenants: a session may only see its own tenant row.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenants_self ON tenants")
    op.execute(
        "CREATE POLICY tenants_self ON tenants "
        "USING (id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (id = current_setting('app.tenant_id', true)::uuid)"
    )

    # audit_events: readable only within the tenant; writable always (system events
    # may have no tenant yet, e.g. a failed login for an unknown email).
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_events_read ON audit_events")
    op.execute(
        "CREATE POLICY audit_events_read ON audit_events FOR SELECT "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("DROP POLICY IF EXISTS audit_events_write ON audit_events")
    op.execute(
        "CREATE POLICY audit_events_write ON audit_events FOR INSERT WITH CHECK (true)"
    )

    # --- least privilege grants for the application role --------------------
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    # The app must never create or alter schema objects.
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM PUBLIC")
    # Global catalogue tables the app only reads.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON permissions FROM {APP_ROLE}")


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)

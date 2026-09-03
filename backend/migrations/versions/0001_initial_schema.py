"""initial schema: tables, pgvector, lexical index, row level security, grants

This baseline is explicit DDL rather than a call to ``metadata.create_all``.
A migration that reads the live model metadata silently absorbs every later
model change, which makes the second migration collide with the first on a
fresh database. Explicit DDL keeps each revision a fixed point in history.

Revision ID: 0001
Revises: 
Create Date: 2026-09-04 03:06:54.639154
"""
from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


APP_ROLE = "praxis_app"

# Tables that carry tenant_id and must be protected by row level security.
TENANT_SCOPED_TABLES = [
    "users", "roles", "user_roles", "conversations", "messages", "documents", "chunks",
    "entities", "facts", "claims", "answers", "provenance_records", "rules",
    "constraint_rules", "policies", "access_constraints", "eval_runs",
    "eval_case_results", "refresh_tokens",
]


def _require_extensions() -> None:
    """Extensions are installed by the container init script as the superuser.

    Creating them here is a convenience for databases provisioned another way; a
    missing extension must produce a clear message rather than a confusing
    failure further down.
    """
    bind = op.get_bind()
    for extension in ("vector", "pg_trgm"):
        try:
            with bind.begin_nested():
                op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        except Exception as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                f"The '{extension}' extension is not installed and the migration role "
                f"cannot create it. Run: CREATE EXTENSION {extension}; as a superuser "
                f"in this database first."
            ) from exc


def _lexical_and_vector_indexes() -> None:
    # A generated column keeps the lexical index in step with the text; there is
    # no application code that can forget to update it.
    op.execute(
        "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text,''))) STORED"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_text_trgm ON chunks "
        "USING gin (text gin_trgm_ops)"
    )
    # ivfflat builds better once there is data; scripts/reindex.py rebuilds it
    # after a bulk ingestion.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_cos ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def _row_level_security() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE applies the policy to the table owner too, so a mistake in role
        # configuration cannot silently disable isolation.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    # A session may only see its own tenant row.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenants_self ON tenants")
    op.execute(
        "CREATE POLICY tenants_self ON tenants "
        "USING (id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (id = current_setting('app.tenant_id', true)::uuid)"
    )

    # Audit events are readable within the tenant and writable always: a failed
    # login for an unknown email has no tenant yet, and must still be recorded.
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


def _least_privilege_grants() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
        f"TO {APP_ROLE}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    # The application must never create or alter schema objects.
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {APP_ROLE}")
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    # The permission catalogue is provisioned by the owner, read by the app.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON permissions FROM {APP_ROLE}")


def upgrade() -> None:
    _require_extensions()
    op.create_table('audit_events',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=True),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=96), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('resource_id', sa.String(length=96), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('client_ip', sa.String(length=64), nullable=False),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index('ix_audit_events_action', 'audit_events', ['action'], unique=False)
    op.create_index('ix_audit_events_tenant_created', 'audit_events', ['tenant_id', 'created_at'], unique=False)
    op.create_table('permissions',
    sa.Column('name', sa.String(length=96), nullable=False),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    sa.UniqueConstraint('name', name=op.f('uq_permissions_name'))
    )
    op.create_table('tenants',
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('settings_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tenants')),
    sa.UniqueConstraint('slug', name=op.f('uq_tenants_slug'))
    )
    op.create_table('access_constraints',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=False),
    sa.Column('applies_to_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expression_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_access_constraints_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_constraints')),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_access_constraints_tenant_code')
    )
    op.create_table('answers',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('conversation_id', sa.UUID(), nullable=True),
    sa.Column('message_id', sa.UUID(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer_text', sa.Text(), nullable=False),
    sa.Column('route', sa.String(length=32), nullable=False),
    sa.Column('gate_decision', sa.String(length=32), nullable=False),
    sa.Column('verification_status', sa.String(length=32), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('pipeline_mode', sa.String(length=32), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('trace_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_answers_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_answers'))
    )
    op.create_index('ix_answers_tenant_message', 'answers', ['tenant_id', 'message_id'], unique=False)
    op.create_table('claims',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('answer_id', sa.UUID(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('claim_kind', sa.String(length=24), nullable=False),
    sa.Column('support_score', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('supporting_fact_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('supporting_chunk_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_claims_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claims'))
    )
    op.create_index('ix_claims_tenant_answer', 'claims', ['tenant_id', 'answer_id'], unique=False)
    op.create_table('constraint_rules',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('expression_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('severity', sa.String(length=24), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_constraint_rules_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_constraint_rules')),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_constraint_rules_tenant_code')
    )
    op.create_table('documents',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('uploaded_by', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=400), nullable=False),
    sa.Column('source_uri', sa.String(length=1024), nullable=False),
    sa.Column('media_type', sa.String(length=64), nullable=False),
    sa.Column('collection', sa.String(length=96), nullable=False),
    sa.Column('sensitivity', sa.String(length=48), nullable=False),
    sa.Column('required_permission', sa.String(length=96), nullable=True),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('effective_date', sa.String(length=32), nullable=True),
    sa.Column('version_label', sa.String(length=64), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_documents_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index('ix_documents_tenant_collection', 'documents', ['tenant_id', 'collection'], unique=False)
    op.create_index('ix_documents_tenant_status', 'documents', ['tenant_id', 'status'], unique=False)
    op.create_table('entities',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_entities_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_entities')),
    sa.UniqueConstraint('tenant_id', 'key', name='uq_entities_tenant_key')
    )
    op.create_index('ix_entities_tenant_type', 'entities', ['tenant_id', 'entity_type'], unique=False)
    op.create_table('eval_runs',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('suite', sa.String(length=96), nullable=False),
    sa.Column('mode', sa.String(length=48), nullable=False),
    sa.Column('llm_provider', sa.String(length=48), nullable=False),
    sa.Column('llm_model', sa.String(length=96), nullable=False),
    sa.Column('case_count', sa.Integer(), nullable=False),
    sa.Column('metrics_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_eval_runs_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_runs'))
    )
    op.create_index('ix_eval_runs_tenant', 'eval_runs', ['tenant_id'], unique=False)
    op.create_table('facts',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('predicate', sa.String(length=120), nullable=False),
    sa.Column('object_value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('valid_from', sa.String(length=32), nullable=True),
    sa.Column('valid_to', sa.String(length=32), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('source_document_id', sa.UUID(), nullable=True),
    sa.Column('source_chunk_id', sa.UUID(), nullable=True),
    sa.Column('source_kind', sa.String(length=32), nullable=False),
    sa.Column('extractor', sa.String(length=64), nullable=False),
    sa.Column('evidence_text', sa.Text(), nullable=False),
    sa.Column('derivation', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('required_permission', sa.String(length=96), nullable=True),
    sa.Column('sensitivity', sa.String(length=48), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_facts_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_facts'))
    )
    op.create_index('ix_facts_tenant_kind', 'facts', ['tenant_id', 'kind'], unique=False)
    op.create_index('ix_facts_tenant_predicate', 'facts', ['tenant_id', 'predicate'], unique=False)
    op.create_index('ix_facts_tenant_subject', 'facts', ['tenant_id', 'subject'], unique=False)
    op.create_table('policies',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('effect', sa.String(length=8), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('subject_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('subject_permissions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('actions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('resource_types', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('condition_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('obligations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_policies_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_policies')),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_policies_tenant_code')
    )
    op.create_index('ix_policies_tenant_active', 'policies', ['tenant_id', 'is_active'], unique=False)
    op.create_table('provenance_records',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('answer_id', sa.UUID(), nullable=False),
    sa.Column('graph_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_provenance_records_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_provenance_records'))
    )
    op.create_index('ix_provenance_tenant_answer', 'provenance_records', ['tenant_id', 'answer_id'], unique=False)
    op.create_table('roles',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_roles_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    sa.UniqueConstraint('tenant_id', 'name', name='uq_roles_tenant_name')
    )
    op.create_index(op.f('ix_roles_tenant_id'), 'roles', ['tenant_id'], unique=False)
    op.create_table('rules',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('body_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('head_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('source_document_id', sa.UUID(), nullable=True),
    sa.Column('source_chunk_id', sa.UUID(), nullable=True),
    sa.Column('origin', sa.String(length=32), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_rules_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rules')),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_rules_tenant_code')
    )
    op.create_index('ix_rules_tenant_active', 'rules', ['tenant_id', 'is_active'], unique=False)
    op.create_table('tenant_lookup',
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_tenant_lookup_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('slug', name=op.f('pk_tenant_lookup'))
    )
    op.create_table('users',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('failed_login_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_users_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email')
    )
    op.create_index('ix_users_email_lookup', 'users', ['email'], unique=False)
    op.create_index(op.f('ix_users_tenant_id'), 'users', ['tenant_id'], unique=False)
    op.create_table('chunks',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('token_estimate', sa.Integer(), nullable=False),
    sa.Column('section', sa.String(length=300), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('tsv', postgresql.TSVECTOR(), sa.Computed("to_tsvector('english', coalesce(text,''))", persisted=True), nullable=True),
    sa.Column('injection_flagged', sa.Boolean(), nullable=False),
    sa.Column('injection_score', sa.Float(), nullable=False),
    sa.Column('injection_patterns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_chunks_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_chunks_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chunks')),
    sa.UniqueConstraint('document_id', 'ordinal', name='uq_chunks_document_ordinal')
    )
    op.create_index('ix_chunks_document', 'chunks', ['document_id'], unique=False)
    op.create_index('ix_chunks_tenant', 'chunks', ['tenant_id'], unique=False)
    op.create_table('conversations',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('is_archived', sa.Boolean(), nullable=False),
    sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_conversations_tenant_id_tenants'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_conversations_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index('ix_conversations_tenant_user', 'conversations', ['tenant_id', 'user_id'], unique=False)
    op.create_table('eval_case_results',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('case_id', sa.String(length=96), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer_text', sa.Text(), nullable=False),
    sa.Column('expected_kind', sa.String(length=32), nullable=False),
    sa.Column('correct', sa.Boolean(), nullable=False),
    sa.Column('grounded', sa.Boolean(), nullable=False),
    sa.Column('hallucinated', sa.Boolean(), nullable=False),
    sa.Column('abstained', sa.Boolean(), nullable=False),
    sa.Column('citation_correct', sa.Boolean(), nullable=False),
    sa.Column('unsupported_claims', sa.Integer(), nullable=False),
    sa.Column('total_claims', sa.Integer(), nullable=False),
    sa.Column('retrieval_precision', sa.Float(), nullable=False),
    sa.Column('retrieval_recall', sa.Float(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('detail_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['eval_runs.id'], name=op.f('fk_eval_case_results_run_id_eval_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_eval_case_results_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eval_case_results'))
    )
    op.create_index('ix_eval_case_results_run', 'eval_case_results', ['run_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=300), nullable=False),
    sa.Column('client_ip', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_refresh_tokens_tenant_id_tenants'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index('ix_refresh_tokens_family', 'refresh_tokens', ['family_id'], unique=False)
    op.create_index('ix_refresh_tokens_user', 'refresh_tokens', ['user_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], name=op.f('fk_role_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permissions'))
    )
    op.create_table('user_lookup',
    sa.Column('email_ci', sa.String(length=320), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_user_lookup_tenant_id_tenants'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_lookup_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('email_ci', name=op.f('pk_user_lookup'))
    )
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_user_roles_tenant_id_tenants'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_roles'))
    )
    op.create_index(op.f('ix_user_roles_tenant_id'), 'user_roles', ['tenant_id'], unique=False)
    op.create_table('messages',
    sa.Column('tenant_id', sa.UUID(), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('route', sa.String(length=32), nullable=True),
    sa.Column('verification_status', sa.String(length=32), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('trace_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_messages_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    op.create_index('ix_messages_conversation_seq', 'messages', ['conversation_id', 'sequence'], unique=False)
    op.create_index('ix_messages_tenant', 'messages', ['tenant_id'], unique=False)
    _lexical_and_vector_indexes()
    _row_level_security()
    _least_privilege_grants()


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_messages_tenant', table_name='messages')
    op.drop_index('ix_messages_conversation_seq', table_name='messages')
    op.drop_table('messages')
    op.drop_index(op.f('ix_user_roles_tenant_id'), table_name='user_roles')
    op.drop_table('user_roles')
    op.drop_table('user_lookup')
    op.drop_table('role_permissions')
    op.drop_index('ix_refresh_tokens_user', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_eval_case_results_run', table_name='eval_case_results')
    op.drop_table('eval_case_results')
    op.drop_index('ix_conversations_tenant_user', table_name='conversations')
    op.drop_table('conversations')
    op.drop_index('ix_chunks_tenant', table_name='chunks')
    op.drop_index('ix_chunks_document', table_name='chunks')
    op.drop_table('chunks')
    op.drop_index(op.f('ix_users_tenant_id'), table_name='users')
    op.drop_index('ix_users_email_lookup', table_name='users')
    op.drop_table('users')
    op.drop_table('tenant_lookup')
    op.drop_index('ix_rules_tenant_active', table_name='rules')
    op.drop_table('rules')
    op.drop_index(op.f('ix_roles_tenant_id'), table_name='roles')
    op.drop_table('roles')
    op.drop_index('ix_provenance_tenant_answer', table_name='provenance_records')
    op.drop_table('provenance_records')
    op.drop_index('ix_policies_tenant_active', table_name='policies')
    op.drop_table('policies')
    op.drop_index('ix_facts_tenant_subject', table_name='facts')
    op.drop_index('ix_facts_tenant_predicate', table_name='facts')
    op.drop_index('ix_facts_tenant_kind', table_name='facts')
    op.drop_table('facts')
    op.drop_index('ix_eval_runs_tenant', table_name='eval_runs')
    op.drop_table('eval_runs')
    op.drop_index('ix_entities_tenant_type', table_name='entities')
    op.drop_table('entities')
    op.drop_index('ix_documents_tenant_status', table_name='documents')
    op.drop_index('ix_documents_tenant_collection', table_name='documents')
    op.drop_table('documents')
    op.drop_table('constraint_rules')
    op.drop_index('ix_claims_tenant_answer', table_name='claims')
    op.drop_table('claims')
    op.drop_index('ix_answers_tenant_message', table_name='answers')
    op.drop_table('answers')
    op.drop_table('access_constraints')
    op.drop_table('tenants')
    op.drop_table('permissions')
    op.drop_index('ix_audit_events_tenant_created', table_name='audit_events')
    op.drop_index('ix_audit_events_action', table_name='audit_events')
    op.drop_table('audit_events')
    # ### end Alembic commands ###

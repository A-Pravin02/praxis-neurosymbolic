"""tenant slug routing table

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "praxis_app"


def upgrade() -> None:
    op.create_table(
        "tenant_lookup",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    # Backfill from any tenants that already exist.
    op.execute(
        "INSERT INTO tenant_lookup (slug, tenant_id, is_active) "
        "SELECT slug, id, is_active FROM tenants "
        "ON CONFLICT (slug) DO NOTHING"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_lookup TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("tenant_lookup")

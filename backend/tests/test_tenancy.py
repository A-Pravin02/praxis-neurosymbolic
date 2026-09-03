"""Multi-tenancy: row level security, least privilege, cross-tenant isolation."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.db.models.chat import Conversation
from app.db.models.knowledge import Fact
from app.db.models.rag import Chunk, Document
from app.db.models.tenancy import User
from app.db.models import TENANT_SCOPED_TABLES
from app.db.session import SessionLocal, admin_engine, bind_tenant, engine, tenant_session
from tests.conftest import auth_headers


# --- database role posture (permanent regression tests) --------------------

def test_application_role_is_not_a_superuser_and_cannot_bypass_rls():
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname = current_user"
        )).one()
    assert row.rolsuper is False, "the application role must not be a superuser"
    assert row.rolbypassrls is False, "the application role must not bypass RLS"
    assert row.rolcreatedb is False
    assert row.rolcreaterole is False


def test_application_role_does_not_own_the_tables():
    """If the app role owned the tables it could ALTER away row level security."""
    with engine.connect() as conn:
        owned = conn.execute(text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tableowner = current_user"
        )).scalars().all()
    assert owned == [], f"application role owns tables: {owned}"


def test_application_role_cannot_create_tables():
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text("CREATE TABLE should_not_exist (id int)"))
            conn.commit()


def test_application_role_cannot_write_the_permission_catalogue():
    with engine.connect() as conn:
        with pytest.raises(Exception):
            conn.execute(text(
                "INSERT INTO permissions (id, name, description, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'evil:permission', '', now(), now())"))
            conn.commit()


def test_every_tenant_scoped_table_has_forced_rls():
    with admin_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )).all()
    state = {r[0]: (r[1], r[2]) for r in rows}
    for table in TENANT_SCOPED_TABLES + ["tenants", "audit_events"]:
        assert table in state, f"missing table {table}"
        enabled, forced = state[table]
        assert enabled, f"row level security is not enabled on {table}"
        assert forced, f"row level security is not FORCEd on {table}"


def test_every_tenant_scoped_table_has_an_isolation_policy():
    with admin_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT tablename, policyname, qual FROM pg_policies "
            "WHERE schemaname = 'public'"
        )).all()
    by_table: dict[str, list[str]] = {}
    for table, policy, qual in rows:
        by_table.setdefault(table, []).append(qual or "")
    for table in TENANT_SCOPED_TABLES:
        assert table in by_table, f"{table} has no row level security policy"
        assert any("app.tenant_id" in q for q in by_table[table]), (
            f"{table} has a policy that does not key on app.tenant_id")


# --- isolation behaviour ---------------------------------------------------

def test_unbound_session_sees_nothing(alice):
    db = SessionLocal()
    try:
        bind_tenant(db, None)
        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(Document)).all() == []
        assert db.scalars(select(Fact)).all() == []
    finally:
        db.close()


def test_tenant_sees_only_its_own_rows(alice, carol):
    with tenant_session(alice.tenant_id) as db:
        emails = {u.email for u in db.scalars(select(User)).all()}
    assert "alice@acme.test" in emails
    assert "carol@globex.test" not in emails

    with tenant_session(carol.tenant_id) as db:
        emails = {u.email for u in db.scalars(select(User)).all()}
    assert emails == {"admin@globex.test", "carol@globex.test"}


def test_primary_key_lookup_across_tenants_returns_nothing(alice, carol):
    with tenant_session(alice.tenant_id) as db:
        assert db.get(User, carol.user_id) is None
    with tenant_session(carol.tenant_id) as db:
        assert db.get(User, alice.user_id) is None


def test_raw_sql_cannot_escape_the_policy(alice):
    with tenant_session(alice.tenant_id) as db:
        total = db.execute(text("SELECT count(*) FROM documents")).scalar()
        globex = db.execute(text(
            "SELECT count(*) FROM documents WHERE title LIKE 'Globex%'")).scalar()
    assert total > 0
    assert globex == 0


def test_writing_into_another_tenant_is_rejected(alice, carol):
    with pytest.raises(Exception):
        with tenant_session(alice.tenant_id) as db:
            db.add(Conversation(tenant_id=carol.tenant_id, user_id=carol.user_id,
                                title="cross-tenant write"))
            db.flush()


def test_documents_and_chunks_are_isolated(alice, carol):
    with tenant_session(alice.tenant_id) as db:
        acme_chunks = db.scalars(select(Chunk)).all()
    with tenant_session(carol.tenant_id) as db:
        globex_chunks = db.scalars(select(Chunk)).all()
    assert acme_chunks and globex_chunks
    assert not ({c.id for c in acme_chunks} & {c.id for c in globex_chunks})


def test_facts_are_isolated(alice, carol):
    with tenant_session(alice.tenant_id) as db:
        acme = {(f.subject, f.predicate, f.object_value["value"])
                for f in db.scalars(select(Fact)).all()}
    with tenant_session(carol.tenant_id) as db:
        globex = {(f.subject, f.predicate, f.object_value["value"])
                  for f in db.scalars(select(Fact)).all()}
    assert ("policy", "annual_leave_days", 20.0) in acme
    assert ("policy", "annual_leave_days", 20.0) not in globex
    assert ("policy", "annual_leave_days", 25.0) in globex


def test_tenant_context_does_not_leak_between_pooled_sessions(alice, carol):
    """set_config(..., is_local => true) must not survive into the next session."""
    for _ in range(3):
        with tenant_session(carol.tenant_id) as db:
            assert db.scalars(select(User)).all()
        db = SessionLocal()
        try:
            bind_tenant(db, None)
            assert db.scalars(select(User)).all() == []
        finally:
            db.close()


# --- API level -------------------------------------------------------------

def test_conversation_ids_are_not_readable_across_tenants(client):
    acme = auth_headers(client, "alice@acme.test")
    globex = auth_headers(client, "carol@globex.test")
    created = client.post("/api/conversations", headers=acme,
                          json={"title": "Acme private"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    # The owner sees it.
    assert client.get(f"/api/conversations/{conversation_id}",
                      headers=acme).status_code == 200
    # Another tenant gets 404, not 403: existence itself is not disclosed.
    other = client.get(f"/api/conversations/{conversation_id}", headers=globex)
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "not_found"


def test_users_cannot_read_each_others_conversations_within_a_tenant(client):
    alice = auth_headers(client, "alice@acme.test")
    bob = auth_headers(client, "bob@acme.test")
    conversation_id = client.post("/api/conversations", headers=alice,
                                  json={"title": "Alice only"}).json()["id"]
    assert client.get(f"/api/conversations/{conversation_id}/messages",
                      headers=bob).status_code == 404


def test_documents_endpoint_is_tenant_scoped(client):
    acme = client.get("/api/documents",
                      headers=auth_headers(client, "alice@acme.test")).json()
    globex = client.get("/api/documents",
                        headers=auth_headers(client, "carol@globex.test")).json()
    acme_titles = {d["title"] for d in acme}
    globex_titles = {d["title"] for d in globex}
    assert acme_titles and globex_titles
    assert not (acme_titles & globex_titles)


# --- migrations ------------------------------------------------------------

def test_migrations_match_the_models():
    """`alembic upgrade head` must produce exactly what the models describe.

    This is the regression test for a real defect: the first migration used
    `Base.metadata.create_all`, so every later model change was silently absorbed
    into revision 0001 and the *second* migration then collided with it on a
    fresh database. Autogenerate diffing the live schema against the models
    catches that drift the moment it appears.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db.models import Base

    with admin_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)

    # The generated tsvector column and the ivfflat/GIN indexes are created with
    # raw DDL, so ignore index-only differences on `chunks`.
    significant = [
        entry for entry in diff
        if not (isinstance(entry, tuple) and entry and "index" in str(entry[0]).lower())
    ]
    assert significant == [], f"models and migrations have drifted: {significant}"

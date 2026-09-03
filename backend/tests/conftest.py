"""Test fixtures.

The suite runs against a real PostgreSQL database with pgvector and row level
security, because the isolation guarantees under test are database behaviour.
A SQLite substitute would test nothing that matters here.

Environment is configured before any application module is imported, so the
settings singleton picks up the test database.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://praxis_app:praxis_app_pw@localhost:5432/praxis_test")
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "postgresql+psycopg://praxis_owner:praxis_owner_pw@localhost:5432/praxis_test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production-0123456789")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("EMBEDDING_PROVIDER", "hashing")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import service as auth_service  # noqa: E402
from app.core import ratelimit  # noqa: E402
from app.db.session import SessionLocal, admin_engine, bind_tenant, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import demo as demo_seed  # noqa: E402
from app.tenancy.context import Principal  # noqa: E402

DEMO_PASSWORD = demo_seed.DEMO_PASSWORD


def _run_migrations() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT, capture_output=True, text=True, env={**os.environ},
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    """Bring the test schema up to date, clear it, then seed the demo workspaces.

    The schema is migrated rather than dropped: pgvector and pg_trgm are installed
    into `public` by the container's init script as the superuser, and the
    migration role deliberately cannot create extensions.
    """
    _run_migrations()
    with admin_engine.connect() as conn:
        conn.execute(text(
            "TRUNCATE tenants, permissions, user_lookup, audit_events "
            "RESTART IDENTITY CASCADE"))
        conn.commit()
    demo_seed.seed_all()
    yield
    engine.dispose()
    admin_engine.dispose()


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> None:
    ratelimit.reset()
    yield


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session():
    """A raw session with no tenant bound. Bind one explicitly in the test."""
    db = SessionLocal()
    try:
        yield db
        db.rollback()
    finally:
        db.close()


def login(client: TestClient, email: str, password: str = DEMO_PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(client: TestClient, email: str, password: str = DEMO_PASSWORD) -> dict:
    return {"Authorization": f"Bearer {login(client, email)['tokens']['access_token']}"}


def principal_for(email: str) -> Principal:
    from app.db.models.tenancy import User, UserLookup

    db = SessionLocal()
    try:
        bind_tenant(db, None)
        lookup = db.get(UserLookup, email.lower())
        assert lookup is not None, f"demo user missing: {email}"
        bind_tenant(db, lookup.tenant_id, lookup.user_id)
        user = db.get(User, lookup.user_id)
        return auth_service.resolve_principal(db, user)
    finally:
        db.close()


@pytest.fixture
def alice() -> Principal:
    return principal_for("alice@acme.test")


@pytest.fixture
def bob() -> Principal:
    return principal_for("bob@acme.test")


@pytest.fixture
def hr_user() -> Principal:
    return principal_for("hr@acme.test")


@pytest.fixture
def carol() -> Principal:
    return principal_for("carol@globex.test")


@pytest.fixture
def admin_acme() -> Principal:
    return principal_for("admin@acme.test")


@pytest.fixture
def tenant_db(alice: Principal):
    """A session bound to Acme, the tenant most tests operate in."""
    db = SessionLocal()
    try:
        bind_tenant(db, alice.tenant_id, alice.user_id)
        yield db
        db.rollback()
    finally:
        db.close()


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.test"

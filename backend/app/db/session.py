"""Engine + session management, including the RLS tenant binding.

The application connects as a least-privilege role. Every request binds
``app.tenant_id`` (and ``app.user_id``) on its connection; row level security
policies read those settings. ``set_config(..., is_local => true)`` scopes the
value to the transaction so a pooled connection can never leak a previous
request's tenant.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# Owner/DDL engine — used ONLY by migrations and provisioning scripts.
admin_engine = create_engine(settings.admin_database_url, pool_pre_ping=True, future=True)
AdminSessionLocal = sessionmaker(bind=admin_engine, autoflush=False, future=True)

SYSTEM_TENANT_SENTINEL = "00000000-0000-0000-0000-000000000000"


def bind_tenant(session: Session, tenant_id: uuid.UUID | str | None,
                user_id: uuid.UUID | str | None = None) -> None:
    """Bind the RLS context for the current transaction."""
    tid = str(tenant_id) if tenant_id else SYSTEM_TENANT_SENTINEL
    uid = str(user_id) if user_id else SYSTEM_TENANT_SENTINEL
    session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true), "
             "set_config('app.user_id', :uid, true)"),
        {"tid": tid, "uid": uid},
    )


@contextmanager
def tenant_session(tenant_id: uuid.UUID | str | None,
                   user_id: uuid.UUID | str | None = None) -> Iterator[Session]:
    """Open a session already bound to a tenant. Commits on success."""
    session = SessionLocal()
    try:
        bind_tenant(session, tenant_id, user_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def admin_session() -> Iterator[Session]:
    """Owner-role session. Migrations/provisioning only — never request handling."""
    session = AdminSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

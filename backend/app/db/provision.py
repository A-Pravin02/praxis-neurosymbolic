"""Bootstrap work that requires the schema owner rather than the app role.

The application role has no INSERT on the global `permissions` catalogue by
design, so the catalogue is provisioned here (at startup and by the seeder)
using the owner connection.
"""
from __future__ import annotations

from sqlalchemy import select

from app.core.logging import get_logger, log_event
from app.db.models.tenancy import Permission
from app.db.session import admin_session
from app.tenancy.permissions import ALL_PERMISSIONS

logger = get_logger("praxis.provision")


def ensure_permission_catalogue() -> int:
    """Insert any missing permission rows. Idempotent."""
    with admin_session() as session:
        existing = {p.name for p in session.scalars(select(Permission)).all()}
        missing = [n for n in ALL_PERMISSIONS if n not in existing]
        for name in missing:
            session.add(Permission(name=name, description=ALL_PERMISSIONS[name]))
        if missing:
            log_event(logger, 20, "permission catalogue provisioned", added=len(missing))
        return len(missing)

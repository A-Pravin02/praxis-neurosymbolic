"""The authenticated Principal: the only trusted source of tenant identity."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    roles: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()
    attributes: dict = field(default_factory=dict)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def has_any_permission(self, permissions: list[str]) -> bool:
        return any(p in self.permissions for p in permissions)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def as_log_fields(self) -> dict:
        return {"user_id": str(self.user_id), "tenant_id": str(self.tenant_id)}

from __future__ import annotations

import uuid


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def short_id(prefix: str = "") -> str:
    s = uuid.uuid4().hex[:12]
    return f"{prefix}{s}" if prefix else s

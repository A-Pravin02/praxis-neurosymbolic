"""In-process sliding-window rate limiter.

Deliberately simple: one uvicorn worker per container in the MVP. The interface
is a single function so it can be swapped for a Redis implementation without
touching call sites.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.config import settings
from app.core.errors import RateLimitError

_WINDOW_SECONDS = 60.0
_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)
_MAX_KEYS = 20000


def _prune(now: float) -> None:
    if len(_hits) <= _MAX_KEYS:
        return
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
        _hits.pop(key, None)


def check(bucket: str, identity: str, limit: int) -> None:
    """Raise RateLimitError if `identity` exceeded `limit` hits/minute in `bucket`."""
    if not settings.rate_limit_enabled or limit <= 0:
        return
    key = f"{bucket}:{identity}"
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        cutoff = now - _WINDOW_SECONDS
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            raise RateLimitError()
        q.append(now)
        _prune(now)


def reset() -> None:
    """Test helper."""
    with _lock:
        _hits.clear()

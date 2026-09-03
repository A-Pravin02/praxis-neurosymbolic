#!/usr/bin/env python
"""Seed the demonstration workspaces. Idempotent."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.core.logging import configure_logging  # noqa: E402
from app.seed.demo import seed_all  # noqa: E402

if __name__ == "__main__":
    configure_logging()
    print(json.dumps(seed_all(), indent=2))

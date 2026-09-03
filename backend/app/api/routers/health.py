from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.schemas import HealthOut
from app.config import settings
from app.db.session import engine

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    db_status = "unknown"
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    return HealthOut(
        status="ok" if db_status == "ok" else "degraded",
        version="1.0.0",
        database=db_status,
        llm_provider=settings.llm_provider,
        embedding_provider=settings.embedding_provider,
    )

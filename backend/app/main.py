"""FastAPI application factory."""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.config import settings
from app.core.errors import AppError
from app.core.logging import (
    configure_logging, get_logger, log_event, request_id_var, tenant_id_var, user_id_var,
)

configure_logging()
logger = get_logger("praxis.http")

DESCRIPTION = """
Praxis is a neuro-symbolic, retrieval-augmented assistant. Factual answers are
gated on evidence: the language model presents conclusions that retrieval, the
knowledge base, the symbolic engine and the verifier have already established.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Praxis API",
        description=DESCRIPTION,
        version="1.0.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming[:64] if incoming.isascii() and len(incoming) <= 64 else ""
        rid = rid or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        user_id_var.set("-")
        tenant_id_var.set("-")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = int((time.perf_counter() - started) * 1000)
            log_event(logger, 40, "request failed", method=request.method,
                      path=request.url.path, latency_ms=elapsed)
            raise
        elapsed = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = rid
        # Baseline hardening headers; the SPA is served by nginx which adds CSP.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path != f"{settings.api_prefix}/health":
            log_event(logger, 20, "request", method=request.method, path=request.url.path,
                      status=response.status_code, latency_ms=elapsed)
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.detail, **exc.extra}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Field names only; never echo submitted values (they may be passwords).
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()})
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error",
                               "message": "The request payload is invalid.",
                               "fields": [f for f in fields if f]}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error",
                               "message": "An unexpected error occurred."}},
        )

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()

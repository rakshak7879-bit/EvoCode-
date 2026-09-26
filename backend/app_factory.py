"""FastAPI application factory (used by main.py and the test-suite)."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import routes, source
from config import Settings, get_settings
from logging_config import configure_logging
from services import build_services

logger = logging.getLogger("evo.api")


def create_app(settings: Settings | None = None, services_override: object | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format == "json")
    services = services_override or build_services(settings)

    app = FastAPI(
        title="Evo Code API",
        version=routes.VERSION,
        description="Persistent AI harness that turns a codebase into verified, reusable memory.",
    )
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def upload_size_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Reject oversized uploads before the multipart body is parsed."""
        if request.method == "POST" and request.url.path.endswith("/repository/analyze"):
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_upload_bytes + 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Upload exceeds the {settings.max_upload_mb} MB limit."},
                )
        return await call_next(request)

    @app.exception_handler(sqlite3.DatabaseError)
    async def database_error(_request: Request, exc: sqlite3.DatabaseError) -> JSONResponse:
        logger.error("Database error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content={"detail": "The memory database is unavailable. Restart the backend to recover it."},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    app.include_router(routes.router, prefix="/api")
    app.include_router(source.router, prefix="/api")
    return app

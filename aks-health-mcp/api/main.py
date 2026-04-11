"""
AKS Health API – FastAPI application entry point.

Responsibilities:
  - CORS (only the configured frontend origin is allowed)
  - Structured request logging (structlog)
  - Azure AD authentication on all /api routes
  - Mounts agent and auth route groups
  - Health / readiness probe endpoint

Run:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Or via the project Makefile:
    make run-api
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import get_api_settings
from api.routes.agent import router as agent_router
from api.routes.auth import router as auth_router
from server.logging_config import configure_logging, get_logger

# ── Bootstrap logging ────────────────────────────────────────────────────────
_settings = get_api_settings()
configure_logging(log_level=_settings.log_level, log_format=_settings.log_format)
logger = get_logger(__name__)


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "api.startup",
        frontend_origin=_settings.frontend_origin,
        allowed_group=_settings.azure_ad_allowed_group,
        tenant=_settings.azure_tenant_id,
    )
    yield
    logger.info("api.shutdown")


# ── Application factory ───────────────────────────────────────────────────────
app = FastAPI(
    title="AKS Health API",
    description=(
        "Backend API for the AKS Health sysadmin dashboard. "
        "All endpoints require a valid Azure AD bearer token issued to a "
        "member of the configured authorised security group."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Only the configured frontend origin is allowed. Wildcards are intentionally
# not used so the backend cannot be called by arbitrary origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["X-Request-Id"],
)


# ── Request logging middleware ────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next: object) -> Response:
    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()

    response: Response = await call_next(request)  # type: ignore[operator]

    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    response.headers["X-Request-Id"] = request_id
    structlog.contextvars.unbind_contextvars("request_id")
    return response


# ── Error handlers ────────────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.error("api.unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."},
    )


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api")
app.include_router(agent_router, prefix="/api")


# ── Health / readiness ───────────────────────────────────────────────────────
@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    """Kubernetes liveness probe – returns 200 when the process is alive."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict:
    """Kubernetes readiness probe – returns 200 when ready to serve traffic."""
    return {"status": "ready"}


# ── Dev entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=_settings.api_host,
        port=_settings.api_port,
        reload=True,
        log_config=None,  # structlog handles logging
    )

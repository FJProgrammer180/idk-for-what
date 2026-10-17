"""PHYSICS LAB AI backend application factory."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from app.api.v1.agent import router as agent_router
from app.api.v1.auth import router as auth_router
from app.api.v1.data import datasets_router, reports_router, sources_router
from app.api.v1.insights import dashboard_router, settings_router
from app.api.v1.lab import (
    EXPERIMENTS_ROUTER,
    METHODS_ROUTER,
    PROJECTS_ROUTER,
    RUNS_ROUTER,
)
from app.api.v1.system import VERSION, router as system_router
from app.core.config import settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger, new_request_id, request_id_var
from app.core.security import constant_time_equals
from app.db.session import create_all, ping as db_ping

logger = get_logger("physicslab.app")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class BodySizeLimitMiddleware:
    """Reject oversized request bodies from being buffered by the framework."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("method") not in SAFE_METHODS:
            headers = {k.lower().decode(): v.decode() for k, v in scope.get("headers", [])}
            declared = headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self.max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": (
                                f"request body exceeds the {self.max_bytes} byte limit for JSON "
                                "endpoints (file uploads use /datasets with their own limit)"
                            ),
                            "details": None,
                        }
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _csrf_required(path: str, request: Request) -> bool:
    if request.method in SAFE_METHODS:
        return False
    if not path.startswith(settings.api_v1_prefix):
        return False
    if path.endswith(("/auth/login", "/auth/register", "/health", "/ready")):
        return False
    if not request.cookies.get(settings.session_cookie_name):
        return False
    if (request.headers.get("authorization") or "").lower().startswith("bearer "):
        return False  # bearer clients carry no ambient browser credentials
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    configure_logging(settings.log_level, as_json=settings.log_json)
    if not db_ping():
        logger.error("database is unreachable at startup", extra={"event": "db_unreachable"})
    elif settings.environment in ("development", "test"):
        create_all()
        if settings.demo_account_enabled:
            try:
                from app.scripts.seed_demo import seed_demo

                seed_demo()
            except Exception as exc:  # noqa: BLE001 - seeding must not block startup
                logger.warning("demo seeding skipped: %s", type(exc).__name__)
    logger.info(
        "%s ready (provider=%s, db=%s)"
        % (settings.app_name, settings.ai_provider, settings.database_url.split("://")[0]),
        extra={"event": "startup"},
    )
    yield
    logger.info("backend shutting down", extra={"event": "shutdown"})


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        summary="Computational physics laboratory with a bounded, tool-using research agent.",
        description=(
            "PHYSICS LAB AI coordinates scientific work through a tool-using agent.\n\n"
            "* Every number comes from a registered numerical method or physics simulation executed "
            "in this backend - never from a language model.\n"
            "* User expressions are parsed by a restricted grammar: no `eval`, no arbitrary Python, "
            "no shell endpoint.\n"
            "* All records are owned by an authenticated user; cross-account access is denied.\n"
            "* `mock` provider = deterministic rule-based planner, explicitly labelled as not an LLM. "
            "Real providers are optional and configured server-side only.\n"
            "* Job status transitions (`queued → running → verifying → completed|failed|cancelled`) "
            "are persisted, so long jobs report real execution state.\n\n"
            "See `docs/` in the repository for architecture and the honesty guarantees."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization", settings.csrf_header_name, "x-request-id"],
        expose_headers=[
            "x-request-id",
            "x-server-time-ms",
            "x-report-checksum",
            "x-content-provenance",
        ],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Correlation id, timing, CSRF enforcement and hardening headers."""
        request_id = (request.headers.get("x-request-id") or new_request_id())[:64]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        if _csrf_required(request.url.path, request):
            cookie_value = request.cookies.get(settings.csrf_cookie_name, "")
            supplied = request.headers.get(settings.csrf_header_name, "")
            if not constant_time_equals(supplied, cookie_value):
                request_id_var.reset(token)
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "csrf_check_failed",
                            "message": (
                                "Missing or mismatched CSRF token. Send the value of the "
                                f"{settings.csrf_cookie_name} cookie in the {settings.csrf_header_name} "
                                "header (the SPA does this automatically)."
                            ),
                            "details": None,
                            "request_id": request_id,
                        }
                    },
                )
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 - logged with the correlation id, never echoed
            if settings.debug_propagate_errors:
                raise
            logger.exception("unhandled request failure", extra={"event": "http_unhandled"})
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "The server failed to complete this request.",
                        "details": None,
                        "request_id": request_id,
                    }
                },
            )
        duration = round((time.perf_counter() - started) * 1000, 3)
        response.headers["x-request-id"] = request_id
        response.headers["x-server-time-ms"] = str(duration)
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "same-origin"
        response.headers["permissions-policy"] = "geolocation=(), microphone=(), camera=()"
        if settings.is_production:
            response.headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "http request",
            extra={
                "event": "http",
                "status_code": response.status_code,
                "duration_ms": duration,
                "user_id": getattr(request.state, "user_id", None),
            },
        )
        request_id_var.reset(token)
        return response

    install_exception_handlers(app)

    for router in (
        system_router,
        auth_router,
        METHODS_ROUTER,
        PROJECTS_ROUTER,
        EXPERIMENTS_ROUTER,
        RUNS_ROUTER,
        agent_router,
        datasets_router,
        reports_router,
        sources_router,
        dashboard_router,
        settings_router,
    ):
        app.include_router(router, prefix=settings.api_v1_prefix)

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "version": VERSION,
            "environment": settings.environment,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "api": settings.api_v1_prefix,
            "hint": "GET /api/v1/capabilities lists every registered method, simulation and tool",
        }

    return app


app = create_app()

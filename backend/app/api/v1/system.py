"""Health, readiness and capability-discovery endpoints."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request

from app.agent.providers import provider_status
from app.security.posture import security_posture
from app.services.jobs import STATUSES as RUN_STATUSES
from app.core.config import settings
from app.db.session import ping as db_ping
from app.numerical.engine import describe as describe_numerics
from app.physics.engine import simulation_catalog
from app.schemas.common import HealthResponse, ReadinessResponse
from app.services.security_limits import enforce_user_limits
from app.tools import REGISTRY

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=VERSION,
        environment=settings.environment,
        time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe (DB + AI + storage)")
async def ready() -> ReadinessResponse:
    started = time.perf_counter()
    checks: dict[str, object] = {}
    checks["database"] = {"ok": db_ping(), "url_scheme": settings.database_url.split(":", 1)[0]}
    data_dir = os.path.abspath(settings.data_dir)
    checks["data_directory"] = {
        "ok": os.access(data_dir, os.W_OK),
        "writable": os.access(data_dir, os.W_OK),
    }
    try:
        status = provider_status()
        checks["ai_provider"] = {
            "ok": bool(status.get("configured")),
            "provider": status.get("provider"),
            "model": status.get("model"),
            "is_mock": status.get("is_mock"),
            "error": status.get("error"),
        }
    except Exception as exc:  # noqa: BLE001
        checks["ai_provider"] = {"ok": False, "error": type(exc).__name__}
    checks["research_integration"] = {
        "ok": bool(settings.research_search_base_url and settings.research_search_api_key),
        "note": "external retrieval is disabled; the agent reports this honestly",
    }
    ok = all(bool(value.get("ok")) for value in checks.values() if isinstance(value, dict))
    return ReadinessResponse(
        status="ok" if ok else "degraded",
        checks=checks,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )


@router.get("/capabilities", summary="Registered methods, simulations, tools and limits")
async def capabilities() -> dict:
    limits = enforce_user_limits(None)
    return {
        "numerical": describe_numerics(),
        "simulations": simulation_catalog(),
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "read_only": spec.read_only,
                "input_schema": spec.json_schema(),
            }
            for name, spec in sorted(REGISTRY.items())
        ],
        "agent_statuses": ["queued", "planning", "running", "verifying", "completed", "failed", "cancelled"],
        "run_statuses": list(RUN_STATUSES),
        "limits": limits,
        "ai": provider_status(),
        "security": security_posture(),
        "version": VERSION,
    }

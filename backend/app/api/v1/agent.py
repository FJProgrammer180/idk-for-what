"""Agent API: sessions, messages, events, cancellation, tool catalogue."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.method_selector import select_method
from app.agent.orchestrator import ResearchOrchestrator, cancel_session, session_events
from app.agent.providers import get_provider, provider_status
from app.core.config import settings
from app.core.deps import assert_project_owner, require_user
from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_db
from app.models.entities import AgentEvent, AgentSession, Project, User
from app.schemas.common import (
    AgentRunResponse,
    MethodRecommendRequest,
    Page,
    SessionCreate,
    MessageCreate,
    ToolCallValidationRequest,
)
from app.services.pagination import paginate, page_params
from app.tools import REGISTRY
from app.tools.base import validate_arguments

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_MESSAGE_CHARS = 6000


@router.get("/provider", summary="Server-side AI provider status (never exposes secrets)")
async def ai_provider() -> dict[str, Any]:
    return provider_status()


@router.get("/tools", summary="Registered agent tools with their input schemas")
async def tools() -> dict[str, Any]:
    return {
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
        "execution_boundaries": {
            "max_agent_steps": settings.max_agent_steps,
            "max_tool_calls_per_session": settings.max_agent_tool_calls,
            "provider_retries": settings.max_provider_retries,
            "tool_execution_timeout_seconds": settings.compute_timeout_seconds,
            "arbitrary_code_execution": False,
            "notes": "only registered tools are callable; expressions use a restricted parser",
        },
    }


@router.post("/recommend-method", summary="Backend method-selection advisory for a task")
async def recommend(payload: MethodRecommendRequest) -> dict[str, Any]:
    """Method-selection advisory: the same classifier the agent plans with, on demand."""
    selection = select_method(payload.task, hint_method=payload.method)
    return {
        "selection": selection.to_dict(),
        "candidates": [
            {"method": name, **spec}
            for name, spec in (
                (name, {"title": s["title"], "assumptions": s["assumptions"], "requires": s["requires"]})
                for name, s in _methods_for(selection.problem_type).items()
            )
        ],
    }


def _methods_for(problem_type: str | None) -> dict[str, dict[str, Any]]:
    from app.numerical.engine import METHODS

    if not problem_type:
        return {}
    return {name: spec for name, spec in METHODS.items() if spec["problem_type"] == problem_type}


@router.get("/sessions", summary="List agent sessions")
async def list_sessions(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
) -> dict[str, Any]:
    stmt = (
        select(AgentSession)
        .where(AgentSession.owner_id == user.id)
        .order_by(AgentSession.updated_at.desc())
    )
    total = db.execute(
        select(func.count()).select_from(AgentSession).where(AgentSession.owner_id == user.id)
    ).scalar_one()
    rows = db.execute(stmt.limit(page["limit"]).offset(page["offset"])).scalars().all()
    items = [
        {
            "session_id": row.id,
            "title": row.title,
            "status": row.status,
            "provider": row.provider,
            "model": row.model,
            "project_id": row.project_id,
            "plan": row.plan or [],
            "usage": row.usage or {},
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "event_count": db.execute(
                select(func.count()).select_from(AgentEvent).where(AgentEvent.session_id == row.id)
            ).scalar_one(),
        }
        for row in rows
    ]
    return {
        "items": items,
        "page": page["offset"] // page["limit"] + 1,
        "page_size": page["limit"],
        "total": int(total),
        "total_pages": (int(total) + page["limit"] - 1) // page["limit"] if total else 0,
        "has_more": page["offset"] + len(items) < int(total),
    }


@router.post("/sessions", status_code=201, summary="Open an agent session")
async def create_session(
    payload: SessionCreate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if payload.project_id:
        assert_project_owner(db.get(Project, payload.project_id), user)
    try:
        provider = get_provider()
        status = provider.status()
    except Exception as exc:  # noqa: BLE001
        raise ConflictError(f"AI provider is unusable: {exc}") from None
    session = AgentSession(
        owner_id=user.id,
        project_id=payload.project_id,
        title=payload.title.strip()[:200],
        status="idle",
        provider=str(status.get("provider")),
        model=str(status.get("model")),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {
        "session_id": session.id,
        "title": session.title,
        "status": session.status,
        "provider": session.provider,
        "model": session.model,
        "project_id": session.project_id,
        "is_mock": bool(status.get("is_mock")),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "note": (
            "mock provider: deterministic rule-based planner (not a language model). Tool calls and "
            "numbers are real backend executions."
            if status.get("is_mock")
            else "live provider selected"
        ),
    }


@router.get("/sessions/{session_id}", summary="Session detail with recent events")
async def get_session(
    session_id: str,
    event_limit: int = Query(default=40, ge=0, le=500),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _owned_session(db, session_id, user)
    events = (
        []
        if event_limit == 0
        else session_events(session_id)[-event_limit:]
    )
    return {
        "session_id": session.id,
        "title": session.title,
        "status": session.status,
        "provider": session.provider,
        "model": session.model,
        "project_id": session.project_id,
        "plan": session.plan or [],
        "usage": session.usage or {},
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        "events": events,
    }


@router.get("/sessions/{session_id}/events", summary="Poll execution events (after a sequence number)")
async def get_events(
    session_id: str,
    after: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _owned_session(db, session_id, user)
    events = [e for e in session_events(session_id) if e["sequence"] > after]
    return {
        "session_id": session_id,
        "status": session.status,
        "events": events,
        "latest_sequence": events[-1]["sequence"] if events else after,
    }


@router.get("/sessions/{session_id}/stream", summary="Server-sent events while the job runs")
async def stream_events(
    session_id: str,
    after: int = Query(default=0, ge=0),
    max_seconds: float = Query(default=60.0, ge=1, le=300),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _owned_session(db, session_id, user)

    async def generator():
        from app.db.session import session_scope

        loop = asyncio.get_event_loop()
        last = after
        deadline = loop.time() + max_seconds
        status = "running"
        while True:
            events = [e for e in session_events(session_id) if e["sequence"] > last]
            for event in events:
                last = event["sequence"]
                yield f"data: {json.dumps(event, default=str)}\n\n"
            with session_scope() as fresh:
                row = fresh.get(AgentSession, session_id)
                status = row.status if row else "gone"
            if status in ("completed", "failed", "cancelled", "idle"):
                yield f"event: done\ndata: {json.dumps({'status': status, 'latest_sequence': last})}\n\n"
                return
            if asyncio.get_event_loop().time() > deadline:
                yield f"event: timeout\ndata: {json.dumps({'status': status, 'latest_sequence': last})}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@router.post("/sessions/{session_id}/messages", summary="Send a task to the research agent")
async def post_message(
    session_id: str,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _owned_session(db, session_id, user)
    # the length bound lives on MessageCreate, so an over-long task is a 422 from the
    # schema rather than a second, easily-forgotten check here
    text = payload.content.strip()
    if session.status in ("running", "planning", "queued", "verifying"):
        raise ConflictError(
            f"this session is currently '{session.status}'. Wait for it or cancel it first."
        )
    orchestrator = ResearchOrchestrator()
    if not payload.wait:
        asyncio.create_task(orchestrator.run(
            session_id=session_id, user_message=text, client_request_id=payload.client_request_id
        ))
        return {
            "session_id": session_id,
            "status": "queued",
            "async": True,
            "poll_url": f"{settings.api_v1_prefix}/agent/sessions/{session_id}",
            "stream_url": f"{settings.api_v1_prefix}/agent/sessions/{session_id}/stream",
            "message": "task queued; watch the event stream for tool-call status",
        }
    result = await orchestrator.run(
        session_id=session_id, user_message=text, client_request_id=payload.client_request_id
    )
    body = {
        "session_id": result.session_id,
        "status": result.status,
        "answer_markdown": result.answer_markdown,
        "plan": result.plan,
        "tool_results": [
            {k: v for k, v in item.items() if k not in {"output"}}
            | {"output_headline": (item.get("output") or {}).get("result")}
            for item in result.tool_results
        ],
        "usage": result.usage,
        "provider": result.provider,
        "error": result.error,
        "events": result.events[-40:],
    }
    return body


@router.post("/sessions/{session_id}/cancel", summary="Request cancellation of the running task")
async def cancel(
    session_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    _owned_session(db, session_id, user)
    ok, detail = cancel_session(session_id)
    return {
        "status": "cancelled" if ok else "not_cancelled",
        "detail": detail,
        "limitation": (
            "an in-flight synchronous tool call finishes its current step; its result is then "
            "discarded and the session stays 'cancelled'" if ok else None
        ),
    }


@router.post("/validate-tool-call", summary="Schema-check a tool call without executing it")
async def validate_call(payload: ToolCallValidationRequest) -> dict[str, Any]:
    """Dry-run a tool call: schema check only, nothing is executed or persisted."""
    name = payload.tool
    if name not in REGISTRY:
        return {"valid": False, "tool": name,
                "error": f"'{name}' is not a registered tool", "registered": sorted(REGISTRY)}
    try:
        normalised = validate_arguments(name, payload.arguments or {})
    except Exception as exc:  # noqa: BLE001 - a schema rejection is data, not a crash
        return {"valid": False, "tool": name, "error": str(exc)[:600]}
    return {"valid": True, "tool": name, "normalised_arguments": normalised}


def _owned_session(db: Session, session_id: str, user: User) -> AgentSession:
    session = db.get(AgentSession, session_id)
    if session is None or session.owner_id != user.id:
        raise NotFoundError("agent session not found in your account")
    return session

"""Dashboard aggregation and user settings (all values derived from real rows)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.agent.providers import provider_status
from app.core.config import settings
from app.core.errors import ValidationError
from app.core.deps import require_user
from app.db.session import get_db
from app.models.entities import (
    AgentEvent,
    AgentSession,
    Dataset,
    Experiment,
    ExperimentRun,
    Project,
    Report,
    ResearchSource,
    User,
)
from app.numerical.common import software_versions
from app.schemas.common import ProfileUpdateRequest
from app.security.posture import security_posture

dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/settings", tags=["settings"])


@dashboard_router.get("", summary="Recent activity computed from stored records")
async def dashboard(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    def count(model, *conditions) -> int:
        stmt = select(func.count()).select_from(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(db.execute(stmt).scalar_one())

    totals = {
        "projects": count(Project, Project.owner_id == user.id),
        "experiments": count(Experiment, Experiment.owner_id == user.id),
        "runs": count(ExperimentRun, ExperimentRun.owner_id == user.id),
        "datasets": count(Dataset, Dataset.owner_id == user.id),
        "reports": count(Report, Report.owner_id == user.id),
        "sources": count(ResearchSource, ResearchSource.owner_id == user.id),
        "agent_sessions": count(AgentSession, AgentSession.owner_id == user.id),
    }
    by_status = dict(
        db.execute(
            select(ExperimentRun.status, func.count(ExperimentRun.id))
            .where(ExperimentRun.owner_id == user.id)
            .group_by(ExperimentRun.status)
        ).all()
    )
    verification_counts = dict(
        db.execute(
            select(
                func.coalesce(ExperimentRun.verification["status"].as_string(), "not_recorded"),
                func.count(ExperimentRun.id),
            )
            .where(ExperimentRun.owner_id == user.id)
            .group_by(
                func.coalesce(ExperimentRun.verification["status"].as_string(), "not_recorded")
            )
        ).all()
    ) if totals["runs"] else {}
    method_usage = [
        {"method": row[0], "kind": row[1], "runs": int(row[2])}
        for row in db.execute(
            select(Experiment.method, Experiment.kind, func.count(ExperimentRun.id))
            .join(ExperimentRun, ExperimentRun.experiment_id == Experiment.id)
            .where(ExperimentRun.owner_id == user.id)
            .group_by(Experiment.method, Experiment.kind)
            .order_by(func.count(ExperimentRun.id).desc())
            .limit(8)
        ).all()
    ]
    runtime_stats = db.execute(
        select(
            func.avg(ExperimentRun.execution_time_ms),
            func.max(ExperimentRun.execution_time_ms),
            func.count(ExperimentRun.id),
        ).where(ExperimentRun.owner_id == user.id, ExperimentRun.execution_time_ms.is_not(None))
    ).one()

    recent_experiments = db.execute(
        select(Experiment).where(Experiment.owner_id == user.id).order_by(Experiment.updated_at.desc()).limit(8)
    ).scalars().all()
    recent_runs = db.execute(
        select(ExperimentRun, Experiment.title)
        .join(Experiment, Experiment.id == ExperimentRun.experiment_id)
        .where(ExperimentRun.owner_id == user.id)
        .order_by(ExperimentRun.created_at.desc())
        .limit(8)
    ).all()
    recent_sessions = db.execute(
        select(AgentSession).where(AgentSession.owner_id == user.id).order_by(
            AgentSession.updated_at.desc()
        ).limit(5)
    ).scalars().all()
    recent_events = db.execute(
        select(AgentEvent, AgentSession.title)
        .join(AgentSession, AgentSession.id == AgentEvent.session_id)
        .where(AgentSession.owner_id == user.id)
        .order_by(AgentEvent.created_at.desc(), AgentEvent.sequence.desc())
        .limit(12)
    ).all()

    return {
        "generated_from": "database queries against this account's rows",
        "totals": totals,
        "runs_by_status": by_status,
        "runs_by_verification": verification_counts,
        "method_usage": method_usage,
        "runtime": {
            "sampled_runs": int(runtime_stats[2] or 0),
            "mean_execution_time_ms": (
                round(float(runtime_stats[0]), 3) if runtime_stats[0] is not None else None
            ),
            "max_execution_time_ms": (
                round(float(runtime_stats[1]), 3) if runtime_stats[1] is not None else None
            ),
        },
        "recent_experiments": [
            {
                "id": e.id,
                "title": e.title,
                "kind": e.kind,
                "method": e.method,
                "status": e.status,
                "headline": (e.result_metadata or {}).get("headline_result"),
                "verification_status": (e.result_metadata or {}).get("verification_status"),
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in recent_experiments
        ],
        "recent_runs": [
            {
                "run_id": r.id,
                "experiment_id": r.experiment_id,
                "experiment_title": title,
                "status": r.status,
                "execution_time_ms": r.execution_time_ms,
                "verification_status": (r.verification or {}).get("status"),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r, title in recent_runs
        ],
        "recent_sessions": [
            {
                "session_id": s.id,
                "title": s.title,
                "status": s.status,
                "provider": s.provider,
                "model": s.model,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in recent_sessions
        ],
        "recent_agent_activity": [
            {
                "sequence": e.sequence,
                "event_type": e.event_type,
                "status": e.status,
                "session_title": title,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "summary": _event_summary(e.payload),
            }
            for e, title in recent_events
        ],
    }


def _event_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    keys = ("tool", "message", "question", "status", "headline", "category", "proposed_method")
    return {k: payload[k] for k in keys if k in payload}


@settings_router.get("", summary="Effective account settings, limits and provider status")
async def get_settings_view(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    provider = provider_status()
    return {
        "profile": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "is_demo": user.is_demo,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        },
        "computation_limits": {
            "max_iterations": user.max_iterations,
            "compute_timeout_seconds": user.compute_timeout_seconds,
            "max_agent_steps": settings.max_agent_steps,
            "max_agent_tool_calls": settings.max_agent_tool_calls,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_expression_chars": settings.max_expression_chars,
            "server_ceiling_max_iterations": settings.max_iterations,
            "enforced_by": "backend; values are ceilings the API will not exceed",
        },
        "data_retention": {
            "retention_days": user.retention_days,
            "policy": "0 = keep indefinitely; >0 = purge runs older than N days on request",
            "stored_rows": {
                "experiments": int(
                    db.execute(
                        select(func.count()).select_from(Experiment).where(Experiment.owner_id == user.id)
                    ).scalar_one()
                ),
                "runs": int(
                    db.execute(
                        select(func.count()).select_from(ExperimentRun).where(ExperimentRun.owner_id == user.id)
                    ).scalar_one()
                ),
                "datasets": int(
                    db.execute(
                        select(func.count()).select_from(Dataset).where(Dataset.owner_id == user.id)
                    ).scalar_one()
                ),
                "reports": int(
                    db.execute(
                        select(func.count()).select_from(Report).where(Report.owner_id == user.id)
                    ).scalar_one()
                ),
            },
        },
        "ai_provider": provider,
        "theme_defaults": {
            "palette": "near-black / graphite with electric-purple accent",
            "monospace_font": "JetBrains Mono fallback stack",
            "note": "stored per browser (localStorage); no server side theme sync implemented",
        },
        "environment": {
            "app_environment": settings.environment,
            "software_versions": software_versions(),
            "cors_origins": settings.cors_allowed_origins,
        },
        "security": security_posture(),
    }


@settings_router.patch("", summary="Update profile preferences")
async def patch_settings(
    payload: ProfileUpdateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Apply the accepted fields and echo the effective values.

    The response reports every field the caller can set (not just what changed), so the
    UI never has to guess whether a preference took effect. A request above the server
    ceiling is rejected with the exact setting that would raise it, rather than being
    silently clamped to something the user did not ask for.
    """
    changed: list[str] = []
    ignored: list[str] = []

    def apply(name: str, desired: Any, current: Any) -> bool:
        """Record a field as changed only if it differs from what is already stored."""
        if desired == current:
            ignored.append(name)
            return False
        changed.append(name)
        return True

    if payload.display_name:
        desired = payload.display_name.strip()[:120]
        if apply("display_name", desired, user.display_name):
            user.display_name = desired
    if payload.retention_days is not None:
        desired = int(payload.retention_days)
        if apply("retention_days", desired, int(user.retention_days or 0)):
            user.retention_days = desired
    if payload.max_iterations is not None:
        requested = int(payload.max_iterations)
        ceiling = int(settings.max_iterations)
        if requested > ceiling:
            # refusing is kinder than silently storing something the caller did not ask
            # for: the message says exactly which knob raises the ceiling
            raise ValidationError(
                f"max_iterations {requested} exceeds the server ceiling of {ceiling}; "
                "an operator raises it with PLAB_MAX_ITERATIONS",
                details={
                    "requested": requested,
                    "server_ceiling": ceiling,
                    "set_by": "PLAB_MAX_ITERATIONS (backend environment)",
                },
            )
        desired = max(1, requested)
        if apply("max_iterations", desired, int(user.max_iterations or 0)):
            user.max_iterations = desired
    if not changed:
        # nothing to write, and the caller is told so instead of being flattered with "updated"
        db.rollback()
    db.commit()
    return {
        "status": "updated" if changed else "noop",
        "changed": changed,
        "ignored": ignored,
        "rejected": None,
        "profile": {"display_name": user.display_name},
        "computation_limits": {
            "max_iterations": user.max_iterations,
            "compute_timeout_seconds": user.compute_timeout_seconds,
            "server_ceiling_max_iterations": int(settings.max_iterations),
        },
        "data_retention": {"retention_days": user.retention_days},
    }


@settings_router.post("/retention/purge", summary="Purge finished runs older than the retention window")
async def purge_retention(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    days: int | None = Query(default=None, ge=0, le=3650),
    dry_run: bool = Query(default=True),
) -> dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    if days is None and not user.retention_days:
        return {
            "status": "no_policy",
            "removed": 0,
            "note": "retention is 0 (keep forever); pass ?days=N and dry_run=false to purge",
        }
    # an explicit ?days=0 means "every finished run is already expired"; only a missing
    # argument falls back to the stored profile
    window = int(user.retention_days or 0) if days is None else int(days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=window)
    from app.services.jobs import TERMINAL_STATUSES as terminal
    stmt = select(ExperimentRun).where(
        ExperimentRun.owner_id == user.id,
        ExperimentRun.created_at < cutoff,
        ExperimentRun.status.in_(terminal),
    )
    rows = db.execute(stmt).scalars().all()
    if dry_run:
        return {
            "status": "dry_run",
            "days": window,
            "would_remove": len(rows),
            "cutoff": cutoff.isoformat(timespec="seconds"),
            "run_ids": [r.id for r in rows[:25]],
        }
    for row in rows:
        db.delete(row)
    db.commit()
    return {"status": "purged", "days": window, "removed": len(rows), "cutoff": cutoff.isoformat(timespec="seconds")}


@settings_router.post("/sessions/revoke", summary="Revoke all issued session tokens")
async def revoke_sessions(user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    user.token_version = int(user.token_version) + 1
    db.commit()
    return {
        "status": "revoked",
        "token_version": user.token_version,
        "note": "existing cookies now fail signature/version checks; sign in again",
    }

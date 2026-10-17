"""Per-account compute limits, applied to every execution path."""

from __future__ import annotations

from typing import Any

from app.core.config import settings


def enforce_user_limits(context: Any) -> dict[str, Any]:
    """Return the effective limits for a tool context.

    Defaults come from the server configuration; the values are the ceiling the
    user's account may request (admins seed higher values via the DB).
    """
    limits = dict(getattr(context, "limits", None) or {})
    return {
        "max_iterations": int(limits.get("max_iterations", settings.max_iterations)),
        "max_grid_points": int(limits.get("max_grid_points", settings.max_grid_points)),
        "compute_timeout_seconds": float(
            limits.get("compute_timeout_seconds", settings.compute_timeout_seconds)
        ),
        "max_agent_steps": int(limits.get("max_agent_steps", settings.max_agent_steps)),
        "max_agent_tool_calls": int(
            limits.get("max_agent_tool_calls", settings.max_agent_tool_calls)
        ),
    }


def account_iteration_cap(owner_id: str | None, requested: Any = None) -> tuple[int, dict[str, Any]]:
    """Smallest iteration budget the caller asked for and the account/server ceilings allow.

    Every execution path (synchronous compute, queued runs, agent tools) resolves its
    ``max_iterations`` here, so a per-account limit configured on the profile is a real
    bound rather than a number the UI displays.  The returned record says which ceiling
    applied and whether the request was reduced, which callers surface in the response.
    """
    server_ceiling = int(settings.max_iterations)
    account_ceiling = server_ceiling
    if owner_id:
        try:
            from app.db.session import session_scope
            from app.models.entities import User

            with session_scope() as db:
                user = db.get(User, owner_id)
                if user is not None and getattr(user, "max_iterations", None):
                    account_ceiling = int(user.max_iterations)
        except Exception:  # pragma: no cover - a broken lookup must not run uncapped
            account_ceiling = server_ceiling
    wanted = int(requested) if requested else account_ceiling
    cap = max(1, min(wanted, account_ceiling, server_ceiling))
    return cap, {
        "max_iterations": cap,
        "requested": int(requested) if requested else None,
        "account_ceiling": account_ceiling,
        "server_ceiling": server_ceiling,
        "clamped": bool(requested) and int(requested) > cap,
        "source": "account profile" if account_ceiling < server_ceiling else "server default",
    }

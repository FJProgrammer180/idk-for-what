"""A self-describing security posture summary for the Settings page.

Every value here is either read from configuration or is a statement about code that
exists in this repository.  Nothing is asserted that a reviewer could not verify by
reading the module named in ``reference``.  Known gaps are listed explicitly instead
of being smoothed over.
"""

from __future__ import annotations

from typing import Any

from app.core.config import REPO_ROOT, settings


def security_posture() -> dict[str, Any]:
    from app.services.datasets import ALLOWED_EXTENSIONS, FORMULA_PREFIXES, MAX_ROWS

    return {
        "secrets": {
            "ai_keys_server_side_only": True,
            "keys_never_returned_by_api": True,
            "password_hash_never_serialised": True,
            "env_example_shipped": (REPO_ROOT / ".env.example").exists(),
            "log_redaction": "password/api_key/token fields are masked before logging",
            "reference": "app/core/security.py, app/core/logging.py, app/agent/providers.py",
            "note": "provider status endpoints expose names and configuration booleans only",
        },
        "execution": {
            "arbitrary_python_execution": False,
            "eval_or_exec_used_for_expressions": False,
            "expression_engine": (
                "restricted tokenizer + recursive-descent parser producing a whitelisted "
                "tree; attribute access, imports, subscripts and unknown call names are "
                "rejected before anything is evaluated"
            ),
            "registered_callable_tools_only": True,
            "compute_wall_clock_limit_seconds": float(settings.compute_timeout_seconds),
            "worker_isolation": (
                "compute runs in an asyncio worker thread inside the API process, bounded by "
                "compute_timeout_seconds. There is no subprocess, shell or container boundary "
                "because user input is never turned into code; the residual risk is a "
                "CPU-saturating job, bounded by the iteration and grid-point ceilings"
            ),
            "shell_endpoint": False,
            "hardening": {
                "max_request_bytes": int(settings.max_body_bytes),
                "max_upload_bytes": int(settings.max_upload_bytes),
                "max_iterations_ceiling": int(settings.max_iterations),
                "max_grid_points_ceiling": int(settings.max_grid_points),
                "max_agent_steps": int(settings.max_agent_steps),
            },
            "reference": "app/services/expr.py, app/services/jobs.py, app/tools/registry.py",
        },
        "access_control": {
            "ownership_required_for_all_records": True,
            "client_supplied_user_ids_trusted": False,
            "password_hash": "argon2id (argon2-cffi, parameterised in app/core/security.py)",
            "session": (
                "signed token in an httpOnly, SameSite=Lax cookie with a server-side expiry "
                "and a per-user token_version that revokes every session on password change"
            ),
            "csrf": "double-submit token + SameSite for cookie-authenticated writes",
            "bearer_alternative": (
                "the same session token may be sent as 'Authorization: Bearer <token>'; it is "
                "only ever delivered in a Set-Cookie header, never in a JSON body"
            ),
            "rate_limits": {
                "enabled": bool(settings.rate_limit_enabled),
                "auth": settings.rate_limit_auth,
                "compute": settings.rate_limit_compute,
                "global": settings.rate_limit_default,
            },
            "reference": "app/core/deps.py, app/core/security.py, app/core/rate_limit.py",
        },
        "data": {
            "uploads": {
                "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
                "max_rows_parsed": int(MAX_ROWS),
                "formula_guard_prefixes": list(FORMULA_PREFIXES),
                "max_bytes": int(settings.max_upload_bytes),
                "content_sanity_checks": [
                    "non-empty",
                    "delimiter sniffing",
                    "NUL byte rejection",
                    "rectangular row count",
                    "extension allow-list",
                ],
                "antivirus_scanning": False,
                "row_cap_enforced": True,
                "path_traversal_guard": (
                    "safe_filename() reduces any client name to one safe component; the stored "
                    "path uses a server-generated uuid instead"
                ),
                "stored_outside_database": True,
            },
            "exports": {
                "csv_formula_injection_guard": True,
                "filename_sanitiser": True,
            },
            "database_access": "SQLAlchemy ORM only, parameterised by the driver; no string SQL",
            "artifacts": "figures and reports are written under the data directory and served "
            "only to the owning user through authenticated routes",
            "reference": "app/services/datasets.py, app/core/security.py, app/db/session.py",
        },
        "observability": {
            "structured_logging": True,
            "request_correlation_ids": True,
            "stack_traces_to_clients": False,
            "health_endpoint": f"{settings.api_v1_prefix}/health",
            "readiness_endpoint": f"{settings.api_v1_prefix}/ready",
            "security_posture_endpoint": f"{settings.api_v1_prefix}/capabilities",
        },
        "honesty_guarantees": {
            "no_invented_results": (
                "every number on a run, report or agent answer is read back from a persisted "
                "payload produced by app/numerical or app/physics; no endpoint synthesises data"
            ),
            "retrieval_claim": (
                "search_scientific_sources reports status='not_performed' unless a live provider "
                "is configured; references are labelled local_curated_reference"
            ),
            "agent_claim": (
                "mock mode is a deterministic rule planner and is labelled as such in every "
                "answer; it is not a language model"
            ),
            "convergence_claim": (
                "status and convergence_achieved come from the method's own stopping criterion; "
                "failed convergence is surfaced as completed_unconverged, never as success"
            ),
        },
        "gaps": [
            "in-process rate limiting resets on restart and is per-worker; use a shared store "
            "(redis) in production",
            "no TLS termination here: deploy behind a TLS proxy and set PLAB_COOKIE_SECURE=true "
            "(HSTS is only emitted when the request is already https)",
            "CPU-bound worker threads cannot be force-killed, so a timeout abandons the job and "
            "discards its result rather than halting the thread",
            "uploads are sanity-checked, not virus-scanned",
            "SQLite in the default configuration serialises writes; a Postgres server is expected "
            "for multi-user deployments",
            "no CSP header: the frontend renders markdown, so sanitisation happens at render time "
            "instead (see frontend/src/lib/markdown.ts); add a CSP once the asset hosts are final",
        ],
    }

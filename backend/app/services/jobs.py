"""Bounded asynchronous job execution for experiment runs.

Execution model
---------------
* The API request only persists a ``queued`` run and (optionally) schedules it.
* The heavy lifting happens in a dedicated thread pool so the event loop keeps
  serving status polls.
* A wall-clock timeout bounds each job; iteration caps inside the numerical
  methods bound it further.
* Cancellation: a cancel request is honoured before the job starts, and while
  it is queued.  A CPU-bound thread already in flight cannot be killed safely in
  CPython, so its result is *discarded* and the run is marked ``cancelled`` -
  this limitation is documented rather than papered over.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import sanitize_payload
from app.db.session import session_scope
from app.models.entities import Experiment, ExperimentRun
from app.numerical.common import NumericalInputError, software_versions
from app.services.execution import perform_job
from app.services.plots import save_artifact, series_from_result
from app.services import plots as plot_service
from app.services.verification import verify_result

logger = get_logger("physicslab.jobs")

# A run can finish in three distinguishable ways, and collapsing them would hide
# information the user needs:
#   completed               solver met its criterion and the independent check agreed
#   completed_unconverged   the solver stopped without meeting its criterion
#   completed_unverified    the solver converged but the independent re-check disagrees
STATUSES = (
    "queued",
    "running",
    "verifying",
    "completed",
    "completed_unconverged",
    "completed_unverified",
    "failed",
    "cancelled",
)
TERMINAL_STATUSES = ("completed", "completed_unconverged", "completed_unverified", "failed", "cancelled")

ACTIVE_TASKS: dict[str, asyncio.Task] = {}


class JobError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _snapshot(run_id: str) -> dict[str, Any] | None:
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        if run is None:
            return None
        return {
            "run_id": run.id,
            "experiment_id": run.experiment_id,
            "owner_id": run.owner_id,
            "status": run.status,
            "cancel_requested": run.cancel_requested,
            "execution_config": dict(run.execution_config or {}),
        }


async def execute_run(run_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    """Run one job to completion, persisting every status transition."""
    snapshot = _snapshot(run_id)
    if snapshot is None:
        raise JobError("run_not_found", "run no longer exists")
    if snapshot["status"] not in ("queued", "running"):
        return {"run_id": run_id, "status": snapshot["status"], "final": True}
    if snapshot["cancel_requested"]:
        _finish(run_id, "cancelled", None, None, error={"code": "cancelled_before_start",
                                                        "message": "cancelled before execution"})
        return {"run_id": run_id, "status": "cancelled", "final": True}

    limit = float(timeout or settings.compute_timeout_seconds)
    spec = dict(snapshot["execution_config"] or {})
    spec["experiment_id"] = snapshot["experiment_id"]
    owner_id = snapshot["owner_id"]

    _set_running(run_id)
    started = time.perf_counter()
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(perform_job, spec, owner_id=owner_id), timeout=limit
        )
    except asyncio.TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        _finish(
            run_id,
            "failed",
            None,
            None,
            error={
                "code": "execution_timeout",
                "message": (
                    f"computation exceeded the {limit:g}s wall-clock limit after "
                    f"{elapsed:.0f} ms and was abandoned"
                ),
                "retryable": True,
            },
            runtime={"execution_time_ms": round(elapsed, 3), "timeout_seconds": limit},
        )
        raise JobError("timeout", f"computation exceeded {limit:g}s and was abandoned") from None
    except NumericalInputError as exc:
        _finish(
            run_id, "failed", None, None,
            error={"code": "invalid_numerical_input", "message": str(exc), "retryable": True},
            runtime={"execution_time_ms": round((time.perf_counter() - started) * 1000, 3)},
        )
        raise JobError("invalid_input", str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - never leak internals into the row
        logger.exception("job failed")
        _finish(
            run_id,
            "failed",
            None,
            None,
            error={
                "code": "execution_error",
                "message": f"{type(exc).__name__}: computation failed",
                "detail": _safe_detail(exc),
                "retryable": False,
            },
            runtime={"execution_time_ms": round((time.perf_counter() - started) * 1000, 3)},
        )
        raise JobError("execution_error", "computation failed; see the run record") from None

    # status may have been changed by a cancel request while the thread ran
    if _cancel_requested(run_id):
        _finish(
            run_id, "cancelled", payload, None,
            error={"code": "cancelled", "message": "cancelled during execution; result discarded"},
        )
        return {"run_id": run_id, "status": "cancelled", "final": True}

    _set_status(run_id, "verifying")
    verification = await asyncio.to_thread(verify_result, payload, spec)

    artifact = _maybe_plot(run_id, payload, spec)
    solver_status = str(payload.get("status") or "")
    if payload.get("error") or solver_status in ("failed", "invalid_input"):
        status = "failed"
    elif solver_status in ("max_iterations_reached", "target_accuracy_not_met", "diverged"):
        status = "completed_unconverged"
    elif verification["status"] == "failed":
        status = "completed_unverified"
    else:
        status = "completed"
    _finish(
        run_id,
        status,
        payload,
        verification,
        runtime={
            "execution_time_ms": payload.get("execution_time_ms"),
            "wall_clock_ms": round((time.perf_counter() - started) * 1000, 3),
            "verification_time_ms": 0.0,
            "artifact": artifact,
            "non_finite_fields": payload.get("non_finite_fields", []),
        },
    )
    return {"run_id": run_id, "status": status, "final": True, "payload": payload,
            "verification": verification}


def _safe_detail(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return sanitize_payload(text, 600) if isinstance(text, str) else "unavailable"


def _set_running(run_id: str) -> None:
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        if run is None:
            return
        run.status = "running"
        from app.core.security import utcnow

        run.started_at = utcnow()


def _set_status(run_id: str, status: str) -> None:
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        if run is not None:
            run.status = status


def _cancel_requested(run_id: str) -> bool:
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        return bool(run and run.cancel_requested)


def _finish(
    run_id: str,
    status: str,
    payload: dict[str, Any] | None,
    verification: dict[str, Any] | None,
    *,
    error: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> None:
    from app.core.security import utcnow

    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        if run is None:
            return
        run.status = status
        run.structured_output = sanitize_payload(payload) if payload else None
        run.verification = sanitize_payload(verification) if verification else None
        run.error = sanitize_payload(error) if error else None
        meta = dict(run.runtime_metadata or {})
        meta.update(runtime or {})
        run.runtime_metadata = meta
        run.software_versions = software_versions()
        run.finished_at = utcnow()
        if payload and payload.get("execution_time_ms") is not None:
            run.execution_time_ms = float(payload["execution_time_ms"])
        experiment = db.get(Experiment, run.experiment_id)
        if experiment is not None:
            experiment.status = (
                "completed" if status in ("completed", "completed_unconverged", "completed_unverified")
                else status
            )
            summary = {
                "last_run_id": run.id,
                "last_run_status": status,
                "method": (payload or {}).get("method"),
                "headline_result": _headline(payload),
                "convergence_achieved": (payload or {}).get("convergence_achieved"),
                "error_estimate": (payload or {}).get("error_estimate"),
                "execution_time_ms": (payload or {}).get("execution_time_ms"),
                "verification_status": (verification or {}).get("status"),
            }
            experiment.result_metadata = sanitize_payload(summary)
            experiment.updated_at = utcnow()


def _headline(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    result = payload.get("result") or {}
    keys = ("root", "integral", "derivative", "solution", "max_height", "range_at_first_impact",
            "r_squared", "final_temperature", "final_state")
    out: dict[str, Any] = {}
    for key in keys:
        value = result.get(key)
        if value is None:
            continue
        if isinstance(value, float):
            out[key] = value if math.isfinite(value) else None
        elif isinstance(value, list):
            out[key] = value if len(value) <= 12 else {"array_length": len(value)}
        elif isinstance(value, dict):
            out[key] = sanitize_payload(value, 800)
        else:
            out[key] = value
    series = payload.get("series")
    if isinstance(series, dict) and series:
        out["final_state"] = {
            name: values[-1] for name, values in series.items() if isinstance(values, list) and values
        }
    return out or None


def _maybe_plot(run_id: str, payload: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    """Persist a Matplotlib PNG so reports and exports embed real output."""
    if not spec.get("options", {}).get("generate_plot", True):
        return None
    try:
        series, xlabel, ylabel = series_from_result(payload)
        if not series:
            return None
        title = f"{payload.get('method', 'computation')}"
        image = plot_service.render_series(
            series, title=title, xlabel=xlabel, ylabel=ylabel,
            scatter=bool(spec.get("options", {}).get("scatter")),
        )
        info = save_artifact(run_id, image)
        info["series_count"] = len(series)
        info["axes"] = {"x": xlabel, "y": ylabel}
        return info
    except Exception as exc:  # noqa: BLE001 - plotting must not fail a run
        logger.warning("plot generation skipped", extra={"run_id": run_id, "event": "plot_error"})
        return {"error": f"plot could not be generated ({type(exc).__name__})"}


def schedule(run_id: str, *, timeout: float | None = None) -> None:
    """Fire-and-forget scheduling for ``wait=false`` submissions."""
    task = asyncio.create_task(_guard(run_id, timeout))
    ACTIVE_TASKS[run_id] = task
    task.add_done_callback(lambda _t: ACTIVE_TASKS.pop(run_id, None))


async def _guard(run_id: str, timeout: float | None) -> None:
    try:
        await execute_run(run_id, timeout=timeout)
    except asyncio.CancelledError:
        _finish(run_id, "cancelled", None, None,
                error={"code": "cancelled", "message": "job cancelled before completion"})
        raise
    except JobError as exc:
        logger.info("scheduled job ended with error", extra={"run_id": run_id, "event": exc.code})
    except Exception:  # noqa: BLE001
        logger.exception("scheduled job crashed")


def request_cancel(run_id: str) -> tuple[bool, str]:
    """Mark a run cancelled. Returns (ok, human-readable state)."""
    with session_scope() as db:
        run = db.get(ExperimentRun, run_id)
        if run is None:
            return False, "not_found"
        if run.status in TERMINAL_STATUSES:
            return False, f"already_finished:{run.status}"
        run.cancel_requested = True
        if run.status == "queued":
            run.status = "cancelled"
            run.error = {"code": "cancelled", "message": "cancelled before execution started"}
            task = ACTIVE_TASKS.pop(run_id, None)
            if task is not None and not task.done():
                task.cancel()
            return True, "cancelled_while_queued"
        return True, "cancel_flagged_during_execution"

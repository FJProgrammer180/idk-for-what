"""Agent tools: inspect, save and compare stored experiments."""

from __future__ import annotations

from typing import Any

from pydantic import Field
from sqlalchemy import select

from app.db.session import session_scope
from app.models.entities import Experiment, ExperimentRun
from app.services.verification import verify_result as run_verifier
from app.tools.base import ToolContext, ToolError, ToolInput, register


class InspectExperimentInput(ToolInput):
    experiment_id: str = Field(description="Experiment id owned by the authenticated user.")
    run_id: str | None = Field(default=None, description="Inspect one specific run instead of the latest.")
    include_iteration_table: bool = Field(default=False, description="Include up to 25 iteration rows.")


@register(
    name="inspect_experiment",
    category="records",
    read_only=True,
    description=(
        "Retrieve a stored experiment with its inputs, latest run output, verification "
        "record and reproduction metadata. Only experiments owned by the authenticated "
        "user (or its session project) are reachable."
    ),
    input_model=InspectExperimentInput,
)
def inspect_experiment(args: InspectExperimentInput, context: ToolContext) -> dict[str, Any]:
    with session_scope() as db:
        experiment = db.get(Experiment, args.experiment_id)
        if experiment is None or experiment.owner_id != context.user_id:
            raise ToolError(
                "forbidden",
                "experiment not found in your account. Cross-user inspection is denied.",
            )
        if args.run_id:
            run = db.get(ExperimentRun, args.run_id)
            if run is None or run.experiment_id != experiment.id or run.owner_id != context.user_id:
                raise ToolError("not_found", "run is not part of this experiment")
        else:
            run = db.execute(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment.id)
                .order_by(ExperimentRun.created_at.desc())
            ).scalars().first()
        runs = db.execute(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_id == experiment.id)
            .order_by(ExperimentRun.created_at.desc())
        ).scalars().all()
        payload = dict((run.structured_output if run else None) or {})
        table = []
        if args.include_iteration_table:
            table = (payload.get("iterations") or [])[:25]
        return {
            "status": "completed",
            "experiment": {
                "id": experiment.id,
                "title": experiment.title,
                "kind": experiment.kind,
                "problem_type": experiment.problem_type,
                "method": experiment.method,
                "status": experiment.status,
                "project_id": experiment.project_id,
                "created_at": experiment.created_at.isoformat() if experiment.created_at else None,
                "updated_at": experiment.updated_at.isoformat() if experiment.updated_at else None,
                "notes": experiment.notes,
                "template_key": experiment.template_key,
                "inputs": experiment.input_parameters,
                "result_metadata": experiment.result_metadata,
            },
            "run_count": len(runs),
            "runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "execution_time_ms": r.execution_time_ms,
                    "verification_status": (r.verification or {}).get("status"),
                    "error": r.error,
                }
                for r in runs[:10]
            ],
            "latest_run": {
                "id": run.id,
                "status": run.status,
                "execution_config": run.execution_config,
                "headline_result": payload.get("result"),
                "error_estimate": payload.get("error_estimate"),
                "convergence_achieved": payload.get("convergence_achieved"),
                "warnings": payload.get("warnings", []),
                "software_versions": run.software_versions,
                "runtime_metadata": run.runtime_metadata,
                "verification": run.verification,
                "iteration_table": table,
            }
            if run
            else None,
        }


class SaveExperimentInput(ToolInput):
    title: str = Field(max_length=200, description="Short descriptive title.")
    result_handle: str | None = Field(default=None, description="Scratchpad handle to persist.")
    project_id: str | None = Field(default=None, description="Optional project to file it under.")
    notes: str | None = Field(default=None, max_length=4000, description="Analyst notes.")
    experiment_id: str | None = Field(default=None, description="Attach a new run to an existing experiment.")


@register(
    name="save_experiment",
    category="persistence",
    description=(
        "Persist a computation from this session (identified by its result handle) as an "
        "Experiment with an immutable ExperimentRun carrying inputs, output, verification "
        "and software versions for reproduction."
    ),
    input_model=SaveExperimentInput,
)
def save_experiment(args: SaveExperimentInput, context: ToolContext) -> dict[str, Any]:
    if not args.result_handle:
        raise ToolError(
            "missing_input",
            "result_handle is required: run a computation tool first and save the handle it returned.",
        )
    entry = context.scratchpad.get(args.result_handle)
    if not entry:
        raise ToolError(
            "unknown_handle",
            f"handle '{args.result_handle}' is not in this session; "
            f"available: {[k for k in context.scratchpad if not k.startswith('__')]}",
        )
    from app.tools.numerics import _persist

    payload = entry["payload"]
    verification = entry.get("verification") or run_verifier(payload, {"params": entry.get("params") or {}})
    title = args.title
    if args.experiment_id:
        with session_scope() as db:
            experiment = db.get(Experiment, args.experiment_id)
            if experiment is None or experiment.owner_id != context.user_id:
                raise ToolError("forbidden", "experiment not found in your account")
            from app.core.security import sanitize_payload, utcnow
            from app.numerical.common import software_versions as versions

            run = ExperimentRun(
                experiment_id=experiment.id,
                owner_id=context.user_id,
                status="completed",
                execution_config={"kind": entry["kind"], "method": entry["method"],
                                  "params": entry.get("params") or {}},
                structured_output=sanitize_payload(payload, 2_000_000),
                verification=sanitize_payload(verification, 200_000),
                runtime_metadata={"origin": "agent_tool", "agent_session_id": context.session_id,
                                  "notes": args.notes or ""},
                software_versions=versions(),
                finished_at=utcnow(),
                execution_time_ms=float(payload.get("execution_time_ms") or 0.0),
            )
            db.add(run)
            db.flush()
            return {
                "status": "completed",
                "experiment_id": experiment.id,
                "run_id": run.id,
                "note": "new run appended to the existing experiment",
            }
    saved = _persist(
        context,
        title=title,
        problem_type=str(entry.get("payload", {}).get("job_kind") or entry["kind"]),
        method=entry["method"],
        params=entry.get("params") or {},
        payload=payload,
        verification=verification,
        kind=entry["kind"],
    )
    if args.notes or args.project_id:
        with session_scope() as db:
            experiment = db.get(Experiment, saved["experiment_id"])
            if experiment is not None:
                if args.notes:
                    experiment.notes = args.notes
                if args.project_id:
                    from app.models.entities import Project

                    project = db.get(Project, args.project_id)
                    if project is None or project.owner_id != context.user_id:
                        raise ToolError("forbidden", "project not found in your account")
                    experiment.project_id = project.id
    saved["verification_status"] = verification.get("status")
    saved["status"] = "completed"
    return saved


class CompareExperimentsInput(ToolInput):
    experiment_ids: list[str] = Field(min_length=2, max_length=8,
                                      description="Experiments to compare side by side.")
    tolerance: float | None = Field(default=None, gt=0, description="Numeric agreement threshold.")


@register(
    name="compare_experiments",
    category="records",
    read_only=True,
    description=(
        "Compare two or more compatible saved experiments: parameters, headline results, "
        "error estimates, convergence status, runtime and verification outcome - with a "
        "compatibility verdict so unlike problems are never silently juxtaposed."
    ),
    input_model=CompareExperimentsInput,
)
def compare_experiments(args: CompareExperimentsInput, context: ToolContext) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with session_scope() as db:
        for exp_id in args.experiment_ids:
            experiment = db.get(Experiment, exp_id)
            if experiment is None or experiment.owner_id != context.user_id:
                raise ToolError("forbidden", f"'{exp_id}' is not an experiment in your account")
            run = db.execute(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment.id)
                .order_by(ExperimentRun.created_at.desc())
            ).scalars().first()
            payload = dict((run.structured_output if run else None) or {})
            rows.append(
                {
                    "experiment_id": experiment.id,
                    "title": experiment.title,
                    "kind": experiment.kind,
                    "method": experiment.method,
                    "problem_type": experiment.problem_type,
                    "inputs": experiment.input_parameters,
                    "run_id": run.id if run else None,
                    "run_status": run.status if run else "no_runs",
                    "convergence_achieved": payload.get("convergence_achieved"),
                    "solver_status": payload.get("status"),
                    "error_estimate": payload.get("error_estimate"),
                    "execution_time_ms": payload.get("execution_time_ms"),
                    "headline_result": _headline(payload),
                    "verification_status": (run.verification or {}).get("status") if run else None,
                    "warnings": payload.get("warnings", [])[:5],
                }
            )
    kinds = {r["kind"] for r in rows}
    methods = {r["method"] for r in rows}
    problem_types = {r["problem_type"] for r in rows}
    numeric_keys = [
        key
        for key in ("root", "integral", "derivative")
        if all(isinstance((r["headline_result"] or {}).get(key), (int, float)) for r in rows)
    ]
    spread = None
    if numeric_keys:
        key = numeric_keys[0]
        values = [float((r["headline_result"] or {})[key]) for r in rows]
        spread = {
            "metric": key,
            "values": values,
            "min": min(values),
            "max": max(values),
            "spread": max(values) - min(values),
            "max_relative_difference": (
                (max(values) - min(values)) / max(abs(min(values)), 1e-12)
                if abs(min(values)) > 1e-12
                else None
            ),
        }
    return {
        "status": "completed",
        "compatible": len(kinds) == 1 and len(problem_types) == 1,
        "compatibility_notes": (
            [] if len(kinds) == 1 else ["different experiment kinds: compare with caution"]
        )
        + ([] if len(problem_types) == 1 else ["different problem types"])
        + (
            []
            if len(methods) == 1
            else [f"{len(methods)} different methods used: {', '.join(sorted(methods))}"]
        ),
        "experiments": rows,
        "numeric_comparison": spread,
        "recommendation": (
            "methods differ - the spread reflects the algorithms, not the physics"
            if len(methods) > 1
            else "same method across runs; differences are attributable to parameters/tolerances"
        ),
    }


def _headline(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") or {}
    keep = ("root", "integral", "derivative", "solution", "r_squared", "max_height",
            "range_at_first_impact", "final_state", "residual_infinity_norm")
    out = {}
    for key in keep:
        value = result.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            out[key] = float(value)
        elif isinstance(value, list) and len(value) <= 12:
            out[key] = [float(v) if isinstance(v, (int, float)) else v for v in value]
        elif isinstance(value, dict):
            out[key] = {k: v for k, v in list(value.items())[:6]}
    return out

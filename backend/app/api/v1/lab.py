"""Computational lab: methods catalogue, ephemeral compute, projects,
experiments, runs, artefacts, templates and exports."""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_experiment_owner, assert_project_owner, require_compute_user, require_user
from app.core.errors import ConflictError, NotFoundError, PayloadTooLargeError
from app.db.session import get_db
from app.models.entities import Dataset, Experiment, ExperimentRun, Project, User
from app.numerical.common import NumericalInputError, software_versions
from app.numerical.engine import catalog as method_catalog
from app.numerical.engine import accepted_parameters, describe as describe_numerics
from app.numerical.engine import run_numerical_method
from app.physics.engine import run_simulation, simulation_catalog
from app.schemas.common import (
    ComputeRequest,
    ExperimentCreate,
    ExperimentOut,
    Page,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    RunCreate,
    RunOut,
    TemplateInstantiateRequest,
)
from app.services import jobs
from app.services.experiment_templates import list_templates, template_by_key
from app.services.pagination import paginate, page_params
from app.services.plots import render_series, series_from_result
from app.services.verification import verify_result

router = APIRouter(tags=["lab"])


# --- helpers -----------------------------------------------------------------


def run_to_model(run: ExperimentRun, *, include_output: bool = True) -> RunOut:
    return RunOut(
        run_id=run.id,
        experiment_id=run.experiment_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        execution_time_ms=run.execution_time_ms,
        execution_config=run.execution_config or {},
        structured_output=(run.structured_output or {}) if include_output else {},
        verification=run.verification,
        runtime_metadata=run.runtime_metadata or {},
        software_versions=run.software_versions or {},
        error=run.error,
        cancel_requested=bool(run.cancel_requested),
    )


def experiment_to_model(
    experiment: Experiment, *, include_runs: int = 1, include_output: bool = True
) -> dict[str, Any]:
    data = ExperimentOut(
        id=experiment.id,
        title=experiment.title,
        kind=experiment.kind,
        problem_type=experiment.problem_type,
        method=experiment.method,
        status=experiment.status,
        project_id=experiment.project_id,
        notes=experiment.notes,
        template_key=experiment.template_key,
        input_parameters=experiment.input_parameters or {},
        result_metadata=experiment.result_metadata or {},
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
        run_count=len(experiment.runs),
        latest_run=None,
    ).model_dump()
    runs = list(experiment.runs or [])[: max(0, include_runs)]
    data["latest_run"] = (
        run_to_model(runs[0], include_output=include_output).model_dump() if runs else None
    )
    if include_runs > 1:
        data["runs"] = [run_to_model(run, include_output=False).model_dump() for run in runs]
    return data


def _spec_for(experiment: Experiment, override: dict[str, Any] | None) -> dict[str, Any]:
    stored = dict(experiment.input_parameters or {})
    kind = str(stored.get("kind") or experiment.kind)
    method = str(stored.get("method") or experiment.method)
    params = dict(stored.get("params") or {})
    if override:
        params = {**params, **{k: v for k, v in override.items() if not k.startswith("_")}}
    return {"kind": kind, "method": method, "params": params, "options": {"generate_plot": True}}


# --- catalogue + synchronous compute ----------------------------------------

METHODS_ROUTER = APIRouter(prefix="/compute", tags=["compute"])

# how each method's error bar is produced, so the UI can state it instead of implying
# a generic tolerance.  Keys missing here fall back to the method's own message.
ERROR_ESTIMATES: dict[str, str] = {
    "bisection": "half the final bracket width (a rigorous bound, not an estimate)",
    "newton_raphson": "last |step| and |f(x)|; no a-posteriori error bound is claimed",
    "secant": "|x_k - x_{k-1}| from the final two iterates",
    "trapezoidal": "Richardson comparison against the doubled-panel rule (O(h^2) leading term)",
    "simpson": "Richardson comparison against the doubled-panel rule (O(h^4) leading term)",
    "finite_difference": "agreement between successive step halvings",
    "gaussian_elimination": "infinity-norm residual ||b - Ax|| with partial pivoting",
    "lu_decomposition": "||A - P@L@U|| reconstruction error plus the solve residual",
    "jacobi": "max-norm change between successive sweeps (contraction estimate)",
    "gauss_seidel": "max-norm change between successive sweeps (contraction estimate)",
    "ode_ivp": "solver rtol/atol with the observed local error between accepted steps",
    "least_squares": "no single error bar: residual RMS, R^2 and parameter standard errors",
}

EXAMPLES: dict[str, dict[str, object]] = {
    "bisection": {"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-12},
    "newton_raphson": {"expression": "cos(x) - x", "x0": 0.5, "tolerance": 1e-12},
    "secant": {"expression": "x^2 - 2", "x0": 1.0, "x1": 2.0, "tolerance": 1e-12},
    "trapezoidal": {"expression": "exp(-x^2)", "a": -2.0, "b": 2.0, "panels": 400},
    "simpson": {"expression": "sin(x)", "a": 0.0, "b": 3.141592653589793, "panels": 100},
    "finite_difference": {"expression": "sin(x)", "x": 1.0, "derivative_order": 2},
    "gaussian_elimination": {"matrix": [[3.0, 1.0], [1.0, 2.0]], "vector": [9.0, 8.0]},
    "lu_decomposition": {"matrix": [[2.0, 1.0], [1.0, 3.0]], "vector": [3.0, 4.0]},
    "jacobi": {"matrix": [[10.0, 2.0], [1.0, 8.0]], "vector": [12.0, 9.0]},
    "gauss_seidel": {"matrix": [[10.0, 2.0], [1.0, 8.0]], "vector": [12.0, 9.0]},
    "ode_ivp": {
        "expressions": ["y2", "-y1"],
        "t_span": [0.0, 6.283185307179586],
        "y0": [1.0, 0.0],
        "state_names": ["x", "v"],
    },
    "least_squares": {"x": [0.0, 1.0, 2.0], "y": [1.1, 2.0, 2.9], "model": "linear"},
}



@METHODS_ROUTER.get("/methods", summary="Registered numerical methods and their preconditions")
async def methods() -> dict[str, Any]:
    return describe_numerics()


@METHODS_ROUTER.get("/methods/{method_name}", summary="One numerical method in full detail")
async def method_detail(method_name: str) -> dict[str, Any]:
    """Preconditions, iteration record schema and example inputs for a single method."""
    from app.core.errors import NotFoundError
    from app.numerical.engine import METHODS

    name = method_name.strip().lower()
    spec = METHODS.get(name)
    if spec is None:
        raise NotFoundError(
            f"method '{method_name}' is not registered",
            details={"available": sorted(METHODS)},
        )
    return {
        "method": name,
        **spec,
        "error_estimate": ERROR_ESTIMATES.get(name, "the method reports its own bound"),
        "example_parameters": EXAMPLES.get(name, {}),
        "software_versions": software_versions(),
    }


@METHODS_ROUTER.get("/simulations/{sim_key}", summary="One simulation: model, assumptions, parameters")
async def simulation_detail(sim_key: str) -> dict[str, Any]:
    from app.core.errors import NotFoundError
    from app.physics.engine import SIM_SPECS

    key = sim_key.strip().lower()
    spec = SIM_SPECS.get(key)
    if spec is None:
        raise NotFoundError(
            f"simulation '{sim_key}' is not registered",
            details={"available": sorted(SIM_SPECS)},
        )
    return {
        "key": key,
        "title": spec.title,
        "category": spec.category,
        "governing_equations": spec.equations,
        "assumptions": spec.assumptions,
        "limitations": spec.limitations,
        "parameters": spec.parameters,
        "state_variables": getattr(spec, "state_names", []),
        "analytical_validation_available": bool(getattr(spec, "analytical_validation", None)),
    }


@METHODS_ROUTER.get("/simulations", summary="Registered physics simulations")
async def simulations() -> dict[str, Any]:
    return {"simulations": simulation_catalog()}


@METHODS_ROUTER.post(
    "/simulate",
    summary="Run a registered simulation synchronously (not persisted)",
)
async def simulate(
    payload: ComputeRequest, user: User = Depends(require_compute_user)
) -> dict[str, Any]:
    if payload.kind != "simulation":
        raise NumericalInputError("set kind='simulation' for this endpoint, or use /compute/run")
    params = dict(payload.parameters or {})
    if payload.max_iterations:
        params["n_points"] = min(int(payload.max_iterations) * 20, 20000)
    result = run_simulation(payload.method, params)
    verification = verify_result(result, {"params": params})
    return {
        "mode": "ephemeral",
        "note": "this response is NOT stored; create an experiment to persist it",
        "owner_scope": user.id[:8] + "…",
        "software_versions": software_versions(),
        "result": result,
        "verification": verification,
    }


@METHODS_ROUTER.post("/run", summary="Run a numerical method synchronously (not persisted)")
async def compute_run(
    payload: ComputeRequest, user: User = Depends(require_compute_user)
) -> dict[str, Any]:
    params = dict(payload.parameters or {})
    if payload.tolerance is not None:
        params["tolerance"] = payload.tolerance
    from app.services.security_limits import account_iteration_cap

    cap, cap_record = account_iteration_cap(user.id, payload.max_iterations or params.get("max_iterations"))
    if payload.kind == "numerical":
        if "max_iterations" in accepted_parameters(payload.method):
            params["max_iterations"] = cap
        else:
            params.pop("max_iterations", None)
            cap_record = {**cap_record, "applies": False,
                          "note": f"'{payload.method}' is a direct method: no iteration budget applies"}
        result = run_numerical_method(payload.method, params)
        payload_out = result.to_dict(max_iterations=200)
    elif payload.kind == "simulation":
        payload_out = run_simulation(payload.method, params)
    else:
        raise NumericalInputError(
            "kind='analysis' requires a stored dataset; POST /datasets/{id}/analyze instead"
        )
    verification = verify_result(payload_out, {"params": params})
    image: str | None = None
    if cap_record.get("clamped") and cap_record.get("applies", True) and isinstance(payload_out, dict):
        payload_out.setdefault("warnings", []).append(
            f"max_iterations reduced from {cap_record['requested']} to {cap} "
            f"({cap_record['source']} ceiling)"
        )
    if payload.generate_plot:
        series, xlabel, ylabel = series_from_result(payload_out)
        if series:
            image = _encode_png(
                render_series(series, title=str(payload.method), xlabel=xlabel, ylabel=ylabel,
                              scatter=payload.kind == "simulation")
            )
    return {
        "mode": "ephemeral",
        "note": "this response is NOT stored; create an experiment to persist it",
        "limits_applied": cap_record,
        "inputs": {"kind": payload.kind, "method": payload.method, "parameters": params},
        "result": payload_out,
        "verification": verification,
        "software_versions": software_versions(),
        "plot_png_base64": image,
    }


def _encode_png(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


# --- projects ---------------------------------------------------------------

PROJECTS_ROUTER = APIRouter(prefix="/projects", tags=["projects"])


@PROJECTS_ROUTER.get("", response_model=dict, summary="List projects")
async def list_projects(
    request: Request,
    archived: bool | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
) -> dict[str, Any]:
    stmt = (
        select(Project, func.count(Experiment.id).label("experiment_count"))
        .outerjoin(Experiment, Experiment.project_id == Project.id)
        .where(Project.owner_id == user.id)
        .group_by(Project.id)
        .order_by(Project.updated_at.desc())
    )
    if archived is not None:
        stmt = stmt.where(Project.is_archived.is_(archived))
    total = db.execute(select(func.count()).select_from(Project).where(Project.owner_id == user.id)).scalar_one()
    rows = db.execute(stmt.limit(page["limit"]).offset(page["offset"])).all()
    items = []
    for project, count in rows:
        data = ProjectOut(
            id=project.id,
            name=project.name,
            description=project.description,
            is_archived=project.is_archived,
            created_at=project.created_at,
            updated_at=project.updated_at,
            experiment_count=int(count or 0),
        ).model_dump()
        items.append(data)
    return {
        "items": items,
        "page": page["offset"] // page["limit"] + 1,
        "page_size": page["limit"],
        "total": int(total),
        "total_pages": math.ceil(int(total) / page["limit"]) if total else 0,
        "has_more": page["offset"] + len(items) < int(total),
    }


@PROJECTS_ROUTER.post("", response_model=ProjectOut, status_code=201, summary="Create a project")
async def create_project(
    payload: ProjectCreate, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> ProjectOut:
    project = Project(owner_id=user.id, name=payload.name.strip()[:160], description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        is_archived=project.is_archived,
        created_at=project.created_at,
        updated_at=project.updated_at,
        experiment_count=0,
    )


@PROJECTS_ROUTER.get("/{project_id}", summary="Project detail with its experiments")
async def get_project(
    project_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = assert_project_owner(db.get(Project, project_id), user)
    experiments = db.execute(
        select(Experiment)
        .where(Experiment.project_id == project.id)
        .order_by(Experiment.created_at.desc())
        .limit(100)
    ).scalars().all()
    return {
        "project": ProjectOut(
            id=project.id,
            name=project.name,
            description=project.description,
            is_archived=project.is_archived,
            created_at=project.created_at,
            updated_at=project.updated_at,
            experiment_count=len(experiments),
        ).model_dump(),
        "experiments": [experiment_to_model(exp, include_runs=0, include_output=False) for exp in experiments],
    }


@PROJECTS_ROUTER.patch("/{project_id}", summary="Update a project")
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = assert_project_owner(db.get(Project, project_id), user)
    if payload.name is not None:
        project.name = payload.name.strip()[:160]
    if payload.description is not None:
        project.description = payload.description
    if payload.is_archived is not None:
        project.is_archived = payload.is_archived
    db.commit()
    return {"status": "updated", "id": project.id}


@PROJECTS_ROUTER.delete("/{project_id}", status_code=204, summary="Delete a project and its runs")
async def delete_project(
    project_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Response:
    project = assert_project_owner(db.get(Project, project_id), user)
    db.delete(project)
    db.commit()
    return Response(status_code=204)


# --- experiments ------------------------------------------------------------

EXPERIMENTS_ROUTER = APIRouter(prefix="/experiments", tags=["experiments"])


@EXPERIMENTS_ROUTER.get("/templates", summary="Bundled experiment templates")
async def templates() -> dict[str, Any]:
    return {"templates": list_templates()}


@EXPERIMENTS_ROUTER.get("", summary="List experiments owned by the caller")
async def list_experiments(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
    project_id: str | None = Query(default=None),
    method: str | None = Query(default=None, max_length=64),
    kind: str | None = Query(default=None, pattern="^(numerical|simulation|analysis)$"),
    q: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    stmt = select(Experiment).where(Experiment.owner_id == user.id)
    if project_id:
        stmt = stmt.where(Experiment.project_id == project_id)
    if method:
        stmt = stmt.where(Experiment.method == method.strip().lower())
    if kind:
        stmt = stmt.where(Experiment.kind == kind)
    if q:
        stmt = stmt.where(Experiment.title.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(Experiment.created_at.desc())
    result = paginate(stmt, db, page, mapper=lambda row: experiment_to_model(row, include_runs=0, include_output=False))
    return result


@EXPERIMENTS_ROUTER.get("/{experiment_id}/runs", summary="Run history for one experiment (newest first)")
async def list_experiment_runs(
    experiment_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    page: dict = Depends(page_params),
    status: str | None = Query(default=None, max_length=32, description="filter by run status"),
) -> dict[str, Any]:
    """Lightweight history list: outputs are omitted, fetch /runs/{id} for a payload."""
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    stmt = select(ExperimentRun).where(ExperimentRun.experiment_id == experiment.id)
    if status:
        stmt = stmt.where(ExperimentRun.status == status.strip().lower())
    stmt = stmt.order_by(ExperimentRun.created_at.desc())
    return paginate(stmt, db, page, mapper=lambda row: run_to_model(row, include_output=False))


@EXPERIMENTS_ROUTER.post("", status_code=201, summary="Create an experiment (validated against the method registry)")
async def create_experiment(
    payload: ExperimentCreate, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    spec = _validate_spec(payload.kind, payload.method, payload.parameters)
    if payload.project_id:
        assert_project_owner(db.get(Project, payload.project_id), user)
    experiment = Experiment(
        owner_id=user.id,
        project_id=payload.project_id,
        title=payload.title.strip()[:200],
        kind=payload.kind,
        problem_type=payload.problem_type,
        method=payload.method,
        input_parameters=spec,
        status="draft",
        notes=payload.notes,
        template_key=payload.template_key,
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment_to_model(experiment, include_runs=0, include_output=False)


@EXPERIMENTS_ROUTER.post("/{experiment_id}/duplicate", status_code=201, summary="Duplicate an experiment")
async def duplicate_experiment(
    experiment_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    source = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    clone = Experiment(
        owner_id=user.id,
        project_id=source.project_id,
        title=f"{source.title} (copy)"[:200],
        kind=source.kind,
        problem_type=source.problem_type,
        method=source.method,
        input_parameters=dict(source.input_parameters or {}),
        status="draft",
        notes=source.notes,
        template_key=source.template_key,
    )
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return experiment_to_model(clone, include_runs=0, include_output=False)


@EXPERIMENTS_ROUTER.post("/templates/{template_key}", status_code=201, summary="Instantiate a template")
async def instantiate_template(
    template_key: str,
    payload: TemplateInstantiateRequest | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create a draft experiment from a bundled template, with optional overrides."""
    template = template_by_key(template_key)
    if template is None:
        raise NotFoundError(f"template '{template_key}' is not bundled with this installation")
    body = payload or TemplateInstantiateRequest()
    overrides = dict(body.parameters or {})
    params = {**template.get("parameters", {}), **overrides}
    kind = template.get("kind", "numerical")
    spec = _validate_spec(kind, template["method"], params)
    title = (body.title or template.get("title") or template_key)[:200]
    notes = (body.notes if body.notes is not None else str(template.get("description", "")))[:4000]
    if body.project_id:
        assert_project_owner(db.get(Project, body.project_id), user)
    experiment = Experiment(
        owner_id=user.id,
        title=title,
        kind=kind,
        problem_type=template.get("problem_type", "root_finding"),
        method=template["method"],
        input_parameters=spec,
        status="draft",
        notes=notes,
        template_key=template_key,
        project_id=body.project_id,
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return {**experiment_to_model(experiment, include_runs=0, include_output=False),
            "template": {"key": template_key, "assumptions": template.get("assumptions", [])}}


@EXPERIMENTS_ROUTER.get("/{experiment_id}", summary="Experiment detail")
async def get_experiment(
    experiment_id: str,
    include_runs: int = Query(default=1, ge=0, le=50),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    return experiment_to_model(experiment, include_runs=include_runs)


@EXPERIMENTS_ROUTER.patch("/{experiment_id}", summary="Update inputs or notes")
async def update_experiment(
    experiment_id: str,
    payload: dict,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    body = payload or {}
    if "title" in body:
        title = str(body["title"]).strip()
        if len(title) < 3 or len(title) > 200:
            raise ConflictError("title must be 3-200 characters")
        experiment.title = title
    if "parameters" in body:
        spec = _validate_spec(experiment.kind, experiment.method, dict(body["parameters"] or {}))
        experiment.input_parameters = spec
    if "notes" in body:
        experiment.notes = str(body["notes"])[:4000]
    if "project_id" in body and body["project_id"]:
        assert_project_owner(db.get(Project, str(body["project_id"])), user)
        experiment.project_id = str(body["project_id"])
    db.commit()
    return experiment_to_model(experiment, include_runs=0, include_output=False)


@EXPERIMENTS_ROUTER.delete("/{experiment_id}", status_code=204, summary="Delete an experiment")
async def delete_experiment(
    experiment_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Response:
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    db.delete(experiment)
    db.commit()
    return Response(status_code=204)


@EXPERIMENTS_ROUTER.post("/{experiment_id}/runs", summary="Execute a run (async, pollable)")
async def create_run(
    experiment_id: str,
    payload: RunCreate,
    request: Request,
    user: User = Depends(require_compute_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    spec = _spec_for(experiment, payload.parameters)
    _validate_spec(spec["kind"], spec["method"], spec["params"])
    spec.setdefault("options", {})["generate_plot"] = payload.generate_plot

    if payload.idempotency_key:
        existing = db.execute(
            select(ExperimentRun).where(
                ExperimentRun.experiment_id == experiment.id,
                ExperimentRun.idempotency_key == payload.idempotency_key,
            )
        ).scalars().first()
        if existing is not None:
            return {
                "status": existing.status,
                "run": run_to_model(existing).model_dump(),
                "idempotent_replay": True,
                "poll_url": f"{settings.api_v1_prefix}/runs/{existing.id}",
            }

    run = ExperimentRun(
        experiment_id=experiment.id,
        owner_id=user.id,
        status="queued",
        execution_config=spec,
        runtime_metadata={"request_id": getattr(request.state, "request_id", None)},
        idempotency_key=payload.idempotency_key,
        software_versions=software_versions(),
    )
    db.add(run)
    experiment.status = "queued"
    db.commit()
    db.refresh(run)
    run_id = run.id

    timeout = float(min(user.compute_timeout_seconds, settings.compute_timeout_seconds))
    if payload.wait:
        try:
            await jobs.execute_run(run_id, timeout=timeout)
        except jobs.JobError as exc:
            db.expire_all()
            run = db.get(ExperimentRun, run_id)
            return {
                "status": run.status if run else "failed",
                "run": run_to_model(run).model_dump() if run else None,
                "error": {"code": exc.code, "message": exc.message, "retryable": exc.code in ("timeout",)},
                "poll_url": f"{settings.api_v1_prefix}/runs/{run_id}",
            }
    else:
        jobs.schedule(run_id, timeout=timeout)

    db.expire_all()
    run = db.get(ExperimentRun, run_id)
    body = {
        "status": run.status if run else "queued",
        "run": run_to_model(run).model_dump() if run else None,
        "poll_url": f"{settings.api_v1_prefix}/runs/{run_id}",
        "async": not payload.wait,
    }
    if not payload.wait:
        body["message"] = (
            f"job queued (status will move {jobs.STATUSES[:3]} → terminal); poll the run endpoint"
        )
    return body


@EXPERIMENTS_ROUTER.get("/{experiment_id}/export", summary="Export an experiment as JSON or CSV")
async def export_experiment(
    experiment_id: str,
    format: str = Query(default="json", pattern="^(json|csv)$"),
    run_id: str | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    experiment = assert_experiment_owner(db.get(Experiment, experiment_id), user)
    if run_id:
        run = db.get(ExperimentRun, run_id)
        if run is None or run.experiment_id != experiment.id or run.owner_id != user.id:
            raise NotFoundError("run not found for this experiment")
    else:
        run = (experiment.runs or [None])[0]
    if run is None:
        raise NotFoundError("this experiment has no runs to export")
    payload = dict(run.structured_output or {})
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in experiment.title)[:60]
    if format == "json":
        document = {
            "experiment_id": experiment.id,
            "title": experiment.title,
            "kind": experiment.kind,
            "method": experiment.method,
            "inputs": experiment.input_parameters,
            "status": experiment.status,
            "run": {
                "id": run.id,
                "status": run.status,
                "execution_config": run.execution_config,
                "output": payload,
                "verification": run.verification,
                "software_versions": run.software_versions,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            },
            "exported_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(timespec="seconds"),
        }
        return Response(
            content=json.dumps(document, indent=2, default=str),
            media_type="application/json",
            headers={"content-disposition": f'attachment; filename="experiment_{safe_name}.json"'},
        )
    records = payload.get("iterations") or []
    buffer = io.StringIO()
    if records:
        keys = list({k for row in records for k in row.keys()})
        ordered = [k for k in ("index", "a", "b", "x", "f_x", "step", "error") if k in keys]
        ordered += [k for k in keys if k not in ordered]
        writer = csv.DictWriter(buffer, fieldnames=ordered, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow({k: _csv_safe(row.get(k)) for k in ordered})
    else:
        writer = csv.writer(buffer)
        writer.writerow(["key", "value"])
        for key, value in (payload.get("result") or {}).items():
            writer.writerow([key, _csv_safe(value)])
    body = buffer.getvalue().encode("utf-8")
    return Response(
        content=body,
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="experiment_{safe_name}.csv"'},
    )


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in exported cells."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    if isinstance(value, (list, dict)):
        text = json.dumps(value, default=str)
        return "'" + text if text[:1] in ("=", "+", "-", "@") else text
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


# --- runs --------------------------------------------------------------------

RUNS_ROUTER = APIRouter(prefix="/runs", tags=["runs"])


@RUNS_ROUTER.get("/{run_id}", summary="Run status and structured output")
async def get_run(
    run_id: str,
    include: str = Query(default="full", pattern="^(status|full)$"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(ExperimentRun, run_id)
    if run is None or run.owner_id != user.id:
        raise NotFoundError("run not found in your account")
    experiment = db.get(Experiment, run.experiment_id)
    body: dict[str, Any] = {
        "run_id": run.id,
        "experiment_id": run.experiment_id,
        "experiment_title": experiment.title if experiment else None,
        "status": run.status,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "execution_time_ms": run.execution_time_ms,
        "cancel_requested": bool(run.cancel_requested),
        "error": run.error,
        "runtime_metadata": run.runtime_metadata or {},
    }
    if include == "full":
        body["structured_output"] = run.structured_output or {}
        body["verification"] = run.verification
        body["execution_config"] = run.execution_config or {}
        body["software_versions"] = run.software_versions or {}
    return body


@RUNS_ROUTER.post("/{run_id}/cancel", summary="Request cancellation")
async def cancel_run(
    run_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(ExperimentRun, run_id)
    if run is None or run.owner_id != user.id:
        raise NotFoundError("run not found in your account")
    ok, detail = jobs.request_cancel(run_id)
    if not ok and detail.startswith("already_finished"):
        return {"status": run.status, "cancelled": False, "detail": detail,
                "note": "the job had already reached a terminal state"}
    db.expire_all()
    run = db.get(ExperimentRun, run_id)
    return {
        "status": run.status if run else "unknown",
        "cancelled": ok,
        "detail": detail,
        "limitation": (
            "a CPU-bound worker thread cannot be force-killed; its result is discarded and "
            "the run is marked cancelled" if detail == "cancel_flagged_during_execution" else None
        ),
    }


@RUNS_ROUTER.post("/{run_id}/verify", summary="Re-run independent verification")
async def verify_run(
    run_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(ExperimentRun, run_id)
    if run is None or run.owner_id != user.id:
        raise NotFoundError("run not found in your account")
    verification = verify_result(dict(run.structured_output or {}), {"params": (run.execution_config or {}).get("params") or {}})
    run.verification = verification
    db.commit()
    return {"run_id": run.id, "verification": verification}


@RUNS_ROUTER.get("/{run_id}/plot.png", summary="Stored Matplotlib figure for this run")
async def run_plot(
    run_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> Response:
    run = db.get(ExperimentRun, run_id)
    if run is None or run.owner_id != user.id:
        raise NotFoundError("run not found in your account")
    artifact = (run.runtime_metadata or {}).get("artifact") or {}
    rel = artifact.get("path") if isinstance(artifact, dict) else None
    if not rel:
        raise NotFoundError("no stored figure for this run")
    path = (Path(settings.data_dir) / rel).resolve()
    if not str(path).startswith(str(Path(settings.data_dir).resolve())) or not path.exists():
        raise NotFoundError("stored figure could not be read")
    return FileResponse(path, media_type="image/png", filename=path.name)


@RUNS_ROUTER.get("/{run_id}/chart-data", summary="Plot-ready arrays for the UI charts")
async def run_chart_data(
    run_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    run = db.get(ExperimentRun, run_id)
    if run is None or run.owner_id != user.id:
        raise NotFoundError("run not found in your account")
    payload = dict(run.structured_output or {})
    series, xlabel, ylabel = series_from_result(payload)
    return {
        "run_id": run.id,
        "status": run.status,
        "method": payload.get("method"),
        "axes": {"x": xlabel, "y": ylabel},
        "series": series,
        "iteration_table": (payload.get("iterations") or [])[:500],
        "units": payload.get("units") or {},
        "note": "all points are the values returned by the computation - none are synthesised",
    }


def _validate_spec(kind: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fail fast (and never execute) when inputs violate a method's preconditions."""
    from app.numerical.engine import METHODS

    if kind == "numerical":
        if method not in METHODS:
            raise NumericalInputError(
                f"method '{method}' is not registered. Available: {', '.join(sorted(METHODS))}"
            )
        spec = METHODS[method]
        missing = [key for key in spec["requires"] if key not in params or params[key] in (None, "", [])]
        if missing:
            raise NumericalInputError(
                f"'{method}' is missing required input(s): {', '.join(missing)}. "
                "See GET /compute/methods for the documented requirements."
            )
    elif kind == "simulation":
        from app.physics.engine import SIM_SPECS

        if method not in SIM_SPECS:
            raise NumericalInputError(
                f"simulation '{method}' is not registered. Available: {', '.join(sorted(SIM_SPECS))}"
            )
    elif kind == "analysis":
        if not params.get("dataset_id"):
            raise NumericalInputError("analysis jobs require a 'dataset_id'")
    else:
        raise NumericalInputError(f"unknown job kind '{kind}'")
    return {"kind": kind, "method": method, "params": params, "options": {"generate_plot": True}}

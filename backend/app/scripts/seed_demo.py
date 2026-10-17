"""Idempotent demo seeding: one clearly-labelled demo account with real runs.

Everything here is computed by the actual engine at seed time - no fabricated
result values are written into the database.
"""

from __future__ import annotations

import sys
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.core.security import hash_password, utcnow
from app.db.session import create_all, session_scope
from app.models.entities import Dataset, Experiment, ExperimentRun, Project, User
from app.numerical.common import software_versions
from app.services.execution import perform_job
from app.services.verification import verify_result

DEMO_EXPERIMENTS: list[dict[str, Any]] = [
    {
        "title": "sqrt(2) by bisection - known-answer test",
        "kind": "numerical",
        "problem_type": "root_finding",
        "method": "bisection",
        "parameters": {"expression": "x^2 - 2", "a": 1.0, "b": 2.0,
                        "tolerance": 1e-12, "max_iterations": 60},
        "notes": "Textbook verification case: the true root is 1.4142135623730951.",
    },
    {
        "title": "Dottie number by Newton-Raphson",
        "kind": "numerical",
        "problem_type": "root_finding",
        "method": "newton_raphson",
        "parameters": {"expression": "cos(x) - x", "derivative_expression": "-sin(x) - 1",
                        "x0": 0.5, "tolerance": 1e-14, "max_iterations": 20},
        "notes": "Fixed point of cos; quadratic convergence is visible in the step column.",
    },
    {
        "title": "Gaussian bell area by Simpson's rule",
        "kind": "numerical",
        "problem_type": "integration",
        "method": "simpson",
        "parameters": {"expression": "exp(-x^2/2)/sqrt(2*pi)", "a": -2.0, "b": 2.0,
                        "tolerance": 1e-12, "panels": 200},
        "notes": "Expected 0.9544997361036416 (two-sigma interval of the standard normal).",
    },
    {
        "title": "3x3 structural system by LU factorisation",
        "kind": "numerical",
        "problem_type": "linear_system",
        "method": "lu_decomposition",
        "parameters": {"matrix": [[0, 2, 1], [1, 1, 2], [2, 1, 1]], "vector": [3, 3, 3]},
        "notes": "Verifier rebuilds A from the stored P, L, U factors.",
    },
    {
        "title": "Damped driven pendulum (ODE system)",
        "kind": "numerical",
        "problem_type": "ode_ivp",
        "method": "ode_ivp",
        "parameters": {
            "expressions": ["y2", "-b*y2 - (g/L)*sin(y1) + A*cos(w*t)"],
            "state_names": ["theta", "omega"],
            "t_span": [0, 40],
            "y0": [0.4, 0.0],
            "solver": "DOP853",
            "rtol": 1e-10,
            "atol": 1e-12,
            "n_points": 1200,
            "constants": {"b": 0.15, "g": 9.81, "L": 1.0, "A": 0.9, "w": 0.85},
        },
        "notes": "Chaotic driving: short-time behaviour is reproducible, the far future is not.",
    },
    {
        "title": "Projectile at 40 m/s, 45 deg (vacuum)",
        "kind": "simulation",
        "problem_type": "simulation",
        "method": "projectile_motion",
        "parameters": {"v0": 40.0, "angle_deg": 45.0, "drag_coefficient_total": 0.0,
                        "launch_height": 0.0, "g": 9.80665, "mass": 1.0, "n_points": 1200},
        "notes": "Validated against R = v0^2 sin(2 theta) / g inside the run record.",
    },
    {
        "title": "Coffee cooling to room temperature",
        "kind": "simulation",
        "problem_type": "simulation",
        "method": "newton_cooling",
        "parameters": {"T0": 358.15, "T_env": 295.15, "cooling_rate": 0.006,
                        "duration": 3000.0, "n_points": 800},
        "notes": "Lumped capacitance model; exponential decay is checked against the closed form.",
    },
    {
        "title": "1-D heat diffusion of a sine mode (FTCS)",
        "kind": "simulation",
        "problem_type": "simulation",
        "method": "heat_1d",
        "parameters": {"alpha": 1.0, "length": 1.0, "grid_points": 121, "duration": 0.05,
                        "initial_condition": "sine", "boundary": "dirichlet_zero"},
        "notes": "Stability number r is reported; peak decay compared with exp(-alpha (pi/L)^2 t).",
    },
]


def seed_demo(verbose: bool = False) -> dict[str, int]:
    """Create/refresh the demo account. Safe to call repeatedly."""
    create_all()
    counts = {"users": 0, "experiments": 0, "runs": 0, "datasets": 0, "projects": 0}
    with session_scope() as db:
        user = db.execute(
            select(User).where(func.lower(User.email) == settings.demo_email.lower())
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=settings.demo_email.lower(),
                password_hash=hash_password(settings.demo_password),
                display_name="Demo Researcher",
                role="researcher",
                is_demo=True,
            )
            db.add(user)
            db.flush()
            counts["users"] = 1
        if user.retention_days != 0:
            user.retention_days = 0

        project = db.execute(
            select(Project).where(Project.owner_id == user.id, Project.name == "Demo laboratory")
        ).scalars().first()
        if project is None:
            project = Project(
                owner_id=user.id,
                name="Demo laboratory",
                description=(
                    "Seeded demo workspace. Every value below was produced by the real engine at "
                    "seed time - nothing is hardcoded sample output."
                ),
            )
            db.add(project)
            db.flush()
            counts["projects"] = 1

        existing = db.execute(
            select(func.count()).select_from(Experiment).where(Experiment.owner_id == user.id)
        ).scalar_one()
        if int(existing) == 0:
            for spec in DEMO_EXPERIMENTS:
                job_spec = {
                    "kind": spec["kind"],
                    "method": spec["method"],
                    "params": spec["parameters"],
                    "options": {"generate_plot": True},
                }
                experiment = Experiment(
                    owner_id=user.id,
                    project_id=project.id,
                    title=spec["title"],
                    kind=spec["kind"],
                    problem_type=spec["problem_type"],
                    method=spec["method"],
                    input_parameters=job_spec,
                    status="running",
                    notes=spec.get("notes", ""),
                )
                db.add(experiment)
                db.flush()
                payload = perform_job({**job_spec, "experiment_id": experiment.id}, owner_id=user.id)
                verification = verify_result(payload, job_spec)
                status = (
                    "failed"
                    if payload.get("status") == "failed" or verification["status"] == "failed"
                    else "completed"
                )
                run = ExperimentRun(
                    experiment_id=experiment.id,
                    owner_id=user.id,
                    status=status,
                    execution_config=job_spec,
                    structured_output=payload,
                    verification=verification,
                    runtime_metadata={"origin": "demo_seed", "artifact": _plot(db, experiment.id, payload)},
                    software_versions=software_versions(),
                    finished_at=utcnow(),
                    execution_time_ms=float(payload.get("execution_time_ms") or 0.0),
                )
                db.add(run)
                experiment.status = status
                experiment.result_metadata = {
                    "headline_result": _headline(payload),
                    "convergence_achieved": payload.get("convergence_achieved"),
                    "error_estimate": payload.get("error_estimate"),
                    "verification_status": verification["status"],
                    "origin": "demo_seed",
                }
                db.flush()
                counts["experiments"] += 1
                counts["runs"] += 1
                if verbose:
                    print(f"  seeded {spec['method']:<20} {status:<10} {verification['status']}")
    return counts


def _plot(db, experiment_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    from app.services.plots import render_series, save_artifact, series_from_result

    try:
        series, xlabel, ylabel = series_from_result(payload)
        if not series:
            return None
        image = render_series(
            series, title=str(payload.get("method", "")), xlabel=xlabel, ylabel=ylabel
        )
        return save_artifact(experiment_id, image)
    except Exception:  # noqa: BLE001
        return None


def _headline(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") or {}
    keep = ("root", "integral", "derivative", "solution", "max_height", "range_at_first_impact",
            "r_squared", "final_temperature", "final_max_temperature")
    out = {}
    for key in keep:
        value = result.get(key)
        if isinstance(value, (int, float, str)) or value is None:
            if value is not None:
                out[key] = value
        elif isinstance(value, list) and len(value) <= 8:
            out[key] = value
    if "summary" in payload and isinstance(payload["summary"], dict):
        for key in ("max_height", "range_at_first_impact", "flight_time_to_impact", "period_s",
                    "final_max_temperature"):
            if key in payload["summary"]:
                out[key] = payload["summary"][key]
    return out


def main() -> int:
    verbose = "-v" in sys.argv
    counts = seed_demo(verbose=verbose)
    print(
        "demo seeded: {users} user, {projects} project, {experiments} experiments, "
        "{runs} runs, {datasets} datasets".format(**counts)
    )
    print(f"  login with {settings.demo_email} / (password in .env: PLAB_DEMO_PASSWORD)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Agent tool: run a registered physics simulation."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.numerical.common import NumericalInputError, software_versions
from app.physics.engine import SIM_SPECS, run_simulation
from app.services.verification import verify_result as run_verifier
from app.tools.base import ToolContext, ToolError, ToolInput, register
from app.tools.numerics import _persist, next_handle

MAX_POINTS = 4000


class RunPhysicsSimulationInput(ToolInput):
    simulation: str = Field(
        description="Registered simulation key: " + ", ".join(sorted(SIM_SPECS))
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Physical parameters (see the simulation's parameter spec)."
    )
    initial_conditions: dict[str, float] = Field(
        default_factory=dict,
        description="Initial state values, e.g. {\"theta\": 0.8, \"omega\": 0.0}.",
    )
    resolution: dict[str, Any] = Field(
        default_factory=dict,
        description="Solver controls: {solver, rtol, atol, n_points, grid_points, time_steps}.",
    )
    persist: bool = Field(default=False, description="Also store an Experiment + run record.")
    title: str | None = Field(default=None, max_length=200)

    @field_validator("simulation")
    @classmethod
    def _known(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SIM_SPECS:
            raise ValueError(f"unknown simulation '{v}'; available: {', '.join(sorted(SIM_SPECS))}")
        return v


@register(
    name="run_physics_simulation",
    category="simulation",
    read_only=False,
    description=(
        "Integrate one registered, idealised physics model (projectile motion, damped "
        "harmonic oscillator, Newton cooling, 1-D heat diffusion, preset dynamical systems). "
        "Returns time series, summary metrics, the model assumptions/limitations, solver "
        "metadata and an analytical-validation block. Never executes user code."
    ),
    input_model=RunPhysicsSimulationInput,
)
def run_physics_simulation(args: RunPhysicsSimulationInput, context: ToolContext) -> dict[str, Any]:
    spec = SIM_SPECS[args.simulation]
    params: dict[str, Any] = dict(args.parameters or {})
    for key, value in (args.initial_conditions or {}).items():
        params[key] = value
    for key, value in (args.resolution or {}).items():
        params[key] = value
    # Every simulation reads n_points (the dispatcher clamps output samples), but a
    # spatial grid belongs only to the PDE model: injecting grid_points everywhere made
    # unrelated simulations receive a parameter they do not accept.
    n_points = int(params.get("n_points") or 600)
    params["n_points"] = max(20, min(n_points, MAX_POINTS))
    declared = set(spec.parameters or {})
    for control, low, high in (("grid_points", 5, 2000), ("time_steps", 1, 20000)):
        if control in declared or control in params:
            params[control] = max(low, min(int(params.get(control) or 101), high))
    try:
        payload = run_simulation(args.simulation, params)
    except NumericalInputError as exc:
        raise ToolError(
            "invalid_simulation_parameters",
            str(exc),
            details={"parameters": spec.parameters, "assumptions": spec.assumptions},
        ) from None

    verification = run_verifier(payload, {"params": params})
    handle = next_handle(context)
    context.remember(
        handle,
        {
            "kind": "simulation",
            "method": args.simulation,
            "params": params,
            "payload": payload,
            "verification": verification,
        },
    )
    series = payload.get("series") or {}
    out: dict[str, Any] = {
        "status": "completed",
        "handle": handle,
        "simulation": args.simulation,
        "title": spec.title,
        "governing_equations": spec.equations,
        "assumptions": spec.assumptions,
        "limitations": spec.limitations,
        "parameters_used": payload.get("model", {}).get("parameters", {}),
        "initial_conditions": payload.get("model", {}).get("initial_state", {}),
        "solver": payload.get("solver", {}),
        "summary": payload.get("summary", {}),
        "validation": payload.get("validation", {}),
        "warnings": payload.get("warnings", []),
        "message": payload.get("message"),
        "units": payload.get("units", {}),
        "plot_ready_data": {
            "time_points": len(series.get("time") or []),
            "variables": [k for k in series if k != "time"],
            "downsampled_preview": {
                k: _preview(v) for k, v in series.items()
            },
        },
        "execution_time_ms": payload.get("execution_time_ms"),
        "software_versions": software_versions(),
        "verification": {
            "status": verification["status"],
            "failed_checks": verification["failed_checks"],
        },
        "interpretation_note": (
            "This is an idealised model: effects not listed in assumptions are absent from "
            "the equations, so agreement with a real apparatus is limited by those omissions."
        ),
    }
    if args.persist:
        out["persisted"] = _persist(
            context,
            title=args.title or f"{spec.title} simulation",
            problem_type="simulation",
            method=args.simulation,
            params=params,
            payload=payload,
            verification=verification,
            kind="simulation",
        )
    return out


def _preview(values: list[float], count: int = 12) -> list[float]:
    if not isinstance(values, list) or not values:
        return []
    if len(values) <= count:
        return [round(float(v), 8) for v in values]
    step = max(1, len(values) // (count - 1))
    picked = [values[i] for i in range(0, len(values), step)][: count - 1]
    picked.append(values[-1])
    return [round(float(v), 8) for v in picked]

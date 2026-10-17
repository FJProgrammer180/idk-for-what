"""Agent tools: numerical computation + independent verification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.db.session import session_scope
from app.models.entities import AgentSession, Experiment, ExperimentRun
from app.numerical.common import NumericalInputError, software_versions
from app.numerical.engine import METHODS, run_numerical_method
from app.services.security_limits import enforce_user_limits
from app.services.verification import verify_result as run_verifier
from app.tools.base import ToolContext, ToolError, ToolInput, register

ProblemType = Literal["root_finding", "linear_system", "integration", "derivative", "regression"]

HANDLE_COUNTER_KEY = "__handle_counter"


def next_handle(context: ToolContext) -> str:
    context.scratchpad.setdefault(HANDLE_COUNTER_KEY, 0)
    context.scratchpad[HANDLE_COUNTER_KEY] += 1
    return f"res_{context.scratchpad[HANDLE_COUNTER_KEY]}"


class SolveNumericalProblemInput(ToolInput):
    problem_type: ProblemType = Field(description="Class of problem being solved.")
    method: str = Field(
        description="Registered method name, e.g. bisection, newton_raphson, simpson, "
        "gaussian_elimination, jacobi, finite_difference, least_squares, ode_ivp."
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Method inputs, e.g. {expression, a, b} or {matrix, vector}. "
        "Expressions use the restricted grammar (no code).",
    )
    tolerance: float | None = Field(default=None, gt=0, le=1e-1, description="Stopping tolerance.")
    max_iterations: int | None = Field(default=None, ge=1, le=5000,
                                      description="Iteration/refinement cap.")
    persist: bool = Field(
        default=False,
        description="Create an Experiment + run record automatically (otherwise the "
        "result is returned only and can be saved later with save_experiment).",
    )
    title: str | None = Field(default=None, max_length=200, description="Title when persist=True.")

    @field_validator("method")
    @classmethod
    def _known_method(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in METHODS:
            raise ValueError(
                f"unknown method '{v}'; registered methods are: {', '.join(sorted(METHODS))}"
            )
        return v

    @field_validator("parameters")
    @classmethod
    def _stringify_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        return {str(k): value for k, value in v.items()}


@register(
    name="solve_numerical_problem",
    category="computation",
    read_only=False,
    description=(
        "Run one registered numerical algorithm (root finding, linear systems, "
        "integration, differentiation, ODE IVP, least squares) with validated inputs, "
        "per-iteration records, error estimates and an explicit failure state. "
        "Returns a result handle that save_experiment/verify_result can reference."
    ),
    input_model=SolveNumericalProblemInput,
)
def solve_numerical_problem(args: SolveNumericalProblemInput, context: ToolContext) -> dict[str, Any]:
    spec = METHODS[args.method]
    if spec["problem_type"] != args.problem_type and args.method != "least_squares":
        raise ToolError(
            "problem_type_mismatch",
            f"method '{args.method}' solves {spec['problem_type']} problems, not "
            f"'{args.problem_type}'. Re-classify the task or pick another method.",
            details={"declared_problem_type": spec["problem_type"], "assumptions": spec["assumptions"]},
        )
    missing = [key for key in spec["requires"] if key not in args.parameters or args.parameters[key] in (None, "")]
    if missing:
        raise ToolError(
            "missing_required_inputs",
            f"'{args.method}' requires {', '.join(missing)} which were not supplied. "
            "Call request_clarification instead of guessing values.",
            details={
                "missing": missing,
                "required": spec["requires"],
                "optional": spec["optional"],
                "assumptions": spec["assumptions"],
            },
        )
    params = dict(args.parameters)
    if args.tolerance is not None:
        params["tolerance"] = args.tolerance
    if args.max_iterations is not None:
        params["max_iterations"] = args.max_iterations
    params = enforce_user_params(params, context, args.method)

    try:
        result = run_numerical_method(args.method, params)
    except NumericalInputError as exc:
        raise ToolError(
            "invalid_numerical_input",
            str(exc),
            details={"method": args.method, "assumptions": spec["assumptions"]},
        ) from None

    payload = result.to_dict(max_iterations=120)
    verification = run_verifier(payload, {"params": params})
    handle = next_handle(context)
    context.remember(
        handle,
        {
            "kind": "numerical",
            "problem_type": args.problem_type,
            "method": args.method,
            "params": params,
            "payload": payload,
            "verification": verification,
        },
    )
    out: dict[str, Any] = {
        "status": "completed",
        "handle": handle,
        "method": args.method,
        "method_title": spec["title"],
        "problem_type": args.problem_type,
        "convergence_achieved": result.convergence_achieved,
        "solver_status": result.status,
        "result": payload["result"],
        "error_estimate": result.error_estimate,
        "tolerance": result.tolerance,
        "iterations_used": len(result.iterations),
        "iteration_table_preview": [r.to_dict() for r in result.iterations[:6]],
        "warnings": result.warnings,
        "message": result.message,
        "assumptions_applied": spec["assumptions"],
        "execution_time_ms": result.execution_time_ms,
        "software_versions": software_versions(),
        "verification": {
            "status": verification["status"],
            "failed_checks": verification["failed_checks"],
            "checks": [
                {k: v for k, v in c.items() if k != "checks"} for c in verification["checks"][:8]
            ],
        },
    }
    if args.persist:
        out["persisted"] = _persist(
            context,
            title=args.title or f"{spec['title']} - {args.problem_type}",
            problem_type=args.problem_type,
            method=args.method,
            params=params,
            payload=payload,
            verification=verification,
        )
    return out


def enforce_user_params(params: dict[str, Any], context: ToolContext, method: str) -> dict[str, Any]:
    limits = enforce_user_limits(context)
    if method in ("bisection", "newton_raphson", "secant", "jacobi", "gauss_seidel",
                  "trapezoidal", "simpson", "finite_difference"):
        cap = int(limits.get("max_iterations", 200))
        current = int(params.get("max_iterations") or cap)
        if current > cap:
            params["max_iterations"] = cap
            params["_limit_notes"] = (
                params.get("_limit_notes", []) + [f"max_iterations reduced from {current} to {cap}"]
            )
    if method == "ode_ivp":
        cap = int(limits.get("max_grid_points", 20000))
        if int(params.get("n_points") or 400) > cap:
            params["n_points"] = cap
    return params


def _persist(
    context: ToolContext,
    *,
    title: str,
    problem_type: str,
    method: str,
    params: dict[str, Any],
    payload: dict[str, Any],
    verification: dict[str, Any],
    kind: str = "numerical",
) -> dict[str, Any]:
    from app.core.security import sanitize_payload, utcnow

    with session_scope() as db:
        experiment = Experiment(
            owner_id=context.user_id,
            project_id=_session_project(db, context),
            title=title[:200],
            problem_type=problem_type,
            kind=kind,
            method=method,
            input_parameters=sanitize_payload(
                {"kind": kind, "method": method, "params": params}, 8000
            ),
            status="completed",
            result_metadata=sanitize_payload(
                {
                    "headline_result": payload.get("result"),
                    "convergence_achieved": payload.get("convergence_achieved"),
                    "error_estimate": payload.get("error_estimate"),
                    "solver_status": payload.get("status"),
                    "verification_status": verification.get("status"),
                    "origin": "agent",
                },
                4000,
            ),
        )
        db.add(experiment)
        db.flush()
        run = ExperimentRun(
            experiment_id=experiment.id,
            owner_id=context.user_id,
            status="completed" if payload.get("status") != "failed" else "failed",
            execution_config={"kind": kind, "method": method, "params": params,
                               "params_echo": {k: v for k, v in (params or {}).items() if k != "dataset_id"}},
            structured_output=sanitize_payload(payload, 2_000_000),
            verification=sanitize_payload(verification, 200_000),
            runtime_metadata={
                "execution_time_ms": payload.get("execution_time_ms"),
                "origin": "agent_tool",
                "agent_session_id": context.session_id,
            },
            software_versions=software_versions(),
            finished_at=utcnow(),
            execution_time_ms=float(payload.get("execution_time_ms") or 0.0),
        )
        db.add(run)
        db.flush()
        return {
            "experiment_id": experiment.id,
            "run_id": run.id,
            "status": "persisted",
            "note": "stored with reproduction metadata (inputs, versions, verification)",
        }


def _session_project(db, context: ToolContext) -> str | None:
    if context.project_id:
        return context.project_id
    if context.session_id:
        session = db.get(AgentSession, context.session_id)
        if session is not None:
            return session.project_id
    return None


class VerifyResultInput(ToolInput):
    run_id: str | None = Field(default=None, description="Persisted run to re-verify.")
    handle: str | None = Field(default=None, description="Scratchpad handle from a previous tool call.")
    recompute: bool = Field(
        default=False,
        description="For a stored run, re-execute the job and compare against the stored output.",
    )


@register(
    name="verify_result",
    category="verification",
    read_only=True,
    description=(
        "Run independent validation checks on a computed result: schema integrity, "
        "finiteness, residual/bracket consistency, cross-algorithm references (SciPy quad, "
        "numpy.linalg.solve, SymPy closed forms), monotone error behaviour and, for "
        "simulations, analytical agreement."
    ),
    input_model=VerifyResultInput,
)
def verify_result_tool(args: VerifyResultInput, context: ToolContext) -> dict[str, Any]:
    if not args.run_id and not args.handle:
        raise ToolError("missing_input", "supply either run_id or handle")
    if args.handle:
        entry = context.scratchpad.get(args.handle)
        if not entry:
            raise ToolError(
                "unknown_handle",
                f"handle '{args.handle}' is not in this session. Known: "
                f"{[k for k in context.scratchpad if not k.startswith('__')]}",
            )
        verification = run_verifier(entry["payload"], {"params": entry.get("params") or {}})
        return {
            "status": "completed",
            "subject": args.handle,
            "verification": verification,
            "summary": verification["status"],
        }
    with session_scope() as db:
        run = db.get(ExperimentRun, args.run_id)
        if run is None or run.owner_id != context.user_id:
            raise ToolError("forbidden", "run not found in your account")
        payload = dict(run.structured_output or {})
        config = dict(run.execution_config or {})
        recomputation = None
        if args.recompute:
            try:
                fresh = run_numerical_method(str(config.get("method")), dict(config.get("params") or {}))
                fresh_payload = fresh.to_dict(max_iterations=0)
                old_head = payload.get("result") or {}
                new_head = fresh_payload.get("result") or {}
                deltas: dict[str, Any] = {}
                for key, value in new_head.items():
                    if isinstance(value, (int, float)) and isinstance(old_head.get(key), (int, float)):
                        deltas[key] = {
                            "stored": float(old_head[key]),
                            "recomputed": float(value),
                            "absolute_difference": abs(float(value) - float(old_head[key])),
                        }
                recomputation = {
                    "status": "completed",
                    "agreement": all(d["absolute_difference"] < 1e-6 for d in deltas.values())
                    if deltas else None,
                    "differences": deltas,
                }
            except Exception as exc:  # noqa: BLE001
                recomputation = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
        verification = run_verifier(payload, {"params": config.get("params") or {}})
        run.verification = verification
        return {
            "status": "completed",
            "subject": args.run_id,
            "run_status": run.status,
            "verification": verification,
            "recomputation": recomputation,
            "summary": verification["status"],
        }

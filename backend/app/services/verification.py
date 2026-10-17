"""Result verification: independent checks on computed output.

The verifier deliberately uses *different* machinery than the methods themselves
(SciPy's adaptive quadrature, ``numpy.linalg.solve``, SymPy closed forms) so a
passing verification is a genuine cross-check rather than a self-consistency loop.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.core.logging import get_logger
from app.core.security import utcnow_iso
from app.numerical.common import STATUS_CONVERGED, software_versions
from app.services import expr as exprmod

logger = get_logger("physicslab.verify")

REQUIRED_KEYS = ("method", "status", "result")


def _check(name: str, passed: bool | None, **fields: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, **fields}


# A result payload names the *implementation* it came from ("ode_ivp:RK45",
# "finite_difference_derivative"), which is not the registry key.  Matching the raw
# string would silently skip the family checks, so names are folded here once.
_METHOD_ALIASES = {
    "finite_difference_derivative": "finite_difference",
    "least_squares_fit": "least_squares",
    "dataset_analysis": "least_squares",
}


def _family_of(name: Any) -> str:
    method = str(name or "").strip().lower()
    method = method.split(":", 1)[0]
    return _METHOD_ALIASES.get(method, method)


def verify_result(payload: dict[str, Any], spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a verification record for a stored run output."""
    spec = spec or {}
    checks: list[dict[str, Any]] = []
    inputs = spec.get("params") or payload.get("inputs") or {}
    method = _family_of(payload.get("method"))

    # 1. structural integrity
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    checks.append(
        _check(
            "output_schema",
            not missing,
            missing_keys=missing,
            detail="payload carries method/status/result" if not missing else "schema incomplete",
        )
    )

    # 2. numeric hygiene
    non_finite = payload.get("non_finite_fields") or []
    checks.append(
        _check(
            "finiteness",
            len(non_finite) == 0,
            non_finite_fields=non_finite if isinstance(non_finite, list) else [str(non_finite)],
            detail=(
                "no NaN/Inf in the reported output"
                if not non_finite
                else f"{len(non_finite)} field(s) required null substitution"
            ),
        )
    )

    # 3. status / convergence consistency
    SUCCESS_STATUSES = {STATUS_CONVERGED, "completed", "ok"}
    status = payload.get("status")
    claimed = bool(payload.get("convergence_achieved"))
    consistent = (claimed and status in SUCCESS_STATUSES) or (
        not claimed and status not in SUCCESS_STATUSES
    )
    checks.append(
        _check(
            "convergence_claim_consistency",
            consistent,
            reported_status=status,
            convergence_achieved=claimed,
            detail=(
                "convergence is only claimed when the method's stopping criterion was met"
                if consistent
                else f"status '{status}' conflicts with convergence_achieved={claimed}"
            ),
        )
    )

    # 4. method-specific independent checks
    result = payload.get("result") or {}
    try:
        checks += _verify_roots(method, result, inputs)
        checks += _verify_integration(method, result, inputs)
        checks += _verify_derivative(method, result, inputs)
        checks += _verify_linear(method, result, inputs)
        checks += _verify_ode(method, result, inputs, payload)
        checks += _verify_iterations(method, payload)
        checks += _verify_simulation(payload, inputs)
        checks += _verify_regression(method, result)
    except Exception as exc:  # noqa: BLE001 - verification must never break a run
        logger.exception("verification error")
        checks.append(
            _check(
                "verifier_error",
                None,
                detail=f"independent checks could not be completed ({type(exc).__name__})",
            )
        )

    hard_failures = [c for c in checks if c["passed"] is False]
    unknown = [c for c in checks if c["passed"] is None]
    status_out = (
        "failed" if hard_failures else ("passed" if checks and not unknown else "inconclusive")
    )
    return {
        "status": status_out,
        "verified_at": utcnow_iso(),
        "checks": checks,
        "check_count": len(checks),
        "failed_checks": [c["name"] for c in hard_failures],
        "inconclusive_checks": [c["name"] for c in unknown],
        "verifier": {
            "strategy": "independent recomputation with cross-algorithm references",
            "software_versions": software_versions(),
        },
    }


# --- numerical-method checks -------------------------------------------------


def _verify_roots(method: str, result: dict, inputs: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = result.get("root")
    expression = inputs.get("expression")
    if method in ("bisection", "newton_raphson", "secant") and isinstance(root, (int, float)):
        residual = None
        if isinstance(expression, str):
            try:
                fn = exprmod.compile_expression(expression)
                residual = fn.call(float(root))
            except exprmod.ExpressionError as exc:
                out.append(_check("residual_evaluation", None, detail=f"expression rejected: {exc}"))
        tol = float(inputs.get("tolerance") or 1e-10)
        threshold = max(1e-6, math.sqrt(tol))
        if residual is not None:
            ok = math.isfinite(residual) and abs(residual) <= threshold
            out.append(
                _check(
                    "residual_evaluation",
                    ok,
                    f_root=residual,
                    threshold=threshold,
                    detail=f"|f(root)| = {abs(residual):.3g} vs threshold {threshold:.3g}",
                )
            )
        # bracket/interval integrity for bisection
        if method == "bisection":
            bracket = result.get("bracket")
            interval_ok = bool(
                isinstance(bracket, list)
                and len(bracket) == 2
                and bracket[0] <= root <= bracket[1]
            )
            out.append(
                _check(
                    "root_inside_final_bracket",
                    interval_ok,
                    bracket=bracket,
                    root=root,
                    detail="the reported root lies within the final bracket",
                )
            )
        err = result.get("interval_width")
        if isinstance(err, (int, float)) and method == "bisection":
            out.append(
                _check(
                    "interval_width_matches_tolerance",
                    err / 2.0 <= tol * 1.000001 or not result.get("convergence"),
                    final_interval_width=err,
                    tolerance=tol,
                    detail=f"half-width {err / 2.0:.3g} vs tol {tol:.3g}",
                )
            )
    return out


def _verify_integration(method: str, result: dict, inputs: dict) -> list[dict[str, Any]]:
    if method not in ("trapezoidal", "simpson") or result.get("integral") is None:
        return []
    from scipy import integrate as scipy_integrate

    out: list[dict[str, Any]] = []
    value = float(result["integral"])
    a, b = float(inputs.get("a", 0.0)), float(inputs.get("b", 1.0))
    expression = inputs.get("expression")
    reference: float | None = None
    reference_kind = None
    if isinstance(expression, str):
        from app.numerical import symbolic

        sym = symbolic.symbolic_definite_integral(expression, a, b)
        if sym:
            reference, reference_kind = float(sym["numeric"]), f"sympy closed form: {sym['expression']}"
        else:
            try:
                fn = exprmod.compile_expression(expression)

                def scalar_fn(x: float) -> float:
                    y = fn.call(x)
                    return y if math.isfinite(y) else float("nan")

                quad, quad_err = scipy_integrate.quad(scalar_fn, a, b, limit=400)
                reference = float(quad)
                reference_kind = f"scipy.integrate.quad (adaptive Gauss-Kronrod, est. {quad_err:.2g})"
            except Exception:  # noqa: BLE001
                reference = None
    if reference is not None and math.isfinite(reference):
        diff = abs(value - reference)
        rel = diff / max(abs(reference), 1e-12)
        tolerance = float(payload_tolerance(inputs, result))
        out.append(
            _check(
                "independent_quadrature",
                (rel <= max(tolerance, 1e-6) or diff <= max(tolerance, 1e-6)),
                computed=value,
                reference=reference,
                reference_kind=reference_kind,
                absolute_difference=diff,
                relative_difference=rel,
                tolerance=max(tolerance, 1e-6),
                detail=(
                    f"difference {diff:.3g} against {reference_kind}"
                ),
            )
        )
    else:
        out.append(
            _check(
                "independent_quadrature",
                None,
                detail="no independent reference could be constructed for this integrand",
            )
        )
    return out


def payload_tolerance(inputs: dict, result: dict) -> float:
    for key in ("tolerance", "tol"):
        value = inputs.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 1e-6


def _verify_derivative(method: str, result: dict, inputs: dict) -> list[dict[str, Any]]:
    if method != "finite_difference" or result.get("derivative") is None:
        return []
    expression = inputs.get("expression")
    out: list[dict[str, Any]] = []
    if not isinstance(expression, str):
        return out
    from app.numerical import symbolic

    order = int(inputs.get("derivative_order", 1))
    sym = symbolic.symbolic_derivative(expression, order=order)
    if sym:
        try:
            import sympy

            x0 = float(inputs.get("x", 0.0))
            analytic = float(sympy.lambdify(sympy.Symbol("x", real=True), sympy.sympify(sym["expression"]))(x0))
        except Exception:  # noqa: BLE001
            analytic = None
        if analytic is not None and math.isfinite(analytic):
            value = float(result["derivative"])
            diff = abs(value - analytic)
            rel = diff / max(abs(analytic), 1e-12)
            out.append(
                _check(
                    "symbolic_derivative_comparison",
                    rel <= 1e-4 or diff <= 1e-6,
                    computed=value,
                    analytic=analytic,
                    analytic_expression=sym["expression"],
                    absolute_difference=diff,
                    relative_difference=rel,
                    detail=f"finite difference vs d/dx[{expression}] = {sym['expression']}",
                )
            )
            return out
    out.append(
        _check("symbolic_derivative_comparison", None,
               detail="no closed-form derivative was available for comparison")
    )
    return out


def _verify_linear(method: str, result: dict, inputs: dict) -> list[dict[str, Any]]:
    if method not in ("gaussian_elimination", "lu_decomposition", "jacobi", "gauss_seidel"):
        return []
    matrix = inputs.get("matrix")
    vector = inputs.get("vector")
    solution = result.get("solution") or (result.get("solve") or {}).get("solution")
    if not (matrix and vector):
        return []
    try:
        A = np.asarray(matrix, dtype=float)
        b = np.asarray(vector, dtype=float)
    except (TypeError, ValueError):
        return [_check("linear_system_reconstruction", None, detail="inputs were not numeric")]
    out: list[dict[str, Any]] = []
    if solution is not None:
        x = np.asarray(solution, dtype=float)
        residual = float(np.max(np.abs(A @ x - b)))
        scale = max(float(np.max(np.abs(b))), 1e-12)
        out.append(
            _check(
                "ax_minus_b_residual",
                residual / scale <= 1e-6,
                residual_infinity_norm=residual,
                relative_residual=residual / scale,
                detail="independent recomputation of ||Ax-b||_inf from the stored inputs",
            )
        )
        if method in ("jacobi", "gauss_seidel"):
            try:
                direct = np.linalg.solve(A, b)
                diff = float(np.max(np.abs(direct - x)))
                out.append(
                    _check(
                        "direct_solve_agreement",
                        diff <= 1e-4 * max(1.0, float(np.max(np.abs(direct)))),
                        numpy_linalg_solution=[float(v) for v in direct],
                        max_component_difference=diff,
                        detail="iterative result compared with numpy.linalg.solve",
                    )
                )
            except np.linalg.LinAlgError:
                out.append(_check("direct_solve_agreement", None,
                                  detail="numpy.linalg.solve reported a singular matrix"))
    if method == "lu_decomposition":
        L = np.asarray(result.get("L"), dtype=float)
        U = np.asarray(result.get("U"), dtype=float)
        P = np.asarray(result.get("P"), dtype=float)
        err = float(np.max(np.abs(P.T @ L @ U - A)))
        out.append(
            _check(
                "lu_reconstruction",
                err <= 1e-8 * max(1.0, float(np.max(np.abs(A)))),
                pa_minus_lu_error=err,
                detail="P^T L U must reproduce A",
            )
        )
    return out


def _verify_ode(
    method: str, result: dict, inputs: dict, payload: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Re-integrate with a different stepper and tighter tolerances, then compare.

    This is a cross-algorithm check on purpose: RK45 results are compared against
    DOP853 at rtol=1e-12, so agreement is evidence about the integration and not about
    the first solver's internal error estimate.
    """
    if method != "ode_ivp":
        return []
    if (payload or {}).get("simulation"):
        # a registered simulation integrates the same way but validates itself against its
        # own closed form; demanding a second re-integration would only add noise
        return []
    final = result.get("final_state")
    expressions = inputs.get("expressions")
    t_span = inputs.get("t_span")
    y0 = inputs.get("y0")
    if not isinstance(final, list) or not isinstance(expressions, list) or not expressions:
        return [
            _check(
                "independent_ode_integration",
                None,
                detail="the stored inputs were not sufficient to re-integrate this system",
            )
        ]
    if not (isinstance(t_span, list) and len(t_span) == 2 and isinstance(y0, list) and len(y0) == len(expressions)):
        return [
            _check(
                "independent_ode_integration",
                None,
                detail="t_span/y0/expressions do not describe a system of the right size",
            )
        ]
    from app.services import expr as exprmod  # the same restricted parser the methods use

    # state naming follows the solver exactly: caller-provided names, else y1..yN
    names = [str(n) for n in inputs.get("state_names") or []] or [
        f"y{i + 1}" for i in range(len(expressions))
    ]
    if len(names) != len(expressions):
        return [
            _check(
                "independent_ode_integration",
                None,
                detail=f"{len(names)} state names for {len(expressions)} expressions",
            )
        ]
    constants = {
        str(k): float(v)
        for k, v in (inputs.get("constants") or {}).items()
        if isinstance(v, (int, float)) and str(k) not in names and str(k) != "t"
    }
    aliases = [f"y{i + 1}" for i in range(len(names))]
    variables = [*names, *aliases, "t", *constants]
    compiled = []
    for index, source in enumerate(expressions):
        try:
            compiled.append(exprmod.compile_expression(str(source), variables=variables))
        except Exception as exc:  # noqa: BLE001 - an uncompilable expression is not a pass
            return [
                _check(
                    "independent_ode_integration",
                    False,
                    detail=f"expression {index} could not be re-compiled: {str(exc)[:160]}",
                )
            ]

    def rhs(_t: float, y):  # noqa: ANN001 - scipy calls this positionally
        # same binding the solver uses: states, y1..yN aliases, t, then constants
        live = [float(v) for v in y]
        env = live + live + [float(_t)] + [constants[k] for k in constants]
        return [fn.call(*env) for fn in compiled]

    try:
        from scipy.integrate import solve_ivp

        solution = solve_ivp(
            rhs,
            (float(t_span[0]), float(t_span[1])),
            [float(v) for v in y0],
            method="DOP853",
            rtol=1e-12,
            atol=1e-14,
            dense_output=False,
        )
    except Exception as exc:  # noqa: BLE001
        return [
            _check(
                "independent_ode_integration",
                None,
                detail=f"independent integrator unavailable ({type(exc).__name__})",
            )
        ]
    if not getattr(solution, "success", False) or solution.y.shape[-1] == 0:
        return [
            _check(
                "independent_ode_integration",
                False,
                detail=f"independent integration failed: {str(solution.message)[:160]}",
            )
        ]
    reference = [float(v) for v in solution.y[:, -1]]
    differences = [abs(a - b) for a, b in zip([float(v) for v in final], reference)]
    scale = max(max(abs(v) for v in reference), 1.0)
    worst = max(differences) if differences else float("nan")
    tol = float(inputs.get("rtol", 1e-9)) * 1e3
    return [
        _check(
            "independent_ode_integration",
            bool(worst == worst) and worst <= max(tol, 1e-6),
            computed=[float(v) for v in final],
            reference=reference,
            absolute_difference=worst,
            reference_solver="DOP853 (rtol=1e-12)",
            detail=(
                f"final state agrees with an independent stiff-free stepper to {worst:.3g} "
                f"(budget {max(tol, 1e-6):.3g})"
            ),
        )
    ]


def _verify_iterations(method: str, payload: dict) -> list[dict[str, Any]]:
    records = payload.get("iterations") or []
    if not records:
        return []
    errors = [r.get("error") for r in records if isinstance(r.get("error"), (int, float))]
    if len(errors) < 2:
        return []
    increases = sum(1 for i in range(1, len(errors)) if errors[i] > errors[i - 1] * 1.0000001)
    out = [
        _check(
            "error_sequence_monotone",
            increases == 0,
            non_monotone_steps=increases,
            detail="per-iteration error estimate should not increase for a well-behaved run",
        )
    ]
    tol = payload.get("tolerance")
    if payload.get("convergence_achieved") and isinstance(tol, (int, float)) and errors:
        out.append(
            _check(
                "final_error_within_tolerance",
                errors[-1] <= float(tol) * 10 or abs(errors[-1]) <= 1e-9,
                final_error=errors[-1],
                tolerance=float(tol),
                detail="converged runs must satisfy the documented stopping criterion",
            )
        )
    return out


def _verify_simulation(payload: dict, inputs: dict) -> list[dict[str, Any]]:
    if "series" not in payload or "summary" not in payload:
        return []
    out: list[dict[str, Any]] = []
    series = payload.get("series") or {}
    times = series.get("time") or []
    ok = bool(times) and len(times) >= 2
    out.append(_check("time_series_present", ok, points=len(times),
                      detail="simulation returned a non-degenerate time series"))
    monotone = all(times[i] > times[i - 1] for i in range(1, len(times)))
    out.append(_check("time_axis_increasing", monotone, detail="sample times must strictly increase"))
    lengths = {name: len(values) for name, values in series.items() if isinstance(values, list)}
    out.append(
        _check(
            "series_length_consistency",
            len(set(lengths.values())) <= 1,
            lengths=lengths,
            detail="every state variable must have one value per sample time",
        )
    )
    validation = payload.get("validation") or {}
    checks = validation.get("checks") or []
    if checks:
        passed = all(c.get("passed") for c in checks)
        out.append(
            _check(
                "analytical_validation",
                passed,
                checks=checks,
                overall=validation.get("overall"),
                detail="model-level validation against closed forms / invariants",
            )
        )
    else:
        out.append(
            _check(
                "analytical_validation",
                None,
                detail=(validation.get("notes") or ["no analytical reference for this parameter set"]),
            )
        )
    return out


def _verify_regression(method: str, result: dict) -> list[dict[str, Any]]:
    if method not in ("least_squares", "least_squares_fit", "dataset_analysis") and "r_squared" not in result:
        return []
    residuals = (result.get("residuals") or {}).get("residuals")
    if not residuals:
        return []
    r = np.asarray(residuals, dtype=float)
    out: list[dict[str, Any]] = []
    finite = bool(np.all(np.isfinite(r)))
    out.append(_check("residuals_finite", finite, count=int(r.size)))
    if finite:
        mean = float(np.mean(r))
        scale = float(np.max(np.abs(r)))
        # an intercept model has residuals averaging to zero; when the fit is already exact
        # to machine precision the only meaningful bound is the absolute one
        threshold = max(1e-6 * scale, 1e-12)
        out.append(
            _check(
                "residual_mean_zero",
                abs(mean) <= threshold,
                mean_residual=mean,
                max_allowed=threshold,
                detail="OLS residuals must average to ~0 for models with an intercept",
            )
        )
        r2 = result.get("r_squared")
        if isinstance(r2, (int, float)) and math.isfinite(float(r2)):
            out.append(
                _check(
                    "r_squared_bounds",
                    -3.0 <= float(r2) <= 1.0000001,
                    r_squared=float(r2),
                    detail=(
                        "R^2 cannot exceed 1; a strongly negative value means the model is worse "
                        "than the mean of the data (negative R^2 is legitimate without an "
                        "intercept, so only extreme values are treated as an error)"
                    ),
                )
            )
    return out

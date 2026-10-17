"""Numerical integration: composite trapezoidal rule and Simpson's rule.

Both support fixed-panel mode and adaptive panel doubling with Richardson error
estimation; convergence is reported only when the estimate meets the tolerance.
"""

from __future__ import annotations

import math
from typing import Callable

from .common import (
    STATUS_CONVERGED,
    STATUS_TARGET_NOT_MET,
    STATUS_MAX_ITERATIONS,
    IterationRecord,
    NumericalInputError,
    NumericalResult,
    Timer,
    check_max_iter,
    check_tolerance,
    require_finite,
    safe_call,
)


def _validate(a, b, f) -> tuple[float, float]:
    a = require_finite(a, "a")
    b = require_finite(b, "b")
    if a == b:
        raise NumericalInputError("integration limits must differ (a != b)")
    return (a, b) if a < b else (b, a)


def _samples(f: Callable[[float], float], a: float, b: float, n: int):
    """Values of f at n+1 equally spaced nodes; raises on non-finite output."""
    h = (b - a) / n
    xs = [a + i * h for i in range(n + 1)]
    ys: list[float] = []
    for x in xs:
        y, err = safe_call(f, x)
        if err or not math.isfinite(y):
            raise NumericalInputError(
                f"integrand is not finite at x={x:.10g}: {err or y}. "
                "The trapezoidal/Simpson rules require a bounded, continuous integrand."
            )
        ys.append(y)
    return xs, ys, h


def trapezoidal(
    f: Callable[[float], float],
    a: float,
    b: float,
    n: int | None = None,
    tol: float = 1e-8,
    max_iter: int = 30,
) -> NumericalResult:
    """Composite trapezoidal rule.

    ``n`` given  -> exactly n panels (single evaluation, Richardson still reported)
    ``n`` omitted-> adaptive panel doubling until |I_2n - I_n| / 3 <= tol
    """
    a, b = _validate(a, b, f)
    tol = check_tolerance(tol, 1e-8)
    warnings: list[str] = []
    with Timer() as t:
        if n is not None:
            n = int(n)
            if n < 1 or n > 200000:
                raise NumericalInputError("n (panel count) must be between 1 and 200000")
            xs, ys, h = _samples(f, a, b, n)
            value = h * (0.5 * ys[0] + sum(ys[1:-1]) + 0.5 * ys[-1])
            xs2, ys2, h2 = _samples(f, a, b, 2 * n)
            refined = h2 * (0.5 * ys2[0] + sum(ys2[1:-1]) + 0.5 * ys2[-1])
            err = abs(refined - value) / 3.0
            status = STATUS_CONVERGED if err <= tol else STATUS_TARGET_NOT_MET
            if err > tol:
                warnings.append(
                    f"the fixed-panel result has Richardson error estimate {err:.3g} > tol {tol:g}; "
                    f"request adaptive mode (omit 'panels') or more panels to reach the target"
                )
            iterations = [
                IterationRecord(index=1, x=float(n), f_x=value, error=err, extra={"h": h})
            ]
            panels, evaluations, converged_value = n, n + 1 + 2 * n + 1, value
        else:
            max_iter = check_max_iter(max_iter, limit=60, minimum=2)
            panels = 4
            iterations = []
            prev: float | None = None
            value = float("nan")
            err = float("inf")
            status = STATUS_MAX_ITERATIONS
            evaluations = 0
            refined = None
            for level in range(1, max_iter + 1):
                xs, ys, h = _samples(f, a, b, panels)
                evaluations += len(ys)
                value = h * (0.5 * ys[0] + sum(ys[1:-1]) + 0.5 * ys[-1])
                err = abs(value - prev) / 3.0 if prev is not None else float("inf")
                iterations.append(
                    IterationRecord(
                        index=level,
                        x=float(panels),
                        f_x=value,
                        error=err if math.isfinite(err) else None,
                        extra={"h": h, "panels": panels},
                    )
                )
                if prev is not None and err <= tol:
                    status = STATUS_CONVERGED
                    refined = value
                    break
                prev = value
                panels *= 2
            converged_value = refined if refined is not None else value
            if status != STATUS_CONVERGED:
                warnings.append(
                    f"adaptive refinement did not reach tol {tol:g} within {max_iter} doublings"
                )
    return NumericalResult(
        method="trapezoidal",
        status=status,
        result={
            "integral": converged_value,
            "a": a,
            "b": b,
            "panels": panels,
            "step_size": (b - a) / panels,
            "function_evaluations": evaluations,
        },
        iterations=iterations,
        error_estimate=err if math.isfinite(err) else None,
        convergence_achieved=status == STATUS_CONVERGED,
        tolerance=tol,
        warnings=warnings,
        message=(
            f"integral ~= {converged_value:.12g} (error estimate "
            f"{err:.3g}, {panels} panels)"
        ),
        execution_time_ms=t.ms,
        metadata={
            "rule": "composite trapezoidal",
            "order": "O(h^2)",
            "error_model": "Richardson extrapolation |I_2n - I_n| / 3",
        },
    )


def simpson(
    f: Callable[[float], float],
    a: float,
    b: float,
    n: int | None = None,
    tol: float = 1e-10,
    max_iter: int = 30,
) -> NumericalResult:
    """Composite Simpson's 1/3 rule (requires an even panel count)."""
    a, b = _validate(a, b, f)
    tol = check_tolerance(tol, 1e-10)
    warnings: list[str] = []
    with Timer() as t:
        if n is not None:
            n = int(n)
            if n < 2 or n > 200000:
                raise NumericalInputError("n (panel count) must be between 2 and 200000")
            if n % 2 == 1:
                raise NumericalInputError(
                    "Simpson's 1/3 rule requires an even panel count; "
                    f"got n={n}. Use n={n + 1}, or omit 'panels' to let the rule adapt."
                )
            xs, ys, h = _samples(f, a, b, n)
            value = (h / 3.0) * (ys[0] + ys[-1] + 4 * sum(ys[1:-1:2]) + 2 * sum(ys[2:-2:2]))
            xs2, ys2, h2 = _samples(f, a, b, 2 * n)
            refined = (h2 / 3.0) * (
                ys2[0] + ys2[-1] + 4 * sum(ys2[1:-1:2]) + 2 * sum(ys2[2:-2:2])
            )
            err = abs(refined - value) / 15.0
            status = STATUS_CONVERGED if err <= tol else STATUS_TARGET_NOT_MET
            if err > tol:
                warnings.append(
                    f"the fixed-panel result has Richardson error estimate {err:.3g} > tol {tol:g}; "
                    f"request adaptive mode (omit 'panels') or more panels to reach the target"
                )
            iterations = [
                IterationRecord(index=1, x=float(n), f_x=value, error=err, extra={"h": h})
            ]
            converged_value, panels, evaluations = value, n, 2 * n + 4 * n + 2
        else:
            max_iter = check_max_iter(max_iter, limit=60, minimum=2)
            panels = 4
            iterations = []
            prev = None
            value = float("nan")
            err = float("inf")
            status = STATUS_MAX_ITERATIONS
            evaluations = 0
            refined_v = None
            for level in range(1, max_iter + 1):
                xs, ys, h = _samples(f, a, b, panels)
                evaluations += len(ys)
                value = (h / 3.0) * (
                    ys[0] + ys[-1] + 4 * sum(ys[1:-1:2]) + 2 * sum(ys[2:-2:2])
                )
                err = abs(value - prev) / 15.0 if prev is not None else float("inf")
                iterations.append(
                    IterationRecord(
                        index=level,
                        x=float(panels),
                        f_x=value,
                        error=err if math.isfinite(err) else None,
                        extra={"h": h, "panels": panels},
                    )
                )
                if prev is not None and err <= tol:
                    status = STATUS_CONVERGED
                    refined_v = value
                    break
                prev = value
                panels *= 2
            converged_value = refined_v if refined_v is not None else value
            if status != STATUS_CONVERGED:
                warnings.append(
                    f"adaptive refinement did not reach tol {tol:g} within {max_iter} doublings"
                )
    return NumericalResult(
        method="simpson",
        status=status,
        result={
            "integral": converged_value,
            "a": a,
            "b": b,
            "panels": panels,
            "step_size": (b - a) / panels,
            "function_evaluations": evaluations,
            "n_requested": n,
        },
        iterations=iterations,
        error_estimate=err if math.isfinite(err) else None,
        convergence_achieved=status == STATUS_CONVERGED,
        tolerance=tol,
        warnings=warnings,
        message=f"integral ~= {converged_value:.12g} (error estimate {err:.3g})",
        execution_time_ms=t.ms,
        metadata={
            "rule": "composite Simpson 1/3",
            "order": "O(h^4)",
            "error_model": "Richardson extrapolation |S_2n - S_n| / 15",
            "requirement": "even number of panels, integrand continuous on [a, b]",
        },
    )

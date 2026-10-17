"""Root-finding methods: bisection, Newton-Raphson, secant.

All three record per-iteration diagnostics and only report convergence when the
documented stopping criterion is actually met.
"""

from __future__ import annotations

import math
from typing import Callable

from .common import (
    STATUS_CONVERGED,
    STATUS_FAILED,
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

ABS_X_LIMIT = 1e12


def _validate_bracket(f: Callable[[float], float], a: float, b: float):
    a = require_finite(a, "a")
    b = require_finite(b, "b")
    if a >= b:
        raise NumericalInputError(f"interval must satisfy a < b (got a={a}, b={b})")
    fa, err_a = safe_call(f, a)
    fb, err_b = safe_call(f, b)
    if err_a or not math.isfinite(fa):
        raise NumericalInputError(f"f(a) is not finite at a={a}: {err_a or 'non-finite'}")
    if err_b or not math.isfinite(fb):
        raise NumericalInputError(f"f(b) is not finite at b={b}: {err_b or 'non-finite'}")
    return a, b, fa, fb


def bisection(
    f: Callable[[float], float],
    a: float,
    b: float,
    tol: float = 1e-10,
    max_iter: int = 100,
) -> NumericalResult:
    """Classical bisection for a continuous f on [a, b] with f(a)*f(b) <= 0."""
    tol = check_tolerance(tol)
    max_iter = check_max_iter(max_iter)
    warnings: list[str] = []
    with Timer() as t:
        a, b, fa, fb = _validate_bracket(f, a, b)
        if fa == 0.0:
            return NumericalResult(
                method="bisection",
                status=STATUS_CONVERGED,
                result={"root": a, "f_root": 0.0, "bracket": [a, b]},
                convergence_achieved=True,
                tolerance=tol,
                error_estimate=0.0,
                message="f(a) = 0 exactly; endpoint a is a root",
                execution_time_ms=0.0,
            )
        if fb == 0.0:
            return NumericalResult(
                method="bisection",
                status=STATUS_CONVERGED,
                result={"root": b, "f_root": 0.0, "bracket": [a, b]},
                convergence_achieved=True,
                tolerance=tol,
                error_estimate=0.0,
                message="f(b) = 0 exactly; endpoint b is a root",
                execution_time_ms=0.0,
            )
        if fa * fb > 0:
            raise NumericalInputError(
                "the interval does not bracket a sign change: "
                f"f({a}) = {fa:.6g} and f({b}) = {fb:.6g} have the same sign. "
                "Choose a different interval, or use Newton-Raphson/secant for a local search."
            )

        records: list[IterationRecord] = []
        root: float | None = None
        f_root: float | None = None
        status = STATUS_MAX_ITERATIONS
        error_estimate: float | None = None
        for i in range(1, max_iter + 1):
            mid = 0.5 * (a + b)
            fm, err = safe_call(f, mid)
            width = b - a
            err_est = width / 2.0
            error_estimate = err_est
            if err or not math.isfinite(fm):
                raise NumericalInputError(
                    f"f produced a non-finite value at x={mid}: {err or fm}"
                )
            records.append(
                IterationRecord(
                    index=i, a=a, b=b, x=mid, f_x=fm, step=width, error=err_est
                )
            )
            if fm == 0.0:
                root, f_root, status = mid, 0.0, STATUS_CONVERGED
                break
            if fa * fm < 0:
                b, fb = mid, fm
            else:
                a, fa = mid, fm
            if (b - a) / 2.0 <= tol:
                root = 0.5 * (a + b)
                f_root, _ = safe_call(f, root)
                status = STATUS_CONVERGED
                break
        else:
            root = 0.5 * (a + b)
            f_root, _ = safe_call(f, root)
            warnings.append(
                f"stopping criterion |b-a|/2 <= {tol:g} was not met within {max_iter} iterations"
            )

        converged = status == STATUS_CONVERGED
        if not converged:
            warnings.append(
                "reported value is the midpoint of the final interval and is NOT a "
                "verified root"
            )
    return NumericalResult(
        method="bisection",
        status=status,
        result={
            "root": root,
            "f_root": f_root,
            "bracket": [a, b],
            "interval_width": b - a,
        },
        iterations=records,
        error_estimate=error_estimate,
        convergence_achieved=converged,
        tolerance=tol,
        warnings=warnings,
        message=(
            f"root = {root:.12g} located to |b-a|/2 <= {tol:g}"
            if converged
            else "convergence criterion not satisfied"
        ),
        execution_time_ms=t.ms,
        metadata={
            "iterations_used": len(records),
            "expected_iterations_to_bracket": (
                round(math.log2((b - a) / tol)) if (b - a) > tol else 0
            ),
            "stopping_criterion": "abs(b - a) / 2 <= tol",
        },
    )


def _numeric_derivative(
    f: Callable[[float], float], x: float, h: float | None = None
) -> tuple[float, str | None]:
    if h is None:
        h = math.sqrt(sys_epsilon()) * max(1.0, abs(x))
    fp, e1 = safe_call(f, x + h)
    fm, e2 = safe_call(f, x - h)
    if e1 or e2:
        return math.nan, e1 or e2
    return (fp - fm) / (2.0 * h), None


def sys_epsilon() -> float:
    return 2.220446049250313e-16


def newton_raphson(
    f: Callable[[float], float],
    x0: float,
    tol: float = 1e-12,
    max_iter: int = 50,
    df: Callable[[float], float] | None = None,
) -> NumericalResult:
    """Newton-Raphson. Falls back to a central-difference derivative when ``df``
    is not supplied (recorded in ``metadata.derivative_source``)."""
    tol = check_tolerance(tol, 1e-12)
    max_iter = check_max_iter(max_iter)
    x = require_finite(x0, "x0")
    warnings: list[str] = []
    derivative_source = "analytic" if df is not None else "central-difference"
    with Timer() as t:
        records: list[IterationRecord] = []
        status = STATUS_MAX_ITERATIONS
        fx, err = safe_call(f, x)
        if err or not math.isfinite(fx):
            raise NumericalInputError(f"f(x0) is not finite at x0={x}: {err or fx}")
        root = None
        for i in range(1, max_iter + 1):
            if df is not None:
                dfx, derr = safe_call(df, x)
                if derr:
                    raise NumericalInputError(f"f'(x) evaluation failed at x={x}: {derr}")
            else:
                dfx, derr = _numeric_derivative(f, x)
                if derr:
                    raise NumericalInputError(f"numerical derivative failed at x={x}: {derr}")
            if not math.isfinite(dfx) or abs(dfx) < 1e-14:
                status = STATUS_FAILED
                warnings.append(
                    f"derivative is zero or non-finite at x={x:.6g}; Newton step undefined"
                )
                records.append(
                    IterationRecord(
                        index=i, x=x, f_x=fx, step=None, error=None,
                        extra={"df_x": dfx, "note": "zero derivative"},
                    )
                )
                break
            step = fx / dfx
            x_new = x - step
            if not math.isfinite(x_new):
                status = STATUS_FAILED
                warnings.append("Newton step produced a non-finite iterate")
                break
            if abs(x_new) > ABS_X_LIMIT:
                status = STATUS_FAILED
                warnings.append(
                    f"iterate escaped the trusted range (|x| > {ABS_X_LIMIT:g}): divergence"
                )
                fx_new = safe_call(f, x_new)[0]
                records.append(
                    IterationRecord(
                        index=i, x=x_new, f_x=fx_new, step=step,
                        error=abs(step), extra={"df_x": dfx},
                    )
                )
                break
            fx_new, err = safe_call(f, x_new)
            records.append(
                IterationRecord(
                    index=i,
                    x=x_new,
                    f_x=fx_new if math.isfinite(fx_new) else None,
                    step=step,
                    error=abs(step),
                    extra={"df_x": dfx},
                )
            )
            x, fx = x_new, fx_new
            if abs(step) <= tol * max(1.0, abs(x)) or (
                math.isfinite(fx) and abs(fx) <= tol
            ):
                status = STATUS_CONVERGED
                root = x
                break
        if root is None and status == STATUS_MAX_ITERATIONS:
            root = x
            warnings.append(
                f"stopping criterion |step| <= {tol:g} not met within {max_iter} iterations"
            )
        error_estimate = records[-1].error if records else None
        converged = status == STATUS_CONVERGED
        if not converged:
            warnings.append("the reported value is an iterate, not a verified root")
    return NumericalResult(
        method="newton_raphson",
        status=status,
        result={"root": root if converged or status == STATUS_MAX_ITERATIONS else None,
                "iterate": x, "f_root": fx if math.isfinite(fx) else None},
        iterations=records,
        error_estimate=error_estimate,
        convergence_achieved=converged,
        tolerance=tol,
        warnings=warnings,
        message=(
            f"root = {root:.12g}"
            if converged
            else "Newton iteration did not satisfy the stopping criterion"
        ),
        execution_time_ms=t.ms,
        metadata={
            "derivative_source": derivative_source,
            "x0": x0,
            "stopping_criterion": "abs(step) <= tol * max(1, |x|) or |f(x)| <= tol",
        },
    )


def secant(
    f: Callable[[float], float],
    x0: float,
    x1: float,
    tol: float = 1e-12,
    max_iter: int = 60,
) -> NumericalResult:
    tol = check_tolerance(tol, 1e-12)
    max_iter = check_max_iter(max_iter)
    x_prev = require_finite(x0, "x0")
    x = require_finite(x1, "x1")
    if x_prev == x:
        raise NumericalInputError("x0 and x1 must be distinct")
    warnings: list[str] = []
    with Timer() as t:
        f_prev, e0 = safe_call(f, x_prev)
        fx, e1 = safe_call(f, x)
        if e0 or e1 or not (math.isfinite(f_prev) and math.isfinite(fx)):
            raise NumericalInputError("both initial points must yield finite function values")
        records: list[IterationRecord] = []
        status = STATUS_MAX_ITERATIONS
        for i in range(1, max_iter + 1):
            denom = fx - f_prev
            if abs(denom) < 1e-300:
                status = STATUS_FAILED
                warnings.append("secant denominator vanished (flat function between iterates)")
                break
            step = -fx * (x - x_prev) / denom
            x_new = x + step
            if not math.isfinite(x_new) or abs(x_new) > ABS_X_LIMIT:
                status = STATUS_FAILED
                warnings.append("secant iterate escaped the trusted range (divergence)")
                break
            f_new, err = safe_call(f, x_new)
            records.append(
                IterationRecord(
                    index=i,
                    a=x_prev,
                    b=x,
                    x=x_new,
                    f_x=f_new if math.isfinite(f_new) else None,
                    step=step,
                    error=abs(step),
                )
            )
            x_prev, f_prev = x, fx
            x, fx = x_new, f_new
            if math.isfinite(fx) and abs(fx) <= tol:
                status = STATUS_CONVERGED
                break
            if abs(step) <= tol * max(1.0, abs(x)):
                status = STATUS_CONVERGED
                break
        else:
            warnings.append(
                f"stopping criterion |step| <= {tol:g} not met within {max_iter} iterations"
            )
        converged = status == STATUS_CONVERGED
        if not converged:
            warnings.append("the reported value is an iterate, not a verified root")
    return NumericalResult(
        method="secant",
        status=status,
        result={"root": x if converged else None, "iterate": x,
                "f_root": fx if math.isfinite(fx) else None},
        iterations=records,
        error_estimate=records[-1].error if records else None,
        convergence_achieved=converged,
        tolerance=tol,
        warnings=warnings,
        message=f"root = {x:.12g}" if converged else "secant iteration did not converge",
        execution_time_ms=t.ms,
        metadata={"x0": x0, "x1": x1, "stopping_criterion": "|step| <= tol*max(1,|x|)"},
    )

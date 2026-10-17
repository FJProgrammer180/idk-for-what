"""Numerical differentiation by finite differences, with Richardson refinement."""

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

MODES = ("central", "forward", "backward")
EPS = 2.220446049250313e-16


def _first(f: Callable[[float], float], x: float, h: float, mode: str) -> tuple[float, str | None]:
    if mode == "central":
        y1, e1 = safe_call(f, x + h)
        y2, e2 = safe_call(f, x - h)
        if e1 or e2:
            return math.nan, e1 or e2
        return (y1 - y2) / (2 * h), None
    if mode == "forward":
        y1, e1 = safe_call(f, x + h)
        y0, e0 = safe_call(f, x)
        if e1 or e0:
            return math.nan, e1 or e0
        return (y1 - y0) / h, None
    if mode == "backward":
        y0, e0 = safe_call(f, x)
        ym, em = safe_call(f, x - h)
        if e0 or em:
            return math.nan, e0 or em
        return (y0 - ym) / h, None
    raise NumericalInputError(f"unknown mode '{mode}' (expected one of {MODES})")


def _second_central(f, x, h):
    y1, e1 = safe_call(f, x + h)
    y0, e0 = safe_call(f, x)
    ym, em = safe_call(f, x - h)
    if e1 or e0 or em:
        return math.nan, e1 or e0 or em
    return (y1 - 2 * y0 + ym) / (h * h), None


def finite_difference_derivative(
    f: Callable[[float], float],
    x: float,
    mode: str = "central",
    order: int = 1,
    h: float | None = None,
    tol: float = 1e-8,
    max_iter: int = 12,
) -> NumericalResult:
    """Estimate d^order f / dx^order at x.

    Uses a step chosen from machine precision unless ``h`` is supplied, then
    refines by halving the step until successive estimates agree within ``tol``.
    """
    x = require_finite(x, "x")
    tol = check_tolerance(tol, 1e-8)
    max_iter = check_max_iter(max_iter, limit=40, minimum=1)
    if mode not in MODES:
        raise NumericalInputError(f"mode must be one of {MODES}")
    if order not in (1, 2):
        raise NumericalInputError("only first and second derivatives are supported")
    if h is not None:
        h = require_finite(h, "h")
        if h <= 0:
            raise NumericalInputError("step size h must be > 0")
    else:
        h = EPS ** (1 / 3) * max(1.0, abs(x)) if order == 1 else EPS ** (1 / 4) * max(
            1.0, abs(x)
        )

    warnings: list[str] = []
    if order == 2 and mode != "central":
        warnings.append(
            f"second derivatives use the symmetric three-point stencil; the requested mode "
            f"'{mode}' does not change the formula"
        )
    records: list[IterationRecord] = []
    status = STATUS_MAX_ITERATIONS
    with Timer() as t:
        prev: float | None = None
        value = float("nan")
        err: float | None = None
        for level in range(1, max_iter + 1):
            if order == 1:
                est, e = _first(f, x, h, mode)
            else:
                est, e = _second_central(f, x, h)
            if e:
                raise NumericalInputError(f"evaluation failed at step h={h:.3g}: {e}")
            if not math.isfinite(est):
                status = STATUS_FAILED
                warnings.append(f"finite-difference estimate became non-finite at h={h:.3g}")
                break
            records.append(
                IterationRecord(
                    index=level,
                    x=est,
                    f_x=None,
                    step=h,
                    error=None if prev is None else abs(est - prev),
                    extra={"h": h, "mode": mode, "order": order},
                )
            )
            if prev is not None:
                err = abs(est - prev)
                if err <= tol * max(1.0, abs(est)):
                    status = STATUS_CONVERGED
                    value = est
                    break
            prev, value = est, est
            h_next = h / 2.0
            if h_next < EPS * max(1.0, abs(x)):
                warnings.append(
                    "step size reached the machine-precision floor; further refinement "
                    "is dominated by round-off, not truncation error"
                )
                break
            h = h_next
    if status != STATUS_CONVERGED and not any("non-finite" in w for w in warnings):
        warnings.append(
            f"successive estimates did not agree within tol {tol:g}; value may carry "
            "truncation or round-off error"
        )
    return NumericalResult(
        method="finite_difference_derivative",
        status=status,
        result={
            "derivative": value if math.isfinite(value) else None,
            "x": x,
            "order": order,
            "mode": mode,
            "step_size_used": h,
        },
        iterations=records,
        error_estimate=err,
        convergence_achieved=status == STATUS_CONVERGED,
        tolerance=tol,
        warnings=warnings,
        message=(
            f"f^({order})({x:.10g}) ~= {value:.10g}"
            if math.isfinite(value)
            else "derivative estimate is not finite"
        ),
        execution_time_ms=t.ms,
        metadata={
            "truncation_order": ("O(h^2)" if mode == "central" else "O(h)") if order == 1 else "O(h^2)",
            "refinement": "step halving until |D_h - D_h/2| <= tol",
            "initial_step": EPS ** (1 / 3) * max(1.0, abs(x)),
        },
    )

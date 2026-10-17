"""Initial-value ODE integration on top of SciPy's solve_ivp."""

from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np
from scipy.integrate import solve_ivp

from .common import (
    STATUS_CONVERGED,
    STATUS_FAILED,
    IterationRecord,
    NumericalInputError,
    NumericalResult,
    Timer,
)

SUPPORTED_SOLVERS = {
    "RK45": "explicit Runge-Kutta (4,5) Dormand-Prince",
    "RK23": "explicit Runge-Kutta (2,3) Bogacki-Shampine",
    "Radau": "implicit Runge-Kutta (Radau IIA) for stiff systems",
    "BDF": "backward differentiation formula for stiff systems",
    "LSODA": "automatic stiff/non-stiff switching (requires scipy>=1.7)",
    "DOP853": "explicit 8th order Dormand-Prince",
}

MAX_STATE_DIM = 64


def integrate_ode(
    rhs: Callable[[float, np.ndarray], Sequence[float] | np.ndarray],
    t_span: tuple[float, float],
    y0: Sequence[float],
    method: str = "RK45",
    rtol: float = 1e-8,
    atol: float = 1e-10,
    n_points: int = 200,
    max_step: float | None = None,
    reference: Callable[[float], Sequence[float]] | None = None,
    state_names: Sequence[str] | None = None,
) -> NumericalResult:
    """Integrate dy/dt = f(t, y) on ``t_span`` and return plot-ready time series."""
    method = str(method).upper()
    if method not in SUPPORTED_SOLVERS:
        raise NumericalInputError(
            f"unsupported solver '{method}'. Choose one of {sorted(SUPPORTED_SOLVERS)}"
        )
    t0, t1 = float(t_span[0]), float(t_span[1])
    if not (math.isfinite(t0) and math.isfinite(t1)) or t1 <= t0:
        raise NumericalInputError("t_span must be [t0, t1] with finite t0 < t1")
    if (t1 - t0) > 1e9:
        raise NumericalInputError("integration window is too large (limit 1e9)")
    y_initial = np.asarray([float(v) for v in y0], dtype=float)
    if y_initial.ndim != 1 or y_initial.size < 1 or y_initial.size > MAX_STATE_DIM:
        raise NumericalInputError(f"y0 must be a vector of 1..{MAX_STATE_DIM} entries")
    if not np.all(np.isfinite(y_initial)):
        raise NumericalInputError("y0 contains non-finite values")
    for name, value, lo in (("rtol", rtol, 1e-14), ("atol", atol, 1e-16)):
        value = float(value)
        if not math.isfinite(value) or value <= lo:
            raise NumericalInputError(f"{name} must be a finite number > {lo}")
    try:
        n_points = int(n_points)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError("n_points must be an integer") from exc
    if n_points < 2 or n_points > 200000:
        raise NumericalInputError("n_points must be between 2 and 200000")

    wrapped_failures: list[str] = []

    def fun(t: float, y: np.ndarray) -> np.ndarray:
        try:
            raw = rhs(t, y)
            out = np.asarray([float(v) for v in raw], dtype=float)
        except (ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
            wrapped_failures.append(f"{type(exc).__name__}: {exc}")
            raise
        if out.shape != y.shape:
            wrapped_failures.append("RHS returned a vector with the wrong length")
            raise ValueError("RHS returned a vector with the wrong length")
        if not np.all(np.isfinite(out)):
            wrapped_failures.append("RHS returned non-finite values")
            raise FloatingPointError("RHS returned non-finite values")
        return out

    t_eval = np.linspace(t0, t1, n_points)
    kwargs: dict[str, object] = {}
    if max_step is not None:
        ms = float(max_step)
        if not math.isfinite(ms) or ms <= 0:
            raise NumericalInputError("max_step must be a positive finite number")
        kwargs["max_step"] = ms

    with Timer() as t:
        try:
            sol = solve_ivp(
                fun,
                (t0, t1),
                y_initial,
                method=method,
                rtol=float(rtol),
                atol=float(atol),
                t_eval=t_eval,
                dense_output=False,
                **kwargs,
            )
        except FloatingPointError as exc:
            raise NumericalInputError(
                f"the RHS produced non-finite values: {wrapped_failures[-1] if wrapped_failures else exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - normalise solver internals
            detail = wrapped_failures[-1] if wrapped_failures else str(exc)
            raise NumericalInputError(f"solver failure: {type(exc).__name__}: {detail}") from exc

        if not sol.success:
            status = STATUS_FAILED
            message = f"solver did not reach the final time: {sol.message}"
        else:
            status = STATUS_CONVERGED
            message = f"integration completed over [{t0:g}, {t1:g}]"

        yy = sol.y
        finite = bool(np.all(np.isfinite(yy))) if yy.size else False
        warnings: list[str] = []
        if not finite:
            status = STATUS_FAILED
            warnings.append("solution contains non-finite values")
        if sol.t.size < n_points:
            warnings.append(
                f"solver stopped early: {sol.t.size}/{n_points} requested output times available"
            )
        if sol.status == -1:
            warnings.append(str(sol.message))

        names = list(state_names) if state_names else [f"y{i + 1}" for i in range(y_initial.size)]
        series = {
            name: [float(v) for v in yy[i]] for i, name in enumerate(names)
        }
        validation = None
        if reference is not None and finite:
            ref_errs: list[float] = []
            for i, time in enumerate(sol.t):
                try:
                    ref = np.asarray([float(v) for v in reference(float(time))], dtype=float)
                except Exception:  # noqa: BLE001 - reference is advisory only
                    ref = None
                if ref is None or not np.all(np.isfinite(ref)):
                    continue
                ref_errs.append(float(np.max(np.abs(ref - yy[:, i]))))
            if ref_errs:
                max_abs = float(np.max(ref_errs))
                scale = max(1e-12, float(np.max(np.abs(yy))))
                validation = {
                    "reference": "analytical",
                    "max_absolute_error": max_abs,
                    "max_relative_error": max_abs / scale,
                    "checked_points": len(ref_errs),
                }

        records = [
            IterationRecord(
                index=i + 1,
                x=float(sol.t[i]) if i < sol.t.size else None,
                f_x=float(np.max(np.abs(yy[:, i]))) if finite and i < yy.shape[1] else None,
                extra={names[j]: float(yy[j, i]) for j in range(yy.shape[0])} if finite else {},
            )
            for i in range(min(sol.t.size, 200))
        ]
    return NumericalResult(
        method=f"ode_ivp:{method}",
        status=status,
        result={
            "time": [float(v) for v in sol.t],
            "series": series,
            "state_names": names,
            "final_state": (
                [float(v) for v in yy[:, -1]] if finite and yy.shape[1] else None
            ),
            "validation": validation,
        },
        iterations=records,
        convergence_achieved=status == STATUS_CONVERGED,
        tolerance=float(rtol),
        warnings=warnings,
        message=message,
        execution_time_ms=t.ms,
        metadata={
            "solver": method,
            "solver_description": SUPPORTED_SOLVERS[method],
            "nfev": int(sol.nfev),
            "njev": int(sol.njev) if sol.njev is not None else None,
            "nlu": int(sol.nlu) if getattr(sol, "nlu", None) is not None else None,
            "steps": int(sol.t.size),
            "rtol": float(rtol),
            "atol": float(atol),
            "t_span": [t0, t1],
            "output_points": n_points,
            "max_step": kwargs.get("max_step"),
        },
    )

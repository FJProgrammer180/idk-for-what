"""Least-squares regression with full residual analysis and parameter uncertainties."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from scipy import optimize

from .common import (
    STATUS_CONVERGED,
    STATUS_FAILED,
    NumericalInputError,
    NumericalResult,
    Timer,
)

POLYNOMIAL_MAX_DEGREE = 5

# Whitelisted nonlinear models: name -> (formula, parameter names, initial guess fn)
def _damped_sine_p0(x, y):
    """Seed the damped-sine fit.

    The frequency dominates this fit, so it is read off the discrete spectrum instead
    of assuming one cycle over the window (which stalls the optimiser on faster
    oscillations).  A single raw FFT bin is only a starting point: the optimiser
    refines it, and the residual statistics below are what the user should trust.
    """
    xs = np.sort(np.asarray(x, dtype=float))
    ys = np.asarray(y, dtype=float)
    amplitude = float(np.max(np.abs(ys)))
    decay = 0.1
    omega = max(2 * math.pi / max(float(xs[-1] - xs[0]), 1e-6), 1e-3)
    if xs.size >= 8:
        dx = float(np.median(np.diff(xs)))
        if dx > 0:
            spectrum = np.abs(np.fft.rfft(ys - float(np.mean(ys))))
            if spectrum.size > 1:
                bin_index = int(np.argmax(spectrum[1:])) + 1
                candidate = float(2 * math.pi * bin_index / (xs.size * dx))
                if math.isfinite(candidate) and candidate > 0:
                    omega = candidate
    return [amplitude, decay, omega, 0.0]


NONLINEAR_MODELS: dict[str, dict[str, object]] = {
    "exponential": {
        "formula": "y = A * exp(B * x)",
        "names": ["A", "B"],
        "p0": lambda x, y: [max(float(np.max(y)), 1e-6), -0.1],
        "fn": lambda x, A, B: A * np.exp(B * x),
    },
    "power_law": {
        "formula": "y = A * x**B",
        "names": ["A", "B"],
        "p0": lambda x, y: [max(float(np.max(y)), 1e-6), 1.0],
        "fn": lambda x, A, B: A * np.power(np.where(x == 0, 1e-12, x), B),
    },
    "gaussian": {
        "formula": "y = A * exp(-(x - mu)**2 / (2 * sigma**2)) + C",
        "names": ["A", "mu", "sigma", "C"],
        "p0": lambda x, y: [
            float(np.max(y) - np.min(y)),
            float(x[int(np.argmax(y))]),
            max(float((x.max() - x.min()) / 6.0), 1e-6),
            float(np.min(y)),
        ],
        "fn": lambda x, A, mu, sigma, C: A * np.exp(-((x - mu) ** 2) / (2 * sigma**2)) + C,
    },
    "damped_sine": {
        "formula": "y = A * exp(-gamma * x) * cos(omega * x + phi)",
        "names": ["A", "gamma", "omega", "phi"],
        "p0": _damped_sine_p0,
        "fn": lambda x, A, g, w, p: A * np.exp(-g * x) * np.cos(w * x + p),
    },
    "logistic": {
        "formula": "y = L / (1 + exp(-k * (x - x0)))",
        "names": ["L", "k", "x0"],
        "p0": lambda x, y: [float(np.max(y)), 1.0, float(np.median(x))],
        "fn": lambda x, L, k, x0: L / (1.0 + np.exp(-k * (x - x0))),
    },
}


def _clean(x: Sequence[float], y: Sequence[float]):
    xs = np.asarray(x, dtype=float).reshape(-1)
    ys = np.asarray(y, dtype=float).reshape(-1)
    if xs.size != ys.size:
        raise NumericalInputError("x and y must have the same length")
    if xs.size < 2:
        raise NumericalInputError("at least two (x, y) pairs are required")
    mask = np.isfinite(xs) & np.isfinite(ys)
    dropped = int(xs.size - mask.sum())
    xs, ys = xs[mask], ys[mask]
    if xs.size < 2:
        raise NumericalInputError("fewer than two finite data points remain after filtering")
    if np.allclose(xs, xs[0]):
        raise NumericalInputError("all x values are identical; regression is undefined")
    return xs, ys, dropped


def _residual_statistics(residuals: np.ndarray, dof: int) -> dict[str, object]:
    n = residuals.size
    rmse = float(np.sqrt(np.mean(residuals**2)))
    ssr = float(np.sum(residuals**2))
    sst = float(np.sum((residuals - np.mean(residuals)) ** 2))
    dw = None
    if n > 1:
        denom = float(np.sum(residuals**2))
        dw = float(np.sum(np.diff(residuals) ** 2) / denom) if denom > 0 else 0.0
    return {
        "count": int(n),
        "degrees_of_freedom": int(dof),
        "sum_squared_residuals": ssr,
        "rmse": rmse,
        "mean_residual": float(np.mean(residuals)),
        "std_residual": float(np.std(residuals, ddof=1)) if dof > 0 else None,
        "max_abs_residual": float(np.max(np.abs(residuals))),
        "min_residual": float(np.min(residuals)),
        "max_residual": float(np.max(residuals)),
        "durbin_watson": dw,
        "residuals": [float(v) for v in residuals],
    }


def least_squares_fit(
    x: Sequence[float],
    y: Sequence[float],
    model: str = "linear",
    degree: int = 1,
    weights: Sequence[float] | None = None,
) -> NumericalResult:
    """Fit ``y = f(x)``.

    model="polynomial" (degree 1..5, solved by normal-equation-free lstsq) or one
    of the whitelisted nonlinear models fitted with scipy.optimize.least_squares.
    """
    xs, ys, dropped = _clean(x, y)
    warnings: list[str] = []
    message = ""
    status = STATUS_CONVERGED
    if dropped:
        warnings.append(f"{dropped} non-finite data point(s) were excluded from the fit")
    model = str(model).lower()
    if model in ("linear", "poly1"):
        model, degree = "polynomial", 1
    with Timer() as t:
        if model == "polynomial":
            try:
                deg = int(degree)
            except (TypeError, ValueError) as exc:
                raise NumericalInputError("degree must be an integer") from exc
            if deg < 1 or deg > POLYNOMIAL_MAX_DEGREE:
                raise NumericalInputError(f"degree must be between 1 and {POLYNOMIAL_MAX_DEGREE}")
            if xs.size < deg + 2:
                raise NumericalInputError(
                    f"need at least {deg + 2} data points for a degree-{deg} polynomial"
                )
            W = None
            if weights is not None:
                w = np.asarray(weights, dtype=float).reshape(-1)
                if w.size != xs.size or not np.all(np.isfinite(w)) or np.any(w <= 0):
                    raise NumericalInputError("weights must be positive and finite, one per point")
                W = np.sqrt(w)
            V = np.vander(xs, deg + 1, increasing=True)
            A, b = (V, ys) if W is None else (V * W[:, None], ys * W)
            coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
            rank = np.linalg.matrix_rank(A)
            if rank < deg + 1:
                warnings.append(
                    f"design matrix rank {rank} < {deg + 1}: parameters are not uniquely determined"
                )
            pred = V @ coeffs
            residuals = ys - pred
            dof = max(xs.size - (deg + 1), 0)
            sigma2 = float(np.sum(residuals**2) / dof) if dof > 0 else float("nan")
            try:
                cov = sigma2 * np.linalg.inv(A.T @ A)
                perr = [float(math.sqrt(max(cov[i, i], 0.0))) for i in range(deg + 1)]
            except np.linalg.LinAlgError:
                cov = None
                perr = [None] * (deg + 1)
                warnings.append("parameter covariance is undefined (singular design matrix)")
            ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
            r2 = 1.0 - float(np.sum(residuals**2)) / ss_tot if ss_tot > 0 else None
            names = [f"c{i}" for i in range(deg + 1)]
            if deg == 1:
                names = ["intercept", "slope"]
            params = {
                name: {"value": float(coeffs[i]), "standard_error": perr[i]}
                for i, name in enumerate(names)
            }
            status = STATUS_CONVERGED
            message = f"degree-{deg} polynomial least-squares fit converged"
            if dof == 0:
                warnings.append(
                    "zero degrees of freedom: residuals and uncertainties are not informative"
                )
            result = {
                "model": "polynomial",
                "degree": deg,
                "formula": "y = "
                + " + ".join(f"c{i}*x^{i}" for i in range(deg + 1)),
                "parameters": params,
                "predictions": [float(v) for v in pred],
                "r_squared": r2,
                "adjusted_r_squared": (
                    1.0 - (1.0 - r2) * (xs.size - 1) / dof if (r2 is not None and dof > 0) else None
                ),
                "residuals": _residual_statistics(residuals, dof),
                "covariance": (
                    [[float(v) for v in row] for row in cov] if cov is not None else None
                ),
                "condition_number_design_matrix": float(np.linalg.cond(A)),
                "x": [float(v) for v in xs],
                "y": [float(v) for v in ys],
            }
        elif model in NONLINEAR_MODELS:
            spec = NONLINEAR_MODELS[model]
            fn = spec["fn"]  # type: ignore[index]
            pnames = spec["names"]  # type: ignore[index]
            p0 = spec["p0"](xs, ys)  # type: ignore[operator]
            n_params = len(pnames)  # type: ignore[arg-type]
            if xs.size < n_params + 2:
                raise NumericalInputError(
                    f"the '{model}' model needs at least {n_params + 2} data points"
                )

            def residual_fun(p):
                try:
                    pred = fn(xs, *p)
                except (OverflowError, FloatingPointError, ValueError):
                    return np.full(xs.size, 1e12)
                pred = np.where(np.isfinite(pred), pred, 1e12)
                return ys - pred

            try:
                out = optimize.least_squares(
                    residual_fun, p0, method="trf", x_scale="jac", ftol=1e-12, xtol=1e-12
                )
            except Exception as exc:  # noqa: BLE001
                raise NumericalInputError(f"nonlinear fit failed to start: {exc}") from exc
            pred = fn(xs, *out.x)
            residuals = ys - pred
            dof = max(xs.size - n_params, 0)
            sigma2 = float(np.sum(residuals**2) / dof) if dof > 0 else float("nan")
            try:
                J = out.jac
                cov = sigma2 * np.linalg.inv(J.T @ J)
                perr = [float(math.sqrt(max(cov[i, i], 0.0))) for i in range(n_params)]
            except np.linalg.LinAlgError:  # pragma: no cover
                cov, perr = None, [None] * n_params
            ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
            r2 = 1.0 - float(np.sum(residuals**2)) / ss_tot if ss_tot > 0 else None
            status = STATUS_CONVERGED if out.status > 0 else STATUS_FAILED
            if status == STATUS_FAILED:
                warnings.append(f"fitting terminated without convergence: {out.message}")
            message = (
                f"{model} fit: R^2 = {r2:.6g}, {out.message}"
                if r2 is not None
                else f"{model} fit: {out.message}"
            )
            result = {
                "model": model,
                "formula": spec["formula"],  # type: ignore[index]
                "parameters": {
                    name: {"value": float(out.x[i]), "standard_error": perr[i]}
                    for i, name in enumerate(pnames)  # type: ignore[arg-type]
                },
                "predictions": [float(v) for v in np.where(np.isfinite(pred), pred, math.nan)],
                "r_squared": r2,
                "residuals": _residual_statistics(residuals, dof),
                "covariance": (
                    [[float(v) for v in row] for row in cov] if cov is not None else None
                ),
                "optimizer_status": out.status,
                "optimizer_message": out.message,
                "function_evaluations": int(out.nfev),
                "x": [float(v) for v in xs],
                "y": [float(v) for v in ys],
            }
        else:
            raise NumericalInputError(
                f"unsupported model '{model}'. Use 'polynomial' or one of "
                f"{sorted(NONLINEAR_MODELS)}"
            )
    return NumericalResult(
        method="least_squares_fit",
        status=status,
        result=result,
        convergence_achieved=status == STATUS_CONVERGED,
        warnings=warnings,
        message=message,
        execution_time_ms=t.ms,
        metadata={
            "estimator": "ordinary least squares (linear in parameters)"
            if model == "polynomial"
            else "scipy.optimize.least_squares (Trust Region Reflective)",
            "data_points": int(xs.size),
            "weights_used": weights is not None,
        },
    )

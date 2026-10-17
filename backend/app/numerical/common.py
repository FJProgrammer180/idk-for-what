"""Shared types, errors and helpers for the numerical engine."""

from __future__ import annotations

import math
import platform
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

STATUS_CONVERGED = "converged"
STATUS_MAX_ITERATIONS = "max_iterations_reached"
STATUS_FAILED = "failed"
STATUS_INVALID_INPUT = "invalid_input"
STATUS_TARGET_NOT_MET = "target_accuracy_not_met"

FLOAT_TINY = 1e-300


class NumericalInputError(ValueError):
    """Raised when method inputs violate the documented mathematical preconditions."""


@dataclass
class IterationRecord:
    index: int
    a: float | None = None
    b: float | None = None
    x: float | None = None
    f_x: float | None = None
    step: float | None = None
    error: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"index": self.index}
        for key in ("a", "b", "x", "f_x", "step", "error"):
            value = getattr(self, key)
            out[key] = value if value is not None else None
        out.update(self.extra)
        return out


@dataclass
class NumericalResult:
    """Uniform structured output for every numerical method."""

    method: str
    status: str
    result: dict[str, Any]
    iterations: list[IterationRecord] = field(default_factory=list)
    error_estimate: float | None = None
    convergence_achieved: bool = False
    tolerance: float | None = None
    warnings: list[str] = field(default_factory=list)
    message: str | None = None
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, max_iterations: int | None = 200) -> dict[str, Any]:
        iterations = [r.to_dict() for r in self.iterations]
        truncated = False
        if max_iterations is not None and len(iterations) > max_iterations:
            iterations = iterations[:max_iterations]
            truncated = True
        return {
            "method": self.method,
            "status": self.status,
            "convergence_achieved": self.convergence_achieved,
            "result": self.result,
            "iterations": iterations,
            "iteration_count": len(self.iterations),
            "iterations_truncated": truncated,
            "error_estimate": self.error_estimate,
            "tolerance": self.tolerance,
            "warnings": self.warnings,
            "message": self.message,
            "execution_time_ms": self.execution_time_ms,
            "metadata": self.metadata,
        }


def software_versions() -> dict[str, str]:
    import numpy
    import pandas
    import scipy
    import sympy

    return {
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "sympy": sympy.__version__,
        "pandas": pandas.__version__,
        "implementation": platform.python_implementation(),
    }


class Timer:
    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = (time.perf_counter() - self._t0) * 1000.0


def require_finite(value: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError(f"{name} must be a real number") from exc
    if not math.isfinite(v):
        raise NumericalInputError(f"{name} must be finite (got {value})")
    return v


def check_tolerance(tol: float, default: float = 1e-10) -> float:
    if tol is None:
        tol = default
    tol = require_finite(tol, "tolerance")
    if tol <= 0:
        raise NumericalInputError("tolerance must be > 0")
    if tol > 1e-1:
        raise NumericalInputError("tolerance must be <= 1e-1")
    return tol


def check_max_iter(max_iter: int, limit: int = 200, minimum: int = 1) -> int:
    try:
        n = int(max_iter)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError("max_iterations must be an integer") from exc
    if n < minimum or n > limit:
        raise NumericalInputError(f"max_iterations must be between {minimum} and {limit}")
    return n


def safe_call(f: Callable[[float], float], x: float) -> tuple[float, str | None]:
    """Evaluate f(x); return (value, error). Never propagates host exceptions."""
    try:
        y = f(x)
    except ZeroDivisionError:
        return math.nan, "division by zero"
    except OverflowError:
        return math.inf, "overflow"
    except (ValueError, TypeError) as exc:
        return math.nan, str(exc)
    except Exception as exc:  # noqa: BLE001 - user supplied callables
        return math.nan, f"evaluation error: {type(exc).__name__}"
    try:
        y = float(y)
    except (TypeError, ValueError):
        return math.nan, "function did not return a real number"
    return y, None


def rel_err(a: float, b: float) -> float | None:
    """Relative error between computed and reference, tolerant of zero."""
    if a is None or b is None:
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    denom = abs(b) if abs(b) > FLOAT_TINY else 1.0
    return abs(a - b) / denom


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def scan_non_finite(payload: Iterable[Any]) -> list[str]:
    bad: list[str] = []
    for item in payload:
        if isinstance(item, float) and not math.isfinite(item):
            bad.append(repr(item))
    return bad

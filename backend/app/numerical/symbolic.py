"""Symbolic helpers used for *verification* (never as the reported answer)."""

from __future__ import annotations

import math

import sympy as sp

X = sp.Symbol("x", real=True)

_ALLOWED_FOR_SUBS = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "exp": sp.exp,
    "log": sp.log,
    "sqrt": sp.sqrt,
    "asin": sp.asin,
    "acos": sp.acos,
    "atan": sp.atan,
    "sinh": sp.sinh,
    "cosh": sp.cosh,
    "tanh": sp.tanh,
    "abs": sp.Abs,
    "pi": sp.pi,
}


def parse_expression(text: str) -> sp.Expr | None:
    """Safely parse a restricted expression into sympy (returns None on failure)."""
    try:
        return sp.sympify(
            text,
            locals={**_ALLOWED_FOR_SUBS, "x": X, "e": sp.E},
        )
    except Exception:  # noqa: BLE001
        return None


def symbolic_derivative(text: str, order: int = 1) -> dict[str, object] | None:
    expr = parse_expression(text)
    if expr is None:
        return None
    try:
        d = sp.diff(expr, X, order)
        simplified = sp.simplify(d)
        return {
            "expression": str(simplified),
            "latex": sp.latex(simplified),
            "at_undefined": bool(simplified.has(sp.zoo, sp.nan, sp.oo)),
        }
    except Exception:  # noqa: BLE001
        return None


def symbolic_definite_integral(text: str, a: float, b: float) -> dict[str, object] | None:
    expr = parse_expression(text)
    if expr is None:
        return None
    try:
        value = sp.integrate(expr, (X, sp.nsimplify(a), sp.nsimplify(b)))
        if value.has(sp.Integral, sp.O):
            return None
        numeric = complex(value.evalf())
        if abs(numeric.imag) > 1e-12:
            return None
        return {"expression": str(value), "numeric": float(numeric.real),
                "latex": sp.latex(value)}
    except Exception:  # noqa: BLE001
        return None


def analytic_root_check(text: str, root: float) -> dict[str, object] | None:
    expr = parse_expression(text)
    if expr is None:
        return None
    try:
        solved = sp.solve(sp.Eq(expr, 0), X)
    except Exception:  # noqa: BLE001
        return None
    candidates: list[float] = []
    for s in solved:
        try:
            val = complex(s.evalf())
        except (TypeError, ValueError):
            continue
        if abs(val.imag) < 1e-12:
            candidates.append(float(val.real))
    if not candidates:
        return None
    best = min(candidates, key=lambda c: abs(c - root))
    return {
        "closed_form_roots": candidates,
        "closest_symbolic_root": best,
        "absolute_difference": abs(best - root),
    }


def projective_closed_form_available(text: str) -> bool:
    """Cheap guard used by the writer to phrase verification honestly."""
    return parse_expression(text) is not None

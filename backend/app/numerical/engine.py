"""Dispatch layer between validated API/agent input and the method implementations.

Keeps method signatures, their input requirements and their assumptions in one
registry so the UI, the OpenAPI docs and the agent share one source of truth.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app.services import expr as exprmod

from .common import (
    STATUS_CONVERGED,
    NumericalInputError,
    NumericalResult,
    software_versions,
)
from .differentiation import finite_difference_derivative
from .integration import simpson, trapezoidal
from .ode import integrate_ode
from .linear import gauss_seidel, gaussian_elimination, jacobi, lu_decomposition
from .regression import NONLINEAR_MODELS, POLYNOMIAL_MAX_DEGREE, least_squares_fit
from .roots import bisection, newton_raphson, secant

PROBLEM_TYPES = ("root_finding", "linear_system", "integration", "derivative", "ode_ivp", "regression")

METHODS: dict[str, dict[str, Any]] = {
    "bisection": {
        "problem_type": "root_finding",
        "title": "Bisection method",
        "requires": ["expression", "a", "b"],
        "optional": ["tolerance", "max_iterations"],
        "assumptions": "f continuous on [a, b] and f(a)*f(b) <= 0 (bracketed sign change)",
        "order": "linear, |b-a| halves per iteration",
    },
    "newton_raphson": {
        "problem_type": "root_finding",
        "title": "Newton-Raphson",
        "requires": ["expression", "x0"],
        "optional": ["tolerance", "max_iterations", "derivative_expression", "initial_guess_2"],
        "assumptions": "f differentiable near the root with f'(x) != 0; local convergence",
        "order": "quadratic near a simple root",
    },
    "secant": {
        "problem_type": "root_finding",
        "title": "Secant method",
        "requires": ["expression", "x0", "x1"],
        "optional": ["tolerance", "max_iterations"],
        "assumptions": "f continuous; two distinct starting points",
        "order": "superlinear ~1.618",
    },
    "gaussian_elimination": {
        "problem_type": "linear_system",
        "title": "Gaussian elimination (partial pivoting)",
        "requires": ["matrix", "vector"],
        "optional": [],
        "assumptions": "square, non-singular coefficient matrix",
        "order": "direct method, ~2n^3/3 flops",
    },
    "lu_decomposition": {
        "problem_type": "linear_system",
        "title": "LU decomposition (Doolittle, PA=LU)",
        "requires": ["matrix"],
        "optional": ["vector"],
        "assumptions": "square, non-singular coefficient matrix",
        "order": "direct method; factorisation cost ~2n^3/3",
    },
    "jacobi": {
        "problem_type": "linear_system",
        "title": "Jacobi iterative method",
        "requires": ["matrix", "vector"],
        "optional": ["tolerance", "max_iterations", "initial_guess"],
        "assumptions": "convergence guaranteed under strict diagonal dominance (checked)",
        "order": "linear; rate governed by spectral radius of the iteration matrix",
    },
    "gauss_seidel": {
        "problem_type": "linear_system",
        "title": "Gauss-Seidel / SOR iterative method",
        "requires": ["matrix", "vector"],
        "optional": ["tolerance", "max_iterations", "initial_guess", "relaxation"],
        "assumptions": "convergence guaranteed for symmetric positive definite or strictly diagonally dominant A",
        "order": "linear; usually about twice as fast as Jacobi",
    },
    "trapezoidal": {
        "problem_type": "integration",
        "title": "Composite trapezoidal rule",
        "requires": ["expression", "a", "b"],
        "optional": ["panels", "tolerance", "max_iterations"],
        "assumptions": "integrand continuous and bounded on [a, b]",
        "order": "O(h^2)",
    },
    "simpson": {
        "problem_type": "integration",
        "title": "Composite Simpson's rule",
        "requires": ["expression", "a", "b"],
        "optional": ["panels", "tolerance", "max_iterations"],
        "assumptions": "integrand continuous on [a, b]; a fixed 'panels' count must be even "
                       "(an odd one is refused rather than quietly rounded)",
        "order": "O(h^4)",
    },
    "finite_difference": {
        "problem_type": "derivative",
        "title": "Finite-difference derivative",
        "requires": ["expression", "x"],
        "optional": ["mode", "derivative_order", "h", "tolerance"],
        "assumptions": (
            "f sufficiently smooth near x (O(h^2) for central mode); derivative_order is 1 or 2 "
            "only, h must be > 0, and second derivatives always use the central three-point "
            "stencil regardless of the requested mode"
        ),
        "order": "O(h^2) central, O(h) forward/backward; step halving refines to tol",
    },
    "ode_ivp": {
        "problem_type": "ode_ivp",
        "title": "Initial-value ODE integration (SciPy solve_ivp)",
        "requires": ["expressions", "t_span", "y0"],
        "notes": "state variables may be referenced by their state_names or as y1..yn",
        "optional": ["solver", "rtol", "atol", "n_points", "max_step", "state_names", "constants"],
        "assumptions": "RHS continuous and Lipschitz in y over the integration window",
        "order": "solver dependent (RK45 4/5, DOP853 8, Radau/BDF implicit)",
    },
    "least_squares": {
        "problem_type": "regression",
        "title": "Least-squares fit with residual analysis",
        "requires": ["x", "y"],
        "optional": ["model", "degree"],
        "assumptions": "independent variable x not constant; residuals assumed i.i.d. for the quoted standard errors",
        "order": "closed form (linear) / Trust-Region-Reflective (nonlinear)",
    },
}


def catalog() -> list[dict[str, Any]]:
    out = []
    for name, spec in METHODS.items():
        out.append({"method": name, **spec})
    return out


def _compile(
    expression: str,
    variables: tuple[str, ...] = ("x",),
    probes: tuple[tuple[float, ...], ...] | None = None,
):
    """Compile through the restricted parser, translating parse failures into the
    API's 400-level input error instead of letting them surface as 500s.

    ``probes`` overrides the default all-variables-swept grid. Callers that already know
    some symbols are fixed parameters (an ODE's constants) must pass probes with those
    bound, or a term like ``g/L`` is evaluated at L = 0 and a perfectly good expression is
    rejected as "never finite".
    """
    try:
        fn = exprmod.compile_expression(expression, variables)
    except exprmod.ExpressionError as exc:
        raise NumericalInputError(
            f"expression rejected by the safe parser: {exc}. Allowed: numbers, the declared "
            "variables, + - * / // % ^, parentheses, implicit multiplication (2x) and the "
            "documented function set; a single '=' is read as lhs - rhs. No comparisons, "
            "attribute access, imports or subscripts."
        ) from exc
    ok, why = exprmod.function_is_valid(fn, probes)
    if not ok:
        raise NumericalInputError(f"expression could not be validated: {why}")
    return fn


def _fn(expression: str, variables: tuple[str, ...] = ("x",)) -> Callable[[float], float]:
    fn = _compile(expression, variables)
    return lambda v: fn.call(float(v))


def _vector(values: Any, name: str) -> list[float]:
    if isinstance(values, str):
        try:
            import json

            values = json.loads(values)
        except ValueError as exc:
            raise NumericalInputError(f"{name} must be a numeric array") from exc
    if not isinstance(values, (list, tuple)) or not values:
        raise NumericalInputError(f"{name} must be a non-empty array of numbers")
    try:
        out = [float(v) for v in values]
    except (TypeError, ValueError) as exc:
        raise NumericalInputError(f"{name} contains non-numeric entries") from exc
    if not all(math.isfinite(v) for v in out):
        raise NumericalInputError(f"{name} contains non-finite values")
    return out


def _matrix(values: Any, name: str = "matrix") -> list[list[float]]:
    if not isinstance(values, (list, tuple)) or not values:
        raise NumericalInputError(f"{name} must be a 2-D array")
    rows = [list(r) for r in values]
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise NumericalInputError(f"{name} rows must all have the same length")
    return [[float(v) for v in row] for row in rows]


def accepted_parameters(name: str) -> set[str]:
    """Every parameter key this method reads - the same set the strict check enforces."""
    spec = METHODS.get(str(name).lower().strip())
    if spec is None:
        return set()
    return set(spec.get("requires", ())) | set(spec.get("optional", ()))


def run_numerical_method(name: str, params: dict[str, Any]) -> NumericalResult:
    """Execute a registered numerical method with validated parameters."""
    method = str(name).lower().strip()
    if method not in METHODS:
        raise NumericalInputError(
            f"method '{name}' is not registered. Available: {', '.join(sorted(METHODS))}"
        )
    spec = METHODS[method]
    p = params or {}

    # A key nobody reads is not a harmless typo: `n_points` instead of `panels` would
    # quietly switch a fixed-grid rule to its adaptive one and still report "completed".
    allowed = set(spec.get("requires", ())) | set(spec.get("optional", ()))
    unknown = sorted(str(k) for k in p if str(k) not in allowed)
    if unknown:
        raise NumericalInputError(
            f"unknown parameter(s) {unknown} for {method}; "
            f"{method} accepts: {', '.join(sorted(allowed))}"
        )

    def need(key: str) -> Any:
        if key not in p or p[key] in (None, "", [], {}):
            raise NumericalInputError(f"'{key}' is required for {method} ({spec['title']})")
        return p[key]

    tol = p.get("tolerance")
    max_iter = p.get("max_iterations", 100)

    if method == "bisection":
        f = _fn(need("expression"))
        return bisection(f, float(need("a")), float(need("b")),
                         tol=1e-10 if tol is None else float(tol),
                         max_iter=int(max_iter))
    if method == "newton_raphson":
        f = _fn(need("expression"))
        df = _fn(p["derivative_expression"]) if p.get("derivative_expression") else None
        return newton_raphson(f, float(need("x0")), tol=1e-12 if tol is None else float(tol),
                              max_iter=int(max_iter), df=df)
    if method == "secant":
        f = _fn(need("expression"))
        return secant(f, float(need("x0")), float(need("x1")),
                      tol=1e-12 if tol is None else float(tol), max_iter=int(max_iter))
    if method == "gaussian_elimination":
        return gaussian_elimination(_matrix(need("matrix")), _vector(need("vector"), "vector"))
    if method == "lu_decomposition":
        return lu_decomposition(
            _matrix(need("matrix")),
            _vector(p["vector"], "vector") if p.get("vector") else None,
        )
    if method in ("jacobi", "gauss_seidel"):
        fn = jacobi if method == "jacobi" else gauss_seidel
        kwargs: dict[str, Any] = {
            "tol": 1e-10 if tol is None else float(tol),
            "max_iter": int(max_iter),
        }
        if p.get("initial_guess"):
            kwargs["x0"] = _vector(p["initial_guess"], "initial_guess")
        if method == "gauss_seidel" and p.get("relaxation") is not None:
            kwargs["relaxation"] = float(p["relaxation"])
        return fn(_matrix(need("matrix")), _vector(need("vector"), "vector"), **kwargs)
    if method in ("trapezoidal", "simpson"):
        f = _fn(need("expression"))
        impl = trapezoidal if method == "trapezoidal" else simpson
        return impl(
            f,
            float(need("a")),
            float(need("b")),
            n=int(p["panels"]) if p.get("panels") else None,
            tol=1e-8 if tol is None else float(tol),
            max_iter=int(max_iter) if p.get("max_iterations") else 30,
        )
    if method == "finite_difference":
        f = _fn(need("expression"))
        return finite_difference_derivative(
            f,
            float(need("x")),
            mode=str(p.get("mode", "central")),
            order=int(p.get("derivative_order", 1)),
            h=float(p["h"]) if p.get("h") is not None else None,
            tol=1e-8 if tol is None else float(tol),
        )
    if method == "ode_ivp":
        raw = need("expressions")
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(";") if part.strip()]
        if not isinstance(raw, (list, tuple)) or not (1 <= len(raw) <= 16):
            raise NumericalInputError("expressions must be 1..16 RHS expressions")
        names = (
            [str(n) for n in p["state_names"]]
            if p.get("state_names")
            else [f"y{i + 1}" for i in range(len(raw))]
        )
        constants: dict[str, float] = {}
        raw_constants = p.get("constants") or {}
        if isinstance(raw_constants, dict):
            for key, value in raw_constants.items():
                if str(key) in names or str(key) == "t":
                    raise NumericalInputError(
                        f"constant '{key}' collides with a state variable name"
                    )
                try:
                    constants[str(key)] = float(value)
                except (TypeError, ValueError) as exc:
                    raise NumericalInputError(f"constant '{key}' must be numeric") from exc
        inline = {k: v for k, v in p.items() if k not in {
            "expressions", "t_span", "y0", "solver", "rtol", "atol", "n_points",
            "max_step", "state_names", "constants", "tolerance", "max_iterations"}}
        for key, value in inline.items():
            if isinstance(value, (int, float)) and str(key) not in names and str(key) != "t":
                constants.setdefault(str(key), float(value))
        aliases = [f"y{i + 1}" for i in range(len(names))]
        symbols = tuple(names + aliases + ["t"] + list(constants.keys()))
        # Probe at real states with the constants bound to their configured values.
        constant_values = tuple(constants[key] for key in constants)
        state_probes = tuple(
            tuple(probe) for probe in (
                [(0.0,) * len(raw), (1.0,) * len(raw), (0.5, -0.5) + (0.0,) * (len(raw) - 2)][:3]
            )
        )
        rhs_probes = tuple(
            tuple(state) + tuple(state) + (0.0,) + constant_values for state in state_probes
        )
        fns = [_compile(str(e), symbols, probes=rhs_probes) for e in raw]
        # Evaluate each RHS at a few sample states with the real constants bound.
        state_probes = [(0.0,) * len(names), (1.0,) * len(names), (0.5, -0.5) + (0.0,) * (len(names) - 2)]
        for i, fn in enumerate(fns):
            good, why = False, None
            for state_probe in state_probes:
                env = dict(zip(names, state_probe))
                env.update({f"y{j + 1}": v for j, v in enumerate(state_probe)})
                env["t"] = 0.0
                env.update(constants)
                try:
                    value = exprmod._evaluate(fn.ast, env)  # noqa: SLF001 - internal evaluator
                    if isinstance(value, float) and math.isfinite(value):
                        good = True
                        break
                    why = f"returned {value}"
                except (ValueError, ZeroDivisionError, OverflowError) as exc:
                    why = str(exc)
            if not good:
                raise NumericalInputError(
                    f"RHS expression {i + 1} did not produce a finite real number at the "
                    f"sample states ({why}). Check the expression and the constants."
                )

        def rhs(t: float, y):  # noqa: ANN001 - scipy contract
            live = [float(v) for v in y]
            env = live + live + [float(t)] + [constants[k] for k in constants]
            return [fn.call(*env) for fn in fns]

        span = _vector(need("t_span"), "t_span")
        if len(span) != 2:
            raise NumericalInputError("t_span must be [t_start, t_end]")
        y0 = _vector(need("y0"), "y0")
        if len(y0) != len(raw):
            raise NumericalInputError("y0 must have one entry per state equation")
        result = integrate_ode(
            rhs,
            (span[0], span[1]),
            y0,
            method=str(p.get("solver", "RK45")),
            rtol=float(p.get("rtol", 1e-9)),
            atol=float(p.get("atol", 1e-12)),
            n_points=int(p.get("n_points", 400)),
            max_step=float(p["max_step"]) if p.get("max_step") else None,
            state_names=names,
        )
        result.metadata["expressions"] = [str(e) for e in raw]
        result.metadata["constants"] = constants
        result.metadata["initial_conditions"] = y0
        return result
    if method == "least_squares":
        return least_squares_fit(
            _vector(need("x"), "x"),
            _vector(need("y"), "y"),
            model=str(p.get("model", "polynomial")),
            degree=int(p.get("degree", 1)),
        )
    raise NumericalInputError(f"method '{method}' has no executor")


def describe() -> dict[str, Any]:
    return {
        "problem_types": list(PROBLEM_TYPES),
        "methods": catalog(),
        "regression_models": {
            "polynomial": f"degree 1..{POLYNOMIAL_MAX_DEGREE}",
            **{k: v["formula"] for k, v in NONLINEAR_MODELS.items()},
        },
        "expression_grammar": exprmod.allowed_names(),
        "software_versions": software_versions(),
    }

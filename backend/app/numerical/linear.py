"""Linear algebra methods: Gaussian elimination, LU, Jacobi, Gauss-Seidel."""

from __future__ import annotations

import math

import numpy as np

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
)

ILL_CONDITIONED = 1e12


def _as_matrix(A, name: str = "A") -> np.ndarray:
    try:
        M = np.array(A, dtype=float)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError(f"{name} must be a rectangular numeric matrix") from exc
    if M.ndim != 2 or M.shape[0] != M.shape[1]:
        raise NumericalInputError(f"{name} must be a square 2-D matrix")
    if M.shape[0] < 1:
        raise NumericalInputError(f"{name} must have at least one row")
    if not np.all(np.isfinite(M)):
        raise NumericalInputError(f"{name} contains non-finite entries")
    return M


def _as_vector(b, n: int, name: str = "b") -> np.ndarray:
    try:
        v = np.array(b, dtype=float).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError(f"{name} must be a numeric vector") from exc
    if v.size != n:
        raise NumericalInputError(f"{name} must have length {n} to match the matrix")
    if not np.all(np.isfinite(v)):
        raise NumericalInputError(f"{name} contains non-finite entries")
    return v


def _condition_warning(A: np.ndarray, warnings: list[str]) -> float | None:
    try:
        cond = float(np.linalg.cond(A))
    except np.linalg.LinAlgError:  # pragma: no cover
        return None
    if math.isfinite(cond) and cond > ILL_CONDITIONED:
        warnings.append(
            f"matrix is ill-conditioned (estimated cond ~ {cond:.3g}); "
            "solution digits may be unreliable"
        )
    return cond if math.isfinite(cond) else None


def gaussian_elimination(
    A, b, pivot_tol: float = 1e-14, partial_pivoting: bool = True
) -> NumericalResult:
    """Gaussian elimination with back substitution (partial pivoting by default)."""
    M = _as_matrix(A)
    rhs = _as_vector(b, M.shape[0])
    n = M.shape[0]
    warnings: list[str] = []
    cond = _condition_warning(M, warnings)
    with Timer() as t:
        U = M.astype(float).copy()
        y = rhs.astype(float).copy()
        pivots: list[int] = []
        multiplier_ops = 0
        for k in range(n):
            if partial_pivoting:
                p = k + int(np.argmax(np.abs(U[k:, k])))
            else:
                p = k
            if abs(U[p, k]) < pivot_tol:
                raise NumericalInputError(
                    f"matrix is singular or numerically singular: zero pivot in column {k + 1} "
                    f"(|value| = {abs(U[p, k]):.3g} < {pivot_tol:g})"
                )
            if p != k:
                U[[k, p], :] = U[[p, k], :]
                y[k], y[p] = y[p], y[k]
            pivots.append(int(p + 1))
            for i in range(k + 1, n):
                factor = U[i, k] / U[k, k]
                multiplier_ops += 1
                U[i, k:] -= factor * U[k, k:]
                y[i] -= factor * y[k]
                if abs(U[i, k]) > 0:
                    U[i, k] = 0.0
            if abs(U[n - 1, n - 1]) < pivot_tol and n - 1 > k:
                raise NumericalInputError(
                    f"matrix is singular: vanishing pivot at row {n} after elimination"
                )
        x = np.zeros(n)
        for i in range(n - 1, -1, -1):
            s = y[i] - U[i, i + 1 :] @ x[i + 1 :]
            x[i] = s / U[i, i]
        residual = float(np.max(np.abs(M @ x - rhs)))
        backward_error = residual / max(1.0, float(np.max(np.abs(rhs))))
    return NumericalResult(
        method="gaussian_elimination",
        status=STATUS_CONVERGED,
        result={
            "solution": [float(v) for v in x],
            "residual_infinity_norm": residual,
            "backward_error": backward_error,
            "upper_triangular": [[float(v) for v in row] for row in U],
            "modified_rhs": [float(v) for v in y],
        },
        iterations=[
            IterationRecord(index=i + 1, x=float(x[i]), f_x=residual,
                            extra={"pivot_column": i + 1})
            for i in range(n)
        ],
        convergence_achieved=True,
        warnings=warnings,
        message=f"direct solve completed for a {n}x{n} system",
        execution_time_ms=t.ms,
        metadata={
            "size": n,
            "pivots": pivots,
            "partial_pivoting": partial_pivoting,
            "multiplier_operations": multiplier_ops,
            "condition_number_estimate": cond,
            "flops_estimate": (2 * n**3 / 3 + 2 * n**2),
        },
    )


def lu_decomposition(A, b=None, pivot_tol: float = 1e-14) -> NumericalResult:
    """Doolittle LU with partial pivoting; returns P, L, U and solves Ax=b if given."""
    M = _as_matrix(A)
    n = M.shape[0]
    rhs = _as_vector(b, n) if b is not None else None
    warnings: list[str] = []
    cond = _condition_warning(M, warnings)
    with Timer() as t:
        U = M.astype(float).copy()
        L = np.eye(n)
        P = np.eye(n)
        pivots: list[int] = []
        for k in range(n):
            p = k + int(np.argmax(np.abs(U[k:, k])))
            if abs(U[p, k]) < pivot_tol:
                raise NumericalInputError(
                    f"LU factorization failed: singular matrix (zero pivot in column {k + 1})"
                )
            if p != k:
                U[[k, p], k:] = U[[p, k], k:]
                P[[k, p], :] = P[[p, k], :]
                L[[k, p], :k] = L[[p, k], :k]
            pivots.append(int(p + 1))
            for i in range(k + 1, n):
                factor = U[i, k] / U[k, k]
                L[i, k] = factor
                U[i, k:] -= factor * U[k, k:]
                U[i, k] = 0.0
        det = float(np.prod(np.diag(U)) * (1.0 if int(np.sum(np.abs(P @ np.arange(n))) >= 0) else 1.0))
        # exact sign from the permutation matrix
        perm = np.argmax(P, axis=1)
        det = float(np.prod(np.diag(U)) * _permutation_sign(perm))
        solve_info = None
        residual = None
        if rhs is not None:
            y = np.linalg.solve(np.tril(L, -1) + np.eye(n), P @ rhs)
            x = np.linalg.solve(U, y)
            residual = float(np.max(np.abs(M @ x - rhs)))
            solve_info = {
                "solution": [float(v) for v in x],
                "residual_infinity_norm": residual,
                "forward_substitution": [float(v) for v in y],
            }
        L_mat = [[float(v) for v in row] for row in L]
        U_mat = [[float(v) for v in row] for row in U]
        P_mat = [[float(v) for v in row] for row in P]
        reconstruction_err = float(np.max(np.abs(P.T @ np.array(L_mat) @ np.array(U_mat) - M)))
        if reconstruction_err > 1e-8 * max(1.0, float(np.max(np.abs(M)))):
            warnings.append(
                f"PA=LU reconstruction error is {reconstruction_err:.3g}; factorization is approximate"
            )
    return NumericalResult(
        method="lu_decomposition",
        status=STATUS_CONVERGED,
        result={
            "L": L_mat,
            "U": U_mat,
            "P": P_mat,
            "determinant": det,
            "reconstruction_error": reconstruction_err,
            "solve": solve_info,
        },
        convergence_achieved=True,
        warnings=warnings,
        message=f"LU factorization of a {n}x{n} matrix completed",
        execution_time_ms=t.ms,
        metadata={
            "size": n,
            "pivots": pivots,
            "decomposition": "Doolittle with partial pivoting (PA = LU)",
            "condition_number_estimate": cond,
        },
    )


def _permutation_sign(perm: np.ndarray) -> float:
    """+1 / -1 parity of a permutation given as an index array."""
    p = list(map(int, perm))
    seen = [False] * len(p)
    sign = 1.0
    for i in range(len(p)):
        if seen[i]:
            continue
        length = 0
        j = i
        while not seen[j]:
            seen[j] = True
            j = p[j]
            length += 1
        if length % 2 == 0:
            sign = -sign
    return sign


def _spectral_radius(T: np.ndarray) -> float:
    try:
        return float(np.max(np.abs(np.linalg.eigvals(T))))
    except np.linalg.LinAlgError:
        return float("nan")


def _iterative_solve(
    method: str,
    A,
    b,
    x0=None,
    tol: float = 1e-10,
    max_iter: int = 200,
    relaxation: float = 1.0,
) -> NumericalResult:
    M = _as_matrix(A)
    n = M.shape[0]
    rhs = _as_vector(b, n)
    tol = check_tolerance(tol)
    max_iter = check_max_iter(max_iter, limit=10000)
    warnings: list[str] = []
    D = np.diag(np.diag(M))
    rest = M - D
    if np.any(np.abs(np.diag(M)) < 1e-14):
        raise NumericalInputError(
            f"{method} requires non-zero diagonal entries; row(s) "
            f"{[i + 1 for i, d in enumerate(np.diag(M)) if abs(d) < 1e-14]} violate this"
        )
    diag_abs = np.abs(np.diag(M))
    off_abs = np.abs(rest).sum(axis=1)
    dd_rows = int(np.sum(off_abs < diag_abs))
    dd_all = float(np.max(off_abs / diag_abs))
    if method == "jacobi":
        T = -np.linalg.inv(D) @ rest
    else:
        T = -np.linalg.inv(D + np.tril(M, -1)) @ np.triu(M, 1)
    rho = _spectral_radius(T)
    c = np.linalg.solve(D + np.tril(M, -1), rhs) if method != "jacobi" else np.linalg.solve(D, rhs)
    cond = _condition_warning(M, warnings)
    with Timer() as t:
        x = (
            np.array(x0, dtype=float).reshape(-1)
            if x0 is not None
            else np.zeros(n)
        )
        if x.size != n:
            raise NumericalInputError("x0 must have the same length as b")
        records: list[IterationRecord] = []
        status = STATUS_MAX_ITERATIONS
        for i in range(1, max_iter + 1):
            if method == "jacobi":
                x_new = T @ x + c
            else:
                y = rhs - np.triu(M, 1) @ x
                x_new = np.linalg.solve(np.tril(M, -1) + D, y)
                x_new = (1 - relaxation) * x + relaxation * x_new
            if not np.all(np.isfinite(x_new)):
                status = STATUS_FAILED
                warnings.append("iteration produced non-finite values; method diverged")
                break
            diff = float(np.max(np.abs(x_new - x)))
            residual = float(np.max(np.abs(M @ x_new - rhs)))
            records.append(
                IterationRecord(
                    index=i,
                    x=float(np.max(x_new)),
                    f_x=residual,
                    step=diff,
                    error=diff,
                    extra={"x_vector": [float(v) for v in x_new]},
                )
            )
            x = x_new
            if diff <= tol * max(1.0, float(np.max(np.abs(x)))):
                status = STATUS_CONVERGED
                break
        else:
            warnings.append(
                f"stopping criterion max|x^(k+1)-x^(k)| <= {tol:g} not met in {max_iter} sweeps"
            )
    converged = status == STATUS_CONVERGED
    residual = float(np.max(np.abs(M @ x - rhs))) if np.all(np.isfinite(x)) else None
    if math.isfinite(rho) and rho >= 1.0 and converged:
        warnings.append(
            "convergence despite spectral radius rho >= 1 for the iteration matrix; "
            "the initial guess may be exceptional - verify against a direct solve"
        )
    return NumericalResult(
        method=method,
        status=status,
        result={
            "solution": [float(v) for v in x],
            "residual_infinity_norm": residual,
            "iterations_used": len(records),
        },
        iterations=records,
        error_estimate=records[-1].error if records else None,
        convergence_achieved=converged,
        tolerance=tol,
        warnings=warnings,
        message=(
            f"{method} converged to max-norm residual {residual:.3g}"
            if converged
            else f"{method} did not satisfy its stopping criterion"
        ),
        execution_time_ms=t.ms,
        metadata={
            "size": n,
            "spectral_radius_iteration_matrix": rho if math.isfinite(rho) else None,
            "strict_diagonal_dominance_rows": dd_rows,
            "max_offdiag_over_diag": dd_all,
            "convergence_guaranteed_by_dd": bool(dd_all < 1.0),
            "relaxation": relaxation,
            "stopping_criterion": "max|x^(k+1) - x^(k)| <= tol * max(1, |x|)",
            "condition_number_estimate": cond,
        },
    )


def jacobi(A, b, x0=None, tol: float = 1e-10, max_iter: int = 200) -> NumericalResult:
    """Jacobi stationary iteration for Ax = b."""
    return _iterative_solve("jacobi", A, b, x0=x0, tol=tol, max_iter=max_iter)


def gauss_seidel(
    A, b, x0=None, tol: float = 1e-10, max_iter: int = 200, relaxation: float = 1.0
) -> NumericalResult:
    """Gauss-Seidel iteration (optionally SOR when relaxation != 1)."""
    if not (0.0 < relaxation <= 2.0):
        raise NumericalInputError("relaxation must be in (0, 2]")
    return _iterative_solve(
        "gauss_seidel", A, b, x0=x0, tol=tol, max_iter=max_iter, relaxation=relaxation
    )

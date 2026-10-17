"""Mathematical correctness and honest-convergence tests for ``app/numerical``.

Every expected value comes from a closed form or an independent library (numpy,
SciPy), not from the implementation under test.  The second theme is honesty: when a
stopping criterion is not met the status must say so, and the number must be described
as an iterate rather than a solution.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.numerical.common import (
    STATUS_CONVERGED,
    STATUS_FAILED,
    STATUS_MAX_ITERATIONS,
    STATUS_TARGET_NOT_MET,
    NumericalInputError,
)
from app.numerical.engine import METHODS, catalog, describe, run_numerical_method

ROOT2 = 1.4142135623730951
DOTTIE = 0.7390851332151607  # the fixed point of cos
PI = math.pi


def run(method, **params):
    return run_numerical_method(method, params)


# --- registry self-description ---------------------------------------------------


def test_every_registered_method_documents_assumptions_and_convergence_order():
    entries = catalog()
    assert len(entries) == len(METHODS) == 12
    for entry in entries:
        for field in ("title", "assumptions", "order", "requires", "problem_type"):
            assert entry.get(field), f"{entry['method']} is missing '{field}'"
        # 'optional' must exist and be a list (it may legitimately be empty)
        assert isinstance(entry.get("optional"), list), f"{entry['method']} has no optional list"


def test_describe_publishes_the_expression_grammar_and_software_versions():
    doc = describe()
    assert set(doc) >= {"problem_types", "methods", "regression_models", "expression_grammar",
                        "software_versions"}
    # the parser's advertised function list must actually be usable
    assert "sin" in doc["expression_grammar"]["unary_functions"]
    assert doc["software_versions"]["numpy"] and doc["software_versions"]["scipy"]


# --- root finding ----------------------------------------------------------------


@pytest.mark.parametrize(
    "method,params",
    [
        ("bisection", {"a": 1.0, "b": 2.0}),
        ("newton_raphson", {"x0": 1.5, "derivative_expression": "2*x"}),
        ("secant", {"x0": 1.0, "x1": 2.0}),
    ],
)
def test_root_of_x_squared_minus_two_matches_the_closed_form(method, params):
    result = run(method, expression="x^2 - 2", tolerance=1e-12, max_iterations=200, **params)
    assert result.status == STATUS_CONVERGED
    assert result.convergence_achieved is True
    assert abs(result.result["root"] - ROOT2) < 1e-9
    assert abs(result.result["f_root"]) < 1e-8
    assert result.error_estimate is not None and result.error_estimate <= 1e-8


def test_bisection_brackets_the_dottie_number_and_records_every_iterate():
    result = run("bisection", expression="cos(x) - x", a=0.0, b=1.0, tolerance=1e-14)
    assert abs(result.result["root"] - DOTTIE) < 1e-12
    low, high = result.result["bracket"]
    assert low <= DOTTIE <= high
    # the iteration log must be complete and monotonically narrowing
    assert len(result.iterations) == len([r for r in result.iterations]) > 0
    assert result.to_dict()["iteration_count"] >= len(result.iterations)
    widths = [record.b - record.a for record in result.iterations if record.a is not None]
    assert all(w > 0 for w in widths)
    assert widths == sorted(widths, reverse=True)


def test_bisection_error_estimate_is_the_bracket_half_width():
    result = run("bisection", expression="x^3 - 2", a=1.0, b=2.0, tolerance=1e-10)
    width = result.result["interval_width"]
    assert math.isclose(result.error_estimate, width, rel_tol=1e-12)
    # the estimate must be a true bound on the distance to the exact root
    assert abs(result.result["root"] - 2.0 ** (1 / 3)) <= result.error_estimate + 1e-15
    assert result.metadata["stopping_criterion"] == "abs(b - a) / 2 <= tol"


def test_bisection_rejects_an_unbracketed_interval():
    with pytest.raises(NumericalInputError) as exc:
        run("bisection", expression="x^2 + 1", a=0.0, b=1.0)
    assert "sign" in str(exc.value).lower() or "bracket" in str(exc.value).lower()


def test_iteration_cap_is_reported_as_unconverged_and_the_value_is_an_iterate():
    result = run("bisection", expression="x^2 - 2", a=1.0, b=2.0, tolerance=1e-16, max_iterations=5)
    assert result.status == STATUS_MAX_ITERATIONS
    assert result.convergence_achieved is False
    assert len(result.iterations) == 5
    assert math.isclose(result.result["interval_width"], 0.5**5, rel_tol=1e-12)
    assert any("NOT a verified root" in warning for warning in result.warnings)
    # it also tells the user how many iterations *would* have been needed
    assert result.metadata["expected_iterations_to_bracket"] > 5


def test_newton_stops_at_a_stationary_point_instead_of_inventing_a_root():
    # f = x^2 + 1 has no real root and f'(0) = 0, so the very first step is undefined
    result = run(
        "newton_raphson",
        expression="x^2 + 1",
        derivative_expression="2*x",
        x0=0.0,
        max_iterations=40,
    )
    assert result.status == STATUS_FAILED
    assert result.convergence_achieved is False
    assert any("derivative is zero" in warning for warning in result.warnings)
    assert "root" not in result.result or result.result.get("root") is None


def test_newton_reports_divergence_when_the_iteration_escapes():
    # Newton on atan(x) from x0 = 10 is the classic diverging case
    result = run("newton_raphson", expression="atan(x)", x0=10.0, tolerance=1e-12, max_iterations=40)
    assert result.status == STATUS_FAILED
    assert result.convergence_achieved is False
    assert any("divergence" in warning.lower() or "non-finite" in warning.lower()
               for warning in result.warnings)


# --- integration -----------------------------------------------------------------


def test_simpson_is_exact_for_a_cubic():
    result = run("simpson", expression="x^3", a=0.0, b=1.0, panels=4, tolerance=1e-12)
    assert abs(result.result["integral"] - 0.25) < 1e-12
    assert result.status == STATUS_CONVERGED


def test_simpson_rejects_an_odd_panel_count():
    with pytest.raises(NumericalInputError):
        run("simpson", expression="sin(x)", a=0.0, b=PI, panels=7)


def test_simpson_matches_scipy_on_a_gaussian():
    from scipy.integrate import quad

    result = run(
        "simpson",
        expression="exp(-x^2/2)/sqrt(2*pi)",
        a=-3.0,
        b=3.0,
        panels=400,
        tolerance=1e-13,
    )
    reference, _ = quad(lambda x: math.exp(-x * x / 2) / math.sqrt(2 * math.pi), -3.0, 3.0)
    assert math.isclose(result.result["integral"], reference, rel_tol=1e-10, abs_tol=1e-12)


def test_fixed_panel_trapezoidal_labels_its_accuracy_honestly():
    result = run("trapezoidal", expression="sin(x)", a=0.0, b=PI, panels=64, tolerance=1e-12)
    true_error = abs(result.result["integral"] - 2.0)
    assert result.status == STATUS_TARGET_NOT_MET
    assert result.convergence_achieved is False
    # the Richardson estimate must not understate the real error by more than its
    # asymptotic factor (the leading term cancels for sin on a full period)
    assert true_error <= max(result.error_estimate * 4, 1e-12)
    assert any("Richardson" in warning for warning in result.warnings)


def test_simpson_reports_the_tolerance_it_cannot_reach_on_a_fine_grid():
    # |I - 2| is ~1e-10 on 200 panels: the value is right, the requested 1e-12 is not,
    # and the status must say so instead of claiming convergence.
    fine = run("simpson", expression="sin(x)", a=0.0, b=PI, panels=200, tolerance=1e-12)
    assert abs(fine.result["integral"] - 2.0) < 1e-9
    assert fine.status == STATUS_TARGET_NOT_MET
    assert fine.convergence_achieved is False
    relaxed = run("simpson", expression="sin(x)", a=0.0, b=PI, panels=200, tolerance=1e-6)
    assert relaxed.status == STATUS_CONVERGED


# --- differentiation -------------------------------------------------------------


@pytest.mark.parametrize("mode", ["central", "forward", "backward"])
def test_finite_difference_second_derivative_of_sine(mode):
    result = run(
        "finite_difference",
        expression="sin(x)",
        x=1.0,
        h=1e-2,
        derivative_order=2,
        mode=mode,
        tolerance=1e-6,
    )
    assert abs(result.result["derivative"] - (-math.sin(1.0))) < 1e-2
    assert result.result["mode"] == mode
    if mode != "central":
        # the second-order stencil is central no matter what was asked for, and the
        # result has to disclose that rather than echoing the requested mode silently
        assert any("symmetric three-point stencil" in w for w in result.warnings)


def test_finite_difference_refines_the_step_and_reports_the_step_it_used():
    result = run("finite_difference", expression="exp(x)*sin(x)", x=0.7, h=1e-1, derivative_order=1)
    exact = math.exp(0.7) * (math.sin(0.7) + math.cos(0.7))
    assert abs(result.result["derivative"] - exact) < 1e-6
    assert result.result["step_size_used"] <= 1e-1


def test_finite_difference_rejects_a_non_positive_step():
    with pytest.raises(NumericalInputError):
        run("finite_difference", expression="sin(x)", x=1.0, h=0.0)


def test_third_derivative_is_refused_with_the_supported_range_in_the_message():
    with pytest.raises(NumericalInputError) as exc:
        run("finite_difference", expression="sin(x)", x=1.0, h=0.2, derivative_order=3)
    assert "first and second" in str(exc.value)


# --- linear systems --------------------------------------------------------------


MATRIX = [[3.0, 1.0, 2.0], [2.0, 4.0, 1.0], [1.0, 2.0, 5.0]]
VECTOR = [9.0, 8.0, 10.0]


def test_gaussian_elimination_matches_numpy_solve():
    result = run("gaussian_elimination", matrix=MATRIX, vector=VECTOR)
    expected = np.linalg.solve(np.array(MATRIX), np.array(VECTOR))
    assert np.allclose(result.result["solution"], expected, rtol=1e-10, atol=1e-12)
    assert result.result["residual_infinity_norm"] < 1e-10
    assert result.status == STATUS_CONVERGED


def test_gaussian_elimination_pivots_when_the_leading_entry_is_zero():
    result = run("gaussian_elimination", matrix=[[0.0, 1.0], [1.0, 0.0]], vector=[2.0, 1.0])
    assert np.allclose(result.result["solution"], [1.0, 2.0], atol=1e-12)


def test_a_singular_matrix_is_an_input_error_not_a_zero_solution():
    with pytest.raises(NumericalInputError):
        run("gaussian_elimination", matrix=[[1.0, 2.0], [2.0, 4.0]], vector=[3.0, 6.0])


def test_lu_decomposition_reproduces_the_matrix_and_the_determinant():
    result = run("lu_decomposition", matrix=MATRIX, vector=VECTOR)
    payload = result.result
    lower = np.array(payload["L"])
    upper = np.array(payload["U"])
    perm = np.array(payload["P"])
    assert np.allclose(perm @ np.array(MATRIX), lower @ upper, atol=1e-12)
    assert math.isclose(payload["determinant"], float(np.linalg.det(np.array(MATRIX))), rel_tol=1e-10)
    assert payload["reconstruction_error"] < 1e-12
    assert np.allclose(payload["solve"]["solution"], np.linalg.solve(np.array(MATRIX), np.array(VECTOR)))


@pytest.mark.parametrize("method", ["jacobi", "gauss_seidel"])
def test_iterative_solvers_converge_on_a_dominant_system(method):
    matrix = [[10.0, 2.0, 1.0], [1.0, 8.0, 2.0], [2.0, 1.0, 9.0]]
    vector = [13.0, 11.0, 12.0]
    result = run(method, matrix=matrix, vector=vector, tolerance=1e-10, max_iterations=200)
    expected = np.linalg.solve(np.array(matrix), np.array(vector))
    assert result.status == STATUS_CONVERGED, result.message
    assert np.allclose(result.result["solution"], expected, rtol=1e-7, atol=1e-9)
    assert result.result["residual_infinity_norm"] <= 1e-9 * 100


def test_jacobi_says_so_when_the_iteration_diverges():
    result = run("jacobi", matrix=[[1.0, 5.0], [5.0, 1.0]], vector=[1.0, 1.0], max_iterations=20)
    assert result.status == STATUS_MAX_ITERATIONS
    assert result.convergence_achieved is False
    assert result.result["residual_infinity_norm"] > 1.0


def test_inconsistent_matrix_shapes_are_rejected():
    with pytest.raises(NumericalInputError):
        run("gaussian_elimination", matrix=[[1.0, 2.0], [3.0, 4.0]], vector=[1.0, 2.0, 3.0])


# --- ODE ------------------------------------------------------------------------


def test_harmonic_oscillator_returns_to_its_initial_state_over_one_period():
    result = run(
        "ode_ivp",
        expressions=["y2", "-y1"],
        t_span=[0.0, 2 * PI],
        y0=[1.0, 0.0],
        state_names=["x", "v"],
        n_points=400,
        rtol=1e-10,
        atol=1e-12,
    )
    assert result.status == STATUS_CONVERGED
    x_end, v_end = result.result["final_state"]
    assert abs(x_end - 1.0) < 1e-6
    assert abs(v_end) < 1e-6
    assert len(result.result["time"]) == 400
    assert result.result["time"][0] == 0.0 and math.isclose(result.result["time"][-1], 2 * PI)
    assert result.metadata["solver"] == "RK45"


def test_damped_oscillator_energy_decides_monotonically():
    result = run(
        "ode_ivp",
        expressions=["y2", "-y1 - 0.4*y2"],
        t_span=[0.0, 20.0],
        y0=[1.0, 0.0],
        n_points=600,
        rtol=1e-10,
        atol=1e-12,
    )
    x = np.asarray(result.result["series"]["y1"])
    v = np.asarray(result.result["series"]["y2"])
    energy = 0.5 * v**2 + 0.5 * x**2
    assert energy[-1] < energy[0]
    # local maxima of the energy must decrease (allowing tiny numerical jitter)
    assert all(np.diff(energy) < 1e-9)


def test_ode_rejects_a_t_span_that_is_not_increasing():
    with pytest.raises(NumericalInputError):
        run("ode_ivp", expressions=["y1"], t_span=[1.0, 1.0], y0=[0.0])


def test_ode_binds_named_constants_into_the_right_hand_side():
    result = run(
        "ode_ivp",
        expressions=["-k*y1"],
        t_span=[0.0, 1.0],
        y0=[1.0],
        constants={"k": 2.0},
        n_points=50,
        rtol=1e-10,
        atol=1e-12,
    )
    assert math.isclose(result.result["final_state"][0], math.exp(-2.0), rel_tol=1e-7)


# --- regression ------------------------------------------------------------------


XS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
YS = [1.1, 1.9, 3.1, 3.9, 5.2, 5.9, 7.1]


def test_least_squares_matches_numpy_polyfit_and_reports_uncertainty():
    result = run("least_squares", x=XS, y=YS, model="linear")
    slope, intercept = np.polyfit(XS, YS, 1)
    params = result.result["parameters"]
    assert math.isclose(params["slope"]["value"], slope, rel_tol=1e-9)
    assert math.isclose(params["intercept"]["value"], intercept, rel_tol=1e-9)
    assert params["slope"]["standard_error"] > 0
    assert result.result["r_squared"] > 0.99
    residuals = np.array(YS) - (slope * np.array(XS) + intercept)
    assert math.isclose(result.result["residuals"]["rmse"], float(np.sqrt((residuals**2).mean())), rel_tol=1e-9)
    assert result.result["residuals"]["mean_residual"] == pytest.approx(0.0, abs=1e-12)


def test_least_squares_prefers_a_quadratic_when_the_data_is_curved():
    y_quad = [x * x for x in XS]
    linear = run("least_squares", x=XS, y=y_quad, model="linear")
    quad = run("least_squares", x=XS, y=y_quad, model="polynomial", degree=2)
    assert quad.result["r_squared"] > linear.result["r_squared"]
    assert math.isclose(quad.result["parameters"]["c2"]["value"], 1.0, rel_tol=1e-6)


@pytest.mark.parametrize("model", ["exponential", "power_law", "logistic", "gaussian", "damped_sine"])
def test_nonlinear_models_are_registered_and_fitted(model):
    describe_models = describe()["regression_models"]
    assert model in describe_models
    xs = [i / 10 for i in range(1, 25)]
    if model == "exponential":
        ys = [2.0 * math.exp(0.3 * x) for x in xs]
    elif model == "power_law":
        ys = [1.5 * x**1.7 for x in xs]
    elif model == "logistic":
        ys = [4.0 / (1.0 + math.exp(-2.0 * (x - 1.2))) for x in xs]
    elif model == "gaussian":
        ys = [3.0 * math.exp(-((x - 1.2) ** 2) / 0.08) + 0.1 for x in xs]
    else:
        ys = [math.exp(-0.4 * x) * math.cos(6.0 * x) for x in xs]
    result = run("least_squares", x=xs, y=ys, model=model)
    assert result.status == STATUS_CONVERGED, result.message
    assert result.result["r_squared"] > 0.9, (model, result.result["r_squared"])


def test_a_fit_needs_more_points_than_parameters():
    with pytest.raises(NumericalInputError):
        run("least_squares", x=[1.0], y=[2.0], model="linear")
    with pytest.raises(NumericalInputError):
        run("least_squares", x=[1.0, 2.0], y=[2.0, 3.0], model="polynomial", degree=5)


def test_x_and_y_must_have_matching_lengths():
    with pytest.raises(NumericalInputError):
        run("least_squares", x=[1.0, 2.0, 3.0], y=[1.0, 2.0], model="linear")

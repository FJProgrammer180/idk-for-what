"""Physics simulations: analytic validation, honest status reporting, precondition errors.

Every simulation that has a closed form must report a real comparison against it; every
simulation that does not must say ``not_available`` instead of pretending it was checked.
"""

from __future__ import annotations

import math
import re

import pytest

from app.numerical.common import NumericalInputError
from app.physics.engine import SIM_SPECS, run_simulation, simulation_catalog

G = 9.80665


def test_catalog_documents_the_model_for_every_simulation():
    entries = simulation_catalog()
    assert len(entries) == len(SIM_SPECS) == 5
    for entry in entries:
        assert entry["assumptions"], entry["key"]
        assert entry["limitations"], entry["key"]
        assert entry["governing_equations"], entry["key"]
        assert entry["parameters"], entry["key"]
        assert entry["analytical_validation_available"] in (True, False)
        for name, meta in entry["parameters"].items():
            assert "default" in meta, (entry["key"], name)
            if "enum" in meta:  # categorical options carry a value set, not a range
                assert meta["default"] in meta["enum"], (entry["key"], name)
            elif meta.get("derived"):
                # a parameter whose default comes from the physics must say what computes it
                assert meta["default"] is None, (entry["key"], name)
                assert "derived" in meta["description"], (entry["key"], name)
            else:
                assert {"min", "max"} <= set(meta), (entry["key"], name)
                assert meta["min"] <= meta["default"] <= meta["max"], (entry["key"], name)


def test_every_payload_carries_the_same_model_block():
    """Reports and the UI read one shape; heat must not be the exception."""
    for key in SIM_SPECS:
        payload = run_simulation(key, {})
        model = payload["model"]
        for field in ("assumptions", "limitations", "governing_equations", "parameters", "title"):
            assert field in model, (key, field)
        assert model["assumptions"] and model["limitations"], key
        assert payload["units"], f"{key} publishes no units"


# --- projectile -------------------------------------------------------------------


def test_vacuum_projectile_matches_the_analytic_range_and_height():
    result = run_simulation("projectile_motion", {"v0": 40.0, "angle_deg": 45.0})
    summary = result["summary"]
    analytic_range = 40.0**2 / G
    analytic_height = (40.0 * math.sin(math.radians(45.0))) ** 2 / (2 * G)
    assert math.isclose(summary["range_at_first_impact"], analytic_range, rel_tol=1e-4)
    assert math.isclose(summary["max_height"], analytic_height, rel_tol=1e-3)
    # the closed form is published next to the numerical value, not instead of it
    assert math.isclose(summary["analytical_range"], analytic_range, rel_tol=1e-9)
    assert result["validation"]["overall"] == "passed"
    check = result["validation"]["checks"][0]
    assert check["name"] == "analytical_solution_comparison"
    assert check["passed"] is True
    assert check["max_relative_error"] < 1e-6


def test_drag_reduces_the_range_and_disables_the_vacuum_reference():
    result = run_simulation(
        "projectile_motion", {"v0": 40.0, "angle_deg": 45.0, "mass": 2.0, "drag_coefficient_total": 0.35}
    )
    vacuum = run_simulation("projectile_motion", {"v0": 40.0, "angle_deg": 45.0})
    assert result["summary"]["range_at_first_impact"] < vacuum["summary"]["range_at_first_impact"]
    assert result["validation"]["checks"] == []
    assert result["validation"]["overall"] == "not_available"
    assert any("no analytical reference" in note for note in result["validation"]["notes"])


def test_symmetric_launch_angles_reach_the_same_range_in_vacuum():
    a = run_simulation("projectile_motion", {"v0": 25.0, "angle_deg": 30.0})
    b = run_simulation("projectile_motion", {"v0": 25.0, "angle_deg": 60.0})
    assert math.isclose(
        a["summary"]["range_at_first_impact"], b["summary"]["range_at_first_impact"], rel_tol=1e-3
    )
    assert b["summary"]["max_height"] > a["summary"]["max_height"]


def test_range_is_the_horizontal_speed_times_the_flight_time():
    result = run_simulation("projectile_motion", {"v0": 30.0, "angle_deg": 30.0})
    summary = result["summary"]
    expected = 30.0 * math.cos(math.radians(30.0)) * summary["flight_time_to_impact"]
    assert math.isclose(summary["range_at_first_impact"], expected, rel_tol=2e-3)
    assert summary["impact_detected"] is True
    # in a vacuum the impact speed equals the launch speed (energy conservation)
    assert math.isclose(summary["impact_speed"], 30.0, rel_tol=1e-4)


def test_launch_speed_beyond_the_documented_range_is_rejected():
    with pytest.raises(NumericalInputError) as exc:
        run_simulation("projectile_motion", {"v0": 1e9, "angle_deg": 45.0})
    assert "outside the allowed range" in str(exc.value)


# --- heat equation -----------------------------------------------------------------


def test_heat_decays_like_the_analytic_single_sine_mode():
    result = run_simulation("heat_1d", {"alpha": 1.0, "grid_points": 121, "duration": 0.05})
    check = result["validation"]["checks"][0]
    assert check["name"] == "analytical_single_sine_decay"
    assert check["passed"] is True
    assert check["relative_error"] < 5e-3
    assert result["status"] == "completed"
    peak = result["summary"]["final_max_temperature"]
    assert 0.0 < peak < 1.0


def test_heat_reports_the_stability_number_it_actually_used():
    result = run_simulation("heat_1d", {"alpha": 1.0, "grid_points": 41, "duration": 0.01})
    solver = result["solver"]
    assert solver["stability_satisfied"] is True
    assert solver["stability_number_r"] <= solver["stability_limit"] + 1e-12
    dx = result["grid"]["dx"]
    assert math.isclose(solver["stability_number_r"], 1.0 * solver["dt"] / dx**2, rel_tol=1e-9)
    assert result["grid"]["points"] == 41


def test_insulated_boundaries_conserve_the_discrete_integral():
    result = run_simulation(
        "heat_1d",
        {"boundary": "neumann_insulated", "initial_condition": "top_hat", "duration": 0.05,
         "grid_points": 101},
    )
    check = next(c for c in result["validation"]["checks"] if c["name"] == "discrete_energy_conservation")
    assert check["passed"] is True
    assert check["relative_drift"] < 1e-9
    summary = result["summary"]
    assert math.isclose(summary["heat_integral_final"], summary["heat_integral_initial"], rel_tol=1e-9)


def test_heat_stays_inside_the_physical_bounds():
    result = run_simulation(
        "heat_1d", {"initial_condition": "top_hat", "duration": 0.2, "grid_points": 201}
    )
    flat = [value for row in result["series"]["snapshots"] for value in row]
    assert max(flat) <= 1.0 + 1e-9
    assert min(flat) >= -1e-9


def test_heat_grid_size_is_validated():
    with pytest.raises(NumericalInputError):
        run_simulation("heat_1d", {"grid_points": 3})


def test_a_non_reference_initial_condition_says_the_validation_is_unavailable():
    result = run_simulation(
        "heat_1d", {"initial_condition": "top_hat", "duration": 0.01, "grid_points": 201}
    )
    assert result["validation"]["overall"] == "not_available"
    assert any("no single-mode closed form" in note for note in result["validation"]["notes"])


# --- pendulum and harmonic motion ---------------------------------------------------


def test_undamped_pendulum_period_is_measured_and_compared_with_the_linear_formula():
    result = run_simulation(
        "dynamical_system", {"preset": "nonlinear_pendulum", "b": 0.0, "theta": 1.0, "duration": 20.0,
                             "n_points": 2000}
    )
    summary = result["summary"]
    # the preset's own g and L, read back from the payload, not from a constant here
    g = result["model"]["parameters"]["g"]
    length = result["model"]["parameters"]["L"]
    assert math.isclose(summary["linear_period_s"], 2 * math.pi / math.sqrt(g / length), rel_tol=1e-12)
    assert summary["linear_period_formula"] == "2*pi/sqrt(g/L)"
    # at 1 rad amplitude the true period exceeds the small-angle formula by ~6.6 %
    assert summary["measured_period_s"] > summary["linear_period_s"]
    assert math.isclose(summary["measured_period_s"] / summary["linear_period_s"], 1.066, rel_tol=0.02)


def test_undamped_pendulum_conserves_energy_to_the_tolerances_of_the_solver():
    result = run_simulation(
        "dynamical_system", {"preset": "nonlinear_pendulum", "b": 0.0, "theta": 1.0, "duration": 20.0,
                             "n_points": 2000}
    )
    check = next(c for c in result["validation"]["checks"] if c["name"] == "invariant_energy_drift")
    assert check["passed"] is True
    assert check["max_relative_drift"] < 1e-6


def test_damped_pendulum_loses_energy_monotonically():
    import numpy as np

    result = run_simulation(
        "dynamical_system", {"preset": "nonlinear_pendulum", "b": 0.3, "theta": 1.2, "duration": 30.0,
                             "n_points": 1500}
    )
    theta = np.asarray(result["series"]["theta"])
    omega = np.asarray(result["series"]["omega"])
    energy = 0.5 * omega**2 + G * (1 - np.cos(theta))
    assert energy[-1] < energy[0]
    assert all(np.diff(energy) < 1e-9)


def test_harmonic_oscillator_period_matches_the_closed_form():
    result = run_simulation(
        "simple_harmonic_motion",
        {"mass": 1.0, "spring_constant": 4.0, "damping": 0.0, "duration": 20.0, "n_points": 4000},
    )
    assert math.isclose(result["summary"]["period_s"], math.pi, rel_tol=1e-6)
    assert math.isclose(result["summary"]["natural_frequency_rad_s"], 2.0, rel_tol=1e-9)
    assert result["summary"]["regime"] == "undamped"
    assert result["summary"]["energy_relative_drift"] < 1e-6


def test_damping_regimes_are_classified_from_the_damping_ratio():
    under = run_simulation("simple_harmonic_motion", {"mass": 1.0, "spring_constant": 4.0,
                                                       "damping": 0.5, "duration": 20.0})
    over = run_simulation("simple_harmonic_motion", {"mass": 1.0, "spring_constant": 4.0,
                                                     "damping": 20.0, "duration": 120.0})
    assert under["summary"]["regime"] == "underdamped"
    assert over["summary"]["regime"] == "overdamped"
    # the slow eigenvalue of s^2 + 20 s + 4 = 0 sets the terminal decay
    slow = (-20.0 + math.sqrt(20.0**2 - 16.0)) / 2.0
    assert math.isclose(math.log(abs(over["summary"]["final_amplitude"])) , slow * 120.0, abs_tol=1.5)
    assert abs(under["summary"]["final_amplitude"]) < 1.0


# --- Newton cooling -----------------------------------------------------------------


def test_newton_cooling_matches_the_exponential_law():
    result = run_simulation(
        "newton_cooling", {"T0": 90.0, "T_env": 20.0, "cooling_rate": 0.05, "duration": 40.0}
    )
    exact = 20.0 + 70.0 * math.exp(-0.05 * 40.0)
    assert math.isclose(result["summary"]["final_temperature"], exact, rel_tol=1e-6)
    assert result["validation"]["overall"] == "passed"
    assert math.isclose(result["summary"]["time_constant_s"], 20.0, rel_tol=1e-12)
    assert math.isclose(result["summary"]["half_life_s"], 20.0 * math.log(2), rel_tol=1e-12)


def test_cooling_is_monotone_and_never_overshoots_the_ambient_temperature():
    result = run_simulation(
        "newton_cooling", {"T0": 90.0, "T_env": 20.0, "cooling_rate": 0.4, "duration": 20.0}
    )
    temps = result["series"]["T"]
    assert all(b <= a + 1e-9 for a, b in zip(temps, temps[1:]))
    assert min(temps) >= 20.0 - 1e-9


def test_warming_towards_a_hotter_environment_is_the_same_linear_law():
    result = run_simulation("newton_cooling", {"T0": -5.0, "T_env": 15.0, "duration": 10.0})
    assert result["summary"]["final_temperature"] > -5.0
    assert result["summary"]["final_temperature"] < 15.0 + 1e-9


# --- series shape / artifacts ---------------------------------------------------------


@pytest.mark.parametrize(
    "method,params",
    [
        ("projectile_motion", {"v0": 20.0, "angle_deg": 35.0}),
        ("heat_1d", {"duration": 0.02}),
        ("newton_cooling", {}),
        ("simple_harmonic_motion", {"duration": 6.0}),
        ("dynamical_system", {"preset": "lorenz", "duration": 12.0}),
    ],
)
def test_series_are_aligned_and_finite(method, params):
    result = run_simulation(method, params)
    times = result["series"]["time"]
    assert times == sorted(times)
    for name, values in result["series"].items():
        if name == "time":
            continue
        if name == "snapshots":
            assert all(len(row) == result["grid"]["points"] for row in values)
        else:
            assert len(values) == len(times), (name, len(values), len(times))


def test_lorenz_reports_no_analytic_reference_and_discloses_chaos():
    result = run_simulation("dynamical_system", {"preset": "lorenz", "duration": 10.0})
    assert result["validation"]["overall"] == "not_available"
    assert max(abs(v) for v in result["series"]["x"]) < 60.0
    assert any("chaotic" in limitation for limitation in result["model"]["limitations"])


def test_unknown_simulation_names_are_listed_in_the_error():
    with pytest.raises(NumericalInputError) as exc:
        run_simulation("quantum_tunneling", {})
    assert "heat_1d" in str(exc.value)


def test_initial_conditions_take_effect_and_aliases_are_disclosed():
    """A parameter is only useful if it changes the trajectory - and a renamed one says so."""
    small = run_simulation("dynamical_system", {"preset": "nonlinear_pendulum", "theta": 0.2,
                                                "duration": 5.0})
    wide = run_simulation("dynamical_system", {"preset": "nonlinear_pendulum", "theta": 1.4,
                                               "duration": 5.0})
    assert small["model"]["initial_state"]["theta"] == pytest.approx(0.2)
    assert wide["model"]["initial_state"]["theta"] == pytest.approx(1.4)
    # the finite-amplitude period grows with amplitude, so the two cannot be identical
    assert "measured_period_s" in small["summary"]
    assert small["summary"]["measured_period_s"] != pytest.approx(
        wide["summary"]["measured_period_s"], rel=1e-3
    )
    # finite amplitude lengthens the period; the small-angle value is the lower bound
    assert small["summary"]["measured_period_s"] < wide["summary"]["measured_period_s"]
    assert small["summary"]["measured_period_s"] >= small["summary"]["linear_period_s"] * 0.999

    aliased = run_simulation("dynamical_system", {"preset": "nonlinear_pendulum", "theta0": 0.9,
                                                  "length": 2.0, "damping": 0.1, "duration": 5.0})
    assert aliased["model"]["initial_state"]["theta"] == pytest.approx(0.9)
    assert aliased["model"]["parameters"]["L"] == pytest.approx(2.0)
    assert aliased["model"]["parameters"]["b"] == pytest.approx(0.1)
    assert any("folded to the documented ones" in w for w in aliased["warnings"]), aliased["warnings"]
    assert aliased["parameter_aliases"] == {"theta0": "theta", "length": "L", "damping": "b"}


def test_an_undeclared_simulation_parameter_is_refused_not_ignored():
    with pytest.raises(NumericalInputError) as excinfo:
        run_simulation("projectile_motion", {"v0": 20.0, "wind": 3.0})
    message = str(excinfo.value)
    assert "wind" in message and "angle_deg" in message
    with pytest.raises(NumericalInputError):
        run_simulation("simple_harmonic_motion", {"omega": 2.0})  # omega is derived, not an input

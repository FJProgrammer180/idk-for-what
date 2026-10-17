"""Physics simulation module.

Every simulation exposes:
  * the governing equations (as LaTeX, for the UI / reports),
  * explicit model assumptions,
  * a validated parameter set,
  * a numerical solver choice,
  * a validation block (analytical comparison where one exists).

Simulations are idealised models.  ``limitation`` strings are returned alongside
results so the UI and the report generator can state clearly what is NOT modelled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from app.numerical.common import NumericalInputError, Timer
from app.numerical.ode import integrate_ode
from app.services.expr import ExpressionError, compile_expression


@dataclass
class SimSpec:
    key: str
    title: str
    category: str
    equations: list[str]
    assumptions: list[str]
    limitations: list[str]
    parameters: dict[str, dict[str, Any]]
    state_names: list[str]
    builder: Callable[[dict[str, float]], tuple[Callable, dict[str, Any]]]
    summary_fields: list[str] = field(default_factory=list)
    analytical_validation: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "category": self.category,
            "governing_equations": self.equations,
            "assumptions": self.assumptions,
            "limitations": self.limitations,
            "parameters": self.parameters,
            "state_variables": self.state_names,
            "analytical_validation_available": self.analytical_validation,
        }


def _num(params: dict[str, Any], key: str, default: float, lo: float, hi: float,
         unit: str = "") -> float:
    raw = params.get(key, default)
    if raw is None:
        raw = default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise NumericalInputError(f"parameter '{key}' must be numeric") from exc
    if not math.isfinite(value):
        raise NumericalInputError(f"parameter '{key}' must be finite")
    if value < lo or value > hi:
        raise NumericalInputError(
            f"parameter '{key}' = {value:g}{unit} is outside the allowed range [{lo:g}, {hi:g}]"
        )
    return value


def _positive(params, key, default, lo=1e-9, hi=1e12, unit=""):
    v = _num(params, key, default, lo, hi, unit)
    if v <= 0:
        raise NumericalInputError(f"parameter '{key}' must be > 0")
    return v


# --- projectile --------------------------------------------------------------


def _build_projectile(params: dict[str, Any]):
    v0 = _positive(params, "v0", 40.0, 1e-6, 1e6, " m/s")
    angle_deg = _num(params, "angle_deg", 45.0, -180.0, 180.0, " deg")
    height = _num(params, "launch_height", 0.0, 0.0, 1e6, " m")
    g = _positive(params, "g", 9.80665, 0.05, 40.0, " m/s^2")
    mass = _positive(params, "mass", 1.0, 1e-6, 1e6, " kg")
    drag = _num(params, "drag_coefficient_total", 0.0, 0.0, 100.0, " kg/m")
    angle = math.radians(angle_deg)
    vx, vy = v0 * math.cos(angle), v0 * math.sin(angle)
    k_over_m = drag / mass

    def rhs(t, y):
        speed = math.hypot(y[2], y[3])
        return [
            y[2],
            y[3],
            -k_over_m * speed * y[2],
            -g - k_over_m * speed * y[3],
        ]

    meta = {
        "v0": v0,
        "angle_deg": angle_deg,
        "g": g,
        "mass": mass,
        "drag": drag,
        "launch_height": height,
        "y0": [0.0, height, vx, vy],
        "t_end": max(2.0 * vy / g + math.sqrt(2 * height / g), 0.05) * 1.6 + 0.5,
        "state_names": ["x", "y", "vx", "vy"],
        "units": {"x": "m", "y": "m", "vx": "m/s", "vy": "m/s"},
    }
    return rhs, meta


# --- simple harmonic motion --------------------------------------------------


def _build_shm(params: dict[str, Any]):
    m = _positive(params, "mass", 1.0, 1e-6, 1e6, " kg")
    k = _positive(params, "spring_constant", 10.0, 1e-6, 1e9, " N/m")
    c = _num(params, "damping", 0.0, 0.0, 1e6, " N*s/m")
    x0 = _num(params, "x0", 1.0, -1e6, 1e6, " m")
    v0 = _num(params, "v0", 0.0, -1e6, 1e6, " m/s")
    omega0 = math.sqrt(k / m)
    zeta = c / (2 * math.sqrt(k * m))
    period = 2 * math.pi / omega0
    t_end = 10 * period if c == 0 else 10 * period / max(1 - min(zeta, 0.995), 0.05)
    meta = {
        "mass": m,
        "k": k,
        "c": c,
        "omega0": omega0,
        "zeta": zeta,
        "period": period,
        "regime": (
            "undamped" if c == 0 else
            "underdamped" if zeta < 1 else
            "critically damped" if abs(zeta - 1) < 1e-9 else "overdamped"
        ),
        "y0": [x0, v0],
        "t_end": min(t_end, 5000.0),
        "state_names": ["x", "v"],
        "units": {"x": "m", "v": "m/s"},
    }

    def rhs(t, y):
        return [y[1], (-k * y[0] - c * y[1]) / m]

    return rhs, meta


# --- Newton cooling ----------------------------------------------------------


def _build_cooling(params: dict[str, Any]):
    T0 = _num(params, "T0", 90.0, -1000, 5000, " K")
    T_env = _num(params, "T_env", 22.0, -1000, 5000, " K")
    k = _positive(params, "cooling_rate", 0.05, 1e-6, 1e3, " 1/s")
    t_end = _positive(params, "duration", 120.0, 1e-3, 1e6, " s")
    meta = {
        "T0": T0,
        "T_env": T_env,
        "k": k,
        "y0": [T0],
        "t_end": t_end,
        "state_names": ["T"],
        "units": {"T": "K"},
        "half_life": math.log(2) / k,
        "time_constant": 1.0 / k,
    }

    def rhs(t, y):
        return [-k * (y[0] - T_env)]

    return rhs, meta


def _reference_cooling(meta):
    def ref(t):
        return [meta["T_env"] + (meta["T0"] - meta["T_env"]) * math.exp(-meta["k"] * t)]

    return ref


def _reference_shm(meta):
    if meta["c"] != 0:
        return None
    w = meta["omega0"]
    x0, v0 = meta["y0"]

    def ref(t):
        return [x0 * math.cos(w * t) + v0 / w * math.sin(w * t),
                -x0 * w * math.sin(w * t) + v0 * math.cos(w * t)]

    return ref


def _reference_projectile(meta):
    if meta["drag"] != 0:
        return None
    g, h = meta["g"], meta["launch_height"]
    vx, vy = meta["y0"][2], meta["y0"][3]
    x0, y0 = meta["y0"][0], meta["y0"][1]

    def ref(t):
        return [x0 + vx * t, y0 + vy * t - 0.5 * g * t * t, vx, vy - g * t]

    return ref


# --- 1-D heat diffusion ------------------------------------------------------


def simulate_heat_1d(params: dict[str, Any]) -> dict[str, Any]:
    """Explicit FTCS finite-difference solver for u_t = alpha u_xx.

    Dirichlet (fixed temperature) or Neumann (insulated) boundaries, with an
    enforced von Neumann stability check and a discrete-energy conservation test.
    """
    alpha = _positive(params, "alpha", 1.0, 1e-9, 1e6, " m^2/s")
    length = _positive(params, "length", 1.0, 1e-6, 1e6, " m")
    nx = int(params.get("grid_points", 101))
    if nx < 5 or nx > 5000:
        raise NumericalInputError("grid_points must be between 5 and 5000")
    t_end = _positive(params, "duration", 0.05, 1e-9, 1e6, " s")
    bc = str(params.get("boundary", "dirichlet_zero")).lower()
    if bc not in ("dirichlet_zero", "neumann_insulated"):
        raise NumericalInputError("boundary must be 'dirichlet_zero' or 'neumann_insulated'")
    ic = str(params.get("initial_condition", "sine")).lower()
    if ic not in ("sine", "top_hat", "gaussian"):
        raise NumericalInputError("initial_condition must be sine | top_hat | gaussian")

    dx = length / (nx - 1)
    max_dt = 0.5 * dx * dx / alpha
    target_steps = max(int(params.get("time_steps", 0)) if params.get("time_steps") else 0, 0)
    if target_steps:
        dt = t_end / target_steps
    else:
        dt = 0.4 * dx * dx / alpha
    r = alpha * dt / (dx * dx)
    if r > 0.5:
        dt = 0.5 * dx * dx / alpha
        r = alpha * dt / (dx * dx)
        note = (
            "requested time step violated the von Neumann stability limit "
            "r = alpha*dt/dx^2 <= 0.5; dt was reduced automatically"
        )
    else:
        note = None
    steps = int(math.ceil(t_end / dt))
    t_out = min(steps, 400)
    save_every = max(steps // t_out, 1)

    x = np.linspace(0.0, length, nx)
    if ic == "sine":
        u = np.sin(np.pi * x / length)
        mode = 1
    elif ic == "top_hat":
        u = ((x > length / 3) & (x < 2 * length / 3)).astype(float)
        mode = None
    else:
        u = np.exp(-((x - length / 2) ** 2) / (2 * (length / 8) ** 2))
        mode = None

    u = u.astype(float)
    u_snapshots: list[list[float]] = []
    t_snapshots: list[float] = []
    heat_history: list[float] = []
    if bc == "dirichlet_zero":
        u[0] = u[-1] = 0.0
    with Timer() as t:
        for step in range(1, steps + 1):
            if bc == "dirichlet_zero":
                u[1:-1] = u[1:-1] + r * (u[2:] - 2 * u[1:-1] + u[:-2])
            else:
                new = u.copy()
                new[1:-1] = u[1:-1] + r * (u[2:] - 2 * u[1:-1] + u[:-2])
                new[0] = u[0] + 2 * r * (u[1] - u[0])
                new[-1] = u[-1] + 2 * r * (u[-2] - u[-1])
                u = new
            if not np.all(np.isfinite(u)):
                raise NumericalInputError("heat solver produced non-finite values")
            if np.max(np.abs(u)) > 1e12:
                raise NumericalInputError("heat solution blew up: solver is unstable")
            if step % save_every == 0 or step == steps:
                u_snapshots.append([float(v) for v in u])
                t_snapshots.append(step * dt)
                # trapezoidal (finite-volume) weights: exactly conserved for the
                # half-cell boundary updates used by the Neumann variant below
                heat_history.append(float((0.5 * u[0] + np.sum(u[1:-1]) + 0.5 * u[-1]) * dx))
        max_value = float(np.max(u))
        checks: list[dict[str, Any]] = []
        notes: list[str] = []
        if mode is not None and bc == "dirichlet_zero":
            exact = math.exp(-alpha * (mode * math.pi / length) ** 2 * t_snapshots[-1])
            rel = abs(exact - max_value) / max(abs(exact), 1e-12)
            checks.append(
                {
                    "name": "analytical_single_sine_decay",
                    "reference": "exp(-alpha*(mode*pi/L)^2*t) applied to the peak amplitude",
                    "analytical_peak": exact,
                    "numerical_peak": max_value,
                    "absolute_error": abs(exact - max_value),
                    "relative_error": rel,
                    "tolerance": 5e-3,
                    "passed": bool(rel < 5e-3),
                }
            )
        if bc == "neumann_insulated":
            drift = abs(heat_history[-1] - heat_history[0]) / max(abs(heat_history[0]), 1e-12)
            checks.append(
                {
                    "name": "discrete_energy_conservation",
                    "reference": "integral of u dx (trapezoidal weights) must stay constant with insulated boundaries",
                    "initial_integral": heat_history[0],
                    "final_integral": heat_history[-1],
                    "relative_drift": drift,
                    "tolerance": 1e-9,
                    "passed": bool(drift < 1e-9),
                }
            )
        if not checks:
            notes.append(
                "the chosen initial condition has no single-mode closed form; only the "
                "stability condition and boundedness were checked"
            )
        validation = {
            "checks": checks,
            "overall": ("passed" if checks and all(c["passed"] for c in checks)
                        else "failed" if checks else "not_available"),
            "notes": notes,
        }
    warnings: list[str] = []
    if note:
        warnings.append(note)
    warnings.append(
        "explicit FTCS is conditionally stable and only first-order accurate in time; "
        f"r = {r:.4f}, O(dx^2) truncation error"
    )
    return {
        "simulation": "heat_1d",
        "status": "completed",
        "message": f"{steps} explicit steps on a {nx}-point grid finished",
        "grid": {"x": [float(v) for v in x], "dx": dx, "points": nx},
        "series": {"time": t_snapshots, "snapshots": u_snapshots},
        "summary": {
            "final_max_temperature": max_value,
            "final_mean_temperature": float(np.mean(u)),
            "heat_integral_initial": heat_history[0],
            "heat_integral_final": heat_history[-1],
        },
        "solver": {
            "scheme": "explicit forward-time centred-space (FTCS)",
            "time_steps": steps,
            "dt": dt,
            "stability_number_r": r,
            "stability_limit": 0.5,
            "stability_satisfied": r <= 0.5,
            "initial_condition": ic,
            "boundary_condition": bc,
        },
        "model": {
            "parameters": {"alpha": alpha, "length": length, "duration": t_end},
            "units": {
                "alpha": "m^2/s",
                "length": "m",
                "x": "m",
                "time": "s",
                # the initial conditions are normalised to a unit peak, so u is a
                # scaled temperature rather than a reading in kelvin
                "u": "dimensionless (unit peak)",
            },
        },
        "validation": validation,
        "warnings": warnings,
        "execution_time_ms": t.ms,
    }


HEAT_SPEC = SimSpec(
    key="heat_1d",
    title="1-D heat diffusion (FTCS)",
    category="parabolic PDE",
    equations=[r"\frac{\partial u}{\partial t} = \alpha \frac{\partial^2 u}{\partial x^2}"],
    assumptions=[
        "homogeneous isotropic rod of constant alpha",
        "explicit FTCS discretisation with r = alpha*dt/dx^2 <= 1/2",
        "boundary: fixed-zero Dirichlet or insulated Neumann",
    ],
    limitations=[
        "first-order accurate in time; solution depends on grid resolution",
        "no temperature-dependent material properties",
    ],
    parameters={
        "alpha": {"default": 1.0, "min": 1e-9, "max": 1e6, "unit": "m^2/s",
                  "description": "thermal diffusivity"},
        "length": {"default": 1.0, "min": 1e-6, "max": 1e6, "unit": "m", "description": "rod length"},
        "grid_points": {"default": 101, "min": 5, "max": 5000, "unit": "", "description": "spatial nodes"},
        "duration": {"default": 0.05, "min": 1e-9, "max": 1e6, "unit": "s", "description": "final time"},
        "time_steps": {"default": 0, "min": 0, "max": 200000, "unit": "",
                       "description": "0 = choose a stable step automatically"},
        "boundary": {"default": "dirichlet_zero", "enum": ["dirichlet_zero", "neumann_insulated"],
                     "description": "boundary condition"},
        "initial_condition": {"default": "sine", "enum": ["sine", "top_hat", "gaussian"],
                              "description": "initial temperature profile"},
    },
    state_names=["u"],
    builder=None,  # type: ignore[arg-type]
    analytical_validation=True,
)


# --- generic dynamical systems ----------------------------------------------

SYSTEM_PRESETS: dict[str, dict[str, Any]] = {
    "nonlinear_pendulum": {
        "title": "Nonlinear pendulum",
        "state_names": ["theta", "omega"],
        "expressions": ["omega", "-(g/L)*sin(theta) - b*omega"],
        "parameters": {
            "g": {"default": 9.81, "min": 0.05, "max": 40.0, "unit": "m/s^2"},
            "L": {"default": 1.0, "min": 0.01, "max": 100.0, "unit": "m"},
            "b": {"default": 0.0, "min": 0.0, "max": 100.0, "unit": "1/s"},
        },
        "initial": {"theta": {"default": 1.0, "min": -20.0, "max": 20.0, "unit": "rad"},
                    "omega": {"default": 0.0, "min": -100.0, "max": 100.0, "unit": "rad/s"}},
        "equations": [r"\dot{\theta} = \omega",
                      r"\dot{\omega} = -\frac{g}{L}\sin\theta - b\,\omega"],
        "assumptions": ["point mass on a rigid massless rod", "no drive torque"],
        "limitations": [
            "2-D ideal pendulum: no pivot friction model beyond the linear b term",
        ],
        "energy": "(g/L)*(1-cos(theta)) + omega^2/2",
        "linear_period": "2*pi/sqrt(g/L)",
    },
    "lorenz": {
        "title": "Lorenz attractor",
        "state_names": ["x", "y", "z"],
        "expressions": ["sigma*(y - x)", "x*(rho - z) - y", "x*y - beta*z"],
        "parameters": {
            "sigma": {"default": 10.0, "min": 0.1, "max": 100.0, "unit": ""},
            "rho": {"default": 28.0, "min": -100.0, "max": 500.0, "unit": ""},
            "beta": {"default": 2.6666666666, "min": 0.01, "max": 100.0, "unit": ""},
        },
        "initial": {"x": {"default": 1.0, "min": -500.0, "max": 500.0, "unit": ""},
                    "y": {"default": 1.0, "min": -500.0, "max": 500.0, "unit": ""},
                    "z": {"default": 1.0, "min": -500.0, "max": 500.0, "unit": ""}},
        "equations": [r"\dot{x} = \sigma (y - x)", r"\dot{y} = x(\rho - z) - y",
                      r"\dot{z} = xy - \beta z"],
        "assumptions": ["idealised 3-mode truncation of Rayleigh-Benard convection",
                        "constant Prandtl/Rayleigh numbers"],
        "limitations": [
            "chaotic: trajectories are exponentially sensitive to tolerances, so "
            "quantitative long-time comparison between solvers is not meaningful",
            "not a fluid simulation of a specific apparatus",
        ],
        "energy": None,
        "linear_period": None,
    },
}


def _build_dynamical(params: dict[str, Any]):
    preset = str(params.get("preset", "nonlinear_pendulum")).lower()
    if preset not in SYSTEM_PRESETS:
        raise NumericalInputError(
            f"unknown preset '{preset}'. Available: {sorted(SYSTEM_PRESETS)}"
        )
    spec = SYSTEM_PRESETS[preset]
    names = spec["state_names"]
    symbols = list(names) + ["t"] + list(spec["parameters"].keys())
    try:
        compiled = [compile_expression(e, symbols) for e in spec["expressions"]]
    except ExpressionError as exc:
        raise NumericalInputError(f"system expression rejected by the safe parser: {exc}") from exc
    resolved: dict[str, float] = {}
    for key, meta in spec["parameters"].items():
        resolved[key] = _num(params, key, meta["default"], meta["min"], meta["max"], meta.get("unit", ""))
    y0 = [
        _num(params, name, meta["default"], meta["min"], meta["max"], meta.get("unit", ""))
        for name, meta in spec["initial"].items()
    ]
    try:
        energy_fn = (
            compile_expression(spec["energy"], list(names) + list(spec["parameters"].keys()))
            if spec["energy"]
            else None
        )
    except ExpressionError as exc:
        raise NumericalInputError(f"energy expression rejected by the safe parser: {exc}") from exc
    t_end = _positive(params, "duration", 10.0, 1e-6, 1e4, " s")
    n_points = int(params.get("n_points", 800))
    if n_points < 10 or n_points > 20000:
        raise NumericalInputError("n_points must be between 10 and 20000")

    def rhs(t, y):
        env_values = [float(v) for v in y] + [float(t)] + [resolved[k] for k in spec["parameters"]]
        return [fn.call(*env_values) for fn in compiled]

    meta = {
        "preset": preset,
        "spec": spec,
        "parameters": resolved,
        "y0": y0,
        "t_end": t_end,
        "n_points": n_points,
        "state_names": names,
        "energy_fn": energy_fn,
        "linear_period": spec["linear_period"],
        "units": {n: spec["initial"][n].get("unit", "") for n in names},
    }
    return rhs, meta


BUILDERS: dict[str, Callable[[dict[str, Any]], tuple[Callable, dict[str, Any]]]] = {
    "projectile_motion": _build_projectile,
    "simple_harmonic_motion": _build_shm,
    "newton_cooling": _build_cooling,
    "dynamical_system": _build_dynamical,
}

REFERENCES: dict[str, Callable[[dict], Callable | None]] = {
    "projectile_motion": _reference_projectile,
    "simple_harmonic_motion": _reference_shm,
    "newton_cooling": _reference_cooling,
}


SIM_SPECS: dict[str, SimSpec] = {
    "projectile_motion": SimSpec(
        key="projectile_motion",
        title="Projectile motion",
        category="classical mechanics",
        equations=[
            r"\ddot{\mathbf{r}} = -g\,\hat{y} - \frac{c_d}{m}\|\mathbf{v}\|\,\mathbf{v}",
            r"R = \frac{v_0^2 \sin 2\theta}{g} \quad (\text{drag}=0,\;h_0=0)",
        ],
        assumptions=[
            "uniform gravitational field, no variation with altitude",
            "flat, non-rotating reference frame (no Coriolis)",
            "drag model (if enabled): quadratic in speed with constant c_d = 0.5*rho*Cd*A",
            "point mass; no lift, spin (Magnus) or ablation forces",
        ],
        limitations=[
            "does not model wind, air-density variation, Earth curvature or rotation",
            "impact is detected on the output grid, so range/flight time are "
            "resolution-limited unless linearly interpolated",
        ],
        parameters={
            "v0": {"default": 40.0, "min": 1e-6, "max": 1e6, "unit": "m/s",
                   "description": "launch speed"},
            "angle_deg": {"default": 45.0, "min": -180.0, "max": 180.0, "unit": "deg",
                          "description": "launch angle above horizontal"},
            "launch_height": {"default": 0.0, "min": 0.0, "max": 1e6, "unit": "m",
                              "description": "initial height"},
            "g": {"default": 9.80665, "min": 0.05, "max": 40.0, "unit": "m/s^2",
                  "description": "gravitational acceleration"},
            "mass": {"default": 1.0, "min": 1e-6, "max": 1e6, "unit": "kg",
                     "description": "projectile mass"},
            "drag_coefficient_total": {"default": 0.0, "min": 0.0, "max": 100.0, "unit": "kg/m",
                                      "description": "0.5*rho*Cd*A; 0 = vacuum"},
            "duration": {"default": None, "derived": True, "min": 1e-6, "max": 1e6, "unit": "s",
                         "description": "integration window; when omitted it is derived from the "
                                        "flight time so the run ends shortly after impact"},
        },
        state_names=["x", "y", "vx", "vy"],
        builder=_build_projectile,
        analytical_validation=True,
    ),
    "simple_harmonic_motion": SimSpec(
        key="simple_harmonic_motion",
        title="Simple harmonic motion (damped)",
        category="oscillations",
        equations=[r"m\ddot{x} = -k x - c\dot{x}", r"\omega_0 = \sqrt{k/m}",
                   r"\zeta = \frac{c}{2\sqrt{km}}"],
        assumptions=[
            "linear Hookean spring, constant k",
            "viscous damping proportional to velocity (c >= 0)",
            "massless spring, point mass, motion confined to one dimension",
        ],
        limitations=[
            "no spring mass, no dry (Coulomb) friction, no driving term, "
            "no geometric nonlinearity of the spring",
        ],
        parameters={
            "mass": {"default": 1.0, "min": 1e-6, "max": 1e6, "unit": "kg"},
            "spring_constant": {"default": 10.0, "min": 1e-6, "max": 1e9, "unit": "N/m"},
            "damping": {"default": 0.0, "min": 0.0, "max": 1e6, "unit": "N*s/m"},
            "x0": {"default": 1.0, "min": -1e6, "max": 1e6, "unit": "m"},
            "v0": {"default": 0.0, "min": -1e6, "max": 1e6, "unit": "m/s"},
            "duration": {"default": None, "derived": True, "min": 1e-6, "max": 1e6, "unit": "s",
                         "description": "integration window; when omitted it is derived as ten "
                                        "natural periods"},
        },
        state_names=["x", "v"],
        builder=_build_shm,
        analytical_validation=True,
    ),
    "newton_cooling": SimSpec(
        key="newton_cooling",
        title="Newton cooling",
        category="thermodynamics",
        equations=[r"\frac{dT}{dt} = -k\,(T - T_{\mathrm{env}})",
                   r"T(t) = T_{\mathrm{env}} + (T_0 - T_{\mathrm{env}}) e^{-kt}"],
        assumptions=[
            "lumped capacitance: uniform body temperature (Bi << 1)",
            "constant ambient temperature and constant heat-transfer coefficient",
            "radiation neglected or linearised into k",
        ],
        limitations=[
            "not valid for bodies with large internal gradients (Bi >~ 0.1)",
            "does not include evaporative or radiative T^4 terms",
        ],
        parameters={
            "T0": {"default": 90.0, "min": -1000, "max": 5000, "unit": "K"},
            "T_env": {"default": 22.0, "min": -1000, "max": 5000, "unit": "K"},
            "cooling_rate": {"default": 0.05, "min": 1e-6, "max": 1e3, "unit": "1/s"},
            "duration": {"default": 120.0, "min": 1e-3, "max": 1e6, "unit": "s"},
        },
        state_names=["T"],
        builder=_build_cooling,
        analytical_validation=True,
    ),
    "dynamical_system": SimSpec(
        key="dynamical_system",
        title="Coupled dynamical system (preset)",
        category="dynamics / chaos",
        equations=SYSTEM_PRESETS["nonlinear_pendulum"]["equations"],
        assumptions=[
            "RHS built from the restricted expression parser (no arbitrary code)",
            "continuous, single-valued vector field over the integration window",
        ],
        limitations=[
            "chaotic presets (e.g. Lorenz) are qualitatively - not quantitatively - reproducible",
            "no event detection: impacts/switches must be handled by the user",
        ],
        parameters={
            # Every key the builder honours is declared here, for both presets: the
            # catalogue is the contract the API validates against, so an undeclared
            # parameter would be rejected as unknown instead of silently ignored.
            "preset": {
                "default": "nonlinear_pendulum", "unit": "", "enum": sorted(SYSTEM_PRESETS),
                "description": "which coupled system to integrate",
            },
            **{
                **{k: {**v, "description": f"{v.get('description', '')} (nonlinear_pendulum)".strip()}
                   for k, v in SYSTEM_PRESETS["nonlinear_pendulum"]["parameters"].items()},
                **{k: {**v, "description": f"{v.get('description', '')} (lorenz)".strip()}
                   for k, v in SYSTEM_PRESETS["lorenz"]["parameters"].items()},
                **{k: {**v, "description": f"initial state {k} (nonlinear_pendulum)"}
                   for k, v in SYSTEM_PRESETS["nonlinear_pendulum"]["initial"].items()},
                **{k: {**v, "description": f"initial state {k} (lorenz)"}
                   for k, v in SYSTEM_PRESETS["lorenz"]["initial"].items()},
            },
            "duration": {"default": 10.0, "min": 1e-6, "max": 1e4, "unit": "s",
                         "description": "integration window"},
            "n_points": {"default": 800, "min": 10, "max": 20000, "unit": "",
                         "description": "output samples (the solver's internal steps are adaptive)"},
        },
        state_names=["theta", "omega"],
        builder=_build_dynamical,
        analytical_validation=False,
    ),
    "heat_1d": HEAT_SPEC,
}


def _attach_model_metadata(spec: SimSpec, payload: dict[str, Any]) -> None:
    """Give every simulation the same model/assumptions block.

    The report builder and the UI read ``model.assumptions`` etc. for all five
    simulations, so a payload that omits them would silently lose its limitations
    section instead of showing them.
    """
    model = payload.setdefault("model", {})
    model.setdefault("assumptions", list(spec.assumptions))
    model.setdefault("limitations", list(spec.limitations))
    model.setdefault("governing_equations", list(spec.equations))
    model.setdefault("title", spec.title)
    payload.setdefault("units", model.get("units") or {})
    payload.setdefault("metadata", payload.get("metadata") or {})


# Parameters the dispatcher itself consumes, plus the aliases users naturally write.
# Anything outside {declared spec parameters} ∪ this set is refused rather than ignored:
# a silently dropped "drag" would report a drag-free trajectory as if it were the ask.
_SIM_PLUMBING = ("solver", "rtol", "atol", "n_points", "duration")
_SIM_ALIASES: dict[str, dict[str, str]] = {
    "projectile_motion": {
        "speed": "v0",
        "angle": "angle_deg",
        "launch height": "launch_height",
        "height": "launch_height",
        "quadratic_drag": "drag_coefficient_total",
    },
    "simple_harmonic_motion": {"amplitude": "x0", "stiffness": "spring_constant"},
    "newton_cooling": {
        "k": "cooling_rate",
        "initial_temperature": "T0",
        "ambient_temperature": "T_env",
        "room_temperature": "T_env",
    },
    "dynamical_system": {
        "length": "L",
        "gravity": "g",
        "damping": "b",
        # initial states are conventionally written with a 0 subscript
        "theta0": "theta",
        "omega0": "omega",
        "x0": "x",
        "y0": "y",
        "z0": "z",
    },
    "heat_1d": {"diffusivity": "alpha", "points": "grid_points", "steps": "time_steps"},
}


def _normalise_parameters(key: str, spec, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Fold documented aliases to their canonical names and refuse unknown keys."""
    accepted = set(spec.parameters or {}) | set(_SIM_PLUMBING)
    aliases = _SIM_ALIASES.get(key, {})
    out: dict[str, Any] = {}
    applied: dict[str, str] = {}
    for name, value in params.items():
        canonical = aliases.get(str(name).lower().replace(" ", "_"), str(name))
        if canonical not in accepted:
            raise NumericalInputError(
                f"unknown parameter(s) '{name}' for '{key}'; it accepts: "
                f"{', '.join(sorted(set(spec.parameters or {})))}"
                + (f" (aliases: {', '.join(sorted(aliases))})" if aliases else "")
            )
        out[canonical] = value
        if canonical != str(name):
            applied[str(name)] = canonical
    return out, applied


def run_simulation(key: str, params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a registered simulation and return a plot-ready result payload."""
    key = str(key)
    if key not in SIM_SPECS:
        raise NumericalInputError(
            f"simulation '{key}' is not registered. Available: {sorted(SIM_SPECS)}"
        )
    spec = SIM_SPECS[key]
    params, applied_aliases = _normalise_parameters(key, spec, dict(params or {}))
    if key == "heat_1d":
        payload = simulate_heat_1d(params)
        _attach_model_metadata(spec, payload)
        if applied_aliases:
            payload.setdefault("warnings", []).append(
                "parameter name(s) folded to the documented ones: "
                + ", ".join(f"{a} → {b}" for a, b in sorted(applied_aliases.items()))
            )
        return payload
    rhs, meta = spec.builder(params)
    if "duration" in params:
        # an explicit window is honoured instead of the derived one, and recorded so the
        # reader can tell "stopped at impact" from "stopped at t = 12 s"
        meta["t_end"] = _positive(params, "duration", float(meta["t_end"]), 1e-6, 1e6, " s")
        meta["duration_override"] = True
    method = str(params.get("solver", "DOP853")).upper()
    rtol = float(params.get("rtol", 1e-9))
    atol = float(params.get("atol", 1e-12))
    n_points = int(params.get("n_points", 600))
    if n_points < 20 or n_points > 20000:
        raise NumericalInputError("n_points must be between 20 and 20000")
    reference = REFERENCES[key](meta) if key in REFERENCES else None

    result = integrate_ode(
        rhs,
        (0.0, meta["t_end"]),
        meta["y0"],
        method=method,
        rtol=rtol,
        atol=atol,
        n_points=n_points,
        reference=reference,
        state_names=meta["state_names"],
    )
    payload = result.to_dict(max_iterations=0)
    if applied_aliases:
        # a renamed parameter is still a substitution the reader is entitled to see
        payload["parameter_aliases"] = applied_aliases
        payload.setdefault("warnings", []).append(
            "parameter name(s) folded to the documented ones: "
            + ", ".join(f"{a} → {b}" for a, b in sorted(applied_aliases.items()))
        )
    series = result.result["series"]
    times = result.result["time"]
    payload["simulation"] = key
    payload["series"] = {"time": times, **series}
    payload["units"] = {"time": "s", **meta.get("units", {})}
    payload["summary"] = _summarize(key, times, series, meta)
    payload["model"] = {
        # meta carries scalars directly for some builders and nested under
        # "parameters" for the preset-driven dynamical systems; both belong in the
        # published model so the report's parameter table is complete
        "parameters": {
            **{k: v for k, v in meta.items() if isinstance(v, (int, float))},
            **{k: v for k, v in (meta.get("parameters") or {}).items()},
        },
        "preset": meta.get("preset"),
        "integration_window": {
            "t_end": float(meta["t_end"]),
            "source": "explicit 'duration' parameter" if meta.get("duration_override")
            else "derived from the model's own timescale",
        },
        "initial_state": dict(zip(meta["state_names"], meta["y0"])),
        "assumptions": spec.assumptions,
        "limitations": spec.limitations,
        "governing_equations": spec.equations,
    }
    payload["validation"] = _validate(key, times, series, meta, reference, result, params)
    payload["solver"] = {
        "scheme": result.metadata.get("solver"),
        "description": result.metadata.get("solver_description"),
        "rtol": result.metadata.get("rtol"),
        "atol": result.metadata.get("atol"),
        "steps": result.metadata.get("steps"),
        "function_evaluations": result.metadata.get("nfev"),
    }
    payload["message"] = result.message
    payload["execution_time_ms"] = result.execution_time_ms
    if reference is None and key != "dynamical_system":
        payload["warnings"].append(
            "no analytical reference was applied for this parameter set "
            "(model terms are active that the closed form excludes)"
        )
    payload["grid"] = {"x": [], "dx": None, "points": len(times)}
    _attach_model_metadata(spec, payload)
    return payload


def _summarize(key: str, times: list[float], series: dict[str, list[float]], meta) -> dict[str, Any]:
    t = np.asarray(times, dtype=float)
    out: dict[str, Any] = {"duration": float(t[-1] - t[0]) if t.size else 0.0,
                           "output_points": len(times)}
    arrs = {k: np.asarray(v, dtype=float) for k, v in series.items()}
    if key == "projectile_motion":
        x, y, vx, vy = arrs["x"], arrs["y"], arrs["vx"], arrs["vy"]
        # first node at or below the launch height, strictly after launch
        landed = np.where((y <= meta["launch_height"] + 1e-12) & (t > t[0]))[0]
        if landed.size:
            i = int(landed[0])
            if i > 0:  # linear interpolation of the crossing for resolution-limited estimates
                y0, y1 = y[i - 1], y[i]
                frac = 1.0 if abs(y1 - y0) < 1e-300 else float((y0 - meta["launch_height"]) / (y0 - y1))
                frac = min(max(frac, 0.0), 1.0)
                t_impact = float(t[i - 1] + frac * (t[i] - t[i - 1]))
                x_impact = float(x[i - 1] + frac * (x[i] - x[i - 1]))
                vx_impact = float(vx[i - 1] + frac * (vx[i] - vx[i - 1]))
                vy_impact = float(vy[i - 1] + frac * (vy[i] - vy[i - 1]))
            else:
                t_impact, x_impact = float(t[0]), float(x[0])
                vx_impact, vy_impact = float(vx[0]), float(vy[0])
            impact_found = True
        else:
            i = len(y) - 1
            t_impact, x_impact = float(t[-1]), float(x[-1])
            vx_impact, vy_impact = float(vx[-1]), float(vy[-1])
            impact_found = False
        out["max_height"] = float(np.max(y) - meta["launch_height"])
        out["range_at_first_impact"] = float(x_impact)
        out["flight_time_to_impact"] = t_impact
        out["impact_speed"] = float(math.hypot(vx_impact, vy_impact))
        out["impact_detected"] = impact_found
        g, v0, ang = meta["g"], meta["v0"], math.radians(meta["angle_deg"])
        if meta["drag"] == 0 and meta["launch_height"] == 0:
            out["analytical_range"] = v0**2 * math.sin(2 * ang) / g
            out["analytical_max_height"] = (v0 * math.sin(ang)) ** 2 / (2 * g)
            out["analytical_flight_time"] = 2 * v0 * math.sin(ang) / g
            out["range_relative_error"] = (
                abs(out["analytical_range"] - out["range_at_first_impact"]) / out["analytical_range"]
                if out["analytical_range"]
                else None
            )
    elif key == "simple_harmonic_motion":
        x, v = arrs["x"], arrs["v"]
        out["max_amplitude"] = float(np.max(np.abs(x)))
        out["final_amplitude"] = float(abs(x[-1]))
        out["natural_frequency_rad_s"] = meta["omega0"]
        out["period_s"] = meta["period"]
        out["damping_ratio"] = meta["zeta"]
        out["regime"] = meta["regime"]
        m, k, c = meta["mass"], meta["k"], meta["c"]
        energy = 0.5 * m * v**2 + 0.5 * k * x**2
        out["energy_initial"] = float(energy[0])
        out["energy_final"] = float(energy[-1])
        out["energy_relative_drift"] = (
            float(abs(energy[-1] - energy[0]) / abs(energy[0])) if abs(energy[0]) > 1e-15 else None
        )
    elif key == "newton_cooling":
        T = arrs["T"]
        out["final_temperature"] = float(T[-1])
        out["time_constant_s"] = meta["time_constant"]
        out["half_life_s"] = meta["half_life"]
        out["temperature_to_1_percent"] = (
            float(T[-1] - meta["T_env"]) / (meta["T0"] - meta["T_env"])
            if meta["T0"] != meta["T_env"]
            else None
        )
    elif key == "dynamical_system":
        for name, arr in arrs.items():
            out[f"{name}_min"] = float(np.min(arr))
            out[f"{name}_max"] = float(np.max(arr))
        if meta.get("linear_period"):
            # the measured value comes from zero crossings of the integrated trajectory,
            # so it is the *actual* period (larger than the linear prediction at big
            # amplitudes); both are published so the difference is visible, not implied
            out["measured_period_s"] = _eval_zero_crossing_period(t, arrs.get("theta"))
            out["linear_period_formula"] = meta["linear_period"]
            g = (meta.get("parameters") or {}).get("g")
            length = (meta.get("parameters") or {}).get("L")
            if g and length:
                out["linear_period_s"] = 2 * math.pi / math.sqrt(g / length)
    return out


def _eval_zero_crossing_period(t: np.ndarray, theta: np.ndarray | None) -> float | None:
    if theta is None:
        return None
    sign = np.sign(theta)
    crossings = np.where(np.diff(sign) > 0)[0]
    if crossings.size < 2:
        return None
    return float(np.mean(np.diff(t[crossings])))


def _validate(key, times, series, meta, reference, result, params) -> dict[str, Any]:
    """Independent checks: analytical comparison, invariants, self-consistency."""
    out: dict[str, Any] = {"checks": [], "overall": "not_available", "notes": []}
    if reference is not None:
        t = np.asarray(times, dtype=float)
        max_abs = 0.0
        scale = 0.0
        for i, time in enumerate(t):
            ref = reference(float(time))
            got = [series[n][i] for n in meta["state_names"]]
            diff = np.abs(np.asarray(ref) - np.asarray(got))
            max_abs = max(max_abs, float(np.max(diff)))
            scale = max(scale, float(np.max(np.abs(ref))))
        rel = max_abs / max(scale, 1e-12)
        out["checks"].append(
            {
                "name": "analytical_solution_comparison",
                "reference": "closed-form model solution for the same ODE",
                "max_absolute_error": max_abs,
                "max_relative_error": rel,
                "tolerance": max(float(params.get("rtol", 1e-9)) ** 0.5, 1e-6),
                "passed": bool(rel <= max(math.sqrt(float(params.get("rtol", 1e-9))), 1e-6)),
            }
        )
    if key == "simple_harmonic_motion" and meta["c"] == 0:
        m, k = meta["mass"], meta["k"]
        x = np.asarray(series["x"], dtype=float)
        v = np.asarray(series["v"], dtype=float)
        e = 0.5 * m * v**2 + 0.5 * k * x**2
        drift = float(np.max(np.abs(e - e[0])) / max(abs(e[0]), 1e-30))
        out["checks"].append(
            {
                "name": "energy_conservation",
                "reference": "E = 1/2 m v^2 + 1/2 k x^2 must be constant when c = 0",
                "max_relative_drift": drift,
                "tolerance": 1e-6,
                "passed": bool(drift < 1e-6),
            }
        )
    if key == "dynamical_system" and meta.get("energy_fn"):
        fn = meta["energy_fn"]
        names = meta["state_names"]
        pnames = list(meta["spec"]["parameters"].keys())
        vals = np.asarray(
            [
                fn.call(
                    *[series[n][i] for n in names],
                    *[meta["parameters"][name] for name in pnames],
                )
                for i in range(len(times))
            ]
        )
        drift = float(np.max(np.abs(vals - vals[0])) / max(abs(vals[0]), 1e-30))
        out["checks"].append(
            {
                "name": "invariant_energy_drift",
                "reference": meta["spec"]["energy"],
                "max_relative_drift": drift,
                "tolerance": 1e-6,
                "passed": bool(drift < 1e-6),
            }
        )
    if key == "projectile_motion" and meta["drag"] == 0:
        vx = np.asarray(series["vx"], dtype=float)
        out["checks"].append(
            {
                "name": "horizontal_velocity_constant",
                "reference": "vx must be invariant when drag = 0",
                "max_deviation": float(np.max(np.abs(vx - vx[0]))),
                "tolerance": 1e-8 * max(1.0, abs(vx[0])),
                "passed": bool(np.max(np.abs(vx - vx[0])) <= 1e-8 * max(1.0, abs(vx[0]))),
            }
        )
    if out["checks"]:
        out["overall"] = "passed" if all(c["passed"] for c in out["checks"]) else "failed"
    else:
        out["notes"].append(
            "no analytical reference exists for this parameter set; results are numerically "
            "consistent but not independently validated"
        )
        out["overall"] = "not_available"
    return out


def simulation_catalog() -> list[dict[str, Any]]:
    catalog = []
    for key, spec in SIM_SPECS.items():
        d = spec.describe()
        if key == "dynamical_system":
            d["presets"] = {
                name: {
                    "title": p["title"],
                    "governing_equations": p["equations"],
                    "parameters": p["parameters"],
                    "initial_conditions": p["initial"],
                    "state_variables": p["state_names"],
                    "assumptions": p["assumptions"],
                    "limitations": p["limitations"],
                }
                for name, p in SYSTEM_PRESETS.items()
            }
        catalog.append(d)
    return catalog

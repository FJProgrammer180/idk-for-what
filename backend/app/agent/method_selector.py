"""Scientific method selection.

Given a natural-language task, classify it, pick a registered method, check the
method's preconditions and report what is missing.  Used by the deterministic
mock planner, by the `/agent/recommend-method` endpoint and as guard-rails for
real provider calls.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.numerical.engine import METHODS

EXPRESSION_HINT = re.compile(r"(sin|cos|tan|exp|log|sqrt|abs|atan|sinh|cosh|tanh|\^|\*|/|pi)")

CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("report_generation", r"\breport\b|\bwrite up\b|\bdocument the\b|\bdraft\b"),
    ("verification", r"\bverify\b|\bvalidat\w*\b|\bcheck\b|\bconfirm\b|\bcross[- ]check\b"),
    ("comparison", r"\bcompare\b|\bv(?:s|ersus)\b|\bwhich (?:method|faster|better)\b"),
    ("data_analysis", r"\bdataset\b|\bcsv\b|\bregression\b|\bfit\b|\bcorrelat\w*\b|\bresidual\w*\b|\bmeasur\w+ data\b"),
    ("simulation", r"\bsimulat\w*\b|\bprojectile\b|\bpendulum\b|\bheat (?:equation|diffusion)\b|"
                   r"\bcool\w*\b|\bnewton'?s? law (?:of cooling)?\b|\boscillat\w*\b|\bspring\b|"
                   r"\blorenz\b|\battractor\b|\btrajectory\b|\bflight\b|\b\d+(?:\.\d+)?\s*(?:m\s*/\s*s)\b"),
    ("integration", r"\bintegrat\w*\b|\bintegral\w*\b|\barea under\b|\bquadrature\b|"
                    r"\btrapez\w*\b|\bsimpson\b|\\int\b"),
    ("derivative", r"\bderivativ\w*\b|\bdifferentiat\w*\b|\bslope at\b|\brates? of change\b"),
    ("linear_system", r"\blinear system\b|\bmatrix\b|\bsolve the system\b|\bax ?= ?b\b|\bgauss\b|\blu\b|\bjacobi\b|\bseidel\b"),
    ("root_finding", r"\broot\b|\bzero(es)?\b|\bsolve\b|\bbisection\b|\bnewton\b|\bsecant\b|\bwhere .{0,20}= ?0\b"),
    ("research", r"\bliterature\b|\bsources?\b|\bcitat\w+\b|\bpapers?\b|\breferences?\b|\bsearch\b"),
]

META_CATEGORIES = {"report_generation", "verification", "comparison", "research"}

METHOD_KEYWORDS: dict[str, re.Pattern[str]] = {
    "bisection": r"\bbisection\b|\bbisect\b|\binterval halving\b",
    "newton_raphson": r"\bnewton[- ]raphson\b|\bnewton's method\b|\bnewton method\b",
    "secant": r"\bsecant\b",
    "gaussian_elimination": r"\bgauss(?:ian)? elimination\b|\bdirect solve\b",
    "lu_decomposition": r"\blu\b|\bfactori[sz]ation\b",
    "jacobi": r"\bjacobi\b",
    "gauss_seidel": r"\bgauss[- ]seidel\b|\bsor\b",
    "trapezoidal": r"\btrapez\w*\b",
    "simpson": r"\bsimpson\b",
    "finite_difference": r"\bfinite difference\w*\b|\bnumerical derivative\b",
    "least_squares": r"\bleast[- ]squares?\b|\blinear fit\b|\bcurve fit\b|\bregression\b",
    "ode_ivp": r"\bode\b|\binitial value problem\b|\bsolve_ivp\b|\brunge[- ]kutta\b|\bdifferential equation\b",
}

PROBLEM_TO_METHODS: dict[str, list[str]] = {
    "root_finding": ["bisection", "newton_raphson", "secant"],
    "linear_system": ["gaussian_elimination", "lu_decomposition", "gauss_seidel", "jacobi"],
    "integration": ["simpson", "trapezoidal"],
    "derivative": ["finite_difference"],
    "regression": ["least_squares"],
    "ode_ivp": ["ode_ivp"],
}


@dataclass
class Selection:
    category: str
    problem_type: str | None
    method: str | None
    rationale: str
    requirements: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extracted: dict[str, Any] = field(default_factory=dict)
    assumptions: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "problem_type": self.problem_type,
            "method": self.method,
            "rationale": self.rationale,
            "requirements": self.requirements,
            "missing_inputs": self.missing,
            "extracted_parameters": self.extracted,
            "assumptions": self.assumptions,
            "notes": self.notes,
            "ready": not self.missing,
        }


# --- extraction helpers ------------------------------------------------------

NUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
CONSTANT_WORDS = {
    "pi": math.pi, "π": math.pi, "tau": math.tau, "e": math.e,
    "inf": math.inf, "infinity": math.inf,
}


def _num_token(text: str) -> float | None:
    text = text.strip().lower()
    if text in CONSTANT_WORDS:
        return CONSTANT_WORDS[text]
    try:
        return float(text)
    except ValueError:
        return None


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for match in re.finditer(NUM, text):
        try:
            out.append(float(match.group()))
        except ValueError:
            continue
    return out


def extract_expression(text: str) -> str | None:
    """Pull a math expression out of the task text (never evaluates anything)."""
    text = re.sub(
        rf"\s+(?:at|where|near)\s+(?:x\s*=?\s*|t\s*=?\s*)?{NUM}\s*$", "", text.strip(), flags=re.IGNORECASE
    )
    # an explicit function definition wins over the generic "A = B" root form
    fn = re.search(r"f\s*\(\s*x\s*\)\s*=\s*(?P<e>.+?)(?=\s+(?:between|on|over|for|with|using|to|at|near|and)\b|\s*$)",
                   text, re.IGNORECASE)
    if fn:
        cand = fn.group("e").strip().strip(".,;:")
        if cand:
            return cand

    # "find where cos(x) = x" / "solve f = g" -> f - (g), the honest root form
    eq = re.search(
        r"(?:find where|find all|where|solve|root of)\s+(?P<lhs>[^=]+?)\s*=\s*(?P<rhs>[^,.;=]+?)"
        r"(?:\s+(?:between|on|over|for|with|using|in|to)\b|\s*[,.]\s*|\s*$)",
        text,
        re.IGNORECASE,
    )
    if eq:
        lhs = eq.group("lhs").strip().rstrip(",.")
        rhs = eq.group("rhs").strip().rstrip(",.")
        if lhs and rhs:
            candidate = lhs if rhs in {"0", "0.0"} else f"({lhs}) - ({rhs})"
            candidate = _strip_filler(candidate).strip(" .,;:")
            if looks_like_expression(candidate):
                return candidate
    patterns = [
        r"f\s*\(\s*x\s*\)\s*=\s*(?P<e>[^,.;]+?)(?=\s+(?:between|on|over|has|with|for|=)|\s*[,.;]\s|\s*$)",
        r"equation\s+(?P<e>[^,.;=]+?)\s*=\s*0",
        r"(?:integrate|integral of)\s+(?P<e>.+?)\s+(?:from|over|between)\s+",
        r"(?:d/?d\s*x\s*(?:of\s*)?|derivative of\s+|differentiat\w*\s+(?:the\s+)?(?:function\s+)?)(?P<e>.+?)(?:\s+at\s+|\s*,|\s*$)",
        r"(?:solve|find the root(?:s)? of|zero of)\s+(?P<e>[^,.;=]+?)"
        r"(?:\s*=\s*0|\s+using|\s+between|\s+on\b|\s+over\b|\s+with\b|\s+to\b|\s+and\b"
        r"|\s+numerically\b|\s+analytically\b|\s*,|\s*$)",
        r"(?P<e>[^,.;:]{2,80}(?:\^|\*|/|\bsin\b|\bcos\b|\bexp\b|\blog\b|\bsqrt\b)[^,.;:]*?)\s*"
        r"(?=(?:,|\.|;|\s+at\b|\s+near\b|\s+using\b|\s+with\b|\s+between\b|\s+from\b|\s+to\b|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = match.group("e")
        candidate = re.sub(
            r"\s+(?:numerically|analytically|exactly|approximately|precisely|iteratively|"
            r"to\s+high\s+accuracy|by\s+hand)\s*$",
            "",
            candidate.strip(),
            flags=re.IGNORECASE,
        )
        candidate = _strip_filler(candidate)
        if looks_like_expression(candidate):
            return candidate
    return None


def looks_like_expression(candidate: str) -> bool:
    if not candidate or len(candidate) > 200:
        return False
    if not EXPRESSION_HINT.search(candidate) and "x" not in candidate:
        return False
    for bad in ("=", ";", "{", "}", "[", "]", "__", "'", '"', "\\", "$", "(", ")"):
        pass
    if any(bad in candidate for bad in (";", "{", "}", "[", "]", "__", "'", '"', "\\", "$")):
        return False
    return True


_FILLER_PREFIXES = re.compile(
    r"^(?:(?:please|can you|could you|i need|we need|help me|now|also|then)[,\s]+)+"
    r"|^(?:use|using|apply|with|try)\s+(?:the\s+)?(?:method\s+)?[a-z_]+\s*(?:to|on|for|,)?\s*"
    r"|^(?:numerical(?:ly)?|compute|calculate|evaluate|determine|estimate|find|solve|get)\s+"
    r"|^(?:the\s+)?(?:root|zero)\s+(?:of|for)\s+|^(?:the\s+)?(?:function|expression|integral|derivative)\s+(?:of|is)?\s*",
    re.IGNORECASE,
)


def _strip_filler(expr: str) -> str:
    """Remove prose wrappers so 'Use bisection on x^2 - 3' becomes 'x^2 - 3'."""
    out = expr.strip()
    for _ in range(4):
        stripped = _FILLER_PREFIXES.sub("", out, count=1).strip()
        stripped = re.sub(r"^(?:of|for|the)\s+", "", stripped).strip()
        if stripped == out or not stripped:
            break
        out = stripped
    return out


def extract_interval(text: str) -> tuple[float, float] | None:
    scalar = rf"({NUM}|pi|\u03c0|tau|inf)"
    m = re.search(rf"\[\s*{scalar}\s*[,;]\s*{scalar}\s*\]", text, re.IGNORECASE)
    if m:
        pair = (_num_token(m.group(1)), _num_token(m.group(2)))
        if all(v is not None for v in pair):
            return pair  # type: ignore[return-value]
    m = re.search(
        rf"(?:between|from|over|on|in)\s+{scalar}\s+(?:and|to|-|through)\s+{scalar}",
        text,
        re.IGNORECASE,
    )
    if m:
        pair = (_num_token(m.group(1)), _num_token(m.group(2)))
        if all(v is not None for v in pair):
            return pair  # type: ignore[return-value]
    m = re.search(rf"[-+]?\d*\.?\d+\s*(?:<|<|<=)\s*x\s*(?:<|<=)\s*{NUM}", text)
    if m:
        nums = _numbers(m.group())
        if len(nums) == 2:
            return nums[0], nums[1]
    return None


def extract_point(text: str) -> float | None:
    m = re.search(
        rf"(?:at|near|start(?:ing)? (?:at|from|guess)|initial(?: guess)?(?: at)?)\s+"
        rf"(?:x\s*)?(?:=|is|of)?\s*(-?{NUM})",
        text,
        re.IGNORECASE,
    )
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def extract_tolerance(text: str) -> float | None:
    m = re.search(rf"tol(?:erance)?\s*(?:of|=|:)?\s*({NUM})", text, re.IGNORECASE)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    return value if 0 < value <= 1e-1 else None


def extract_max_iterations(text: str) -> int | None:
    m = re.search(r"max(?:imum)? (?:number of )?(?:iterations|iters)\s*(?:of|=|:)?\s*(\d+)", text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d{1,4})\s+iterations", text, re.IGNORECASE)
    if not m:
        return None
    value = int(m.group(1))
    return value if 1 <= value <= 5000 else None


def extract_kv_params(text: str) -> dict[str, float]:
    """`key=value` / `key of value` / `value unit key` triples for simulations."""
    out: dict[str, float] = {}
    for match in re.finditer(rf"\b([A-Za-z][A-Za-z_0-9]*)\s*(?:=|:)\s*({NUM})", text):
        key = match.group(1)
        try:
            out[key] = float(match.group(2))
        except ValueError:
            continue
    for phrase, key in (
        # unit-suffixed wording is the common case ("at 60 degrees", "30 m/s"), and
        # missing it would silently run the model at its default instead of the ask
        (rf"speed of ({NUM})\s*(?:m\s*/\s*s)?", "v0"),
        (rf"velocity of ({NUM})\s*(?:m\s*/\s*s)?", "v0"),
        (rf"(?:launch |initial )?speed\s*(?:of|at|is|=|:)?\s*({NUM})\s*(?:m\s*/\s*s|meters? per second|metres? per second)?", "v0"),
        (rf"\bv0\s*(?:of|at|is|=|:)?\s*({NUM})\s*(?:m\s*/\s*s)?", "v0"),
        (rf"(?:at|with)\s+({NUM})\s*m\s*/\s*s", "v0"),
        (rf"angle of ({NUM})\s*(?:degrees?|deg|°)?", "angle_deg"),
        (rf"angle\s*(?:of|at|is|=|:)?\s*({NUM})\s*(?:degrees?|deg|°)", "angle_deg"),
        (rf"({NUM})\s*(?:degrees?|deg|°)\s*(?:from the horizontal|above the horizontal|incline)?", "angle_deg"),
        (rf"mass of ({NUM})\s*(?:kg)?", "mass"),
        (rf"spring constant of ({NUM})", "spring_constant"),
        (rf"height of ({NUM})\s*(?:m\b|met)", "launch_height"),
        (rf"duration of ({NUM})\s*(?:s\b|seconds?)", "duration"),
        (rf"temperature of ({NUM})", "T0"),
    ):
        m = re.search(phrase, text, re.IGNORECASE)
        if m and key not in out:
            try:
                out[key] = float(m.group(1))
            except ValueError:
                continue
    for identifier, key in (
        (r"\bC_d\b|\bdrag coefficient\b|\bdrag\b", "drag_coefficient_total"),
        (r"\bdamping\b", "damping"),
    ):
        if re.search(identifier, text, re.IGNORECASE):
            tail = re.search(rf"{identifier}\s*(?:=|:|of)?\s*({NUM})", text, re.IGNORECASE)
            if tail and key not in out:
                try:
                    out[key] = float(tail.group(1))
                except ValueError:
                    pass
    return out


def extract_identifier(text: str, prefix: str) -> str | None:
    m = re.search(rf"\b{prefix}_[0-9a-f]{{6,40}}\b", text)
    return m.group() if m else None


def extract_dataset_column(text: str, kind: str) -> str | None:
    m = re.search(rf"\b{kind}\s*(?:column)?\s*(?:=|:|is|as)?\s*([A-Za-z][\w\s]*?)\b", text, re.IGNORECASE)
    if not m:
        return None
    value = m.group(1).strip()
    return value if 1 <= len(value) <= 40 else None


# --- selection ---------------------------------------------------------------


def select_method(task: str, hint_method: str | None = None) -> Selection:
    text = " ".join(task.split())
    lowered = text.lower()

    # A task can mention both a computation and a follow-up meta-action
    # ("solve ... then verify and report").  Computation intent wins whenever the
    # request does not point at an already-stored record, because verification /
    # reporting of a *fresh* result is handled by extending the plan, not by
    # replacing the calculation.
    stored_id = re.search(r"\b(?:exp|run|dat|ses)_[0-9a-f]{6,40}\b", text) is not None
    compute_category: str | None = None
    meta_category: str | None = None
    for name, pattern in CATEGORY_PATTERNS:
        if not re.search(pattern, lowered):
            continue
        if name in META_CATEGORIES:
            meta_category = meta_category or name
        else:
            compute_category = compute_category or name
    if meta_category and (stored_id or not compute_category):
        category = meta_category
    elif compute_category:
        category = compute_category
    else:
        category = meta_category or "unclassified"

    explicit = None
    for method, pattern in METHOD_KEYWORDS.items():
        if re.search(pattern, lowered):
            explicit = method
            break
    if hint_method and hint_method in METHODS:
        explicit = hint_method

    if category in ("simulation",):
        return _select_simulation(lowered)
    if category == "data_analysis":
        return _select_analysis(lowered)
    if category == "comparison":
        ids = re.findall(r"\bexp_[0-9a-f]{6,40}\b", text)
        missing = [] if len(ids) >= 2 else ["experiment_ids (at least two)"]
        return Selection(
            category="comparison",
            problem_type="comparison",
            method="compare_experiments",
            rationale="The task compares stored experiments, which is a persistence-level "
                      "operation rather than a new computation.",
            requirements=["two or more experiment ids owned by the authenticated user"],
            missing=missing,
            extracted={"experiment_ids": ids},
            notes=["comparison is limited to runs that share a kind and problem type"],
        )
    if category == "report_generation":
        exp_id = extract_identifier(text, "exp")
        run_id = extract_identifier(text, "run")
        return Selection(
            category="report_generation",
            problem_type="report",
            method="generate_research_report",
            rationale="Reports must be derived from persisted experiment records, so a "
                      "stored experiment or run id is required.",
            requirements=["experiment_id or run_id belonging to the authenticated user"],
            missing=[] if (exp_id or run_id) else ["experiment_id or run_id"],
            extracted={"experiment_id": exp_id, "run_id": run_id},
        )
    if category == "research":
        return Selection(
            category="research",
            problem_type="research",
            method="search_scientific_sources",
            rationale="The task asks for external literature. Retrieval is only performed "
                      "through the configured integration; otherwise this is reported honestly.",
            requirements=["a configured retrieval integration for live sources"],
            missing=[],
            extracted={"query": text[:300]},
        )
    if category == "verification":
        run_id = extract_identifier(text, "run")
        return Selection(
            category="verification",
            problem_type="verification",
            method="verify_result",
            rationale="Verification re-checks stored output with independent algorithms; "
                      "it needs the run to exist.",
            requirements=["run_id of a stored run"],
            missing=[] if run_id else ["run_id or result handle"],
            extracted={"run_id": run_id},
        )

    problem_type = {
        "integration": "integration",
        "derivative": "derivative",
        "linear_system": "linear_system",
        "root_finding": "root_finding",
    }.get(category)
    if problem_type is None and explicit in PROBLEM_TO_METHODS:
        problem_type = next(pt for pt, names in PROBLEM_TO_METHODS.items() if explicit in names)
        category = problem_type
    if problem_type is None:
        if extract_expression(text) and extract_interval(text):
            problem_type = "root_finding"
            category = "root_finding"
            notes = ["unclassified request; a bracketed expression implied root finding"]
        else:
            return Selection(
                category="unclassified",
                problem_type=None,
                method=None,
                rationale="The task does not clearly state a calculation, simulation, dataset "
                          "analysis, comparison, report or research request.",
                requirements=["a more specific task statement"],
                missing=["task classification"],
                notes=["the agent must ask for clarification rather than guess a problem type"],
            )
    else:
        notes = []

    expression = extract_expression(text)
    interval = extract_interval(text)
    tolerance = extract_tolerance(text)
    max_iterations = extract_max_iterations(text)
    point = extract_point(text)

    candidates = PROBLEM_TO_METHODS[problem_type]
    method = explicit if explicit in candidates else candidates[0]
    if problem_type == "root_finding" and method == "bisection" and not extract_interval(text):
        # no bracket but a starting guess -> an open-end method is the honest choice
        if extract_point(text) is not None:
            method = "newton_raphson"
            spec = METHODS[method]
            notes = (notes if 'notes' in dir() else []) + [
                "bisection needs a bracketed interval; switched to Newton-Raphson because only a "
                "starting guess was supplied"
            ]
    spec = METHODS[method]
    extracted: dict[str, Any] = {}
    if problem_type == "linear_system":
        method = explicit if explicit in candidates else "gaussian_elimination"
        spec = METHODS[method]
        matrix = _extract_matrix(text)
        vector = _extract_vector(text)
        if matrix:
            extracted["matrix"] = matrix
        if vector:
            extracted["vector"] = vector
    else:
        if expression:
            extracted["expression"] = expression
        if problem_type == "root_finding":
            if method == "bisection":
                if interval:
                    extracted["a"], extracted["b"] = interval
            else:
                if point is not None:
                    extracted["x0"] = point
                    if method == "secant":
                        second = _second_number(text)
                        if second is not None and second != point:
                            extracted["x1"] = second
                elif interval:
                    extracted["x0"] = interval[0]
                    if method == "secant":
                        extracted["x1"] = interval[1]
        elif problem_type in ("integration",):
            if interval:
                extracted["a"], extracted["b"] = interval
        elif problem_type == "derivative":
            if point is not None:
                extracted["x"] = point

    if tolerance is not None:
        extracted["tolerance"] = tolerance
    if max_iterations is not None:
        extracted["max_iterations"] = max_iterations

    missing = [key for key in spec["requires"] if key not in extracted]
    rationale = _rationale(problem_type, method, extracted, category)
    if problem_type == "root_finding" and method == "bisection" and interval:
        rationale += " The supplied interval is testable for a sign change before iterating."
    if problem_type == "root_finding" and method != "bisection":
        rationale += " Open-end search: convergence is local and the initial guess matters."
    return Selection(
        category=category,
        problem_type=problem_type,
        method=method,
        rationale=rationale,
        requirements=list(spec.get("assumptions", [])) if isinstance(spec.get("assumptions"), list)
        else [spec["assumptions"]],
        missing=missing,
        extracted=extracted,
        assumptions=spec["assumptions"],
        notes=notes or _method_notes(method, extracted),
    )


SIM_ALIASES: dict[str, tuple[str, str]] = {
    "projectile": ("projectile_motion", "a closed-form-checkable ballistic trajectory"),
    "ballistic": ("projectile_motion", "a closed-form-checkable ballistic trajectory"),
    "pendulum": ("dynamical_system", "the exact nonlinear pendulum ODE (small-angle limits differ)"),
    "oscillat": ("simple_harmonic_motion", "linear restoring force with optional viscous damping"),
    "spring": ("simple_harmonic_motion", "linear restoring force with optional viscous damping"),
    "shm": ("simple_harmonic_motion", "linear restoring force with optional viscous damping"),
    "cool": ("newton_cooling", "lumped-capacitance exponential cooling with a known closed form"),
    "heat": ("heat_1d", "a parabolic PDE whose FTCS stability limit can be checked"),
    "diffusion": ("heat_1d", "a parabolic PDE whose FTCS stability limit can be checked"),
    "lorenz": ("dynamical_system", "a chaotic attractor for qualitative dynamics"),
    "chaos": ("dynamical_system", "a chaotic attractor for qualitative dynamics"),
    "attractor": ("dynamical_system", "a chaotic attractor for qualitative dynamics"),
}


def _select_simulation(lowered: str) -> Selection:
    key, reason = "projectile_motion", "the task describes a trajectory under gravity"
    for alias, (candidate, candidate_reason) in SIM_ALIASES.items():
        # word-boundary prefix match: "heat" must catch "heating" but not "wheat",
        # "cool" must catch "cooling"/"cools" but not "school"
        if re.search(rf"\b{alias}", lowered):
            key, reason = candidate, candidate_reason
            break
    params = extract_kv_params(lowered)
    notes: list[str] = []
    extracted: dict[str, Any] = {"simulation": key, "parameters": params}
    if key == "dynamical_system":
        preset = "lorenz" if any(word in lowered for word in ("lorenz", "chaos", "attractor")) else "nonlinear_pendulum"
        extracted["parameters"] = {**params, "preset": preset}
        notes.append(f"preset '{preset}' selected from the task wording")
    if key == "heat_1d":
        notes.append("grid/time-step defaults are chosen to satisfy r <= 1/2; refine them for accuracy studies")
    return Selection(
        category="simulation",
        problem_type="simulation",
        method=key,
        rationale=f"Selected '{key}' because it is the registered model that matches the request and offers {reason}.",
        requirements=[f"parameters for the '{key}' model (all have documented defaults)"],
        missing=[],
        extracted=extracted,
        assumptions="see the registered model's documented assumptions",
        notes=notes or ["idealised model: effects not listed in the assumptions are absent"],
    )


_MODEL_WORDS: tuple[tuple[str, str, int], ...] = (
    ("linear", "linear", 1),
    ("quadratic", "polynomial", 2),
    ("parabola", "polynomial", 2),
    ("cubic", "polynomial", 3),
    ("exponential", "exponential", 1),
    ("power law", "power_law", 1),
    ("power-law", "power_law", 1),
    ("gaussian", "gaussian", 1),
    ("damped sine", "damped_sine", 1),
    ("logistic", "logistic", 1),
)


def _model_from_wording(lowered: str) -> tuple[str, int, str | None]:
    """Model name, degree and a note, taken from what the user actually wrote.

    A bare "polynomial of degree 4" is honoured exactly; without any wording the tool
    default (linear) is used and the task is told which shape it got.
    """
    explicit = re.search(r"\b(?:poly(?:nomial)?|degree)\s*(?:of\s*)?(?:degree\s*)?([1-5])\b", lowered)
    if explicit:
        degree = int(explicit.group(1))
        return "polynomial", degree, f"degree {degree} read from the task wording"
    for word, model, degree in _MODEL_WORDS:
        if word in lowered:
            return model, degree, f"'{word}' in the task mapped to model='{model}', degree={degree}"
    return "linear", 1, None


def _select_analysis(lowered: str) -> Selection:
    dataset_id = extract_identifier(lowered, "dat")
    model, degree, model_note = _model_from_wording(lowered)
    missing = [] if dataset_id else ["dataset_id (an uploaded dataset in your account)"]
    return Selection(
        category="data_analysis",
        problem_type="regression",
        method="analyze_experimental_data",
        rationale="The task concerns measurements, so the analysis path profiles the stored "
                  "dataset and fits a supported model between two of its columns.",
        requirements=["an authorised dataset_id", "numeric x and y columns"],
        missing=missing,
        extracted={
            "dataset_id": dataset_id,
            "x_column": extract_dataset_column(lowered, "x"),
            "y_column": extract_dataset_column(lowered, "y"),
            "model": model,
            "degree": degree,
        },
        assumptions="residuals assumed independent and homoscedastic for the quoted standard errors",
        notes=[
            "if no dataset id is present, ask the user to upload or name one - never invent data",
            *([model_note] if model_note else ["no model shape was named, so a linear fit is used"]),
        ],
    )


def _rationale(problem_type: str, method: str, extracted: dict[str, Any], category: str) -> str:
    spec = METHODS[method]
    base = f"'{method}' ({spec['order']}) matches a {problem_type} task"
    if problem_type == "root_finding":
        if method == "bisection":
            base += (
                " and is the only listed method with a guaranteed bracket: it converges for any "
                "continuous f with a sign change, at linear rate"
            )
        elif method == "newton_raphson":
            base += " and gives quadratic convergence near a simple root, at the cost of needing a good initial guess"
        else:
            base += " and needs one evaluation per step without a derivative, at superlinear rate (~1.618)"
    elif problem_type == "integration":
        base += (
            "; Simpson's rule attains O(h^4) for smooth integrands"
            if method == "simpson"
            else "; the trapezoidal rule stays reliable for non-smooth or noisy data"
        )
    elif problem_type == "linear_system":
        base += (
            "; a direct method is preferred unless the system is large or sparse"
            if method in ("gaussian_elimination", "lu_decomposition")
            else "; an iterative method suits large/sparse systems and needs diagonal dominance for guaranteed convergence"
        )
    return base + "."


def _method_notes(method: str, extracted: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if method == "bisection" and "a" in extracted and "b" in extracted:
        notes.append("the bracket sign change will be verified before iterating; failure is reported, not hidden")
    if method in ("newton_raphson", "secant"):
        notes.append("no global convergence guarantee: a different root may be found from another starting point")
    if method in ("jacobi", "gauss_seidel"):
        notes.append("convergence is checked via the spectral radius of the iteration matrix and reported explicitly")
    if method == "simpson":
        notes.append(
            "Simpson's 1/3 rule needs pairs of panels: an odd 'panels' value is refused with the "
            "suggested even count, so omit it to let the rule refine adaptively"
        )
    return notes


def _extract_matrix(text: str) -> list[list[float]] | None:
    m = re.search(r"(?:matrix|A)\s*(?:=|is)?\s*\[?\s*(\[[^\]]+\]\s*,\s*\[[^\]]+\](?:\s*,\s*\[[^\]]+\])*)", text)
    if m:
        rows = re.findall(r"\[([^\]]+)\]", m.group(1))
        matrix = [[float(v) for v in row.split(",") if v.strip()] for row in rows]
        if matrix and len({len(r) for r in matrix}) == 1:
            return matrix
    m = re.search(rf"(\d+(?:\s+\d+){{2,8}})\s*(?:and|;|,)\s*(?:b\s*=|vector)", text)
    if m:
        flat = [float(v) for v in m.group(1).split()]
        side = int(round(math.sqrt(len(flat))))
        if side * side == len(flat):
            return [flat[i * side : (i + 1) * side] for i in range(side)]
    return None


def _extract_vector(text: str) -> list[float] | None:
    m = re.search(r"(?:b|rhs|vector)\s*(?:=|is|:)?\s*\[([^\]]+)\]", text)
    if m:
        try:
            return [float(v) for v in m.group(1).split(",") if v.strip()]
        except ValueError:
            return None
    return None


def _second_number(text: str) -> float | None:
    nums = _numbers(text)
    if len(nums) >= 2:
        return nums[1]
    return None

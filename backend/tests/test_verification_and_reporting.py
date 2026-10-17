"""Verification and reporting: the two places where honesty is easiest to fake.

Verification is an *independent* recomputation (SciPy quadrature, ``numpy.linalg.solve``,
SymPy closed forms, a different ODE stepper), so a tampered payload must be caught.
Reports must be assembled from the persisted run row alone - never from a fresh
computation that could quietly disagree with what was stored.
"""

from __future__ import annotations

import copy
import json
import math
import re

import pytest

from app.numerical.engine import run_numerical_method
from app.physics.engine import run_simulation
from app.services.reporting import build_report, render_markdown_file, render_pdf
from app.services.verification import _family_of, verify_result  # noqa: SLF001 - unit test
from app.security.posture import security_posture

API = "/api/v1"
ROOT2 = math.sqrt(2.0)

BISECTION = {"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-12}
SIMPSON = {"expression": "sin(x)", "a": 0.0, "b": math.pi}
SOLVER_2X2 = {"matrix": [[2.0, 1.0], [1.0, 3.0]], "vector": [3.0, 5.0]}
ODE_SHM = {"expressions": ["y2", "-y1"], "t_span": [0.0, 2.0 * math.pi], "y0": [1.0, 0.0]}
DERIV = {"expression": "sin(x)", "x": 1.0, "derivative_order": 2}


def run_method(method: str, params: dict) -> dict:
    return run_numerical_method(method, params).to_dict()


def check(record: dict, name: str) -> dict:
    matches = [entry for entry in record["checks"] if entry["name"] == name]
    assert matches, f"{name} missing from {[c['name'] for c in record['checks']]}"
    return matches[0]


# --- independent recomputation ----------------------------------------------------------


@pytest.mark.parametrize("method,params", [
    ("bisection", BISECTION),
    ("simpson", SIMPSON),
    ("trapezoidal", SIMPSON),
    ("gaussian_elimination", SOLVER_2X2),
    ("lu_decomposition", SOLVER_2X2),
    ("finite_difference", DERIV),
    ("ode_ivp", ODE_SHM),
])
def test_every_method_family_verifies_against_something(method, params):
    payload = run_method(method, params)
    record = verify_result(payload, {"params": params})
    names = [entry["name"] for entry in record["checks"]]
    assert "output_schema" in names and "finiteness" in names
    assert "verifier_error" not in names, names
    assert record["failed_checks"] == [], record
    # each family must add at least one check of its own, not just the three generic ones
    assert len(record["checks"]) > 3, (method, names)
    assert record["status"] in {"passed", "inconclusive"}


def test_a_wrong_root_fails_the_residual_and_bracket_checks():
    payload = run_method("bisection", BISECTION)
    tampered = copy.deepcopy(payload)
    tampered["result"]["root"] = 1.5  # f(1.5) = 0.25, plainly not a root
    record = verify_result(tampered, {"params": BISECTION})
    assert record["status"] == "failed"
    assert "residual_evaluation" in record["failed_checks"]
    assert "root_inside_final_bracket" in record["failed_checks"]
    assert check(record, "residual_evaluation")["f_root"] == pytest.approx(0.25)


def test_a_wrong_integral_fails_against_independent_quadrature():
    payload = run_method("simpson", SIMPSON)
    assert check(verify_result(payload, {"params": SIMPSON}), "independent_quadrature")["passed"] is True
    tampered = copy.deepcopy(payload)
    tampered["result"]["integral"] = 1.9
    record = verify_result(tampered, {"params": SIMPSON})
    assert record["status"] == "failed"
    entry = check(record, "independent_quadrature")
    assert entry["passed"] is False
    assert math.isclose(entry["reference"], 2.0, abs_tol=1e-10), entry


def test_a_wrong_linear_solution_fails_the_residual_check():
    payload = run_method("gaussian_elimination", SOLVER_2X2)
    assert math.isclose(payload["result"]["solution"][0], 0.8, rel_tol=1e-12)
    assert math.isclose(payload["result"]["solution"][1], 1.4, rel_tol=1e-12)
    tampered = copy.deepcopy(payload)
    tampered["result"]["solution"] = [1.0, 1.0]
    record = verify_result(tampered, {"params": SOLVER_2X2})
    assert record["status"] == "failed"
    assert "ax_minus_b_residual" in record["failed_checks"]
    entry = check(record, "ax_minus_b_residual")
    assert entry["residual_infinity_norm"] == pytest.approx(1.0)
    assert entry["relative_residual"] > 0.1


def test_a_wrong_derivative_is_caught_by_the_symbolic_reference():
    """The result payload names itself `finite_difference_derivative`; matching the
    registry key alone used to skip this check entirely."""
    payload = run_method("finite_difference", DERIV)
    assert payload["method"] == "finite_difference_derivative"
    record = verify_result(payload, {"params": DERIV})
    entry = check(record, "symbolic_derivative_comparison")
    assert entry["passed"] is True
    assert entry["analytic_expression"] == "-sin(x)"
    assert math.isclose(entry["analytic"], -math.sin(1.0), rel_tol=1e-12)

    tampered = copy.deepcopy(payload)
    tampered["result"]["derivative"] = -0.5
    tampered_record = verify_result(tampered, {"params": DERIV})
    assert tampered_record["status"] == "failed"
    assert "symbolic_derivative_comparison" in tampered_record["failed_checks"]


def test_method_names_are_folded_to_their_family():
    assert _family_of("ode_ivp:DOP853") == "ode_ivp"
    assert _family_of("finite_difference_derivative") == "finite_difference"
    assert _family_of("least_squares_fit") == "least_squares"
    assert _family_of(None) == ""
    assert _family_of("bisection") == "bisection"


def test_a_wrong_ode_final_state_fails_against_another_stepper():
    payload = run_method("ode_ivp", ODE_SHM)
    assert check(verify_result(payload, {"params": ODE_SHM}), "independent_ode_integration")["passed"] is True
    # one period of x'' = -x returns the state to its start
    assert payload["result"]["final_state"][0] == pytest.approx(1.0, abs=1e-6)
    tampered = copy.deepcopy(payload)
    tampered["result"]["final_state"] = [0.9, 0.9]
    record = verify_result(tampered, {"params": ODE_SHM})
    assert record["status"] == "failed"
    assert "independent_ode_integration" in record["failed_checks"]


def test_a_convergence_claim_must_match_the_reported_status():
    payload = run_method("bisection", BISECTION)
    inconsistent = copy.deepcopy(payload)
    inconsistent["status"] = "max_iterations_reached"  # honest status...
    inconsistent["convergence_achieved"] = True  # ...dishonest claim
    record = verify_result(inconsistent, {"params": BISECTION})
    assert check(record, "convergence_claim_consistency")["passed"] is False
    assert record["status"] == "failed"
    assert "status 'max_iterations_reached' conflicts" in check(record, "convergence_claim_consistency")["detail"]


def test_an_unfinished_bracket_cannot_be_certified_by_claiming_convergence():
    payload = run_method("bisection", {**BISECTION, "max_iterations": 5})
    assert payload["status"] == "max_iterations_reached"
    assert payload["convergence_achieved"] is False
    lying = copy.deepcopy(payload)
    lying["status"] = "converged"
    lying["convergence_achieved"] = True
    record = verify_result(lying, {"params": BISECTION})
    # both fields now claim success, so the independent checks must be the ones to refuse
    assert check(record, "final_error_within_tolerance")["passed"] is False
    assert record["status"] == "failed"


def test_non_finite_output_is_reported_not_silently_passed():
    payload = run_method("bisection", BISECTION)
    broken = copy.deepcopy(payload)
    broken["non_finite_fields"] = ["$.result.root"]
    broken["result"]["root"] = None
    record = verify_result(broken, {"params": BISECTION})
    assert record["status"] == "failed"
    entry = check(record, "finiteness")
    assert entry["passed"] is False
    assert entry["non_finite_fields"] == ["$.result.root"]


def test_a_non_monotone_error_history_is_flagged():
    payload = run_method("bisection", BISECTION)
    wobbled = copy.deepcopy(payload)
    for index, record in enumerate(wobbled["iterations"]):
        record["error"] = 10.0 + index  # errors that grow instead of shrink
    record = verify_result(wobbled, {"params": BISECTION})
    entry = check(record, "error_sequence_monotone")
    assert entry["passed"] is False
    assert entry["non_monotone_steps"] > 0


def test_garbage_payloads_fail_closed_and_never_raise():
    for payload in ({}, {"method": "bisection"}, {"result": {"root": "not a number"}},
                    {"method": "bisection", "status": "converged", "result": {"root": "nonsense"}}):
        record = verify_result(payload, {})
        assert isinstance(record["checks"], list) and record["checks"]
        assert record["status"] in {"failed", "inconclusive"}, payload
        assert "verifier_error" not in [c["name"] for c in record["checks"]]

    incomplete = verify_result({"method": "bisection"}, {})
    entry = check(incomplete, "output_schema")
    assert entry["passed"] is False
    assert entry["missing_keys"] == ["status", "result"]


def test_simulation_payloads_are_checked_for_series_consistency():
    payload = run_simulation("simple_harmonic_motion", {"spring_constant": 4.0, "mass": 1.0,
                                                        "x0": 0.5, "duration": 12.0})
    record = verify_result(payload, {"params": {}})
    assert check(record, "time_series_present")["passed"] is True
    assert check(record, "time_axis_increasing")["passed"] is True
    assert check(record, "series_length_consistency")["passed"] is True
    assert check(record, "analytical_validation")["passed"] is True
    assert record["status"] == "passed", (record["failed_checks"], record["inconclusive_checks"])
    assert record["inconclusive_checks"] == [], "an ODE re-check must not be demanded of a simulation"


def test_a_simulation_without_a_closed_form_is_inconclusive_not_passed():
    # quadratic drag removes the closed-form reference, and the record must say so
    payload = run_simulation("projectile_motion",
                             {"v0": 30.0, "angle_deg": 45.0, "drag_coefficient_total": 0.2})
    record = verify_result(payload, {"params": {}})
    entry = check(record, "analytical_validation")
    assert entry["passed"] is None
    assert record["status"] == "inconclusive"
    assert "no analytical reference" in str(entry["detail"]).lower()


def test_verification_record_carries_its_own_provenance():
    payload = run_method("simpson", SIMPSON)
    record = verify_result(payload, {"params": SIMPSON})
    assert record["verifier"]["strategy"].startswith("independent recomputation")
    versions = record["verifier"]["software_versions"]
    assert versions["python"] and versions["numpy"] and versions["scipy"]
    assert record["verified_at"].endswith("Z")
    assert record["check_count"] == len(record["checks"])


# --- report generation ---------------------------------------------------------------------


def finished_experiment(authed, **overrides):
    spec = {
        "title": "square root of two",
        "kind": "numerical",
        "method": "bisection",
        "parameters": BISECTION,
        **overrides,
    }
    experiment_id = authed.post(f"{API}/experiments", json=spec).json()["id"]
    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()
    return experiment_id, run["run"]["run_id"]


def test_report_sections_follow_the_lab_structure(authed):
    experiment_id, run_id = finished_experiment(authed)
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id}).json()
    # the API summary carries the headings; render_markdown_file consumes the section blocks
    headings = list(report["sections"])
    assert all(isinstance(heading, str) for heading in headings), headings[:3]
    assert headings[0].startswith("1. Title")
    for required in ("Research question", "Model and assumptions", "Governing equations",
                     "Numerical method", "Validation and error analysis", "Limitations",
                     "References", "Reproducibility"):
        assert any(required in heading for heading in headings), heading
    assert len(headings) >= 13
    assert report["format"] == "markdown+pdf"
    assert set(report["artifacts"]) == {"markdown", "pdf"}


def test_markdown_quotes_stored_numbers_and_declares_its_source(authed):
    experiment_id, run_id = finished_experiment(authed)
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "markdown"}).json()
    stored = authed.get(f"{API}/runs/{run_id}").json()["structured_output"]
    text = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text

    assert f"{stored['result']['root']:.6f}" in text
    assert "stored run" in text.lower() or "persisted" in text.lower()
    assert run_id in text and experiment_id in text
    # documented assumptions and limitations are carried into the report
    assert "sign change" in text.lower() or "continuous" in text.lower()
    assert "1e-12" in text, "the requested tolerance must be visible"
    assert "bisection" in text
    # the reference section must not pretend to be literature
    assert "No external sources were retrieved or verified" in text
    # a real word count over the whole document, not just its prose blocks. Table pipes
    # and blank lines are rendering, not words, so they are removed before comparing.
    words = len(re.findall(r"\S+", text.replace("|", " ")))
    assert report["word_count"] > 300, report["word_count"]
    assert abs(report["word_count"] - words) / words < 0.35, (report["word_count"], words)


def test_report_is_assembled_from_stored_rows_without_recomputing(authed, monkeypatch):
    experiment_id, run_id = finished_experiment(authed)
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "markdown"}).json()

    # any attempt to re-run mathematics while rendering must be visible
    def explode(*args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("reporting must read stored rows, not recompute")

    monkeypatch.setattr("app.numerical.engine.run_numerical_method", explode)
    monkeypatch.setattr("app.physics.engine.run_simulation", explode)
    text = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text
    assert "1.414214" in text
    assert authed.get(f"{API}/runs/{run_id}").json()["status"] == "completed"


def test_unconverged_runs_are_reported_as_unfinished_work(authed):
    spec = {
        "title": "starved bisection",
        "kind": "numerical",
        "method": "bisection",
        "parameters": {**BISECTION, "tolerance": 1e-16, "max_iterations": 4},
    }
    experiment_id = authed.post(f"{API}/experiments", json=spec).json()["id"]
    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]
    assert run["status"] == "completed_unconverged"
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "markdown"}).json()
    assert report["warnings"], "an unconverged run must raise a warning"
    text = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text
    assert "unconverged" in text.lower() or "not converged" in text.lower()
    assert "iterate" in text.lower(), "the value must be labelled an iterate, not a root"
    assert report["run_id"] == run["run_id"]


def test_pdf_is_a_real_document_with_the_same_facts(authed):
    experiment_id, run_id = finished_experiment(authed)
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "both"}).json()
    pdf = authed.get(f"{API}/reports/{report['report_id']}/download?format=pdf")
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"
    assert b"%%EOF" in pdf.content[-2048:]
    assert pdf.headers["content-type"] == "application/pdf"
    assert len(pdf.content) > 5000
    assert b"/Type /Page" in pdf.content or b"/Type/Page" in pdf.content
    assert pdf.headers["content-disposition"].startswith('attachment; filename="')
    assert ".pdf" in pdf.headers["content-disposition"]


def test_report_can_target_a_run_directly_and_rejects_other_peoples_rows(authed, client, second_user_token):
    experiment_id, run_id = finished_experiment(authed)
    direct = authed.post(f"{API}/reports", json={"run_id": run_id, "format": "markdown"})
    assert direct.status_code == 201, direct.text
    assert direct.json()["experiment_id"] == experiment_id

    authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "both"})
    listing = authed.get(f"{API}/reports").json()
    assert listing["total"] == 2 and len(listing["items"]) == 2
    assert {item["format"] for item in listing["items"]} == {"markdown", "markdown+pdf"}
    for item in listing["items"]:
        assert item["artifact_path"].endswith(".md")
        assert len(item["checksum_sha256"]) == 64
        assert item["word_count_estimate"] > 40
        assert item["verification_snapshot"]["run_verification_status"] == "passed"
        assert item["has_pdf"] == (item["format"] == "markdown+pdf")

    other = client
    other.headers.update({"Authorization": f"Bearer {second_user_token}"})
    assert other.get(f"{API}/reports/{listing['items'][0]['report_id']}").status_code == 404
    assert other.delete(f"{API}/reports/{listing['items'][0]['report_id']}").status_code == 404
    assert other.get(f"{API}/reports").json()["total"] == 0

    assert authed.delete(f"{API}/reports/{direct.json()['report_id']}").status_code == 204
    assert authed.get(f"{API}/reports/{direct.json()['report_id']}").status_code == 404


def test_reports_refuse_an_experiment_without_runs(authed):
    experiment_id = authed.post(f"{API}/experiments", json={
        "title": "no runs yet", "kind": "numerical", "method": "bisection", "parameters": BISECTION,
    }).json()["id"]
    response = authed.post(f"{API}/reports", json={"experiment_id": experiment_id})
    assert response.status_code == 409
    assert "no runs" in response.json()["error"]["message"].lower()
    assert authed.post(f"{API}/reports", json={}).status_code == 409


def test_a_failed_run_is_reported_as_inconclusive_work_not_hidden(authed):
    """A document about a run that never computed anything is useful only if it says so."""
    failed_id = authed.post(f"{API}/experiments", json={
        "title": "broken precondition", "kind": "numerical", "method": "bisection",
        "parameters": {"expression": "x^2 + 1", "a": 0.0, "b": 1.0},
    }).json()["id"]
    run = authed.post(f"{API}/experiments/{failed_id}/runs", json={"wait": True})
    assert run.status_code == 200
    assert run.json()["run"]["status"] == "failed"

    report = authed.post(f"{API}/reports", json={"experiment_id": failed_id, "format": "markdown"}).json()
    assert any("status 'failed'" in warning for warning in report["warnings"]), report["warnings"]
    assert "no validated numerical conclusion" in " ".join(report["warnings"])
    text = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text
    assert "| run status | failed |" in text
    assert "same sign" in text, "the recorded precondition error must be quoted, not summarised away"
    assert "| run error | recorded value |" in text
    assert "invalid_numerical_input" in text
    # and it must not invent a result table for a run that produced none
    assert "1.414214" not in text


def test_simulation_reports_include_the_model_and_units(authed):
    spec = {
        "title": "cannon shot",
        "kind": "simulation",
        "method": "projectile_motion",
        "parameters": {"v0": 40.0, "angle_deg": 45.0},
    }
    experiment_id = authed.post(f"{API}/experiments", json=spec).json()["id"]
    authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True})
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "markdown"}).json()
    text = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text
    assert "projectile" in text.lower()
    assert "m/s" in text or "metre" in text.lower()
    assert "163.15" in text, "the stored range must be quoted, not re-derived"
    assert "no air resistance" in text.lower() or "drag" in text.lower()


def test_build_report_returns_a_document_not_a_rendering(experiment_and_run):
    experiment, run = experiment_and_run
    doc = build_report(experiment=experiment, run=run)
    assert doc["title"] == experiment.title
    assert doc["run_id"] == run.id
    assert doc["sections"] and all("heading" in section and "blocks" in section for section in doc["sections"])
    markdown = render_markdown_file(doc)
    assert isinstance(markdown, bytes) and markdown.startswith(b"# ")
    pdf = render_pdf(doc)
    assert pdf[:5] == b"%PDF-"
    assert doc["warnings"] == [] or isinstance(doc["warnings"], list)


@pytest.fixture
def experiment_and_run(authed):
    """Detached ORM rows, exactly what the report builder is given by the API layer."""
    experiment_id, run_id = finished_experiment(authed)
    from app.db.session import session_scope
    from app.models.entities import Experiment, ExperimentRun

    with session_scope() as db:
        experiment = db.get(Experiment, experiment_id)
        run = db.get(ExperimentRun, run_id)
        db.expunge(experiment)
        db.expunge(run)
    return experiment, run


def test_rendered_markdown_is_utf8_safe_and_self_contained(experiment_and_run):
    experiment, run = experiment_and_run
    doc = build_report(experiment=experiment, run=run)
    text = render_markdown_file(doc).decode("utf-8")
    assert "\x00" not in text
    assert "PHYSICS LAB AI" in text
    assert experiment.title in text
    # every table row must be a pipe row, or the document is not renderable
    table_rows = [line for line in text.splitlines() if line.startswith("|")]
    assert table_rows and all(line.count("|") >= 2 for line in table_rows)
    assert len(text) > 1200


# --- security posture ----------------------------------------------------------------------


def test_posture_reports_the_six_faces_it_claims_to():
    posture = security_posture()
    assert set(posture) >= {"secrets", "execution", "access_control", "data", "observability",
                            "honesty_guarantees", "gaps"}
    assert posture["execution"]["arbitrary_python_execution"] is False
    assert posture["execution"]["eval_or_exec_used_for_expressions"] is False
    assert posture["honesty_guarantees"]
    assert isinstance(posture["gaps"], list) and len(posture["gaps"]) >= 5


def test_posture_admits_what_is_not_hardened():
    text = json.dumps(security_posture()).lower()
    assert "tls" in text and "csp" in text
    assert "not" in text  # explicit negatives, not reassurance
    assert "redis" in text or "per-worker" in text
    # the gap about markdown rendering names the file that actually sanitises it
    assert "frontend/src/lib/markdown.ts" in text


def test_posture_reflects_the_live_configuration(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "rate_limit_enabled", False, raising=False)
    text = json.dumps(security_posture()).lower()
    assert "rate limit" in text
    monkeypatch.setattr(settings, "cookie_secure", False, raising=False)
    assert "plab_cookie_secure" in json.dumps(security_posture()).lower()


def test_posture_is_published_on_the_capabilities_route(client):
    body = client.get(f"{API}/capabilities").json()
    security = body["security"]
    assert set(security) >= {"secrets", "execution", "access_control", "data", "observability",
                             "honesty_guarantees", "gaps"}
    assert security["secrets"]["ai_keys_server_side_only"] is True
    assert security["secrets"]["keys_never_returned_by_api"] is True
    assert "password" in security["secrets"]["log_redaction"]
    assert security["secrets"]["reference"].startswith("app/core/")
    guarantees = security["honesty_guarantees"]
    assert json.dumps(guarantees).count("not_performed") >= 1
    assert "every number" in guarantees["no_invented_results"]
    assert len(security["gaps"]) >= 5
    assert all(isinstance(gap, str) and len(gap) > 20 for gap in security["gaps"])

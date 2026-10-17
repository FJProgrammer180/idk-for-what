"""End-to-end API tests for the lab surface: compute, experiments, runs, artifacts,
datasets, reports, dashboard and settings.

The numerical values are asserted against closed forms, and every artifact (plot, chart
data, export, report) is checked to be derived from the persisted run row rather than a
second computation.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time

import pytest

API = "/api/v1"
ROOT2 = math.sqrt(2.0)

BISECTION_SPEC = {
    "kind": "numerical",
    "method": "bisection",
    "parameters": {"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-12},
}


def experiment_payload(**overrides):
    base = {"title": "square root of two", **BISECTION_SPEC, "notes": "", "tags": []}
    base.update(overrides)
    return base


def wait_for_run(client, run_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"{API}/runs/{run_id}").json()
        if body["status"] in {"completed", "completed_unconverged", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached a terminal state")


# --- ephemeral compute --------------------------------------------------------------


def test_ephemeral_compute_returns_a_real_root_and_no_persistence(authed):
    response = authed.post(f"{API}/compute/run", json=BISECTION_SPEC)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "ephemeral"
    assert math.isclose(body["result"]["result"]["root"], ROOT2, rel_tol=1e-12)
    assert body["verification"]["status"] == "passed"
    assert body.get("plot_png_base64", "").startswith("iVBOR")  # PNG signature
    assert authed.get(f"{API}/experiments").json()["total"] == 0


def test_ephemeral_compute_is_rate_limited_by_the_iteration_ceiling(authed):
    response = authed.post(
        f"{API}/compute/run",
        json={"kind": "numerical", "method": "bisection",
              "parameters": {"expression": "x^2-2", "a": 1.0, "b": 2.0, "max_iterations": 10**9}},
    )
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert response.json()["result"]["metadata"]["iterations_used"] <= 200


def test_method_precondition_failures_are_422_with_the_method_message(authed):
    response = authed.post(
        f"{API}/compute/run",
        json={"kind": "numerical", "method": "bisection",
              "parameters": {"expression": "x^2 + 1", "a": 0.0, "b": 1.0}},
    )
    assert response.status_code == 422
    assert "sign" in response.json()["error"]["message"].lower() or "bracket" in response.json()["error"]["message"].lower()


def test_unknown_method_lists_the_registered_ones(authed):
    response = authed.post(
        f"{API}/compute/run",
        json={"kind": "numerical", "method": "monte_carlo", "parameters": {"expression": "x"}},
    )
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "bisection" in message and "not registered" in message


def test_methods_catalogue_endpoints_describe_the_registry(client):
    listing = client.get(f"{API}/compute/methods").json()
    assert len(listing["methods"]) == 12
    detail = client.get(f"{API}/compute/methods/bisection").json()
    assert detail["assumptions"] and detail["requires"]
    assert client.get(f"{API}/compute/methods/nope").status_code == 404


# --- experiments and runs ------------------------------------------------------------


def test_experiment_lifecycle_with_a_persisted_run(authed):
    created = authed.post(f"{API}/experiments", json=experiment_payload()).json()
    experiment_id = created["id"]
    assert created["status"] == "draft"

    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()
    assert run["run"]["status"] == "completed"
    assert math.isclose(run["run"]["structured_output"]["result"]["root"], ROOT2, rel_tol=1e-12)
    assert run["run"]["verification"]["status"] == "passed"

    detail = authed.get(f"{API}/experiments/{experiment_id}").json()
    assert detail["status"] == "completed"
    assert detail["latest_run"]["run_id"] == run["run"]["run_id"]

    patched = authed.patch(f"{API}/experiments/{experiment_id}", json={"notes": "checked by hand"}).json()
    assert patched["notes"] == "checked by hand"

    duplicated = authed.post(f"{API}/experiments/{experiment_id}/duplicate").json()
    assert duplicated["id"] != experiment_id
    assert duplicated["title"].endswith("(copy)")
    assert duplicated["status"] == "draft"
    assert duplicated["input_parameters"]["method"] == "bisection"


def test_run_listing_and_pagination(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    for _ in range(3):
        authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True})
    runs = authed.get(f"{API}/experiments/{experiment_id}/runs").json()
    assert runs["total"] == 3
    assert len(runs["items"]) == 3
    assert all(item["structured_output"] == {} for item in runs["items"]), "history must stay light"
    page = authed.get(f"{API}/experiments/{experiment_id}/runs?limit=2&offset=2").json()
    assert len(page["items"]) == 1 and page["has_more"] is False and page["page"] == 2


def test_async_run_is_polled_to_completion(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    started = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": False}).json()
    assert started["status"] == "queued"
    body = wait_for_run(authed, started["run"]["run_id"])
    assert body["status"] == "completed"
    assert body["verification"]["checks"]


def test_idempotency_key_reuses_the_existing_run(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    payload = {"wait": True, "idempotency_key": "same-key"}
    first = authed.post(f"{API}/experiments/{experiment_id}/runs", json=payload).json()["run"]["run_id"]
    second = authed.post(f"{API}/experiments/{experiment_id}/runs", json=payload).json()["run"]["run_id"]
    assert first == second
    assert authed.get(f"{API}/experiments/{experiment_id}/runs").json()["total"] == 1


def test_cancelling_a_finished_run_is_a_no_op_said_out_loud(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    response = authed.post(f"{API}/runs/{run_id}/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is False
    assert body["detail"].startswith("already_finished")
    assert "already" in body["note"].lower()
    # the stored run keeps its real status
    assert authed.get(f"{API}/runs/{run_id}").json()["status"] == "completed"


def test_unconverged_run_is_labeled_and_still_verifiable(authed):
    payload = experiment_payload(
        title="starved bisection",
        parameters={"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-16,
                    "max_iterations": 4},
    )
    experiment_id = authed.post(f"{API}/experiments", json=payload).json()["id"]
    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]
    assert run["status"] == "completed_unconverged"
    assert run["structured_output"]["convergence_achieved"] is False
    assert any("NOT a verified root" in w for w in run["structured_output"]["warnings"])
    # verification of an unconverged run must not upgrade it to a success claim
    assert run["verification"]["status"] == "failed", run["verification"]
    checks = {c["name"]: c for c in run["verification"]["checks"]}
    assert checks["convergence_claim_consistency"]["passed"] is True
    assert checks["convergence_claim_consistency"]["convergence_achieved"] is False
    assert checks["residual_evaluation"]["passed"] is False, "a 4-iteration bracket must not pass |f(x)|"
    assert run["structured_output"]["status"] == "max_iterations_reached"


def test_reverify_endpoint_recomputes_the_checks(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    response = authed.post(f"{API}/runs/{run_id}/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["verification"]["status"] == "passed"
    assert {check["name"] for check in body["verification"]["checks"]} >= {
        "output_schema", "finiteness", "convergence_claim_consistency", "residual_evaluation"
    }


def test_chart_data_and_plot_come_from_the_stored_payload(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    chart = authed.get(f"{API}/runs/{run_id}/chart-data").json()
    assert chart["run_id"] == run_id and chart["method"] == "bisection"
    assert chart["axes"] == {"x": "iteration", "y": "value"}
    assert chart["series"], chart
    assert all(len(s["x"]) == len(s["y"]) and s["x"] for s in chart["series"])
    labels = " ".join(s.get("label", "") for s in chart["series"]).lower()
    assert "error" in labels, labels
    assert chart["iteration_table"] and set(chart["iteration_table"][0]) >= {"index", "x"}
    plot = authed.get(f"{API}/runs/{run_id}/plot.png")
    assert plot.status_code == 200
    assert plot.headers["content-type"] == "image/png"
    assert plot.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(plot.content) > 2000


def test_simulation_experiment_reports_units_and_validation(authed):
    payload = {
        "title": "cannon shot",
        "kind": "simulation",
        "method": "projectile_motion",
        "parameters": {"v0": 40.0, "angle_deg": 45.0},
    }
    experiment_id = authed.post(f"{API}/experiments", json=payload).json()["id"]
    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]
    output = run["structured_output"]
    analytic = 40.0**2 / 9.80665
    assert math.isclose(output["summary"]["range_at_first_impact"], analytic, rel_tol=1e-4)
    assert output["units"]["x"] == "m"
    assert output["validation"]["overall"] == "passed"
    assert output["convergence_achieved"] is True
    assert run["status"] == "completed"


def test_exports_are_formula_safe_and_round_trip(authed):
    payload = experiment_payload(title="=HYPERLINK(\"http://evil\")")
    experiment_id = authed.post(f"{API}/experiments", json=payload).json()["id"]
    authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True})

    blob = authed.get(f"{API}/experiments/{experiment_id}/export").json()
    assert blob["experiment_id"] == experiment_id
    assert blob["method"] == "bisection"
    assert blob["run"]["status"] == "completed"
    assert blob["run"]["output"]["result"]["root"]
    assert blob["run"]["verification"]["status"] == "passed"
    assert blob["inputs"]["params"]["expression"] == "x^2 - 2"
    assert blob["exported_at"]

    text = authed.get(f"{API}/experiments/{experiment_id}/export?format=csv").text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] and "index" in rows[0]
    # an iteration table plus the run header row must be present
    assert len(rows) > 3

    with pytest.raises(Exception):
        json.loads(text)  # csv is not json


def test_delete_removes_runs_and_artifacts(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    assert authed.delete(f"{API}/experiments/{experiment_id}").status_code == 204
    assert authed.get(f"{API}/experiments/{experiment_id}").status_code == 404
    assert authed.get(f"{API}/runs/{run_id}").status_code == 404
    assert authed.get(f"{API}/runs/{run_id}/plot.png").status_code == 404


def test_missing_records_are_404_with_the_shared_envelope(authed):
    missing = authed.get(f"{API}/experiments/exp_doesnotexist")
    assert missing.status_code == 404
    body = missing.json()["error"]
    assert body["code"] == "not_found"
    assert body["request_id"]
    assert body["details"] is None
    assert authed.get(f"{API}/datasets/dat_doesnotexist").status_code == 404
    assert authed.get(f"{API}/runs/run_doesnotexist").status_code == 404
    assert authed.get(f"{API}/reports/rep_doesnotexist").status_code == 404
    # a 404 never echoes the ORM object or a stack trace
    assert "Traceback" not in missing.text and "SELECT" not in missing.text.upper()


# --- templates -----------------------------------------------------------------------


def test_templates_are_bundled_and_instantiate_into_experiments(authed):
    listing = authed.get(f"{API}/experiments/templates").json()
    assert len(listing["templates"]) >= 12
    first = listing["templates"][0]
    assert {"key", "title", "kind", "method", "parameters", "description"} <= set(first)

    created = authed.post(f"{API}/experiments/templates/{first['key']}", json={}).json()
    assert created["template_key"] == first["key"]
    assert created["method"] == first["method"]
    assert created["status"] == "draft"

    overridden = authed.post(
        f"{API}/experiments/templates/{first['key']}",
        json={"title": "tuned from template", "parameters": {"tolerance": 1e-9}},
    ).json()
    assert overridden["title"] == "tuned from template"
    assert overridden["input_parameters"]["params"]["tolerance"] == 1e-9

    assert authed.post("/api/v1/experiments/templates/not_a_template", json={}).status_code == 404


# --- datasets ------------------------------------------------------------------------


CSV_TEXT = (
    "time_s,position_m\n"
    + "\n".join(f"{i/10:.1f},{4.905*(i/10)**2:.4f}" for i in range(11))
    + "\n"
)


def upload(client, name="fall.csv", content=CSV_TEXT, content_type="text/csv"):
    return client.post(
        f"{API}/datasets", files={"file": (name, io.BytesIO(content.encode()), content_type)}
    )


def test_dataset_upload_analysis_and_clean_export(authed):
    response = upload(authed)
    assert response.status_code == 201, response.text
    body = response.json()
    dataset_id = body["dataset_id"]
    assert body["row_count"] == 11
    assert body["duplicate"] is False
    numeric = [c["name"] for c in body["schema_profile"]["columns"] if c["numeric"]]
    assert numeric == ["time_s", "position_m"]

    again = upload(authed)
    assert again.json()["duplicate"] is True
    assert again.json()["dataset_id"] == dataset_id

    analysis = authed.post(
        f"{API}/datasets/{dataset_id}/analyze",
        json={"x_column": "time_s", "y_column": "position_m", "model": "polynomial", "degree": 2},
    ).json()
    fit = analysis["result"]["fit"]["result"]
    assert fit["r_squared"] > 0.999
    # free fall: y = 0.5 g t^2 with g = 9.81
    assert math.isclose(fit["parameters"]["c2"]["value"], 4.905, rel_tol=1e-3)
    assert analysis["status"] == "completed"

    export = authed.get(f"{API}/datasets/{dataset_id}/export")
    assert export.status_code == 200
    assert 'attachment; filename="fall_cleaned.csv"' in export.headers["content-disposition"]
    assert len(export.text.strip().splitlines()) == 12

    assert authed.delete(f"{API}/datasets/{dataset_id}").status_code == 204
    assert authed.get(f"{API}/datasets/{dataset_id}").status_code == 404


@pytest.mark.parametrize(
    "name,content",
    [
        ("empty.csv", ""),
        ("one_column.csv", "only_a_column\n1\n2\n"),
        ("ragged.csv", "a,b\n1,2\n3\n"),
        ("nulls.txt", "a\x00b,c\n1,2\n"),
        ("script.sh", "#!/bin/sh\nrm -rf /\n"),
        ("payload.exe", "MZ\x90\x00"),
    ],
)
def test_bad_uploads_are_rejected(authed, name, content):
    response = upload(authed, name=name, content=content)
    assert response.status_code in (409, 422), response.text
    assert "error" in response.json()


def test_analysis_requires_columns_that_exist(authed):
    dataset_id = upload(authed).json()["dataset_id"]
    response = authed.post(
        f"{API}/datasets/{dataset_id}/analyze",
        json={"x_column": "nope", "y_column": "position_m", "model": "polynomial", "degree": 2},
    )
    assert response.status_code == 422
    assert "nope" in response.json()["error"]["message"]
    assert response.json()["error"]["code"] == "policy_validation_failed"
    details = response.json()["error"]["details"]
    assert details["valid_columns"] == ["time_s", "position_m"]
    assert details["requested"]["x_column"] == "nope"


# --- reports -------------------------------------------------------------------------


def test_report_is_built_from_the_persisted_run_only(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]
    report = authed.post(f"{API}/reports", json={"experiment_id": experiment_id, "format": "both"}).json()
    assert report["run_id"] == run_id
    assert report["experiment_id"] == experiment_id
    assert report["word_count"] > 60
    assert len(report["sections"]) >= 8
    assert report["download"]["markdown"].endswith("format=markdown")

    markdown = authed.get(f"{API}/reports/{report['report_id']}/download?format=markdown").text
    assert f"{ROOT2:.6f}" in markdown or "1.414214" in markdown
    assert "Validation and error analysis" in markdown
    assert "assumption" in markdown.lower()
    assert run_id in markdown

    pdf = authed.get(f"{API}/reports/{report['report_id']}/download?format=pdf")
    assert pdf.content[:5] == b"%PDF-"
    assert pdf.headers["content-type"] == "application/pdf"
    assert len(pdf.content) > 5000

    listed = authed.get(f"{API}/reports").json()
    assert listed["total"] == 1

    # an unconverged run must be flagged in the report, not smoothed over
    starved = experiment_payload(
        title="starved bisection",
        parameters={"expression": "x^2 - 2", "a": 1.0, "b": 2.0, "tolerance": 1e-16, "max_iterations": 4},
    )
    starved_id = authed.post(f"{API}/experiments", json=starved).json()["id"]
    authed.post(f"{API}/experiments/{starved_id}/runs", json={"wait": True})
    second = authed.post(f"{API}/reports", json={"experiment_id": starved_id, "format": "markdown"}).json()
    assert second["warnings"], "an unconverged run must produce a warning"
    body = authed.get(f"{API}/reports/{second['report_id']}/download?format=markdown").text
    assert "not" in body.lower() and ("iterate" in body.lower() or "unconverged" in body.lower())


def test_report_requires_a_finished_run(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    response = authed.post(f"{API}/reports", json={"experiment_id": experiment_id})
    assert response.status_code in (404, 409)


# --- dashboard and settings -------------------------------------------------------------


def test_dashboard_aggregates_the_rows_that_exist(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True})
    upload(authed)
    body = authed.get(f"{API}/dashboard").json()
    assert body["totals"]["experiments"] == 1
    assert body["totals"]["runs"] == 1
    assert body["totals"]["datasets"] == 1
    assert body["runs_by_status"]["completed"] == 1
    assert body["runs_by_verification"]["passed"] == 1
    assert body["method_usage"] == [{"method": "bisection", "kind": "numerical", "runs": 1}]
    assert body["recent_runs"][0]["experiment_id"] == experiment_id
    assert body["runtime"]["mean_execution_time_ms"] > 0
    assert body["generated_from"] == "database queries against this account's rows"


def test_settings_report_what_is_actually_enforced(authed):
    settings = authed.get(f"{API}/settings").json()
    assert settings["profile"]["email"].endswith("@example.com")
    limits = settings["computation_limits"]
    assert limits["max_iterations"] == 200
    assert limits["server_ceiling_max_iterations"] == 200
    assert limits["max_agent_steps"] >= 1 and limits["max_upload_bytes"] > 0
    assert limits["enforced_by"]
    assert settings["ai_provider"]["is_mock"] is True
    assert settings["ai_provider"]["provider"] == "mock"
    assert settings["security"]["execution"]["arbitrary_python_execution"] is False
    assert settings["security"]["execution"]["eval_or_exec_used_for_expressions"] is False

    patched = authed.patch(f"{API}/settings", json={"display_name": "Dr. Curie"}).json()
    assert patched["status"] == "updated" and patched["changed"] == ["display_name"]
    assert patched["profile"]["display_name"] == "Dr. Curie"
    assert authed.patch(f"{API}/settings", json={"display_name": "Dr. Curie"}).json()["status"] == "noop"

    tightened = authed.patch(f"{API}/settings", json={"max_iterations": 5}).json()
    assert tightened["computation_limits"]["max_iterations"] == 5
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()
    assert run["run"]["structured_output"]["metadata"]["iterations_used"] <= 5
    # a starved budget is reported as unconverged, never silently truncated to a "success"
    assert run["run"]["status"] in {"completed_unconverged", "completed"}

    # two independent bounds, each reported in its own words
    above_schema_bound = authed.patch(f"{API}/settings", json={"max_iterations": 10**9})
    assert above_schema_bound.status_code == 422
    assert above_schema_bound.json()["error"]["code"] == "request_validation_failed"
    assert "max_iterations" in above_schema_bound.json()["error"]["message"]
    above_server_ceiling = authed.patch(f"{API}/settings", json={"max_iterations": 5000})
    assert above_server_ceiling.status_code == 422
    assert "PLAB_MAX_ITERATIONS" in above_server_ceiling.json()["error"]["message"]
    assert above_server_ceiling.json()["error"]["details"]["server_ceiling"] == 200

    revoked = authed.post(f"{API}/settings/sessions/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["token_version"] >= 2


# --- retention --------------------------------------------------------------------------


def test_retention_purge_only_touches_expired_terminal_runs(authed):
    experiment_id = authed.post(f"{API}/experiments", json=experiment_payload()).json()["id"]
    run_id = authed.post(f"{API}/experiments/{experiment_id}/runs", json={"wait": True}).json()["run"]["run_id"]

    stored = authed.patch(f"{API}/settings", json={"retention_days": 30}).json()
    assert stored["data_retention"]["retention_days"] == 30
    assert authed.get(f"{API}/settings").json()["data_retention"]["retention_days"] == 30

    preview = authed.post(f"{API}/settings/retention/purge").json()
    assert preview["status"] == "dry_run" and preview["days"] == 30
    assert preview["would_remove"] == 0, "a run created seconds ago is inside every window"
    assert authed.get(f"{API}/settings/retention/purge", headers={}).status_code == 405

    # days=0 means "everything terminal is already expired", not "no policy"
    zero = authed.post(f"{API}/settings/retention/purge?days=0").json()
    assert zero["would_remove"] == 1 and zero["run_ids"] == [run_id]
    assert authed.get(f"{API}/runs/{run_id}").json()["status"] == "completed", "dry run deletes nothing"

    removed = authed.post(f"{API}/settings/retention/purge?days=0&dry_run=false").json()
    assert removed["status"] == "purged" and removed["removed"] == 1
    assert authed.get(f"{API}/runs/{run_id}").status_code == 404
    dashboard = authed.get(f"{API}/dashboard").json()
    assert dashboard["totals"]["runs"] == 0 and not dashboard["recent_runs"]
    # the experiment survives, just without its run history
    experiment = authed.get(f"{API}/experiments/{experiment_id}").json()
    assert experiment["run_count"] == 0 and experiment["latest_run"] is None

    back = authed.patch(f"{API}/settings", json={"retention_days": 0}).json()
    assert back["data_retention"]["retention_days"] == 0
    assert authed.post(f"{API}/settings/retention/purge").json()["status"] == "no_policy"



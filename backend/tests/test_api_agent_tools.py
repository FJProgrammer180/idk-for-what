"""The agentic layer: manifest, dry-run validation, planning, execution, research honesty.

Two properties matter here and both are checked rather than asserted in prose:

1. the planner may only call registered tools with schema-validated arguments - there is
   no path from a user sentence to arbitrary code;
2. every number in an answer must come from an executed tool, and anything the platform
   cannot do (live literature retrieval, token accounting for a local planner) is said to
   be unavailable instead of being filled in.
"""

from __future__ import annotations

import json
import math

import pytest

from app.tools.base import CATEGORIES

API = "/api/v1"
SQRT3 = math.sqrt(3.0)

TOOL_NAMES = {
    "analyze_experimental_data",
    "compare_experiments",
    "generate_research_report",
    "inspect_experiment",
    "request_clarification",
    "run_physics_simulation",
    "save_experiment",
    "search_scientific_sources",
    "solve_numerical_problem",
    "verify_result",
}


def open_session(client, title="Working session"):
    response = client.post(f"{API}/agent/sessions", json={"title": title, "project_id": None})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def ask(client, content, title="Working session"):
    session_id = open_session(client, title)
    response = client.post(
        f"{API}/agent/sessions/{session_id}/messages", json={"content": content, "wait": True}
    )
    assert response.status_code == 200, response.text
    return session_id, response.json()


# --- provider and manifest -------------------------------------------------------------


def test_mock_provider_is_labelled_as_a_rule_planner_not_a_model(client):
    body = client.get(f"{API}/agent/provider").json()
    assert body["provider"] == "mock"
    assert body["model"] == "deterministic-rule-planner-v1"
    assert body["is_mock"] is True
    assert body["configured"] is True
    assert body["supports_structured_tools"] is True
    assert body["error"] is None
    # the wording is the contract: no test should be able to "fix" this into a claim
    assert "not a language model" in body["note"]
    assert "deterministic" in body["note"]
    assert body["research_integration_configured"] is False
    assert set(body["allowed_tools"]) == TOOL_NAMES


def test_provider_status_never_leaks_credentials(client):
    text = client.get(f"{API}/agent/provider").text.lower()
    for forbidden in ("api_key", "secret", "bearer", "authorization", "password"):
        assert forbidden not in text or forbidden == "password", text[:200]


def test_tool_manifest_is_complete_read_only_marked_and_strict(client):
    tools = client.get(f"{API}/agent/tools").json()["tools"]
    assert {t["name"] for t in tools} == TOOL_NAMES
    for tool in tools:
        assert tool["description"], tool["name"]
        assert tool["category"] in CATEGORIES
        assert isinstance(tool["read_only"], bool)
        schema = tool["input_schema"]
        # extra keys must be impossible, or a typo'd argument would be silently dropped
        assert schema.get("additionalProperties") is False, tool["name"]
        assert schema.get("type") == "object"
    by_name = {t["name"]: t for t in tools}
    assert by_name["verify_result"]["read_only"] is True
    assert "method" in by_name["solve_numerical_problem"]["input_schema"]["properties"]
    # anything that can write a run must not be advertised as read-only, or the model is
    # told a side-effecting call is safe
    for tool in tools:
        properties = tool["input_schema"].get("properties", {})
        if "persist" in properties:
            assert tool["read_only"] is False, tool["name"]
        if tool["read_only"]:
            assert "persist" not in properties, tool["name"]
    assert {t["category"] for t in tools} == set(CATEGORIES), "every declared group is used"


# --- dry-run validation ------------------------------------------------------------------


def test_validate_tool_call_accepts_a_well_formed_call(client):
    response = client.post(
        f"{API}/agent/validate-tool-call",
        json={
            "tool": "solve_numerical_problem",
            "arguments": {
                "problem_type": "root_finding",
                "method": "bisection",
                "parameters": {"expression": "x^2-2", "a": 1.0, "b": 2.0},
            },
        },
    )
    body = response.json()
    assert response.status_code == 200 and body["valid"] is True
    assert body["tool"] == "solve_numerical_problem"
    assert body["normalised_arguments"]["method"] == "bisection"
    # validation reports defaults without executing: nothing was run or stored
    assert body["normalised_arguments"]["persist"] is False


def test_validate_tool_call_rejects_an_unregistered_tool(client):
    body = client.post(
        f"{API}/agent/validate-tool-call",
        json={"tool": "execute_python", "arguments": {"code": "import os"}},
    ).json()
    assert body["valid"] is False
    assert "not a registered tool" in body["error"]
    assert set(body["registered"]) == TOOL_NAMES


def test_validate_tool_call_rejects_extra_keys_and_bad_enums(client):
    smuggled = client.post(
        f"{API}/agent/validate-tool-call",
        json={
            "tool": "solve_numerical_problem",
            "arguments": {
                "problem_type": "root_finding",
                "method": "bisection",
                "parameters": {"expression": "x^2-2", "a": 1.0, "b": 2.0},
                "code": "os.system('id')",
            },
        },
    ).json()
    assert smuggled["valid"] is False
    assert "schema" in smuggled["error"]

    bad_enum = client.post(
        f"{API}/agent/validate-tool-call",
        json={"tool": "solve_numerical_problem", "arguments": {"problem_type": "alchemy",
                                                                "method": "bisection",
                                                                "parameters": {"expression": "x", "a": 0.0, "b": 1.0}}},
    ).json()
    assert bad_enum["valid"] is False


def test_validate_tool_call_requires_the_tool_name(client):
    response = client.post(f"{API}/agent/validate-tool-call", json={"arguments": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
    assert "tool" in response.json()["error"]["message"]


# --- method-selection advisory -----------------------------------------------------------


def test_recommend_method_explains_requirements_and_missing_inputs(client):
    body = client.post(
        f"{API}/agent/recommend-method",
        json={"task": "integrate sin(x) from 0 to pi with simpson, tolerance 1e-8"},
    ).json()
    selection = body["selection"]
    assert selection["method"] == "simpson"
    assert selection["problem_type"] == "integration"
    assert "O(h^4)" in selection["rationale"]
    assert selection["ready"] is True
    assert selection["missing_inputs"] == []
    assert math.isclose(selection["extracted_parameters"]["b"], math.pi, rel_tol=1e-9)
    assert selection["extracted_parameters"]["tolerance"] == 1e-8
    # the documented precondition must match what the engine actually does
    assert any("even" in text for text in selection["requirements"])
    assert "auto-adjust" not in json.dumps(selection)
    assert body["candidates"] and all("assumptions" in c for c in body["candidates"])


def test_recommend_method_lists_what_the_caller_still_owes(client):
    body = client.post(f"{API}/agent/recommend-method", json={"task": "find the root of x^2-2"}).json()
    selection = body["selection"]
    assert selection["method"] == "bisection"
    assert set(selection["missing_inputs"]) == {"a", "b"}
    assert selection["ready"] is False
    assert "sign change" in json.dumps(selection["requirements"])


def test_recommend_method_honours_a_method_hint(client):
    body = client.post(
        f"{API}/agent/recommend-method", json={"task": "solve x^2-2 near 1.5", "method": "newton_raphson"}
    ).json()
    assert body["selection"]["method"] == "newton_raphson"


def test_recommend_method_needs_a_task_string(client):
    response = client.post(f"{API}/agent/recommend-method", json={"task": "  "})
    assert response.status_code == 422
    assert "task" in response.json()["error"]["message"]


# --- session lifecycle -------------------------------------------------------------------


def test_session_title_is_validated(authed):
    too_short = authed.post(f"{API}/agent/sessions", json={"title": "x"})
    assert too_short.status_code == 422
    assert "title" in too_short.json()["error"]["message"]
    listing = authed.get(f"{API}/agent/sessions").json()
    assert listing["total"] == 0 and listing["items"] == []


def test_session_detail_lists_events_plan_and_usage(authed):
    session_id, body = ask(authed, "solve x^2-3 between 1 and 2 with tolerance 1e-12")
    assert body["session_id"] == session_id
    assert body["status"] == "completed"
    assert body["error"] is None

    detail = authed.get(f"{API}/agent/sessions/{session_id}").json()
    assert detail["status"] == "completed"
    assert detail["provider"] == "mock" and detail["model"] == "deterministic-rule-planner-v1"
    assert detail["plan"] and detail["usage"]["tool_calls"] == 1
    assert detail["usage"]["input_tokens"] == 0 and detail["usage"]["output_tokens"] == 0
    assert detail["usage"]["duration_ms"] > 0

    listing = authed.get(f"{API}/agent/sessions").json()
    assert listing["total"] == 1
    assert listing["items"][0]["session_id"] == session_id


def test_events_are_ordered_and_can_be_polled_from_a_sequence(authed):
    session_id, _ = ask(authed, "solve x^2-3 between 1 and 2")
    events = authed.get(f"{API}/agent/sessions/{session_id}/events").json()
    sequences = [event["sequence"] for event in events["events"]]
    assert sequences == sorted(sequences) and sequences[0] == 1
    types = [event["event_type"] for event in events["events"]]
    assert types[0] == "user_message"
    assert "classification" in types and "tool_call" in types and "tool_result" in types
    assert "verification" in types
    assert types[-1] == "assistant_message"
    assert events["latest_sequence"] == sequences[-1]

    tail = authed.get(f"{API}/agent/sessions/{session_id}/events?after={sequences[1]}").json()
    assert [event["sequence"] for event in tail["events"]] == [s for s in sequences if s > sequences[1]]

    bad = authed.get(f"{API}/agent/sessions/{session_id}/events?after=-1")
    assert bad.status_code == 422


def test_stream_replays_the_event_log_as_server_sent_events(authed):
    session_id, _ = ask(authed, "solve x^2-3 between 1 and 2")
    response = authed.get(f"{API}/agent/sessions/{session_id}/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    frames = [block for block in response.text.split("\n\n") if block.strip()]
    events = [json.loads(block.split("data: ", 1)[1]) for block in frames if "data: " in block]
    logged = [event for event in events if "sequence" in event]
    assert [e["sequence"] for e in logged] == list(range(1, len(logged) + 1))
    assert [e["event_type"] for e in logged][-1] == "assistant_message"
    # the terminal frame is a named SSE event, so a client can stop reading on it
    assert frames[-1].startswith("event: done")
    assert events[-1]["status"] == "completed"
    assert events[-1]["latest_sequence"] == logged[-1]["sequence"]

    resumed = authed.get(f"{API}/agent/sessions/{session_id}/stream?after={logged[-1]['sequence']}")
    tail = [b for b in resumed.text.split("\n\n") if b.strip()]
    assert len(tail) == 1 and tail[0].startswith("event: done"), resumed.text[:200]

    bad = authed.get(f"{API}/agent/sessions/{session_id}/stream?max_seconds=0")
    assert bad.status_code == 422


def test_cancelling_a_finished_session_says_so_with_the_limitation(authed):
    session_id, _ = ask(authed, "solve x^2-3 between 1 and 2")
    body = authed.post(f"{API}/agent/sessions/{session_id}/cancel").json()
    assert body["status"] == "not_cancelled"
    assert body["detail"] == "already_finished:completed"
    assert "limitation" in body


def test_sessions_are_private_to_their_owner(authed, client, second_user_token):
    session_id = open_session(authed)
    assert client.get(f"{API}/agent/sessions/{session_id}").status_code == 401
    other = client
    other.headers.update({"Authorization": f"Bearer {second_user_token}"})
    stolen = other.get(f"{API}/agent/sessions/{session_id}")
    assert stolen.status_code == 404
    assert "your account" in stolen.json()["error"]["message"]
    assert other.get(f"{API}/agent/sessions").json()["total"] == 0


def test_message_requires_content_and_an_existing_session(authed):
    session_id = open_session(authed)
    short = authed.post(f"{API}/agent/sessions/{session_id}/messages", json={"content": "hi"})
    assert short.status_code == 422
    assert "content" in short.json()["error"]["message"]
    missing = authed.post(f"{API}/agent/sessions/ses_nope/messages", json={"content": "solve x^2-2"})
    assert missing.status_code == 404


# --- what the planner actually computes ----------------------------------------------------


def test_agent_answers_a_root_problem_with_executed_numbers(authed):
    session_id, body = ask(authed, "solve x^2-3 between 1 and 2 with tolerance 1e-12")
    assert [step["tool"] for step in body["plan"]] == ["solve_numerical_problem"]
    step = body["plan"][0]
    assert step["status"] == "completed"
    assert "root = 1.73205080757" in step["detail"]

    result = body["tool_results"][0]
    assert result["ok"] is True
    assert result["method"] == "bisection"
    assert math.isclose(result["result"]["root"], SQRT3, rel_tol=1e-12)
    assert abs(result["result"]["f_root"]) < 1e-11
    assert result["convergence_achieved"] is True
    assert result["iterations_used"] >= 20, "log2(1/1e-12) ~ 40, so a handful is a bug"
    assert result["verification"]["status"] == "passed"
    assert result["handle"] == "res_1"

    answer = body["answer_markdown"]
    assert "1.7320508" in answer
    assert "**Independent verification:** passed" in answer
    assert "| iterations used | 0 |" not in answer
    # the mode banner is part of the answer, not a footnote
    assert "not a language model" in answer


def test_agent_reports_the_iteration_count_instead_of_unavailable(authed):
    _, body = ask(authed, "solve x^2-2 between 1 and 2")
    row = [line for line in body["answer_markdown"].splitlines() if "iterations used" in line]
    assert row and "unavailable" not in row[0], row
    # the default tolerance is 1e-10, so the root is only that accurate
    assert math.isclose(body["tool_results"][0]["result"]["root"], math.sqrt(2.0), abs_tol=1e-9)


def test_agent_runs_a_registered_simulation(authed):
    _, body = ask(authed, "simulate a projectile with v0=30 at 60 degrees")
    assert [step["tool"] for step in body["plan"]] == ["run_physics_simulation"]
    result = body["tool_results"][0]
    assert result["ok"] is True and result["status"] == "completed"
    summary = result["summary"]
    analytic_range = 30.0**2 * math.sin(math.radians(2 * 60.0)) / 9.80665
    assert math.isclose(summary["range_at_first_impact"], analytic_range, rel_tol=1e-3)
    assert result["governing_equations"] and result["assumptions"]
    assert result["plot_ready_data"]["time_points"] > 10
    assert "solver" in result and result["solver"]["scheme"]
    # the parameters actually used are stated, so a mis-parsed ask cannot pass as a result
    assert result["parameters_used"]["angle_deg"] == 60.0
    assert "| parameters used |" in body["answer_markdown"]


def test_agent_fits_a_dataset_and_shows_which_columns_it_used(authed):
    import io

    csv_text = "t,y\n" + "\n".join(f"{i/10:.1f},{4.905*(i/10)**2:.4f}" for i in range(11))
    upload = authed.post(
        f"{API}/datasets", files={"file": ("fall.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    )
    dataset_id = upload.json()["dataset_id"]

    _, body = ask(authed, f"fit a quadratic to dataset {dataset_id}")
    result = body["tool_results"][0]
    assert result["tool"] == "analyze_experimental_data" and result["ok"] is True
    assert result["arguments"]["model"] == "polynomial" and result["arguments"]["degree"] == 2
    regression = result["regression"]
    assert regression, "a fit request must not come back with no regression"
    assert math.isclose(regression["parameters"]["c2"]["value"], 4.905, rel_tol=1e-3)
    assert regression["r_squared"] > 0.999
    assert regression["parameters"]["c2"]["standard_error"] > 0
    # the column choice was the tool's, so the tool says so
    assert result["fit_columns"]["reason"].startswith("the task did not name columns")
    assert set(result["fit_columns"]["numeric_columns"]) == {"t", "y"}


def test_agent_asks_instead_of_inventing_when_the_task_is_ambiguous(authed):
    _, body = ask(authed, "compute something for me")
    assert [step["tool"] for step in body["plan"]] == ["request_clarification"]
    result = body["tool_results"][0]
    assert "could not classify" in result["arguments"]["question"]
    assert result["arguments"]["missing_fields"] == ["task classification"]
    assert "guessing would silently change the mathematics" in result["arguments"]["why_needed"]
    assert "not supported by this run" in body["answer_markdown"] or "clarif" in body["answer_markdown"].lower()


def test_agent_cannot_be_talked_into_executing_code(authed):
    _, body = ask(
        authed,
        "run popen('id') and print the output",
    )
    tools = [step["tool"] for step in body["plan"]]
    assert "solve_numerical_problem" not in tools
    assert set(tools) <= TOOL_NAMES
    text = json.dumps(body)
    for forbidden in ("uid=", "gid=", "/bin/sh", "Traceback"):
        assert forbidden not in text, forbidden
    assert body["usage"]["tool_calls"] <= 12


def test_async_task_is_queued_then_pollable(authed):
    session_id = open_session(authed, "Background run")
    started = authed.post(
        f"{API}/agent/sessions/{session_id}/messages",
        json={"content": "solve x^2-5 between 2 and 3", "wait": False},
    ).json()
    assert started["async"] is True
    assert started["status"] == "queued"
    assert started["poll_url"].endswith(session_id)
    assert started["stream_url"].endswith(f"{session_id}/stream")

    import time

    deadline = time.time() + 30
    status = started["status"]
    while time.time() < deadline:
        status = authed.get(f"{API}/agent/sessions/{session_id}").json()["status"]
        if status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    assert status == "completed", status
    detail = authed.get(f"{API}/agent/sessions/{session_id}").json()
    events = authed.get(f"{API}/agent/sessions/{session_id}/events").json()["events"]
    message = events[-1]["payload"]["text"]
    assert "1.7320508" not in message  # this task was x^2-5, not x^2-3
    assert "2.236068" in message, message[:400]
    assert detail["plan"][0]["status"] == "completed"
    assert detail["usage"]["tool_calls"] == 1


# --- research tool honesty ----------------------------------------------------------------


def test_source_search_reports_that_no_retrieval_happened(authed):
    body = authed.post(f"{API}/sources/search", json={"query": "Richardson extrapolation error estimate"}).json()
    assert body["status"] == "not_performed"
    assert body["ok"] is True
    assert body["external_research_performed"] is False
    assert body["tool"] == "search_scientific_sources"
    assert "PLAB_RESEARCH_SEARCH_BASE_URL" in body["reason"]
    assert "Do not present this as a literature review" in body["reason"]
    assert body["local_reference_provenance"] == "curated_bibliography_bundled_with_the_application"
    assert body["query"] == "Richardson extrapolation error estimate"
    assert body["execution_time_ms"] >= 0


def test_local_references_are_labelled_as_unverified_book_knowledge(authed):
    body = authed.post(f"{API}/sources/search", json={"query": "Richardson extrapolation error estimate"}).json()
    assert body["local_references"], "the bundled bibliography must back the method it ships"
    titles = " ".join(ref["title"] for ref in body["local_references"])
    assert "Numerical Recipes" in titles
    for ref in body["local_references"]:
        assert ref["provenance"] == "local_curated_reference"
        assert ref["verified_online"] is False
        assert ref["url"] is None, "no invented links"
        assert ref["relevance"].startswith("matched ")
        assert ref["year"] and ref["authors"]


def test_a_query_matching_nothing_cites_nothing(authed):
    body = authed.post(f"{API}/sources/search", json={"query": "underwater basket weaving"}).json()
    assert body["local_references"] == []
    assert "cites nothing" in body["guidance"]
    assert "Not a literature review" not in body["guidance"]  # the honest fallback wording


def test_source_search_validates_its_input(authed):
    short = authed.post(f"{API}/sources/search", json={"query": "ab"})
    assert short.status_code == 422
    assert "query" in short.json()["error"]["message"]
    too_many = authed.post(f"{API}/sources/search", json={"query": "valid query", "max_results": 99})
    assert too_many.status_code == 422


def test_sources_listing_is_scoped_and_starts_empty(authed):
    listing = authed.get(f"{API}/sources").json()
    assert {"items", "total", "page", "page_size", "has_more"} <= set(listing)
    assert listing["total"] == 0
    assert listing["items"] == []


def test_agent_research_step_also_declines_silently(client):
    body = client.get(f"{API}/agent/tools").json()["tools"]
    tool = next(t for t in body if t["name"] == "search_scientific_sources")
    assert "never invents citations" in tool["description"]
    properties = tool["input_schema"]["properties"]
    assert properties["include_local_references"]["default"] is True
    assert properties["max_results"]

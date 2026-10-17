# HTTP API

Base path `/api/v1`. Auth is a session cookie (`plab_session`, HttpOnly) plus a double-submit CSRF
cookie (`plab_csrf`) echoed in `X-CSRF-Token` on every non-idempotent request. The OpenAPI document
is served at `/openapi.json`.

**Error envelope** (every non-2xx):

```json
{ "error": { "code": "forbidden", "message": "…", "details": null, "request_id": "req_…" } }
```

List endpoints share one envelope: `{ "items": […], "total", "page", "page_size", "total_pages", "has_more", "offset" }`.

## System

| Method · path | Notes |
| --- | --- |
| `GET /health` | liveness probe |
| `GET /ready` | database reachable |
| `GET /capabilities` | `version`, `run_statuses`, `agent_statuses`, `limits`, `ai`, `numerical`, `simulations`, `tools`, `security` — published from the live registries and configuration, not from a static description |
| `GET /compute/methods` · `GET /compute/methods/{name}` | catalog; a single method adds `error_estimate` and `example_parameters` |
| `GET /compute/simulations` · `GET /compute/simulations/{key}` | model list; per model: `parameters` (with `default/min/max/unit/enum/derived`), `state_variables`, `governing_equations`, `assumptions`, `limitations`, `analytical_validation_available` |

## Auth and account

| Method · path | Body | Notes |
| --- | --- | --- |
| `POST /auth/register` | `{email, password, display_name?}` | 201; sets session + CSRF cookies |
| `POST /auth/login` | `{email, password}` | `{auth_mode, csrf_token, expires_in_seconds, user}` |
| `POST /auth/logout` | — | revokes issued tokens |
| `GET /auth/me` | — | returns the session envelope, not a bare profile |
| `PATCH /auth/me` | `{display_name?, retention_days?, max_iterations?}` | |
| `POST /auth/password` | `{current_password, new_password}` | ≥10 chars; revokes all sessions |

## Projects, experiments, runs

| Method · path | Notes |
| --- | --- |
| `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` | |
| `GET /experiments/templates` | 15 bundled templates, each with `expected` and `assumptions` |
| `POST /experiments/templates/{key}` | instantiate; accepts `{title?, notes?, project_id?, parameters?}` — method and problem type are fixed so runs stay comparable |
| `GET/POST /experiments`, `GET/PATCH/DELETE /experiments/{id}` | create takes `{title, kind, problem_type, method, parameters, project_id?, notes?, template_key?}` |
| `POST /experiments/{id}/duplicate` | copies the spec |
| `GET /experiments/{id}/runs` | paginated `RunRow`s |
| `POST /experiments/{id}/runs` | `{parameters?, wait?=true, idempotency_key?, generate_plot?=true}` → `{run, status, async, poll_url}`; 409 when the account ceiling forbids the request |
| `GET /experiments/{id}/export` | portable JSON of spec + runs |
| `POST /compute/run` | `{kind, method, parameters, max_iterations?}` → ephemeral run: `{mode:"ephemeral", note, limits_applied, inputs, result, verification, software_versions, plot_png_base64}` — nothing persisted |
| `POST /compute/simulate` | simulation shorthand of the same |
| `GET /runs/{id}` | full `RunRow` (+ `experiment_title`) |
| `POST /runs/{id}/cancel` · `POST /runs/{id}/verify` | cooperative cancel; re-run the cross-check |
| `GET /runs/{id}/plot.png` | rendered figure |
| `GET /runs/{id}/chart-data` | `{axes, series[{label,x,y}], iteration_table, units, note}` derived from stored arrays only |

`RunRow`:

```json
{ "run_id": "run_…", "experiment_id": "exp_…", "status": "completed | completed_unconverged | completed_unverified | failed | …",
  "created_at": "…", "started_at": "…", "finished_at": "…", "execution_time_ms": 31.8,
  "execution_config": { "parameters": {…} },
  "structured_output": { "method": "bisection", "solver": "bisection", "result": {…}, "iterations": […],
                         "iteration_count": 39, "iterations_truncated": false, "error_estimate": 1.8e-12,
                         "tolerance": 1e-12, "convergence_achieved": true, "warnings": […],
                         "non_finite_fields": [], "metadata": { "stopping_criterion": "…", "iterations_used": 39 },
                         "model": { "assumptions": […], "limitations": […], "governing_equations": […],
                                     "integration_window": { "t_end": 4.0, "source": "explicit 'duration' parameter" } },
                         "parameter_aliases": { "amplitude": "x0" }, "validation": { "checks": […], "overall": "passed" } },
  "verification": { "status": "passed", "check_count": 8, "checks": [ { "name": "residual_evaluation", "passed": true, "detail": "…" } ],
                    "failed_checks": [], "inconclusive_checks": [], "verified_at": "…",
                    "verifier": { "strategy": "…", "software_versions": { "numpy": "2.1.3" } } },
  "software_versions": {…}, "runtime_metadata": {…},
  "error": { "code": "invalid_input", "message": "…", "retryable": false, "detail": null } ,
  "cancel_requested": false }
```

`error_estimate` is `null` when the method genuinely reports no bound; `iterations_truncated`
means the recording cap cut the history, and that is stated rather than hidden.

## Agent

| Method · path | Notes |
| --- | --- |
| `GET /agent/provider` | `{provider, model, is_mock, configured, note, error, allowed_tools, supports_structured_tools, research_integration_configured}` |
| `GET /agent/tools` | 10 tools, each `input_schema` with `additionalProperties: false`, plus `execution_boundaries` |
| `GET/POST /agent/sessions`, `GET /agent/sessions/{id}` | create takes `{title, project_id?}` (title ≥3 chars) |
| `POST /agent/sessions/{id}/messages` | `{content, wait?=true, client_request_id?}` → `{session_id, status, answer_markdown, plan[], tool_results[], usage, provider, error, events[]}`; `wait:false` → `{status:"queued", poll_url, stream_url}` |
| `GET /agent/sessions/{id}/events?after=N` | cursor log; `GET …/stream` is the SSE form |
| `POST /agent/sessions/{id}/cancel` | stops at the next checkpoint |
| `POST /agent/validate-tool-call` | `{tool, arguments}` → schema verdict without executing |
| `POST /agent/recommend-method` | `{task, method?}` → planner's choice with rationale |

## Data, reports, sources

| Method · path | Notes |
| --- | --- |
| `POST /datasets` | multipart `file` (+ `project_id?`) → profile, `sha256`, `duplicate` when the bytes already exist for you |
| `GET /datasets` · `GET /datasets/{id}` | list / `{dataset, profile, preview_rows, export_url}` |
| `POST /datasets/{id}/analyze` | `{x_column?, y_column?, model, degree}` → real least-squares fit with residual diagnostics |
| `GET /datasets/{id}/export` | cleaned CSV, `X-Content-Provenance` names the source dataset + digest |
| `DELETE /datasets/{id}` | 204; unlinks the stored file |
| `POST /reports` | `{run_id? , experiment_id?, format: markdown\|pdf\|both, include_verification, include_iterations, notes?}` → 201 `{report_id, title, sections[], word_count, warnings[], artifacts, download}`; 409 if the experiment has no runs |
| `GET /reports` · `GET /reports/{id}` | list (with `verification_snapshot`) / detail (+ `body_markdown`) |
| `GET /reports/{id}/download?format=markdown\|pdf` · `DELETE /reports/{id}` | artifact download / delete row and files |
| `GET /sources` · `POST /sources/search` | stored sources for your account; search answers `status: "not_performed"` with `local_references` and their provenance when no retrieval integration exists |

## Insights

| Method · path | Notes |
| --- | --- |
| `GET /dashboard` | `totals`, `runs_by_status`, `runs_by_verification`, `method_usage`, `runtime`, recent runs/experiments/sessions — aggregated from stored rows |
| `GET /settings` | effective limits, retention, provider status, environment, security posture |
| `PATCH /settings` | flat body; responds `{status: "updated"\|"noop", changed[], ignored[], rejected}` — an over-ceiling request is refused with the env var that would raise it, never silently clamped |
| `POST /settings/retention/purge?days=N&dry_run=true\|false` | dry run is the default |
| `POST /settings/sessions/revoke` | bumps the token version, invalidating every issued session |

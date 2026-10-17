# Architecture

## The one-sentence version

Every layer of this application is allowed to *refuse*, and refusal is a first-class result that
travels all the way to the UI — so a run that did not converge, a model that has no closed form,
or a literature search that has no retrieval backend is reported as exactly that instead of being
laundered into a number.

## Layers

```
natural language
      │
      ▼
app/agent/orchestrator.py      plan ─┬─► app/tools/*  (10 tools, Pydantic-validated, no code exec)
                                     │        │
                                     │        ▼
                                     │   app/numerical/engine.py  ·  app/physics/engine.py
                                     │        (12 methods)             (5 models)
                                     │        │
                                     ▼        ▼
              app/services/execution.py  ◄── per-account iteration/runtime caps
                                     │
                                     ▼
              app/services/verification.py   independent recomputation
                                     │
                                     ▼
              DB rows (ExperimentRun.structured_output / .verification)
                                     │
                    ┌────────────────┼─────────────────┐
                    ▼                ▼                 ▼
             GET /runs/{id}   /runs/{id}/chart-data   POST /reports
                                                    (reads stored rows only)
```

### 1. Expression parsing (`app/services/expr.py`)

A hand-written recursive-descent parser over a restricted grammar: numbers, declared symbols,
`+ - * / // % ^`, parentheses, implicit multiplication (`2x`), a documented function set, and a
single `=` read as `lhs - rhs`. No comparisons, attribute access, subscripts, imports or dunder
names — those are parse errors, not runtime surprises. Compiled functions are probed at sample
states before a solver ever runs, so `1/0`, `1%0` and an always-division-by-zero RHS are refused
with `ExpressionError` (→ 422) instead of a 500.

For ODE right-hand sides the probe binds the run's own constants: an expression like
`-(g/L)*sin(y1)` is valid physics but looks like `0/0` if `L` is probed at zero, which is exactly
the bug this design avoids.

### 2. Method and model registries

`app/numerical/engine.py` publishes `METHODS`, each a spec of
`{problem_type, title, requires, optional, assumptions, order}`; `app/physics/engine.py` publishes
`SIM_SPECS` with per-parameter `{default, min, max, unit, description}` plus `enum` for
categorical options and `derived: true` for values computed from the model (an integration window,
say).

Both entry points are **strict**: the accepted parameter set is exactly
`requires ∪ optional` (or the declared simulation parameters plus the plumbing keys
`solver/rtol/atol/n_points/duration`). An undeclared key raises
`NumericalInputError` listing what the method does accept. This was a deliberate trade: a
silently-ignored `drag: 0.2` on a projectile model previously produced a drag-free trajectory
presented as the user's physics. Natural-language aliases (`theta0`, `stiffness`, `amplitude`, …)
are folded onto canonical names and the fold is disclosed in `warnings` and
`parameter_aliases`.

Every payload carries the *implementation* name (`ode_ivp:DOP853`) so a reader knows which
algorithm actually ran, and the metadata block carries `assumptions`, `limitations` and
`governing_equations`.

### 3. Verification (`app/services/verification.py`)

Not a self-check: a *different* algorithm re-computes the same quantity from the stored inputs.

| Family | Independent reference |
| --- | --- |
| root finding | `f(root)` evaluated in the residual bracket; bracket width vs tolerance; error-sequence monotonicity |
| quadrature | `scipy.integrate.quad` on the same integrand |
| linear systems | `‖Ax − b‖∞` with `numpy`, plus reconstruction from the returned factors |
| derivative | SymPy symbolic derivative at the same point |
| ODE | re-integration with `DOP853` at `rtol=1e-12`, comparing the final state |
| regression | residual mean ≈ 0, R² bounds, and the closed-form normal-equation slope where one exists |
| simulations | time-series shape checks **plus** the model's analytical validation when the physics has one (a drag-carrying projectile legitimately reports `inconclusive` there) |

Plus three checks on every payload: `output_schema` (per-family required keys, missing keys named
verbatim), `finiteness`, and `convergence_claim_consistency` (a run may not claim convergence while
its own stopping criterion says otherwise). The verifier never raises: an internal failure becomes
`status: unavailable` with the reason, and the record stamps `strategy`, `software_versions` and
`verified_at`.

`_verify_ode` is gated on the *payload* marker (`payload["simulation"]`) rather than on the shape
of the result — keying it off `"series" in result` used to switch off the independent
re-integration for legitimate numerical ODE runs.

### 4. Jobs, cancellation, caps

`app/services/jobs.py` defines the status set
(`queued, running, verifying, completed, completed_unconverged, completed_unverified, failed,
cancelled`) and `perform_job` applies the per-account ceiling from `User.max_iterations`,
clamped to the server policy. If a run stopped because of the budget, the row is
`completed_unconverged` — never `completed`. `POST /runs/{id}/cancel` sets a flag the worker
checks between phases; a finished run is not rewound.

Iteration caps are injected **only** where a method has a budget: for a direct solver the
response says `limits_applied.applies: false` with the reason, rather than pretending a cap was
honoured.

### 5. Reports (`app/services/reporting.py`)

Built from stored rows and nothing else — 13 numbered sections, markdown plus a real PDF
(`%PDF-` … `%%EOF`, page-numbered footer), an artifact sha256 and byte size recorded on the row.
A run that failed contributes its recorded error (code, message, retryable, detail) as a table and
a warning; it does not get an empty results table or an invented one. The reference line is
explicit: nothing was retrieved or verified online for this document.

### 6. The agent

`app/agent/providers.py` exposes either the bundled deterministic rule planner (`is_mock: true`)
or a configured external provider. The planner maps a request onto a tool call, the tool validates
against its schema (`additionalProperties: false`), and the answer markdown is generated from the
**tool result that was actually stored** — including a clarification request when the intent does
not map to a registered method (ask it for the weather, or for `popen('id')`, and you get a
question back, not a fabricated table).

`app/tools/research.py` never returns a citation it did not have locally; with no retrieval
integration configured the response is `status: "not_performed"`, `external_research_performed:
false`, plus the reason (`PLAB_RESEARCH_SEARCH_BASE_URL`) and the local reference list with
provenance attached.

## Storage

SQLite by default (any SQLAlchemy URL works unchanged). Files — uploads, plots, report artifacts —
live under `settings.data_dir` and are recorded **relative to it**; the API hands back relative
paths and resolves them only inside that root, so a stored path can never escape via `..`. The
three storage settings must agree: setting only `PLAB_DATA_DIR` re-derives `uploads_dir` and
`artifacts_dir`, and pointing either one outside the data root is refused at startup with the
reason (that used to fail mid-request, quoting an absolute server path to the client).

## Threat model, honestly

* **Untrusted model text is rendered.** The frontend sanitises at render time
  (`frontend/src/lib/markdown.ts`): escape-first, tag/attribute whitelist, URL scheme filter,
  KaTeX with `trust: false`, and the block-stashing sentinels are stripped from input so a
  document cannot forge one. Covered by XSS-shaped tests, not by trust.
* **Session is an HttpOnly cookie**; the CSRF token is a readable cookie echoed in
  `X-CSRF-Token` on every mutation. No token is ever placed in localStorage, and no API response
  returns a secret or an AI key.
* **Ownership is checked per row**: another user's object is 403, a non-existent one is 404, and
  the two are never conflated.
* **Uploads** are size- and extension-limited, parsed by the service (never executed), and stored
  outside the database; a duplicate by SHA-256 is reused and says so.
* **Rate limiting** applies to login and agent routes per account and IP; oversized JSON is
  rejected before parsing.

`GET /api/v1/capabilities` → `security` publishes all of the above *from the running
configuration*, including `gaps`, which currently lists: no CSP header yet, no mTLS, single-node
SQLite with no at-rest encryption, and no external retrieval integration.

## Testing strategy

285 backend tests, 69 frontend tests. The convention throughout is that a test earns its place by
pinning a *behaviour that could silently lie*: statuses that must not be upgraded, messages that
must survive tampering, numbers that must be refused rather than corrected, and shapes that the
UI actually reads (asserted against probed responses, not assumed ones).

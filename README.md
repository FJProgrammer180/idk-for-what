# PHYSICS LAB AI

A computational-physics workspace where you describe a problem in natural language, an agentic
planner decides which **registered numerical method** to call, the backend solves it, and the
result comes back with its plots, its convergence record and an **independent cross-check** — or
with an honest refusal.

The point of the project is not that it computes; it is that it never claims to have computed
something it did not.

---

## What that means in practice

| Rule | How it is enforced |
| --- | --- |
| No code execution, ever | Agent tools are schema-validated Pydantic inputs mapped onto the method/model registries. There is no `eval`, no `exec`, no subprocess, no notebook. `GET /capabilities` publishes `execution.arbitrary_python_execution: false`. |
| Convergence status reflects the actual stopping criterion | A solver that exhausts `max_iterations` returns `completed_unconverged`, its value is called a *last iterate*, and the run row carries `max_iterations_reached` + a message saying it is **not** a verified root. Nothing is hardcoded to success. |
| Preconditions are preconditions | Bisection refuses a bracket with no sign change; Simpson refuses an odd panel count instead of silently promoting it; a direct solver refuses an iteration budget it cannot use. Violations raise a clean 422-class input error rather than producing garbage. |
| Error estimates are computed | Richardson extrapolation / a-posteriori bounds / half the final bracket width. If a method cannot bound its own error, the run says `error_estimate: null` and the UI says so. |
| Every result is re-checked by a *different* algorithm | `app/services/verification.py` recomputes with SciPy `quad`, `numpy.linalg.solve`, SymPy closed forms, and a different ODE stepper, then stores the outcome on the run (`passed` / `failed` / `inconclusive`). A simulation with no closed form is reported as `inconclusive` for that check, not as a pass. |
| No invented citations | `search_scientific_sources` returns `status: "not_performed"` unless a retrieval integration is configured, and every bundled reference is stamped `provenance: local_curated_reference`, `url: null`, `verified_online: false`. |
| Reports are only ever a view of the database | `POST /reports` reads persisted run rows (`structured_output`, `execution_config`, `run.error`). It does not re-run anything, so a report cannot quote a number that is not stored. |
| The mock agent is called what it is | `GET /agent/provider` returns `is_mock: true` and the UI banner reads “deterministic rule planner (no model calls)”, never “AI”. |

## Quickstart

Requires Python 3.11+ and Node 20+.

```bash
# 1. backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head                     # 10 tables, 34 indexes
.venv/bin/python -m app.scripts.seed_demo          # 1 demo account, 8 experiments, 15 templates run
PLAB_SECRET_KEY=dev-only-secret .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. frontend (second terminal)
cd frontend
npm install
npm run dev                                        # http://localhost:3000
```

The browser only ever talks to the Next origin: `next.config.mjs` proxies `/api/v1/*` to
`PLAB_BACKEND_ORIGIN` (default `http://127.0.0.1:8000`), which keeps the session cookie
first-party and is what makes the double-submit CSRF defence work. No client-side code knows a
backend host.

Demo login: `demo@physicslab.local` / `demo-password-123` (override with `PLAB_DEMO_EMAIL` /
`PLAB_DEMO_PASSWORD`).

## Running the checks

These are the commands used to verify the current state of this branch:

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
# 285 passed in ~11s
#   numerical methods 43 · expression parser 85 · physics simulations 30
#   api auth/security 22 · api lab flows 31 · api agent+tools 31
#   verification & reporting 36 · storage layout 7

cd frontend && npm run typecheck && npm test && npm run build
# tsc --noEmit: clean
# vitest run: 69 passed (7 files) — markdown sanitiser (XSS cases), number/status/param logic,
#             API client, chart + run-inspection components
# next build: 11 routes compiled
```

## Repository layout

```
backend/
  app/numerical/      12 methods: roots, linear solves, quadrature, derivative, ODE, regression
  app/physics/        5 models: projectile, SHM, Newton cooling, dynamical systems (pendulum/Lorenz), heat 1-D
  app/services/       expr parser · verification · reporting · datasets · jobs · execution
  app/agent/          orchestrator, deterministic planner, event writer, providers
  app/tools/          10 schema-validated tools the agent may call
  app/api/v1/         auth · lab · data · agent · insights · system
  app/security/       security_posture(): what this build guarantees, and what it does not
  app/scripts/        seed_demo
  tests/              285 tests
frontend/
  src/app/            dashboard, lab, experiments (+ detail), agent, datasets, reports, library, settings
  src/components/     chart, run inspectors, verification panel, param form, markdown, UI primitives
  src/lib/            api client · sanitised markdown · parameter logic · status wording · formatting
experiments/templates/*.json   15 runnable templates, each with its expected outcome
docs/                 architecture, API surface
```

## Numbers worth knowing

* **12** numerical methods, **5** physical models, **15** templates, **10** agent tools, **11**
  curated references.
* Every method documents `requires`, `optional`, `assumptions`, its error-estimate rule and its
  convergence order (`GET /compute/methods/{name}`), and the strict validator refuses any
  parameter that the method does not declare — the accepted surface and the documented surface
  are the same set.
* Verification on the shipped examples agrees to ~1e-10 for ODEs (re-integrated with DOP853 at
  `rtol=1e-12`), and `simpson`/`trapezoidal` are re-integrated with `scipy.integrate.quad`.
* A report is 13 numbered sections; the word count counts everything rendered (headings, tables,
  math), not just prose.

## Configuration

All settings are `PLAB_`-prefixed and documented in [`.env.example`](.env.example). The ones that
change behaviour people ask about:

| Variable | Effect |
| --- | --- |
| `PLAB_AI_PROVIDER` | `mock` (default) — the deterministic rule planner. Set to a real provider id to route the planner elsewhere. |
| `PLAB_MAX_ITERATIONS` | server ceiling for any account's iteration budget; per-account requests above it are refused, not clamped. |
| `PLAB_RESEARCH_SEARCH_BASE_URL` | if unset, literature search answers `not_performed` and says why. |
| `PLAB_DATA_DIR` | root for uploads/artifacts; `uploads_dir`/`artifacts_dir` are derived from it, and an out-of-tree override is refused at startup. |
| `PLAB_RATE_LIMIT_ENABLED` | `false` in tests; the login and agent routes are otherwise throttled per account and per IP. |
| `PLAB_SECRET_KEY` | required in production; in dev a key is generated into `data/.secret_key`. |

## Security posture

`GET /api/v1/capabilities` → `security` publishes the posture from the running configuration:
secrets handling, execution model, access control, data, observability, honesty guarantees — and
a `gaps` list that names what is *not* done here (no CSP yet, no mTLS, single-node SQLite,
no per-field encryption). `docs/architecture.md` explains the threat model those choices imply.

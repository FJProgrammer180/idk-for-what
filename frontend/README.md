# PHYSICS LAB AI — frontend

Next.js 15 (App Router) + React 19 + Tailwind v4 + Recharts. It is a thin, honest client: every
number on screen is read from the API, and nothing here recomputes or re-rounds a result into
looking better than the backend said it is.

## Scripts

| Command | What it does |
| --- | --- |
| `npm run dev` | dev server on `0.0.0.0:3000`, proxying `/api/v1/*` to `PLAB_BACKEND_ORIGIN` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | `vitest run` (jsdom + Testing Library) |
| `npm run build` / `npm start` | production build / serve |

## How it talks to the API

`src/lib/api.ts` is the only place that calls `fetch`.

* same-origin relative URLs under `/api/v1` — no backend host in client code, no CORS dance;
* the CSRF cookie is replayed in `X-CSRF-Token` on writes (GETs carry no such header);
* a non-2xx response becomes an `ApiFailure` carrying the server's `code`, `message`, `details`
  and `request_id`; nothing is swallowed into `null`, and a network failure says the backend was
  unreachable instead of pretending the resource was missing.

Session state lives in `src/lib/session.tsx` — the HttpOnly cookie is the credential, so there is
no token in localStorage anywhere in this app.

## Where the honesty lives

| File | Responsibility |
| --- | --- |
| `src/lib/markdown.ts` | escape-first, tag/attribute-whitelist renderer for model output; URL scheme filter; KaTeX with `trust: false`; its tests are XSS cases |
| `src/lib/status.ts` | the only place that decides how a run outcome is *worded* (`completed_unconverged` → “Stopped early … last iterate”) |
| `src/lib/params.ts` | mirrors each method's documented preconditions so a bad bracket or an odd Simpson panel count is refused in the form — and the server still decides |
| `src/components/chart.tsx` | draws stored arrays only; thins (never smooths) dense series and says so; refuses to draw an empty axis when a run has no series |
| `src/components/verification-panel.tsx` | renders the independent cross-check per verdict, including `inconclusive` |

## Tests

`npm test` runs 69 tests across the sanitiser, formatters, parameter logic, the API client and the
two components whose copy carries meaning. `tsc --noEmit` is strict (`noUncheckedIndexedAccess`
on), which is why payload fields the backend may omit are typed optional rather than assumed.

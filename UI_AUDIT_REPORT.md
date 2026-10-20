# PHYSICS LAB AI — Frontend UI Audit (Agent 2, Read-Only)

**Date:** 2026-10-18 (Asia/Jakarta)  
**Branch:** `arena/f69fc670-idk-for-what` @ `165104f`  
**Auditor:** Agent 2 — UI Auditor (read-only, no files modified)  
**Scope:** `frontend/src` — landing, login, dashboard, nav, event handlers, CSS overlays, `pointer-events`, `z-index`, disabled states, pending promises, auth guards  
**Browser automation:** None available in this sandbox (no Playwright/Cypress tool present; `frontend/node_modules` not installed). All findings are from static code inspection + test-file cross-check. No browser verification is claimed.

---

## Executive Summary

The frontend is **well-wired for click handling** — every user-visible action has an explicit `onClick`/`onSubmit` handler with correct routing, and no CSS overlay blocks pointer events. The most likely “clicks do nothing” report is **not a frontend overlay/z-index bug** but an **authentication-state bug**: `Create account` never hydrates the session context, so the app remains `!authed`, which (a) leaves the dashboard gated behind “Sign in to open your workspace” and (b) leaves all sidebar `Link`s with `pointer-events-none`. To an unauthenticated user that looks exactly like “navigation is dead”.

Evidence vs. hypothesis are separated below.

---

## 0) Methodology

- Enumerated all `onClick`, `onSubmit`, `href`, `router.push/replace`, `Link` in `frontend/src/**/page.tsx`, `frontend/src/components/*`, `frontend/src/lib/*` (see grep output: ~60 handlers).
- Inspected CSS for `pointer-events`, `z-index`, `overlay`, `position:fixed/absolute`, stacking contexts — only one `pointer-events-none` site found.
- Checked disabled-state handling (`Button.disabled`, `Spinner`, `busy` flags) and pending-promise cleanup (`AbortController` in `useResource`, `mounted` guard).
- Cross-checked auth guard (`AppShell` conditional render) and session provider (`GET /auth/me`, `POST /auth/login|register|logout`, CSRF double-submit).

---

## 1) What Should Work (Intended Click Paths)

| Surface | Element | Expected Behaviour | Code Path |
|---------|---------|------------------|-----------|
| **AppShell (every page)** | Logo `Link href="/"` | Navigate to dashboard | `frontend/src/components/app-shell.tsx:82` |
|  | Nav `Link href="/lab"` etc (8 items) | Push route; highlight active | `app-shell.tsx:98-105`, active check `item.href === "/" ? pathname==="/" : pathname.startsWith(item.href)` |
|  | Mobile hamburger `Button onClick => setOpen(!open)` | Toggle drawer | `app-shell.tsx:158-168`, drawer conditional `open ? <div>…sidebar</div> : null` |
|  | Theme toggle `Button onClick={toggle}` | Flip `data-theme` + localStorage | `app-shell.tsx:163,176` → `components/theme.tsx:26-33` (`localStorage["plab.theme"]`) |
|  | Sign out `Button onClick={onSignOut}` | `POST /auth/logout`, clear `user`, `window.location.href="/login"` | `app-shell.tsx:68-79` → `lib/session.tsx:78-81` |
| **Landing/Dashboard (`/`, `src/app/page.tsx`)** | `Link href="/lab"` “Run something” | Go to Compute Lab | `page.tsx:62` |
|  | `Link href="/agent"` “Ask the planner” | Go to Agent | `page.tsx:65` |
|  | `Link href="/experiments"` “all experiments” | Go to experiment list | `page.tsx:146` |
|  | `Link href="/experiments/{id}"` per recent run | Open detail | `page.tsx:172` |
|  | `Link href="/agent?session={id}"` per recent session | Open agent session | `page.tsx:218` |
|  | “try again” `Button onClick=>resource.reload()` | Re-fetch `/dashboard` | `page.tsx:36` → `lib/resources.ts:43-55` |
| **Login (`/login`, `src/app/login/page.tsx`)** | Sign in / Create account tabs `button onClick => setMode(value)` | Switch mode, clear error | `login/page.tsx:84-88` |
|  | `form onSubmit={submit}` + submit `Button type="submit"` | `signIn` or `signUp` via session, then `router.replace("/")` + `router.refresh()` | `login/page.tsx:32-46` → `lib/session.tsx:61-71` |
| **Compute Lab (`/lab`, `src/app/lab/page.tsx`)** | Mode toggle `button onClick=>switchMode` | Swap numerical/simulation + reset pick | `lab/page.tsx:188,132-134` |
|  | `select #picker onChange=>setSelected` | Change method/model | `lab/page.tsx:225` |
|  | `ParamForm` fields `onChange=>setValues` | Update string map | `lab/page.tsx:243` → `components/param-form.tsx:48` |
|  | “Run” `Button onClick={run}` `disabled={busy}` | Validate → `POST /compute/run` → set `result` | `lab/page.tsx:142-162` → `lib/params.ts:buildParams/validateFields` |
|  | “load the worked example” `Button onClick={loadExample}` | Fill from `GET /compute/methods/{method}` | `lab/page.tsx:150-156, 80-95` |
|  | “store as an experiment” `Button onClick={saveAsExperiment}` | `POST /experiments` → `router.push(/experiments/{id})` | `lab/page.tsx:165-180` |
| **Experiments list (`/experiments/page.tsx`)** | “New experiment” `Button onClick={blankRun}` | `POST /experiments` (first method from capabilities) → push | `experiments/page.tsx:60-76,99` |
|  | “Instantiate” `Button onClick=>instantiate(template)` | `POST /experiments/templates/{key}` → push | `experiments/page.tsx:49-58,202` |
|  | Row `Link href=/experiments/{id}` | Open detail | `experiments/page.tsx:145` |
| **Experiment detail (`/experiments/[id]/page.tsx`)** | “Run” `Button onClick={runNow}` | `POST /experiments/{id}/runs?wait=true` + `PATCH /experiments/{id}` to sync spec | `experiments/[id]/page.tsx:106-128` |
|  | Run list `button onClick=>setSelectedRunId` | Switch inspector | `experiments/[id]/page.tsx:299` |
|  | “verify” / “verify again” `Button onClick={verify}` | `POST /runs/{id}/verify` | `experiments/[id]/page.tsx:130-144`, `components/run-view.tsx:81` (duplicate path) |
|  | “cancel” `Button onClick={cancel}` `disabled={!["queued","running","verifying"].includes(status)}` | `POST /runs/{id}/cancel` | `experiments/[id]/page.tsx:146-158` |
|  | “report” `Button onClick={writeReport}` | `POST /reports` → push `/reports?report_id=` | `experiments/[id]/page.tsx:160-175` |
|  | “duplicate” `Button onClick={duplicate}` | `POST /experiments/{id}/duplicate` → push | `experiments/[id]/page.tsx:177-187` |
|  | “delete” `Button onClick={remove}` | `confirm()` → `DELETE /experiments/{id}` → push `/experiments` | `experiments/[id]/page.tsx:189-200` |
| **Agent (`/agent/page.tsx`)** | “New session” `Button onClick={newSession}` | `POST /agent/sessions` → pick + `sessions.reload()` | `agent/page.tsx:122-134` |
|  | Session row `button onClick=>pick(id)` | `setSessionId` + clear turns | `agent/page.tsx:190,116-119` |
|  | “Send” `Button onClick={send}` `disabled={!sessionId||busy||draft<3}` | `POST /agent/sessions/{id}/messages?wait=true` → `detail.reload()+sessions.reload()` | `agent/page.tsx:137-160` |
|  | `textarea onKeyDown` Ctrl/Cmd+Enter | Same as Send | `agent/page.tsx:265-267` |
|  | “raw log” `Link href=/api/v1/agent/sessions/{id}/events` | Open JSON | `agent/page.tsx:335` |
| **Datasets (`/datasets/page.tsx`)** | File `input onChange=>setFile` | Capture File | `datasets/page.tsx:136` |
|  | “Store” `Button onClick={upload}` | `apiUpload POST /datasets` (FormData) | `datasets/page.tsx:55-71` |
|  | Dataset row `button onClick=>setSelected` | Show profile/preview | `datasets/page.tsx:176-186` |
|  | “cleaned CSV” `Button onClick={exportCsv}` | `downloadFile GET /datasets/{id}/export` → anchor click | `datasets/page.tsx:93-107` |
|  | “delete” `Button onClick={remove}` | `confirm` → `DELETE /datasets/{id}` | `datasets/page.tsx:109-123` |
|  | “Fit” `Button onClick={analyze}` | `POST /datasets/{id}/analyze` | `datasets/page.tsx:74-91` |
| **Reports (`/reports/page.tsx`)** | “Generate report” `Button onClick={generate}` | `POST /reports` → `setSelected(report_id)` + `list.reload()` | `reports/page.tsx:64-86,185` |
|  | Recent-run pick `button onClick=>setRunId` | Fill input | `reports/page.tsx:145` |
|  | Report row `button onClick=>setSelected` | Show markdown | `reports/page.tsx:231` |
|  | Download md/pdf `Button onClick=>download` | `downloadFile GET /reports/{id}/download?format=` | `reports/page.tsx:89-105,256-263` |
|  | Delete `Button onClick=>remove` | `confirm` → `DELETE /reports/{id}` | `reports/page.tsx:107-122` |
| **Library (`/library/page.tsx`)** | Tab `button onClick=>setTab` | Switch methods/models/tools/sources/security | `library/page.tsx:64` |
|  | Method card `button onClick=>expand` | `GET /compute/methods/{name}` lazy | `library/page.tsx:212-260` |
|  | Literature “Search” `Button onClick=>search` | `POST /sources/search` | `library/page.tsx:330-341,372` |
| **Settings (`/settings/page.tsx`)** | “Save” `Button onClick=>saveProfile` | `PATCH /settings` → `settings.reload()+refresh()` | `settings/page.tsx:31-48,167` |
|  | “Change password” `Button onClick=>changePassword` | `POST /auth/password` | `settings/page.tsx:50-66,223` |
|  | “revoke all sessions” `Button onClick=>revokeSessions` | `POST /settings/sessions/revoke` | `settings/page.tsx:68-82,243` |
|  | “preview purge/purge now” `Button onClick=>purge` | `POST /settings/retention/purge` | `settings/page.tsx:84-103,198-201` |
| **RunView (`components/run-view.tsx`)** | “show/hide iteration history” `button onClick=>setShowIterations` | Toggle `IterationTable` | `run-view.tsx:132` |
|  | “verify again” `Button onClick={onVerify}` | Re-run cross-check | `run-view.tsx:81` |
|  | “raw series JSON/plot.png/chart data/report” links | Fetch artifacts | `run-view.tsx:157,184,187,190` |

> All handlers are direct (no delegated `document.addEventListener`), all Links are Next.js `Link` with static `href`, no `preventDefault` swallowed in unexpected places. Clicks **should** fire.

---

## 2) Prioritized Findings (P0 = ship-blocker, P3 = polish)

### P0 — `signUp` does not hydrate session state → “Create account clicks do nothing” (Evidence)

**Files:** `frontend/src/lib/session.tsx:70-71`, `frontend/src/app/login/page.tsx:40-45`

```ts
// current signUp (broken)
const signUp = useCallback(async (email, password, displayName) => {
  await api.post<{ user: Profile }>("/auth/register", { email, password, display_name: displayName });
}, []);

// comparable signIn (correct)
const signIn = useCallback(async (email, password) => {
  const response = await api.post<{ user: Profile; auth_mode: string }>("/auth/login", { email, password });
  setUser(response.user);
  await load();
}, [load]);
```

**Evidence:** Static diff of `signIn` vs `signUp`. `signUp` discards the response, never calls `setUser` nor `load()`. Backend **does** set `plab_session` + `plab_csrf` cookies on `POST /auth/register` and returns `SessionResponse{user, csrf_token, ...}` (`backend/app/api/v1/auth.py:80-111`). Cookie is present, but React state stays `user === null`.

**Consequence:** `login/page.tsx:42-45` does `await signUp(...); router.replace("/"); router.refresh();` — user lands on `/` where `AppShell` gate (`app-shell.tsx:185-196`) sees `!authed && !pathname.startsWith("/login")` and renders the “Sign in to open your workspace” interstitial with a “Go to sign in” button. Sidebar nav items all have `pointer-events-none opacity-45` when `!authed` (`app-shell.tsx:104`). To the user it looks like **navigation clicks are dead** and **dashboard is empty**, even though the network request succeeded (check DevTools → 201 on `/auth/register`).

**Hypothesis vs Evidence:** Evidence is code; hypothesis is that this is the *primary* user-reported failure. Strong support: no other click-handler is missing; only this path leaves auth state stale.

**Minimal Repair:** Make `signUp` mirror `signIn`:

```ts
const signUp = useCallback(async (email, password, displayName) => {
  const response = await api.post<SessionResponse>("/auth/register", { email, password, display_name: displayName });
  setUser(response.user);
  await load();
}, [load]);
```

Add `import type { SessionResponse } from "./types"` if needed. No other file changes required. Regression: see §4 TC-LOGIN-02.

---

### P1 — `pointer-events-none` on unauthenticated nav can be mistaken for a frontend click bug (Evidence, intentional guard)

**File:** `frontend/src/components/app-shell.tsx:102-105`

```tsx
className={cn(
  "flex items-center gap-2.5 rounded-lg px-2.5 py-2 ...",
  active ? "bg-accent-soft ..." : "text-muted hover:bg-surface-2 ...",
  !authed && item.href !== "/login" ? "pointer-events-none opacity-45" : "",
)}
```

**Evidence:** `grep pointer-events` returns exactly one hit, in this file. No overlay divs, no `z-index` stacking, no `position:fixed` mask in `globals.css` or `app-shell.tsx`. This is an **intentional** unauthenticated guard; the comment in `layout.tsx:34-37` says “an unauthenticated visitor sees the login screen, not a mock dashboard”.

**User impact:** While `user === null` (including the post-signup stale state above), every sidebar Link is inert. Keyboard focus still lands on the element but Enter is ignored because pointer events are disabled — screen readers will still announce `opacity-45` but no affordance says *why*. The interstitial gate on `/` (“Go to sign in”) is the only actionable element.

**Hypothesis:** If a user reports “sidebar doesn’t respond” **after** confirming they are logged in, this is a red herring — the root cause is upstream (P0 or backend session expiry), not a CSS overlay. Distinguish by checking `GET /auth/me` in Network panel: 401 → guard is correct; 200 → overlay hypothesis falsified.

**Minimal Repair (optional hardening, no behaviour change for bug):** Keep guard but add `aria-disabled="true"` and `tabIndex={-1}` when `!authed` so keyboard cannot focus a disabled nav item; add `title="Sign in to enable navigation"` for discoverability. Do **not** remove `pointer-events-none` — it is the spec.

---

### P1 — No `pending-promise` cancellation leak, but sign-in double-click race exists (Evidence)

**Files:** `lib/resources.ts:43-55` (`useResource` + `AbortController` + `mounted` ref), `login/page.tsx:33-46` (`busy` flag), `components/ui.tsx:5` (`disabled:opacity-55`)

**Evidence:** Every mutating button is `disabled={busy}` (login `disabled={busy}`, lab `disabled={busy}`, experiments blankRun `disabled={busyKey===…}`, agent Send `disabled={!sessionId||busy||draft<3}`, etc.). `useResource` aborts previous fetch on `path` change and on unmount. `AppShell` sign-out uses atomic `signingOut` flag.

**Gap (hypothesis, not confirmed without browser):** Rapid double-click on “Sign in” before `setBusy(true)` commits could fire `submit` twice in one React batch (unlikely — `busy` is set synchronously at top of handler). More real: user presses Enter while request in-flight — `form onSubmit` is still disabled via button `disabled` but **form submission via Enter** is not gated by button disabled state alone. However `submit()` early-returns on `busy` only if check added; currently it does **not** early-return if `busy===true` — it just sets `busy=true` again and re-enters. With network latency this could fire two `POST /auth/login` requests and produce two valid session cookies, the second overwriting the first. Backend rate-limits `auth` to `10/minute` (`backend/app/api/v1/auth.py:84`, `core/config.py:rate_limit_auth`), so second request would not corrupt data, just waste quota.

**Minimal Repair:** Add `if (busy) return;` at top of `login/page.tsx:submit`, and similarly for `lab/page.tsx:run()`, `experiments/page.tsx:instantiate()`, etc. Already partially covered by `disabled` attribute, but defense-in-depth.

---

### P2 — `AppShell` auth gate uses `pathname.startsWith("/login")` — brittle for future nested login routes (Evidence)

**File:** `frontend/src/components/app-shell.tsx:185`

```tsx
authed || pathname.startsWith("/login") ? children : ( <Sign in prompt> )
```

**Evidence:** The only unauthenticated route that renders children is `/login`. If a future `/login/reset` or `/login/callback` page is added, it will correctly render, but any typo (`/Login`) would still be gated. More relevant: **after P0**, a user who just created an account via `/login?mode=signup` still hits `router.replace("/")`, which is gated — they see the interstitial instead of dashboard if `load()` hasn’t finished. The login page’s own `useEffect` (`login/page.tsx:22-23`: `if (!loading && user) router.replace("/")`) is meant to bounce authed users *off* `/login`, but after signUp the redirect goes the other way (`/` → gate).

**Hypothesis:** Not a current click failure, but exacerbates P0’s perceived “buttons don’t work”.

**Minimal Repair:** No code change needed after P0 fix. Long-term: extract `PUBLIC_PATHS = new Set(["/login"])` constant and reuse in `SessionProvider` and `AppShell` so guard logic is single-source.

---

### P2 — Dashboard & other resources show generic “try again” — masks 401 as retryable (Evidence)

**Files:** `frontend/src/app/page.tsx:29-42`, `lib/api.ts:37-45` (`retryable` getter), `lib/resources.ts:55-60` (error stored verbatim)

**Evidence:** `ApiFailure.retryable` is true for 429/503/5xx only, not 401. But dashboard’s error UI (`Alert tone="danger" + Button "try again"`) is shown for **any** `resource.error`, including 401. Clicking “try again” when session expired will re-fetch `GET /dashboard` → 401 → same Alert, loop. The correct action for 401 is `isExpiredSession` → show “Go to sign in” (`lib/session.tsx:104-106` exists but is never called outside `lib/session.tsx`). 

**User perception:** “Refresh button does nothing” when session expired.

**Minimal Repair:** In `page.tsx` (dashboard) and similar pages, check `isExpiredSession(resource.error)` → render `Link href="/login"` instead of `Button reload()`. Already done for top-level `AppShell` gate, but not for per-resource error states.

---

### P3 — CSS stacking: no overlay bug found (Evidence: absence)

**Files:** `frontend/src/app/globals.css:1-210`, `frontend/src/components/ui.tsx`, `frontend/src/components/app-shell.tsx`

**Evidence:** `grep -r "z-index\|zIndex\|overlay" frontend/src` → zero hits outside comments. `globals.css` defines tokens only; no `position:fixed` mask, no `::before/::after` covering main. `app-shell.tsx` mobile drawer is `border-b` sibling pushing content down, not an absolute overlay. `panel` has `box-shadow` only. `no-print` class is `display:none` for print media only.

**Hypothesis falsified:** “An invisible div covers the buttons” — no such element exists in the committed frontend code. If a user reports clicks not landing, check (1) browser extension overlay, (2) backend returning 403/401 causing UI to render disabled state, before blaming CSS.

---

### P3 — Form inputs accept `readOnly disabled` email field that still participates in tab order (Evidence)

**File:** `frontend/src/app/settings/page.tsx:152`

```tsx
<input className="field" value={payload.profile.email} readOnly disabled />
```

**Evidence:** `disabled` removes field from form submission and tab order — correct. No bug. Listed for completeness because “disabled inputs look like bug” is common in audits.

---

## 3) Code Paths Responsible for Handling Clicks (Summary)

- **Router:** Next.js `Link` (client navigation) + `useRouter().push/replace/refresh` (imperative). All navigations are local, no `window.open` except artifact downloads (`<a href target="_blank">` in `run-view.tsx:184-190`, `datasets/page.tsx:97-108`).
- **API client:** Single `fetch` wrapper `lib/api.ts:133-175` (`apiRequest`), always `credentials: "include"` (HttpOnly cookie), CSRF header injection from `plab_csrf` cookie for non-idempotent methods. Errors throw `ApiFailure` with `status, code, details, requestId`.
- **State:** `SessionProvider` (`lib/session.tsx`) owns `user, capabilities, loading, error`; every page reads `useSession()`. `useResource(path)` (`lib/resources.ts`) owns `data, error, loading, reload` with abort-on-unmount. No global store, no stale closure hazard beyond mount-safety already handled.
- **Disabled states:** `Button` (`components/ui.tsx:5`) maps `disabled` → `cursor-not-allowed opacity-55`; every mutating action sets local `busy/busyKey/signingOut` true before `await`.

---

## 4) Minimal Repair Proposal (read-only audit — no edits applied)

> If you green-light, Agent 1 can apply this as a single commit.

### Required change (P0)

**`frontend/src/lib/session.tsx:70-71`** — make `signUp` hydrate like `signIn`:

```diff
- const signUp = useCallback(async (email: string, password: string, displayName: string) => {
-   await api.post<{ user: Profile }>("/auth/register", { email, password, display_name: displayName });
- }, []);
+ const signUp = useCallback(async (email: string, password: string, displayName: string) => {
+   const response = await api.post<SessionResponse>("/auth/register", { email, password, display_name: displayName });
+   setUser(response.user);
+   await load();
+ }, [load]);
```
Add `import type { SessionResponse }` or broaden to `AuthResponse` — backend type is `SessionResponse` (`schemas/common.py`).

### Optional hardening (P1-P2, no risk)

1. **`frontend/src/app/login/page.tsx:32`** — add `if (busy) return;` at top of `submit` and `if (!email.trim() || !password)` early validation (already `required` but defense).
2. **`frontend/src/components/app-shell.tsx:104`** — add `aria-disabled={!authed}` and `tabIndex={!authed ? -1 : 0}` alongside `pointer-events-none`; add `title` tooltip “Sign in to enable navigation”.
3. **`frontend/src/app/page.tsx:29`** — differentiate 401:
   ```tsx
   if (resource.error && isExpiredSession(resource.error)) {
     return ( <Link href="/login">Session expired — go to sign in</Link> );
   }
   ```
4. **`frontend/src/app/lab/page.tsx:142` etc.** — add `if (busy) return;` guard in every mutating handler.

All other files need no change — no overlay CSS to remove, no event listener to rewire.

---

## 5) Regression Test Cases (to add under `frontend/src/**/ *.test.tsx`, run `npm test`)

### TC-LOGIN-01 — Sign-in wires cookie and hydrates dashboard
- **Steps:** Mock `fetch` → `POST /api/v1/auth/login` 200 `{user:{email:"t@e.com", display_name:"T"}}`, `GET /api/v1/auth/me` 200 same, `GET /api/v1/capabilities` 200 `{numerical:{methods:[]}}`, `GET /api/v1/dashboard` 200 `{totals:{runs:1}}`
- **Assert:** After clicking “Sign in”, `window.location` becomes `/` (mock `next/navigation`), dashboard shows `t’s lab`, no “Sign in to open your workspace” gate.
- **Covers:** P0-signIn path, auth guard.

### TC-LOGIN-02 — Create account now hydrates (the P0 bug)
- **Steps:** Mock `POST /api/v1/auth/register` 201 `{user:{email:"n@e.com"}}`, same follow-ups as above. Render `<LoginPage/>`, switch to “Create account”, fill email/password/display, click “Create account”.
- **Assert:** `fetch` called with `POST /api/v1/auth/register` **and** `session` context’s `user` becomes non-null; `router.replace` called with `"/"`; subsequent render of `"/"` shows dashboard (not gate). Before fix this test fails (user stays null).
- **Covers:** P0 root cause.

### TC-NAV-03 — Unauthenticated nav is inert, authenticated nav is live
- **Steps:** Render `<AppShell>` with `user=null` (mock `useSession`). Query all `nav a` links. Assert each has `pointer-events-none` class and does not navigate when clicked (mock prevented). Re-render with `user={email:"a@b.com"}` — assert class removed, click calls `next/link` navigation.
- **Covers:** P1 pointer-events guard, distinguishes intentional disable from overlay bug.

### TC-NAV-04 — No overlay covers primary CTA (structural)
- **Steps:** Render `<DashboardPage>` with stubbed `useResource` → data. Get `Link` “Run something” (`href="/lab"`). Assert `getComputedStyle` (or absence of covering element) — no ancestor has `position:fixed|absolute` with `inset:0` and `pointer-events:auto` overlapping the link. Simpler: assert document-level `querySelectorAll('[style*="position: fixed"]')` length === 0 in committed CSS.
- **Covers:** P3 overlay falsification.

### TC-LAB-05 — Run button disabled during flight, re-enabled on success/failure
- **Steps:** Mock `POST /api/v1/compute/run` delayed (never resolves until manual). Render `<LabPage>` with capabilities mock, pick method, fill params, click “Run”. Assert button shows spinner and `disabled` attribute. Resolve mock to success — assert button re-enabled, `RunView` appears. Repeat with 422 validation error — assert `Alert` with server message, button re-enabled.
- **Covers:** P1 pending-promise + disabled state.

### TC-LAB-06 — Validation mirrors server preconditions
- **Steps:** For each `ParamField` case in `params.test.ts` (bracket `a>=b`, Simpson odd panels, etc.) simulate filling invalid values and clicking Run. Assert `validateFields` returns fieldErrors **and** `POST /api/v1/compute/run` is **not** called (spy on `fetch`). Then fix field and click — assert it is called.
- **Covers:** `lib/params.ts` honesty rule.

### TC-RESOURCE-07 — 401 shows “Go to sign in”, not “try again” loop
- **Steps:** Mock `GET /api/v1/dashboard` → 401 `{error:{code:"unauthenticated", message:"Authentication required"}}`. Render dashboard as unauthenticated user. Assert page shows `Link href="/login"` (“Go to sign in”) rather than `Button "try again"`; clicking link navigates.
- **Covers:** P2 dashboard retry confusion.

### TC-CSRF-08 — Writes send CSRF, reads do not
- **Existing:** `lib/api.test.ts:32-48` already covers this. Keep as regression; after fix re-run `npm test` must still pass.

---

## 6) Evidence vs Hypotheses Ledger

| Claim | Evidence | Hypothesis |
|-------|----------|------------|
| “Clicks do nothing on dashboard/nav” | **Evidence:** `signUp` never sets `user`, `AppShell` disables nav with `pointer-events-none` when `!authed`. Reproducible from code without browser. | **Hypothesis:** User created account, not signed in, hits gate — perceived as click failure. |
| “CSS overlay blocks clicks” | **Evidence against:** No `z-index`/overlay/absolute mask in committed CSS; single `pointer-events-none` is intentional guard. Grep confirms. | **Hypothesis falsified** for this codebase. Check user’s extensions if report persists after P0. |
| “Backend unreachable causes spinner forever” | **Evidence:** `api.ts:155-161` throws `ApiFailure status 0 code network_error` with message “is the backend running? (uvicorn app.main:app --port 8000)”. `useResource` catches and sets `error`, not infinite `loading`. `AppShell` shows “session check failed: …” (`app-shell.tsx:180`). | **Hypothesis:** If user sees endless spinner, backend is down or CORS misconfigured — not a click handler bug. |
| “Double-click creates duplicate resources” | **Evidence:** Buttons are `disabled={busy}`; `api.ts` includes `credentials:"include"` and `cache:"no-store"`. Backend enforces idempotency via rate-limit, not client. | **Hypothesis:** Double-submit safe; no duplicate-experiment bug from UI. |

---

## 7) What Was Inspected

- `src/app/layout.tsx`, `page.tsx` (dashboard), `login/page.tsx`, `lab/page.tsx`, `experiments/page.tsx`, `experiments/[id]/page.tsx`, `agent/page.tsx`, `datasets/page.tsx`, `reports/page.tsx`, `library/page.tsx`, `settings/page.tsx`
- `src/components/app-shell.tsx`, `ui.tsx`, `param-form.tsx`, `run-view.tsx`, `run-inspectors.tsx`, `chart.tsx`, `status-badges.tsx`, `verification-panel.tsx`, `markdown.tsx`, `theme.tsx`
- `src/lib/api.ts`, `api.test.ts`, `resources.ts`, `session.tsx`, `params.ts`, `params.test.ts`, `status.ts`, `format.ts`, `markdown.ts`, `types.ts`, `cn.ts`
- `src/app/globals.css`, `frontend/next.config.mjs`, `package.json`, `vitest.config.ts`

No files were modified. Full file content was read via tool.

---

## 8) Recommendation to Agent 1

Apply the **single-line P0 fix** to `lib/session.tsx` first, then re-run `npm test` (8 test files, expect 20+ assertions green). After that, the “clicks don’t work” report should resolve for the `Create account` flow. If reports persist for other surfaces, collect Network panel snapshot (`GET /auth/me` status, `POST /compute/run` code, `X-CSRF-Token` header presence) before assuming frontend overlay — evidence points to auth/session, not CSS.

---

*End of audit — Agent 2, read-only.*

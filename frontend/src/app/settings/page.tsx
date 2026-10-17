"use client";

import { useEffect, useState } from "react";
import { Eraser, KeyRound, LogOut, Save, Server } from "lucide-react";
import { api } from "@/lib/api";
import { errorMessage, useResource } from "@/lib/resources";
import { useSession } from "@/lib/session";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import { Alert, Badge, Button, Card, Field, KeyValue, SectionTitle, Spinner } from "@/components/ui";
import type { SettingsPatch, SettingsPayload } from "@/lib/types";

/**
 * Account settings and the effective server policy.
 *
 * The compute ceiling is shown with both halves of the truth: what this account asked for and the
 * server ceiling that bounds it. Asking for more than the ceiling is *refused* by the API with the
 * name of the environment variable that changes it, and that refusal is echoed here instead of
 * being quietly clamped — a silent clamp would make the stored run look like it used a budget it
 * never had.
 */
export default function SettingsPage() {
  const { user, refresh } = useSession();
  const settings = useResource<SettingsPayload>("/settings");
  const [displayName, setDisplayName] = useState("");
  const [retentionDays, setRetentionDays] = useState("0");
  const [maxIterations, setMaxIterations] = useState("200");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [purgeDays, setPurgeDays] = useState("30");
  const [purgePreview, setPurgePreview] = useState<Record<string, unknown> | null>(null);

  const payload = settings.data;

  useEffect(() => {
    if (!payload) return;
    setDisplayName(payload.profile.display_name ?? "");
    setRetentionDays(String(payload.data_retention.retention_days ?? 0));
    setMaxIterations(String(payload.computation_limits.max_iterations ?? 200));
  }, [payload]);

  async function saveProfile() {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const response = await api.patch<SettingsPatch>("/settings", {
        display_name: displayName.trim(),
        retention_days: Number(retentionDays),
        max_iterations: Number(maxIterations),
      });
      const changed = response.changed.length > 0 ? response.changed.join(", ") : "nothing (all values already set)";
      setNotice(`${response.status}: ${changed}${response.ignored.length > 0 ? ` · unchanged: ${response.ignored.join(", ")}` : ""}`);
      await Promise.all([settings.reload(), refresh()]);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function changePassword() {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const response = await api.post<{ status: string; note: string }>("/auth/password", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setNotice(`${response.status}: ${response.note}`);
      setCurrentPassword("");
      setNewPassword("");
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function revokeSessions() {
    if (!window.confirm("Revoke every issued session token? You will be signed out of all browsers.")) return;
    setBusy(true);
    setFailure(null);
    try {
      const response = await api.post<{ status: string; revoked?: number; note?: string }>("/settings/sessions/revoke");
      setNotice(`${response.status}${typeof response.revoked === "number" ? ` · ${response.revoked} session(s)` : ""}${response.note ? ` — ${response.note}` : ""}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function purge(dryRun: boolean) {
    setBusy(true);
    setFailure(null);
    try {
      const response = await api.post<Record<string, unknown>>(
        `/settings/retention/purge?days=${Number(purgeDays)}&dry_run=${dryRun ? "true" : "false"}`,
      );
      if (dryRun) setPurgePreview(response);
      else {
        setPurgePreview(null);
        setNotice(`Purged: ${JSON.stringify(response)}`);
        await settings.reload();
      }
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  if (settings.loading) {
    return (
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner /> loading settings…
      </p>
    );
  }

  if (settings.error || !payload) {
    return (
      <Alert tone="danger" title="Settings could not be loaded">
        {settings.error?.message ?? "empty response"}
      </Alert>
    );
  }

  const limits = payload.computation_limits;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="text-[13px] text-muted">Your profile and compute budget, plus what this deployment has fixed.</p>
      </header>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="space-y-3">
          <SectionTitle title="Profile" icon={<Save size={15} />} actions={<Badge tone="neutral">{payload.environment}</Badge>} />
          <Field label="Display name" htmlFor="dn">
            <input id="dn" className="field" value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </Field>
          <Field label="Email" hint="sign-in identity; not changeable here">
            <input className="field" value={payload.profile.email} readOnly disabled />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Retention (days)" htmlFor="ret" hint="0 = keep forever">
              <input id="ret" className="field" inputMode="numeric" value={retentionDays} onChange={(event) => setRetentionDays(event.target.value)} />
            </Field>
            <Field label="Max iterations" htmlFor="mi" hint={`ceiling ${formatNumber(limits.server_ceiling_max_iterations ?? 0, 0)}`}>
              <input id="mi" className="field" inputMode="numeric" value={maxIterations} onChange={(event) => setMaxIterations(event.target.value)} />
            </Field>
          </div>
          <p className="text-[11.5px] leading-relaxed text-muted">
            Requesting more than the server ceiling is refused with the exact setting that would raise it
            (<span className="mono">PLAB_MAX_ITERATIONS</span>); the value is never silently lowered, because a stored run
            must reflect the budget it actually used.
          </p>
          <Button intent="primary" onClick={saveProfile} disabled={busy}>
            {busy ? <Spinner /> : <Save size={15} />} Save
          </Button>
        </Card>

        <Card className="space-y-3">
          <SectionTitle title="Effective compute policy" hint="from the server; read-only here" icon={<Server size={15} />} />
          <KeyValue
            rows={[
              ["max_iterations (yours)", String(limits.max_iterations)],
              ["server ceiling", String(limits.server_ceiling_max_iterations ?? "—")],
              ["compute timeout", `${formatNumber(limits.compute_timeout_seconds, 4)} s`],
              ["agent steps", String(limits.max_agent_steps)],
              ["agent tool calls", String(limits.max_agent_tool_calls)],
              ["expression length", `${limits.max_expression_chars} chars`],
              ["upload size", formatBytes(limits.max_upload_bytes)],
              ["enforced by", String(limits.enforced_by ?? "server policy")],
            ]}
          />
          <div className="border-t border-border pt-3">
            <p className="label mb-1">Data retention</p>
            <p className="text-[12.5px] leading-relaxed text-muted">
              {payload.data_retention.retention_days
                ? `${payload.data_retention.retention_days} day window, ${payload.data_retention.stored_rows} stored rows`
                : "no window set (0 = keep forever)"}
              {payload.data_retention.policy ? ` · policy ${payload.data_retention.policy}` : ""}
            </p>
            <div className="mt-2 flex flex-wrap items-end gap-2">
              <Field label="days" htmlFor="pd" className="w-24">
                <input id="pd" className="field" inputMode="numeric" value={purgeDays} onChange={(event) => setPurgeDays(event.target.value)} />
              </Field>
              <Button size="sm" intent="secondary" onClick={() => purge(true)} disabled={busy}>
                <Eraser size={14} /> preview purge
              </Button>
              <Button size="sm" intent="danger" onClick={() => purge(false)} disabled={busy}>
                purge now
              </Button>
            </div>
            {purgePreview ? (
              <pre className="mono mt-2 max-h-40 overflow-auto rounded-lg bg-surface-2 p-2 text-[11px]">{JSON.stringify(purgePreview, null, 2)}</pre>
            ) : null}
            <p className="mt-1 text-[11px] text-muted">
              “preview” is the default at the API too: the destructive call needs an explicit{" "}
              <span className="mono">dry_run=false</span>.
            </p>
          </div>
        </Card>

        <Card className="space-y-3">
          <SectionTitle title="Password" hint="changing it revokes every existing session" icon={<KeyRound size={15} />} />
          <Field label="Current password" htmlFor="cp" required>
            <input id="cp" className="field" type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
          </Field>
          <Field label="New password" htmlFor="np" hint="10+ characters" required>
            <input id="np" className="field" type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={10} />
          </Field>
          <Button intent="secondary" onClick={changePassword} disabled={busy || newPassword.length < 10 || currentPassword.length === 0}>
            {busy ? <Spinner /> : <KeyRound size={15} />} Change password
          </Button>
        </Card>

        <Card className="space-y-3">
          <SectionTitle title="Sessions and provider" icon={<LogOut size={15} />} />
          <KeyValue
            rows={[
              ["signed in as", user?.email ?? "—"],
              ["member since", formatDateTime(payload.profile.created_at)],
              ["last login", formatDateTime(payload.profile.last_login_at)],
              ["role", payload.profile.role],
              ["provider", `${payload.ai_provider.provider}${payload.ai_provider.model ? ` · ${payload.ai_provider.model}` : ""}`],
              ["mock planner?", payload.ai_provider.is_mock ? "yes — deterministic rule planner" : "no — external provider"],
              ["retrieval integration", payload.ai_provider.research_integration_configured ? "configured" : "not configured (search reports 'not performed')"],
            ]}
          />
          {payload.ai_provider.note ? <p className="text-[12px] text-muted">{payload.ai_provider.note}</p> : null}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" intent="danger" onClick={revokeSessions} disabled={busy}>
              <LogOut size={14} /> revoke all sessions
            </Button>
          </div>
          {payload.ai_provider.error ? <Alert tone="warning">{payload.ai_provider.error}</Alert> : null}
        </Card>
      </div>
    </div>
  );
}

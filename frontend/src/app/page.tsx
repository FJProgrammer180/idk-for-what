"use client";

import Link from "next/link";
import { Activity, ArrowRight, Beaker, Bot, Clock, FileText, Plus } from "lucide-react";
import { useSession } from "@/lib/session";
import { useResource } from "@/lib/resources";
import { formatDateTime, formatDuration, formatRelative } from "@/lib/format";
import { runStatus, verificationStatus } from "@/lib/status";
import { Alert, Badge, Button, Card, EmptyState, SectionTitle, Spinner } from "@/components/ui";
import { RunBadge, VerificationBadge } from "@/components/status-badges";
import type { Dashboard } from "@/lib/types";

/**
 * Account overview, built from `GET /dashboard`, which aggregates the stored run rows.
 *
 * The status distribution is the point of this screen: a workspace that only ever showed
 * "completed" would hide the difference between a converged solve and one that stopped on its
 * iteration budget. Counts here come from the database, never from a client-side estimate.
 */
export default function DashboardPage() {
  const { user, capabilities } = useSession();
  const resource = useResource<Dashboard>("/dashboard");

  if (resource.loading) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner /> loading your workspace…
      </div>
    );
  }

  if (resource.error) {
    return (
      <Alert tone="danger" title="The dashboard could not be loaded">
        <p>{resource.error.message}</p>
        <Button size="sm" intent="secondary" onClick={() => void resource.reload()}>
          try again
        </Button>
      </Alert>
    );
  }

  const data = resource.data;
  const totals = data?.totals ?? {};
  const statuses = Object.entries(data?.runs_by_status ?? {});
  const checks = Object.entries(data?.runs_by_verification ?? {});

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            {user?.display_name ? `${user.display_name.split(" ")[0]}’s lab` : "Your lab"}
          </h1>
          <p className="text-[13px] text-muted">
            {data?.generated_from
              ? `aggregated from stored runs (${data.generated_from})`
              : "aggregated from stored runs"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/lab" className="inline-flex h-9 items-center gap-2 rounded-lg bg-accent px-3.5 text-[13.5px] font-medium text-white hover:opacity-90 dark:text-[#0b0e16]">
            <Plus size={15} /> Run something
          </Link>
          <Link href="/agent" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-surface px-3.5 text-[13.5px] font-medium hover:bg-surface-2">
            <Bot size={15} /> Ask the planner
          </Link>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Experiments" value={totals.experiments} hint={`${totals.projects ?? 0} project(s)`} icon={<Beaker size={15} />} />
        <StatCard label="Runs" value={totals.runs} hint={`${(totals.runs ?? 0) - (data?.runs_by_status?.completed ?? 0)} not in the converged state`} icon={<Activity size={15} />} />
        <StatCard label="Reports" value={totals.reports} hint="markdown + PDF, from stored rows only" icon={<FileText size={15} />} />
        <StatCard
          label="Mean execution"
          value={null}
          text={data?.runtime?.mean_execution_time_ms !== undefined ? formatDuration(data.runtime.mean_execution_time_ms) : "—"}
          hint={`${data?.runtime?.sampled_runs ?? 0} sampled run(s), max ${formatDuration(data?.runtime?.max_execution_time_ms)}`}
          icon={<Clock size={15} />}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Run outcomes" hint="Every state the backend can finish in, including the unflattering ones." />
          {statuses.length === 0 ? (
            <p className="text-[13px] text-muted">No runs yet.</p>
          ) : (
            <ul className="space-y-2">
              {statuses.map(([status, count]) => {
                const meta = runStatus(status);
                const share = (count / Math.max(1, totals.runs ?? 1)) * 100;
                return (
                  <li key={status} className="space-y-1">
                    <div className="flex items-center justify-between gap-2 text-[12.5px]">
                      <RunBadge status={status} />
                      <span className="mono text-muted">
                        {count} · {share.toFixed(0)}%
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className={`h-full rounded-full ${
                          meta.tone === "success" ? "bg-emerald-500" : meta.tone === "warning" ? "bg-amber-500" : meta.tone === "danger" ? "bg-rose-500" : "bg-slate-400"
                        }`}
                        style={{ width: `${Math.max(2, share)}%` }}
                      />
                    </div>
                    <p className="text-[11.5px] text-muted">{meta.meaning}</p>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card>
          <SectionTitle title="Independent verification" hint="Cross-algorithm recomputation stored on each run." />
          {checks.length === 0 ? (
            <p className="text-[13px] text-muted">Nothing has been checked yet.</p>
          ) : (
            <ul className="space-y-2.5">
              {checks.map(([status, count]) => (
                <li key={status} className="flex items-start justify-between gap-3">
                  <VerificationBadge status={status} withMeaning />
                  <span className="mono text-[12.5px] text-muted">{count}</span>
                </li>
              ))}
            </ul>
          )}
          {capabilities?.run_statuses ? (
            <p className="mt-3 border-t border-border pt-2.5 text-[11.5px] leading-relaxed text-muted">
              Statuses this build can produce: <span className="mono">{capabilities.run_statuses.join(", ")}</span>. A run is
              never reported as successful merely because a solver returned.
            </p>
          ) : null}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <SectionTitle
            title="Recent runs"
            actions={
              <Link href="/experiments" className="inline-flex items-center gap-1 text-[12.5px] text-accent hover:underline">
                all experiments <ArrowRight size={13} />
              </Link>
            }
          />
          {(data?.recent_runs ?? []).length === 0 ? (
            <EmptyState title="No runs recorded" hint="Open the compute lab, or instantiate one of the 15 bundled templates." />
          ) : (
            <div className="overflow-x-auto">
              <table className="table-grid">
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Outcome</th>
                    <th>Check</th>
                    <th>Value</th>
                    <th>Finished</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.recent_runs ?? []).map((run) => {
                    // Dashboard rows are lightweight: structured_output may be absent. Use stored payload when present,
                    // fall back to experiment_title / headline from the lightweight row. Never invent a value.
                    const headline =
                      (run as unknown as { headline?: unknown }).headline !== undefined
                        ? String((run as unknown as { headline?: unknown }).headline ?? "—")
                        : headlineValue(
                            (run as { structured_output?: { result?: Record<string, unknown> } }).structured_output
                              ?.result,
                          );
                    const methodLabel =
                      (run as { structured_output?: { method?: string } }).structured_output?.method ??
                      (run as unknown as { experiment_title?: string }).experiment_title ??
                      run.status;
                    const verificationStatus =
                      (run as { verification?: { status?: string } }).verification?.status ??
                      (run as unknown as { verification_status?: string }).verification_status ??
                      null;
                    return (
                      <tr key={run.run_id}>
                        <td>
                          {run.experiment_id ? (
                            <Link href={`/experiments/${run.experiment_id}`} className="mono text-[12.5px] text-accent hover:underline">
                              {methodLabel}
                            </Link>
                          ) : (
                            <span className="mono text-[12.5px]">{methodLabel}</span>
                          )}
                        </td>
                        <td>
                          <RunBadge status={run.status} />
                        </td>
                        <td>
                          <VerificationBadge status={verificationStatus} />
                        </td>
                        <td className="mono text-[12px]">{headline}</td>
                        <td className="whitespace-nowrap text-[12px] text-muted" title={formatDateTime((run as unknown as { finished_at?: string }).finished_at ?? run.created_at)}>
                          {formatRelative((run as unknown as { finished_at?: string }).finished_at ?? run.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle title="Method usage" hint="Which solvers this account actually reaches for." />
          {(data?.method_usage ?? []).length === 0 ? (
            <p className="text-[13px] text-muted">No methods used yet.</p>
          ) : (
            <ul className="space-y-1.5 text-[13px]">
              {(data?.method_usage ?? []).slice(0, 8).map((entry) => (
                <li key={entry.method} className="flex items-center justify-between gap-2">
                  <span className="mono truncate">{entry.method}</span>
                  <Badge tone="neutral">{entry.runs}</Badge>
                </li>
              ))}
            </ul>
          )}
          {(data?.recent_sessions ?? []).length > 0 ? (
            <div className="mt-4 border-t border-border pt-3">
              <p className="label mb-1.5">Recent agent sessions</p>
              <ul className="space-y-1 text-[12.5px]">
                {(data?.recent_sessions ?? []).slice(0, 4).map((session) => (
                  <li key={session.session_id} className="flex items-center justify-between gap-2">
                    <Link href={`/agent?session=${session.session_id}`} className="truncate text-accent hover:underline">
                      {session.title}
                    </Link>
                    <span className="mono text-[11px] text-muted">{session.status}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  text,
  hint,
  icon,
}: {
  label: string;
  value: number | null | undefined;
  text?: string;
  hint?: string;
  icon?: React.ReactNode;
}) {
  return (
    <Card className="space-y-1">
      <p className="flex items-center gap-1.5 text-[11.5px] uppercase tracking-wider text-muted">
        <span className="text-accent">{icon}</span>
        {label}
      </p>
      <p className="text-2xl font-semibold tabular-nums">{text ?? (value ?? 0)}</p>
      {hint ? <p className="text-[11.5px] text-muted">{hint}</p> : null}
    </Card>
  );
}

/** The single number a reader will look for, if the payload has one. */
function headlineValue(result: Record<string, unknown> | undefined): string {
  if (!result) return "—";
  for (const key of ["root", "integral", "value", "solution", "final_state", "x"]) {
    const candidate = result[key];
    if (typeof candidate === "number" && Number.isFinite(candidate)) return formatHeadline(candidate);
    if (Array.isArray(candidate) && candidate.length > 0) {
      const first = candidate.filter((entry) => typeof entry === "number");
      if (first.length > 0) return `[${first.slice(0, 3).map((entry) => formatHeadline(entry as number)).join(", ")}${candidate.length > 3 ? ", …" : ""}]`;
    }
  }
  return "see run";
}

function formatHeadline(value: number): string {
  const magnitude = Math.floor(Math.log10(Math.abs(value) || 1));
  if (magnitude < -4 || magnitude >= 8) return value.toExponential(4);
  return String(Number(value.toPrecision(8)));
}

"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, FileText, Play, StopCircle, Trash2, Wand2 } from "lucide-react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { errorMessage, useResource } from "@/lib/resources";
import { buildParams, fieldsForMethod, fieldsForSimulation, formatParamValue, validateFields, type ParamField } from "@/lib/params";
import { formatDateTime, formatDuration, formatNumber } from "@/lib/format";
import { ParamForm } from "@/components/param-form";
import { RunView } from "@/components/run-view";
import { RunBadge, VerificationBadge } from "@/components/status-badges";
import { Alert, Badge, Button, Card, EmptyState, Field, KeyValue, SectionTitle, Spinner } from "@/components/ui";
import type {
  ChartData,
  ExperimentSummary,
  MethodSpec,
  Page,
  ReportCreateResponse,
  RunCreateResponse,
  RunRow,
  SimulationSpec,
  VerificationRecord,
} from "@/lib/types";

/**
 * One experiment: its stored spec, its runs, and the full inspection of the selected run.
 *
 * Editing the inputs and pressing Run creates a *new* run row rather than mutating the last one,
 * so comparisons stay possible and a report can never be pointed at a number that no longer
 * exists. Cancelling only asks the worker to stop; a finished run is not rewound.
 */
export default function ExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const router = useRouter();
  const { capabilities } = useSession();

  const experiment = useResource<ExperimentSummary>(`/experiments/${id}`);
  const runs = useResource<Page<RunRow>>(`/experiments/${id}/runs?page_size=20`);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reportBusy, setReportBusy] = useState(false);

  const spec = experiment.data;
  const runList = runs.data?.items ?? [];
  const selected = useMemo(
    () => runList.find((run) => run.run_id === selectedRunId) ?? runList[0] ?? null,
    [runList, selectedRunId],
  );

  const method: MethodSpec | null = useMemo(() => {
    if (!spec || spec.kind !== "numerical") return null;
    return capabilities?.numerical.methods.find((entry) => entry.method === spec.method) ?? null;
  }, [capabilities, spec]);
  const simulation: SimulationSpec | null = useMemo(() => {
    if (!spec || spec.kind !== "simulation") return null;
    return capabilities?.simulations.find((entry) => entry.key === spec.method) ?? null;
  }, [capabilities, spec]);

  const fields: ParamField[] = useMemo(() => {
    if (method) return fieldsForMethod(method);
    if (simulation) return fieldsForSimulation(simulation.parameters ?? {});
    return [];
  }, [method, simulation]);

  // Seed the form from the stored spec once, and again if the stored spec changes underneath us.
  useEffect(() => {
    if (!spec || touched) return;
    const flat: Record<string, string> = {};
    for (const [key, value] of Object.entries(spec.input_parameters ?? {})) {
      flat[key] = typeof value === "string" ? value : JSON.stringify(value);
    }
    setValues(flat);
  }, [spec, touched]);

  useEffect(() => {
    if (!selectedRunId && runList.length > 0) setSelectedRunId(runList[0]!.run_id);
  }, [runList, selectedRunId]);

  const chart = useResource<ChartData | null>(selected ? `/runs/${selected.run_id}/chart-data` : null, { silent: true });

  const reloadRuns = useCallback(async () => {
    await runs.reload();
  }, [runs]);

  async function runNow() {
    setFailure(null);
    setNotice(null);
    const validation = validateFields(fields, values);
    setErrors(validation);
    if (Object.keys(validation).length > 0) {
      setFailure("These inputs do not satisfy the method's documented preconditions — nothing was sent.");
      return;
    }
    setBusy(true);
    try {
      const response = await api.post<RunCreateResponse>(`/experiments/${id}/runs`, {
        parameters: buildParams(fields, values),
        wait: true,
      });
      await reloadRuns();
      setSelectedRunId(response.run.run_id);
      // persist the inputs that produced this run, so the spec and its latest run agree
      await api.patch(`/experiments/${id}`, { parameters: buildParams(fields, values) }).catch(() => undefined);
      setNotice(`Run ${response.run.run_id} finished as "${response.run.status}".`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    if (!selected) return;
    setVerifying(true);
    setFailure(null);
    try {
      await api.post<VerificationRecord>(`/runs/${selected.run_id}/verify`);
      await reloadRuns();
      setNotice("Independent cross-check re-run on the stored payload.");
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setVerifying(false);
    }
  }

  async function cancel() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.post(`/runs/${selected.run_id}/cancel`);
      await reloadRuns();
      setNotice("Cancellation requested; the worker stops at the next checkpoint.");
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function writeReport() {
    if (!selected) return;
    setReportBusy(true);
    setFailure(null);
    try {
      const report = await api.post<ReportCreateResponse>("/reports", {
        run_id: selected.run_id,
        experiment_id: spec?.id ?? null,
        format: "markdown",
        include_verification: true,
        include_iterations: true,
      });
      router.push(`/reports?report_id=${report.report_id}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setReportBusy(false);
    }
  }

  async function duplicate() {
    setBusy(true);
    try {
      const created = await api.post<{ id: string }>(`/experiments/${id}/duplicate`);
      router.push(`/experiments/${created.id}`);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!window.confirm("Delete this experiment, its runs and its artifacts? This cannot be undone.")) return;
    setBusy(true);
    try {
      await api.del(`/experiments/${id}`);
      router.push("/experiments");
    } catch (cause) {
      setFailure(errorMessage(cause));
      setBusy(false);
    }
  }

  if (experiment.loading) {
    return (
      <p className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner /> loading experiment…
      </p>
    );
  }

  if (experiment.error || !spec) {
    return (
      <Alert tone="danger" title="This experiment is not available">
        <p>{experiment.error?.message ?? "not found in your account"}</p>
        <p className="text-[12px] text-muted">
          A missing row and someone else&rsquo;s row are different answers; the API returns 404 for the first and 403 for
          the second, and this page shows whichever it was.
        </p>
      </Alert>
    );
  }

  const payload = selected?.structured_output ?? null;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{spec.title}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
            <Badge tone="accent" className="mono">
              {spec.method}
            </Badge>
            <span className="mono">{spec.kind}</span>
            <span className="mono">{spec.problem_type}</span>
            {spec.template_key ? <span>from template {spec.template_key}</span> : null}
            <span>{spec.run_count} run(s)</span>
          </p>
          {spec.notes ? <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-muted">{spec.notes}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" intent="ghost" onClick={duplicate} disabled={busy} title="Copy the spec into a new experiment">
            <Copy size={14} /> duplicate
          </Button>
          <Button size="sm" intent="danger" onClick={remove} disabled={busy}>
            <Trash2 size={14} /> delete
          </Button>
        </div>
      </header>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      {notice ? <Alert tone="info">{notice}</Alert> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <SectionTitle title="Inputs" hint="Edit and run: each press stores a new run." icon={<Wand2 size={15} />} />
            <ParamForm
              fields={fields}
              values={values}
              defaults={
                simulation
                  ? Object.fromEntries(Object.entries(simulation.parameters ?? {}).map(([key, entry]) => [key, entry.default]))
                  : {}
              }
              onChange={(key, next) => {
                setTouched(true);
                setValues((current) => ({ ...current, [key]: next }));
              }}
              errors={errors}
              disabled={busy}
            />
            <div className="mt-3 flex flex-wrap gap-2">
              <Button intent="primary" onClick={runNow} disabled={busy}>
                {busy ? <Spinner /> : <Play size={15} />}
                {busy ? "running…" : "Run"}
              </Button>
              {fields.length === 0 ? (
                <span className="self-center text-[12px] text-muted">
                  no editable inputs for {spec.kind} &ldquo;{spec.method}&rdquo; — the registry has no spec for it
                </span>
              ) : null}
            </div>
          </Card>

          <Card>
            <SectionTitle title="Stored inputs" hint="Exactly what the experiment record holds." />
            <KeyValue
              rows={Object.entries(spec.input_parameters ?? {}).map(([key, value]) => [key, formatParamValue(value)])}
            />
            {Object.keys(spec.input_parameters ?? {}).length === 0 ? (
              <p className="text-[12.5px] text-muted">
                empty: the engine’s documented defaults were used for every run of this experiment.
              </p>
            ) : null}
          </Card>

          <Card>
            <SectionTitle title="Runs" hint={`${runList.length} shown of ${runs.data?.total ?? 0}`} />
            {runList.length === 0 ? (
              <EmptyState title="No runs yet" hint="Press Run above; the outcome, convergence record and cross-check appear here." />
            ) : (
              <ul className="space-y-1.5">
                {runList.map((run) => (
                  <li key={run.run_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedRunId(run.run_id)}
                      className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                        selected?.run_id === run.run_id ? "border-accent/60 bg-accent-soft" : "border-border hover:bg-surface-2"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <RunBadge status={run.status} />
                        <span className="mono text-[11px] text-muted">{formatDuration(run.execution_time_ms)}</span>
                      </span>
                      <span className="mt-1 flex items-center justify-between gap-2 text-[11.5px] text-muted">
                        <VerificationBadge status={run.verification?.status} />
                        <span title={formatDateTime(run.created_at)}>{formatDateTime(run.created_at)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          {!selected ? (
            <Card>
              <EmptyState title="Select or create a run" hint="The inspection pane shows the stored payload, its convergence record and the independent verification." />
            </Card>
          ) : (
            <>
              <Card className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="mono text-[12px] text-muted">{selected.run_id}</p>
                  <p className="text-[13px]">
                    {selected.structured_output?.method ?? selected.status} ·{" "}
                    <span className="mono">{formatNumber(selected.execution_time_ms ?? NaN)} ms</span>
                    {selected.started_at ? ` · started ${formatDateTime(selected.started_at)}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" intent="outline" onClick={verify} disabled={verifying}>
                    <RefreshIcon spinning={verifying} /> {verifying ? "verifying…" : "verify"}
                  </Button>
                  <Button size="sm" intent="secondary" onClick={cancel} disabled={busy || !["queued", "running", "verifying"].includes(selected.status)}>
                    <StopCircle size={14} /> cancel
                  </Button>
                  <Button size="sm" intent="secondary" onClick={writeReport} disabled={reportBusy}>
                    {reportBusy ? <Spinner /> : <FileText size={14} />} report
                  </Button>
                </div>
              </Card>

              {selected.cancel_requested && ["queued", "running", "verifying"].includes(selected.status) ? (
                <Alert tone="warning">Cancellation was requested; the worker stops at its next checkpoint and records the run as cancelled.</Alert>
              ) : null}

              <RunView
                payload={payload}
                verification={selected.verification}
                status={selected.status}
                runId={selected.run_id}
                chart={chart.data}
                inputs={payload?.inputs ?? selected.execution_config?.parameters ?? null}
                softwareVersions={selected.software_versions}
                executionTimeMs={selected.execution_time_ms}
                error={selected.error}
                onVerify={verify}
                verifying={verifying}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function RefreshIcon({ spinning }: { spinning: boolean }) {
  return <Wand2 size={14} className={spinning ? "animate-spin" : undefined} />;
}

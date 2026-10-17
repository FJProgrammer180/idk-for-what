"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, BarChart3, Image as ImageIcon, RefreshCw, ScrollText, Sigma } from "lucide-react";
import { formatDuration, formatNumber } from "@/lib/format";
import { ConvergenceSummary, IterationTable, ModelDisclosure, ResultTable } from "./run-inspectors";
import { OutcomeLine, VerificationBadge } from "./status-badges";
import { SeriesChart, type ChartSeries } from "./chart";
import { VerificationPanel } from "./verification-panel";
import { Alert, Badge, Button, Card, SectionTitle, KeyValue } from "./ui";
import { formatParamValue } from "@/lib/params";
import type { ChartData, RunPayload, VerificationRecord } from "@/lib/types";

/**
 * The whole story of one run, in the order a reviewer needs it: what it says, whether the
 * stopping criterion was met, whether an independent algorithm agrees, and what inputs produced
 * it.
 *
 * Nothing here recomputes or rounds toward a nicer answer: the numbers are the stored payload's,
 * the chart is served by `/runs/{id}/chart-data` from those same rows, and a run without a
 * verification record says "no verification recorded" instead of looking clean.
 */
export function RunView({
  payload,
  verification,
  status,
  runId,
  chart,
  inputs,
  softwareVersions,
  executionTimeMs,
  error,
  onVerify,
  verifying = false,
  compact = false,
}: {
  payload: RunPayload | null;
  verification: VerificationRecord | null | undefined;
  status: string | null | undefined;
  runId?: string | null;
  chart?: ChartData | null;
  inputs?: Record<string, unknown> | null;
  softwareVersions?: Record<string, string>;
  executionTimeMs?: number | null;
  error?: { code: string; message: string; retryable: boolean; detail?: string | null } | null;
  onVerify?: () => void;
  verifying?: boolean;
  compact?: boolean;
}) {
  const [showIterations, setShowIterations] = useState(false);
  const warnings = (payload?.warnings ?? []) as string[];
  const series = chartSeries(chart, payload);
  const message = typeof payload?.message === "string" ? payload.message : null;
  const nonFinite = (payload?.non_finite_fields ?? []) as string[];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="accent" className="mono">
                {payload?.method ?? "unknown method"}
              </Badge>
              {payload?.solver ? <span className="mono text-[11.5px] text-muted">{payload.solver}</span> : null}
              {executionTimeMs !== undefined && executionTimeMs !== null ? (
                <span className="text-[11.5px] text-muted">{formatDuration(executionTimeMs)}</span>
              ) : null}
            </div>
            <OutcomeLine status={status} verification={verification?.status} message={message} />
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <VerificationBadge status={verification?.status} />
            {runId ? (
              <span className="mono text-[11px] text-muted">{runId}</span>
            ) : (
              <Badge tone="neutral">not stored</Badge>
            )}
            {onVerify ? (
              <Button size="sm" intent="outline" onClick={onVerify} disabled={verifying} title="Re-run the independent cross-check on this stored run">
                <RefreshCw size={13} className={verifying ? "animate-spin" : undefined} />
                {verifying ? "verifying…" : "verify again"}
              </Button>
            ) : null}
          </div>
        </div>
      </Card>

      {error ? (
        <Alert tone="danger" title={`Run failed (${error.code})`}>
          <p>{error.message}</p>
          {error.detail ? <p className="mono text-[12px]">{error.detail}</p> : null}
          <p className="text-[12px] text-muted">
            {error.retryable
              ? "The server marks this failure as retryable; the inputs are worth re-sending unchanged."
              : "This failure is not retryable as-is: the input has to change first."}
          </p>
        </Alert>
      ) : null}

      {nonFinite.length > 0 ? (
        <Alert tone="danger" title="Non-finite output">
          <p>
            These fields came back NaN or infinite and are listed rather than hidden:{" "}
            <span className="mono">{nonFinite.join(", ")}</span>
          </p>
        </Alert>
      ) : null}

      {warnings.length > 0 ? (
        <Alert tone="warning" title={`${warnings.length} warning${warnings.length === 1 ? "" : "s"} from the computation`}>
          {warnings.map((warning) => (
            <p key={warning} className="flex items-start gap-1.5">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{warning}</span>
            </p>
          ))}
        </Alert>
      ) : null}

      <Card>
        <SectionTitle title="Computed values" hint="Exactly what the run stored, with its own error estimate where one exists." icon={<Sigma size={15} />} />
        <ResultTable output={payload} />
        <div className="mt-4 border-t border-border pt-3">
          <ConvergenceSummary run={null} output={payload} />
        </div>
        {(payload?.iterations ?? []).length > 0 ? (
          <div className="mt-3">
            <button
              type="button"
              onClick={() => setShowIterations((value) => !value)}
              className="text-[12.5px] text-muted underline-offset-2 hover:text-ink hover:underline"
              aria-expanded={showIterations}
            >
              {showIterations ? "hide" : `show`} the iteration history ({(payload?.iterations ?? []).length} row
              {(payload?.iterations ?? []).length === 1 ? "" : "s"})
            </button>
            {showIterations ? (
              <div className="mt-2">
                <IterationTable output={payload} />
              </div>
            ) : null}
          </div>
        ) : null}
      </Card>

      {series.length > 0 ? (
        <Card>
          <SectionTitle
            title="Solution"
            hint="Plotted from the stored arrays; every value is one the solver returned."
            icon={<BarChart3 size={15} />}
            actions={
              runId ? (
                <Link
                  href={`/api/v1/runs/${runId}/chart-data`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[12px] text-muted underline-offset-2 hover:underline"
                >
                  raw series JSON
                </Link>
              ) : null
            }
          />
          <SeriesChart
            series={series}
            xAxisLabel={chart?.axes?.x}
            yAxisLabel={chart?.axes?.y}
            height={compact ? 240 : 320}
          />
          {chart && chart.iteration_table.length > 0 && !showIterations ? (
            <p className="mt-2 text-[11.5px] text-muted">
              Convergence history is available above ({chart.iteration_table.length} recorded rows).
            </p>
          ) : null}
        </Card>
      ) : null}

      {runId ? (
        <Card className="flex flex-wrap items-center gap-2 text-[12.5px]">
          <span className="text-muted">Artifacts:</span>
          <a href={`/api/v1/runs/${runId}/plot.png`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
            <ImageIcon size={13} /> plot.png
          </a>
          <a href={`/api/v1/runs/${runId}/chart-data`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
            <BarChart3 size={13} /> chart data
          </a>
          <Link href={`/reports?run_id=${runId}`} className="inline-flex items-center gap-1 text-accent hover:underline">
            <ScrollText size={13} /> write a report from this run
          </Link>
        </Card>
      ) : null}

      <Card>
        <SectionTitle title="Model and method honesty notes" hint="Documented assumptions, limitations and the cross-check." />
        <ModelDisclosure output={payload} />
        <div className="mt-4 border-t border-border pt-3">
          <VerificationPanel record={verification} runStatus={status} />
        </div>
      </Card>

      {!compact ? (
        <Card>
          <SectionTitle title="Reproducibility" hint="What was sent, and what produced it." />
          <KeyValue
            rows={[
              [
                "Inputs",
                inputs && Object.keys(inputs).length > 0 ? (
                  <span className="mono text-[12px]">
                    {Object.entries(inputs)
                      .map(([key, value]) => `${key}=${formatParamValue(value)}`)
                      .join(", ")}
                  </span>
                ) : (
                  <span className="text-muted">the engine’s documented defaults (nothing was overridden)</span>
                ),
              ],
              [
                "Error estimate",
                payload?.error_estimate === null || payload?.error_estimate === undefined
                  ? "none reported"
                  : formatNumber(payload.error_estimate, 8),
              ],
              ["Tolerance", payload?.tolerance === undefined || payload?.tolerance === null ? "—" : formatNumber(payload.tolerance, 6)],
              [
                "Software",
                softwareVersions && Object.keys(softwareVersions).length > 0
                  ? Object.entries(softwareVersions)
                      .map(([name, version]) => `${name} ${version}`)
                      .join(", ")
                  : "—",
              ],
            ]}
          />
          <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
            The report generator reads only these stored rows, so a report can never quote a number
            that is not in this table.
          </p>
        </Card>
      ) : null}
    </div>
  );
}

/** Prefer the server-derived chart (it knows the axes); fall back to the payload's own arrays. */
function chartSeries(chart: ChartData | null | undefined, payload: RunPayload | null): ChartSeries[] {
  if (chart && chart.series.length > 0) {
    return chart.series.map((entry) => ({ label: entry.label, x: entry.x ?? [], y: entry.y ?? [] }));
  }
  const out: ChartSeries[] = [];
  const series = payload?.series;
  if (series && typeof series === "object" && !Array.isArray(series)) {
    const times = (series as Record<string, number[]>).time ?? [];
    for (const [key, values] of Object.entries(series as Record<string, number[]>)) {
      if (key === "time" || !Array.isArray(values)) continue;
      out.push({ label: key, x: times, y: values });
    }
  }
  const resultSeries = payload?.result?.series;
  if (out.length === 0 && resultSeries && typeof resultSeries === "object") {
    const times = (payload?.result?.time as number[]) ?? [];
    for (const [key, values] of Object.entries(resultSeries as Record<string, number[]>)) {
      if (!Array.isArray(values)) continue;
      out.push({ label: key, x: times, y: values });
    }
  }
  return out;
}

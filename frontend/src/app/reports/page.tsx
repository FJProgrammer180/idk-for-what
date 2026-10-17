"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Download, FileText, Trash2, Wand2 } from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import { errorMessage, useResource } from "@/lib/resources";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";
import { Markdown } from "@/components/markdown";
import { RunBadge, VerificationBadge } from "@/components/status-badges";
import { Alert, Badge, Button, Card, EmptyState, Field, KeyValue, SectionTitle, Spinner } from "@/components/ui";
import type { Dashboard, Page, ReportCreateResponse, ReportDetail, ReportRow, ReportRequestFormat } from "@/lib/types";

/**
 * Reports, generated from stored runs.
 *
 * The generator on the server reads the persisted run row (its payload, convergence record and
 * verification) and nothing else, so a report is a view of the database rather than a re-run of
 * the computation. That is also why this page shows the run status and verification status beside
 * every report: a report written from a stopped-early run must not look like a clean one, and the
 * artifact itself carries the warning.
 */
export default function ReportsPage() {
  return (
    <Suspense fallback={<p className="text-[13px] text-muted">loading reports…</p>}>
      <ReportsWorkspace />
    </Suspense>
  );
}

function ReportsWorkspace() {
  const searchParams = useSearchParams();
  const queryRun = searchParams.get("run_id");
  const queryReport = searchParams.get("report_id");

  const list = useResource<Page<ReportRow>>("/reports?page_size=20");
  const dashboard = useResource<Dashboard>("/dashboard");
  const [runId, setRunId] = useState(queryRun ?? "");
  const [format, setFormat] = useState<ReportRequestFormat>("markdown");
  const [includeVerification, setIncludeVerification] = useState(true);
  const [includeIterations, setIncludeIterations] = useState(true);
  const [notes, setNotes] = useState("");
  const [selected, setSelected] = useState<string | null>(queryReport);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [created, setCreated] = useState<ReportCreateResponse | null>(null);

  const detail = useResource<ReportDetail>(selected ? `/reports/${selected}` : null);

  useEffect(() => {
    if (queryRun) setRunId(queryRun);
  }, [queryRun]);
  useEffect(() => {
    if (queryReport) setSelected(queryReport);
  }, [queryReport]);

  const recentRuns = useMemo(() => dashboard.data?.recent_runs ?? [], [dashboard.data]);

  async function generate() {
    if (!runId.trim()) {
      setFailure("Pick a run first — a report is always derived from one stored run.");
      return;
    }
    setBusy(true);
    setFailure(null);
    setCreated(null);
    try {
      const response = await api.post<ReportCreateResponse>("/reports", {
        run_id: runId.trim(),
        experiment_id: null,
        format,
        include_verification: includeVerification,
        include_iterations: includeIterations,
        notes: notes.trim() || null,
      });
      setCreated(response);
      setSelected(response.report_id);
      await list.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function download(row: { report_id: string; format: string }, kind: "markdown" | "pdf") {
    setBusy(true);
    setFailure(null);
    try {
      const { blob, filename } = await downloadFile(`/reports/${row.report_id}/download?format=${kind}`, `report-${row.report_id.slice(0, 8)}.${kind === "pdf" ? "pdf" : "md"}`);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove(row: ReportRow) {
    if (!window.confirm(`Delete the report “${row.title}” and its stored artifacts?`)) return;
    setBusy(true);
    try {
      await api.del(`/reports/${row.report_id}`);
      if (selected === row.report_id) setSelected(null);
      await list.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Reports</h1>
        <p className="text-[13px] text-muted">
          Markdown and PDF write-ups assembled from persisted run rows, with the verification record and the
          convergence evidence attached.
        </p>
      </header>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="space-y-3">
            <SectionTitle title="Generate from a run" icon={<Wand2 size={15} />} hint="nothing is recomputed: the report quotes what was stored" />
            <Field label="Run" htmlFor="run" required>
              <input id="run" className="field" placeholder="run_…" value={runId} onChange={(event) => setRunId(event.target.value)} spellCheck={false} />
            </Field>
            {recentRuns.length > 0 ? (
              <div className="space-y-1">
                <p className="label">or pick a recent run</p>
                <ul className="max-h-40 space-y-1 overflow-y-auto pr-1">
                  {recentRuns.map((run) => (
                    <li key={run.run_id}>
                      <button
                        type="button"
                        onClick={() => setRunId(run.run_id)}
                        className={`w-full rounded-lg border px-2 py-1.5 text-left text-[12px] transition-colors ${
                          runId === run.run_id ? "border-accent/60 bg-accent-soft" : "border-border hover:bg-surface-2"
                        }`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="mono truncate">{run.structured_output?.method ?? run.status}</span>
                          <RunBadge status={run.status} />
                        </span>
                        <span className="mono block truncate text-[10.5px] text-muted">{run.run_id}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <Field label="Format" htmlFor="format">
              <select id="format" className="field" value={format} onChange={(event) => setFormat(event.target.value as ReportRequestFormat)}>
                <option value="markdown">markdown</option>
                <option value="pdf">pdf</option>
                <option value="both">markdown + pdf</option>
              </select>
            </Field>

            <div className="flex flex-wrap gap-3 text-[12.5px]">
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={includeVerification} onChange={(event) => setIncludeVerification(event.target.checked)} />
                include verification
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="checkbox" checked={includeIterations} onChange={(event) => setIncludeIterations(event.target.checked)} />
                include iteration table
              </label>
            </div>

            <Field label="Notes" htmlFor="notes" hint="appended verbatim to the document">
              <textarea id="notes" className="field min-h-16 resize-y" value={notes} onChange={(event) => setNotes(event.target.value)} />
            </Field>

            <Button intent="primary" full onClick={generate} disabled={busy}>
              {busy ? <Spinner /> : <FileText size={15} />} Generate report
            </Button>

            {created ? (
              <Alert tone="success" title={`${created.title.split(" ")[0]}… written`}>
                <p>
                  {created.sections.length} sections, {created.word_count} words, format {created.format}.
                </p>
                {created.warnings.length > 0 ? (
                  <ul className="mt-1 list-disc pl-4 text-[12px]">
                    {created.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                ) : null}
              </Alert>
            ) : null}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <SectionTitle title="Your reports" hint={`${list.data?.total ?? 0} stored`} />
            {list.loading ? (
              <p className="flex items-center gap-2 text-[13px] text-muted">
                <Spinner /> loading…
              </p>
            ) : (list.data?.items ?? []).length === 0 ? (
              <EmptyState title="No reports yet" hint="Pick a run on the left and generate one." />
            ) : (
              <div className="overflow-x-auto">
                <table className="table-grid">
                  <thead>
                    <tr>
                      <th>Report</th>
                      <th>Run state</th>
                      <th>Size</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(list.data?.items ?? []).map((row) => (
                      <tr key={row.report_id} className={row.report_id === selected ? "bg-accent-soft" : undefined}>
                        <td>
                          <button type="button" className="text-left" onClick={() => setSelected(row.report_id)}>
                            <span className="block max-w-[34ch] truncate text-[13px] font-medium hover:underline">{row.title}</span>
                            <span className="mono block text-[11px] text-muted">
                              {row.format} · {row.word_count_estimate} words
                              {row.has_pdf ? " · pdf" : ""}
                            </span>
                          </button>
                        </td>
                        <td>
                          <span className="flex flex-col gap-1">
                            <RunBadge status={row.verification_snapshot?.run_status} />
                            <VerificationBadge status={row.verification_snapshot?.run_verification_status} />
                          </span>
                        </td>
                        <td className="mono text-[11.5px]">
                          {formatBytes(row.artifact_size_bytes)}
                          {row.checksum_sha256 ? (
                            <span className="block text-muted" title={row.checksum_sha256}>
                              sha256 {row.checksum_sha256.slice(0, 12)}…
                            </span>
                          ) : null}
                        </td>
                        <td className="whitespace-nowrap text-[12px] text-muted">{formatDateTime(row.created_at)}</td>
                        <td>
                          <span className="flex flex-wrap justify-end gap-1">
                            <Button size="sm" intent="ghost" onClick={() => download(row, "markdown")} disabled={busy} title="Download the stored markdown artifact">
                              <Download size={13} /> md
                            </Button>
                            {row.has_pdf ? (
                              <Button size="sm" intent="ghost" onClick={() => download(row, "pdf")} disabled={busy} title="Download the stored PDF artifact">
                                <Download size={13} /> pdf
                              </Button>
                            ) : null}
                            <Button size="sm" intent="ghost" onClick={() => remove(row)} disabled={busy} title="Delete the report row and its artifacts">
                              <Trash2 size={13} />
                            </Button>
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {selected && detail.data ? (
            <Card>
              <SectionTitle
                title={detail.data.title}
                hint={`${detail.data.sections?.length ?? 0} sections · ${detail.data.word_count_estimate} words · ${formatBytes(detail.data.artifact_size_bytes)}`}
                actions={
                  <Badge tone="neutral" className="mono">
                    {detail.data.report_id}
                  </Badge>
                }
              />
              <div className="mb-3 flex flex-wrap gap-3 border-y border-border py-2 text-[12px] text-muted">
                <span>
                  run: <span className="mono">{detail.data.run_id}</span>
                </span>
                <span>
                  cited external sources:{" "}
                  <span className="mono">{formatNumber(detail.data.verification_snapshot?.cited_sources ?? 0, 0)}</span>
                </span>
                <span>
                  generated: <span className="mono">{formatDateTime(detail.data.verification_snapshot?.generated_at)}</span>
                </span>
                {detail.data.verification_snapshot?.software_versions ? (
                  <span>
                    software:{" "}
                    <span className="mono">
                      {Object.entries(detail.data.verification_snapshot.software_versions)
                        .map(([name, version]) => `${name}@${version}`)
                        .join(", ")}
                    </span>
                  </span>
                ) : null}
              </div>
              <Markdown source={detail.data.body_markdown} />
            </Card>
          ) : null}

          {selected && detail.error ? (
            <Alert tone="danger" title="This report could not be loaded">
              {detail.error.message}
            </Alert>
          ) : null}
        </div>
      </div>
    </div>
  );
}

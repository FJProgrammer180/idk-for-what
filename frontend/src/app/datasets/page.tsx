"use client";

import { useRef, useState } from "react";
import { Download, LineChart, Table2, Trash2, Upload } from "lucide-react";
import { api, apiUpload, downloadFile } from "@/lib/api";
import { errorMessage, useResource } from "@/lib/resources";
import { useSession } from "@/lib/session";
import { formatBytes, formatNumber } from "@/lib/format";
import { Alert, Badge, Button, Card, EmptyState, Field, KeyValue, SectionTitle, Spinner } from "@/components/ui";
import type { DatasetAnalysis, DatasetDetail, DatasetInfo, Page, DatasetRow } from "@/lib/types";

const MODELS = ["linear", "polynomial", "power", "exponential"] as const;

/**
 * Measurement data: upload, profile, fit.
 *
 * The fit is a real least-squares solve in the backend (`least_squares_fit`), and this screen
 * shows its residual diagnostics rather than only the pretty line: R², residual mean and worst
 * residual. A dataset that cannot be fitted says so with the server's own reason (a `message`)
 * instead of rendering an empty chart.
 */
export default function DatasetsPage() {
  const { capabilities } = useSession();
  const list = useResource<Page<DatasetRow>>("/datasets?page_size=20");
  const [selected, setSelected] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [xColumn, setXColumn] = useState("");
  const [yColumn, setYColumn] = useState("");
  const [model, setModel] = useState<string>("linear");
  const [degree, setDegree] = useState("2");
  const [analysis, setAnalysis] = useState<DatasetAnalysis | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  const detail = useResource<DatasetDetail>(selected ? `/datasets/${selected}` : null);
  const numericColumns = (detail.data?.profile as { numeric_columns?: string[] } | undefined)?.numeric_columns
    ?? (detail.data?.dataset?.schema_profile?.columns ?? []).filter((column) => column.numeric).map((column) => column.name);
  const publishedModels = Object.keys(capabilities?.numerical?.regression_models ?? {});
  const regressionModels = publishedModels.length > 0 ? publishedModels : [...MODELS];

  async function upload() {
    if (!file) return;
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const created = await apiUpload<DatasetInfo>("/datasets", form);
      setNotice(
        created.duplicate
          ? "An identical file (same SHA-256) already exists in your account; the stored dataset was reused."
          : `Stored ${created.row_count} rows from ${created.original_filename}.`,
      );
      setSelected(created.dataset_id);
      setFile(null);
      if (input.current) input.current.value = "";
      await list.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    if (!selected) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await api.post<DatasetAnalysis>(`/datasets/${selected}/analyze`, {
        x_column: xColumn || null,
        y_column: yColumn || null,
        model,
        degree: Number(degree),
      });
      setAnalysis(result);
      if (result.status !== "completed") {
        setNotice(result.result?.message ? `Analysis ended as ${result.status}: ${result.result.message}` : `Analysis ended as ${result.status}.`);
      } else {
        setNotice(null);
      }
      await detail.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  async function exportCsv() {
    if (!selected) return;
    setBusy(true);
    setFailure(null);
    try {
      const { blob, filename } = await downloadFile(`/datasets/${selected}/export`, "dataset_cleaned.csv");
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

  async function remove() {
    if (!selected) return;
    if (!window.confirm("Delete this dataset and its stored file?")) return;
    setBusy(true);
    try {
      await api.del(`/datasets/${selected}`);
      setSelected(null);
      setAnalysis(null);
      await list.reload();
    } catch (cause) {
      setFailure(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  const regression = analysis?.result?.regression ?? null;
  const profileColumns = detail.data?.dataset?.schema_profile?.columns ?? [];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Datasets</h1>
        <p className="text-[13px] text-muted">
          CSV/TSV measurements, profiled on upload and fitted with the same least-squares code the agent can call.
        </p>
      </header>

      {failure ? <Alert tone="danger">{failure}</Alert> : null}
      {notice ? <Alert tone="info">{notice}</Alert> : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="space-y-3">
            <SectionTitle title="Upload" hint="stored as CSV in the controlled upload directory, never inside the database" icon={<Upload size={15} />} />
            <input
              ref={input}
              type="file"
              accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values"
              className="field file:mr-3 file:rounded-md file:border-0 file:bg-surface-3 file:px-2 file:py-1 file:text-[12px]"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <Button intent="primary" full onClick={upload} disabled={!file || busy}>
              {busy ? <Spinner /> : <Upload size={15} />} {file ? `Store ${file.name}` : "Choose a file"}
            </Button>
            <p className="text-[11.5px] leading-relaxed text-muted">
              Limit: {formatBytes(capabilities?.limits.max_upload_bytes ?? null)} per file, {formatNumber(capabilities?.limits.max_expression_chars ?? 0, 0)} chars per expression elsewhere.
              The server re-reads and normalises the file itself; the preview below is what it actually stored.
            </p>
          </Card>

          <Card>
            <SectionTitle title="Your datasets" hint={`${list.data?.total ?? 0} stored`} />
            {list.loading ? (
              <p className="flex items-center gap-2 text-[13px] text-muted">
                <Spinner /> loading…
              </p>
            ) : (list.data?.items ?? []).length === 0 ? (
              <EmptyState title="No datasets yet" hint="Upload a CSV with a header row to begin." />
            ) : (
              <ul className="space-y-1">
                {(list.data?.items ?? []).map((row) => (
                  <li key={row.dataset_id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(row.dataset_id);
                        setAnalysis(null);
                        setXColumn("");
                        setYColumn("");
                      }}
                      className={`w-full rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors ${
                        row.dataset_id === selected ? "bg-accent-soft font-medium" : "hover:bg-surface-2"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate">{row.original_filename}</span>
                        <span className="mono text-[11px] text-muted">{row.row_count}×{row.column_count}</span>
                      </span>
                      <span className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-muted">
                        <span>{formatBytes(row.size_bytes)}</span>
                        {row.has_analysis ? <Badge tone="success">analysed</Badge> : <Badge tone="neutral">not analysed</Badge>}
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
              <EmptyState title="Select a dataset" hint="Its profile, preview rows, fit and export live on this side." />
            </Card>
          ) : (
            <>
              <Card>
                <SectionTitle
                  title={detail.data?.dataset?.original_filename ?? "dataset"}
                  hint={`${detail.data?.dataset?.row_count ?? 0} rows · sha256 ${detail.data?.dataset?.sha256?.slice(0, 16)}…`}
                  icon={<Table2 size={15} />}
                  actions={
                    <div className="flex gap-2">
                      <Button size="sm" intent="ghost" onClick={exportCsv} disabled={busy}>
                        <Download size={14} /> cleaned CSV
                      </Button>
                      <Button size="sm" intent="danger" onClick={remove} disabled={busy}>
                        <Trash2 size={14} /> delete
                      </Button>
                    </div>
                  }
                />
                {detail.loading ? (
                  <p className="flex items-center gap-2 text-[13px] text-muted">
                    <Spinner /> loading profile…
                  </p>
                ) : (
                  <div className="space-y-3">
                    <div className="overflow-x-auto rounded-xl border border-border">
                      <table className="table-grid">
                        <thead>
                          <tr>
                            <th>column</th>
                            <th>dtype</th>
                            <th>count</th>
                            <th>missing</th>
                            <th>unique</th>
                            <th>min</th>
                            <th>max</th>
                            <th>mean</th>
                            <th>std</th>
                          </tr>
                        </thead>
                        <tbody>
                          {profileColumns.map((column) => (
                            <tr key={column.name}>
                              <td className="mono">{column.name}</td>
                              <td className="mono text-[11.5px]">{column.dtype}</td>
                              <td className="mono">{column.count ?? "—"}</td>
                              <td className="mono">
                                {column.missing}
                                {column.missing_fraction > 0 ? <span className="text-amber-600"> ({(column.missing_fraction * 100).toFixed(1)}%)</span> : null}
                              </td>
                              <td className="mono">{column.unique}</td>
                              <td className="mono">{column.min === null || column.min === undefined ? "—" : formatNumber(column.min)}</td>
                              <td className="mono">{column.max === null || column.max === undefined ? "—" : formatNumber(column.max)}</td>
                              <td className="mono">{column.mean === null || column.mean === undefined ? "—" : formatNumber(column.mean)}</td>
                              <td className="mono">{column.std === null || column.std === undefined ? "—" : formatNumber(column.std)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="text-[11.5px] text-muted">
                      Non-numeric columns carry no min/max/mean; that is reported as “—” rather than as a zero, because a
                      zero would be a fabricated measurement.
                    </p>
                  </div>
                )}
              </Card>

              <Card>
                <SectionTitle title="Fit a model" hint="least squares on the stored rows; the fit is reported with its residuals" icon={<LineChart size={15} />} />
                <div className="grid gap-3 sm:grid-cols-4">
                  <Field label="x column" htmlFor="xcol">
                    <select id="xcol" className="field" value={xColumn} onChange={(event) => setXColumn(event.target.value)}>
                      <option value="">auto (first numeric)</option>
                      {numericColumns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="y column" htmlFor="ycol">
                    <select id="ycol" className="field" value={yColumn} onChange={(event) => setYColumn(event.target.value)}>
                      <option value="">auto (second numeric)</option>
                      {numericColumns.map((column) => (
                        <option key={column} value={column}>
                          {column}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="model" htmlFor="model">
                    <select id="model" className="field" value={model} onChange={(event) => setModel(event.target.value)}>
                      {regressionModels.map((entry) => (
                        <option key={entry} value={entry}>
                          {entry}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Field label="degree" htmlFor="degree" hint={model === "polynomial" ? "polynomial order" : "ignored"}>
                    <input id="degree" className="field" value={degree} inputMode="numeric" onChange={(event) => setDegree(event.target.value)} />
                  </Field>
                </div>
                <div className="mt-3">
                  <Button intent="primary" onClick={analyze} disabled={busy}>
                    {busy ? <Spinner /> : <LineChart size={15} />} Fit
                  </Button>
                </div>

                {analysis ? (
                  <div className="mt-4 space-y-3 border-t border-border pt-3">
                    {regression ? (
                      <>
                        <KeyValue
                          rows={[
                            ["model", regression.model],
                            [
                              "parameters",
                              <span key="p" className="mono">
                                {Object.entries(regression.parameters)
                                  .map(([key, value]) => `${key} = ${formatNumber(value, 8)}`)
                                  .join(",  ")}
                              </span>,
                            ],
                            ["R²", regression.r_squared === null || regression.r_squared === undefined ? "—" : formatNumber(regression.r_squared, 8)],
                            ["residual mean", formatNumber(regression.residual_mean, 8)],
                            ["worst |residual|", formatNumber(regression.residual_max_abs, 8)],
                            ["points fitted", String(regression.n)],
                          ]}
                        />
                        {numericColumns.includes(xColumn || numericColumns[0] || "") && (
                          <p className="text-[11.5px] text-muted">
                            The line is drawn from the fitted parameters over the stored x range; the dots are your data, unmodified.
                          </p>
                        )}
                      </>
                    ) : (
                      <Alert tone="warning" title={`status: ${analysis.status}`}>
                        <p>{analysis.result?.message ?? "The backend returned no fit for these columns."}</p>
                      </Alert>
                    )}
                    {(analysis.result?.notes ?? []).length > 0 ? (
                      <ul className="list-disc space-y-0.5 pl-4 text-[12px] text-muted">
                        {(analysis.result?.notes ?? []).map((note) => (
                          <li key={note}>{note}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ) : null}
              </Card>

              {(detail.data?.preview_rows ?? []).length > 0 ? (
                <Card>
                  <SectionTitle title="Stored rows (preview)" hint="the first rows of what the server normalised and saved" />
                  <div className="overflow-x-auto rounded-xl border border-border">
                    <table className="table-grid">
                      <thead>
                        <tr>
                          {Object.keys(detail.data!.preview_rows![0] ?? {}).map((key) => (
                            <th key={key}>{key}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {detail.data!.preview_rows!.map((row, index) => (
                          <tr key={index}>
                            {Object.entries(row).map(([key, value]) => (
                              <td key={key} className="mono text-[12px]">
                                {typeof value === "number" ? formatNumber(value, 8) : value === null ? <span className="text-muted">missing</span> : String(value)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

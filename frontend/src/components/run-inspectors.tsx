"use client";

import { formatNumber, formatWithUncertainty, isFiniteNumber } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Badge, KeyValue } from "./ui";
import type { RunRow, RunPayload } from "@/lib/types";

const HIDDEN_KEYS = new Set(["series", "chart_data", "time", "grid", "temperature", "profile"]);

function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b[a-z]/g, (char) => char.toUpperCase());
}

function isScalar(value: unknown): value is number | string | boolean {
  return typeof value === "number" || typeof value === "string" || typeof value === "boolean";
}

/**
 * The numbers a run returned, exactly as stored.
 *
 * A finite real is printed with the run's own error estimate next to it; a non-finite value is
 * labelled as such rather than rendered as `NaN`; arrays are summarised by length and extremes
 * because printing 1200 floats is noise. Keys the backend reports in `non_finite_fields` are
 * flagged - a `+inf` in a result is a finding, not a formatting problem.
 */
export function ResultTable({
  output,
  run,
  className,
}: {
  output: RunPayload | null;
  run?: RunRow | null;
  className?: string;
}) {
  // Numerical runs answer in `result`, simulations in `summary`; a simulation may also carry
  // scalars inside `result`, so both are merged (result wins on a clash).
  const result = {
    ...((output?.summary ?? {}) as Record<string, unknown>),
    ...((output?.result ?? {}) as Record<string, unknown>),
  };
  const errorEstimate = output?.error_estimate ?? null;
  const flagged = new Set((output?.non_finite_fields ?? []).map(String));
  const entries = Object.entries(result).filter(([key]) => !HIDDEN_KEYS.has(key));

  if (entries.length === 0) {
    return (
      <p className={cn("text-[13px] text-muted", className)}>
        This run stored no scalar result block. The arrays below (if any) and the iteration table
        are what was computed.
      </p>
    );
  }

  const rows: [string, React.ReactNode][] = entries.map(([key, value]) => [
    humanize(key),
    renderValue(key, value, errorEstimate, flagged.has(key) || flagged.has(`result.${key}`)),
  ]);

  return <KeyValue rows={rows} className={className} />;
}

function renderValue(key: string, value: unknown, errorEstimate: unknown, flagged: boolean): React.ReactNode {
  if (isFiniteNumber(value)) {
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        <span>{formatWithUncertainty(value, key === "error_estimate" ? null : errorEstimate)}</span>
        {flagged ? <Badge tone="danger">non-finite neighbour</Badge> : null}
      </span>
    );
  }
  if (typeof value === "boolean") return <span>{value ? "yes" : "no"}</span>;
  if (typeof value === "string") return <span className="whitespace-pre-wrap">{value}</span>;
  if (value === null || value === undefined) return <span className="text-muted">not reported</span>;
  if (Array.isArray(value)) {
    const numbers = value.filter(isFiniteNumber);
    if (numbers.length === 0) {
      return (
        <span className="text-muted">
          array of {value.length} non-numeric entries
        </span>
      );
    }
    const min = Math.min(...numbers);
    const max = Math.max(...numbers);
    return (
      <span>
        {numbers.length} value{numbers.length === 1 ? "" : "s"}, range [{formatNumber(min)}, {formatNumber(max)}]
        {numbers.length < value.length ? (
          <span className="text-muted"> ({value.length - numbers.length} non-finite)</span>
        ) : null}
      </span>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    const scalars = entries.filter(([, item]) => isScalar(item) || isFiniteNumber(item));
    if (scalars.length > 0 && scalars.length === entries.length) {
      return (
        <span className="inline-flex flex-wrap gap-x-3 gap-y-1">
          {scalars.map(([inner, item]) => (
            <span key={inner}>
              <span className="text-muted">{humanize(inner)}</span> {formatWithUncertainty(item, null)}
            </span>
          ))}
        </span>
      );
    }
    return (
      <span className="text-muted">
        object with {entries.length} key{entries.length === 1 ? "" : "s"}:{" "}
        <span className="mono">{entries.map(([inner]) => inner).join(", ")}</span>
      </span>
    );
  }
  return <span>{String(value)}</span>;
}

/**
 * Assumptions and limitations travel with the result. The backend stores them on every payload;
 * showing them here is what stops a chart from being read as a claim about the real world.
 */
export function ModelDisclosure({
  output,
  title = "Model assumptions",
}: {
  output: RunPayload | null;
  title?: string;
}) {
  const modelMeta = (output?.model ?? {}) as Record<string, unknown>;
  const assumptions = ((output?.assumptions ?? modelMeta.assumptions ?? []) as string[]) ?? [];
  const limitations = ((output?.limitations ?? modelMeta.limitations ?? []) as string[]) ?? [];
  const model = modelMeta;
  const equations = (model.governing_equations ?? []) as string[];
  const window = (model.integration_window ?? null) as { t_end?: number; source?: string } | null;
  const aliases = (output?.parameter_aliases ?? {}) as Record<string, string>;
  const preset = typeof model.preset === "string" ? model.preset : null;

  if (assumptions.length === 0 && limitations.length === 0 && equations.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3 text-[13px]">
      {preset ? (
        <p className="text-muted">
          Model preset: <span className="mono text-ink">{preset}</span>
          {window?.t_end ? (
            <>
              {" "}· integrated to t = <span className="mono text-ink">{formatNumber(window.t_end)}</span>
              <span className="mono text-muted"> ({window.source ?? "window source not recorded"})</span>
            </>
          ) : null}
        </p>
      ) : null}
      {Object.keys(aliases).length > 0 ? (
        <p className="text-muted">
          Parameter names you supplied were folded onto the model&rsquo;s own:{" "}
          {Object.entries(aliases)
            .map(([from, to]) => (
              <span key={from} className="mono text-ink">
                {from}→{to}
              </span>
            ))
            .join(", ")}
          . No other input was inferred.
        </p>
      ) : null}
      {equations.length > 0 ? (
        <div>
          <p className="label mb-1">Governing equations</p>
          <ul className="mono space-y-1 text-[12.5px]">
            {equations.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {assumptions.length > 0 ? (
        <div>
          <p className="label mb-1">{title}</p>
          <ul className="list-disc space-y-1 pl-4 text-[12.5px] leading-relaxed">
            {assumptions.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {limitations.length > 0 ? (
        <div>
          <p className="label mb-1">Known limitations</p>
          <ul className="list-disc space-y-1 pl-4 text-[12.5px] leading-relaxed text-muted">
            {limitations.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/** Everything the solver said about *how* it stopped. */
export function ConvergenceSummary({ run, output }: { run: RunRow | null; output: RunPayload | null }) {
  const metadata = (output?.metadata ?? {}) as Record<string, unknown>;
  const iterationsUsed = isFiniteNumber(metadata.iterations_used)
    ? metadata.iterations_used
    : output?.iteration_count ?? null;
  const criterion = typeof metadata.stopping_criterion === "string" ? metadata.stopping_criterion : null;
  const tolerance = output?.tolerance ?? null;
  const errorEstimate = output?.error_estimate ?? null;
  const achieved = output?.convergence_achieved ?? null;
  const order = isFiniteNumber(metadata.order) ? metadata.order : null;
  const diagnostics = Object.entries(metadata).filter(
    ([key, value]) => !["iterations_used", "stopping_criterion", "order"].includes(key) && isFiniteNumber(value),
  );

  return (
    <div className="space-y-2 text-[13px]">
      <KeyValue
        rows={[
          [
            "Stopping criterion",
            criterion ? <span className="mono">{criterion}</span> : <span className="text-muted">not recorded by this method</span>,
          ],
          [
            "Iterations",
            iterationsUsed === null ? (
              <span className="text-muted">not applicable (direct method)</span>
            ) : (
              `${iterationsUsed}${output?.iterations_truncated ? " (table truncated for display)" : ""}`
            ),
          ],
          ["Tolerance", tolerance === null ? "—" : formatNumber(tolerance)],
          [
            "Error estimate",
            errorEstimate === null ? (
              <span className="text-muted">none reported — the method gave no a-posteriori bound</span>
            ) : (
              formatNumber(errorEstimate)
            ),
          ],
          [
            "Convergence",
            achieved === null ? (
              "not reported"
            ) : achieved ? (
              <Badge tone="success">criterion met</Badge>
            ) : (
              <Badge tone="warning">criterion not met</Badge>
            ),
          ],
          order !== null ? ["Documented order", `O(h^${order})`] : ["Documented order", "—"],
          ...diagnostics.map(([key, value]) => [humanize(key), formatNumber(value)] as [string, React.ReactNode]),
        ]}
      />
      {errorEstimate === null && achieved === true ? (
        <p className="text-[12px] text-muted">
          The criterion was met, so the run is reported as converged; note that this method returns
          no independent error bound, so the last digit is not certified.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The iteration table is printed from the stored rows with whatever columns that method actually
 * produced. It is the evidence behind the answer, so it is never summarised away.
 */
export function IterationTable({ output, maxRows = 200 }: { output: RunPayload | null; maxRows?: number }) {
  const rows = (output?.iterations ?? []) as Record<string, number | string | null>[];
  const count = rows.length;
  if (count === 0) {
    return <p className="text-[13px] text-muted">This method is direct: it performs no iterations, so there is nothing to show.</p>;
  }
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const shown = rows.slice(0, maxRows);

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="table-grid">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column}>{column === "index" ? "#" : humanize(column)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, index) => (
              <tr key={`${String(row.index ?? index)}`}>
                {columns.map((column) => {
                  const value = row[column];
                  return (
                    <td key={column} className={isFiniteNumber(value) ? "mono" : undefined}>
                      {isFiniteNumber(value) ? formatNumber(value, 8) : value === null || value === undefined ? <span className="text-muted">·</span> : String(value)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-muted">
        {rows.length > shown.length
          ? `Showing the first ${shown.length} of ${rows.length} recorded iterations.`
          : `${rows.length} iteration${rows.length === 1 ? "" : "s"} recorded, in the order the method produced them.`}
        {output?.iterations_truncated ? " The solver itself stopped recording early (server cap), so later iterations were never stored." : ""}
      </p>
    </div>
  );
}

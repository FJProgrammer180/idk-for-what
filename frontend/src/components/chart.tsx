"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { isFiniteNumber } from "@/lib/format";

export interface ChartSeries {
  label: string;
  x: number[];
  y: number[];
  /** Optional axis units, shown in the legend. */
  xUnit?: string;
  yUnit?: string;
}

const PALETTE = ["#4f46e5", "#0891b2", "#c026d3", "#16a34a", "#ea580c", "#2563eb", "#9333ea"];
const MAX_POINTS = 1400;

/**
 * Every point shown comes from the stored run output; nothing is interpolated into existence.
 * When a series is denser than the canvas can carry, points are *thinned* (every k-th sample
 * kept) and that is stated under the chart - the alternative, smoothing, would invent data.
 */
export function SeriesChart({
  series,
  xAxisLabel,
  yAxisLabel,
  height = 300,
  yLogarithmic = false,
}: {
  series: ChartSeries[];
  xAxisLabel?: string;
  yAxisLabel?: string;
  height?: number;
  yLogarithmic?: boolean;
}) {
  const { rows, thinnedBy, usable } = useMemo(() => buildRows(series, yLogarithmic), [series, yLogarithmic]);

  if (usable.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-[12.5px] text-muted">
        This run produced no plottable series. That is reported rather than drawn as an empty axis:
        methods without a time series (a factorisation, a linear solve) have their numbers in the
        result table instead.
      </p>
    );
  }

  return (
    <figure className="space-y-2">
      <div style={{ height }} aria-label="chart of the computed series" role="img">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 14, bottom: 18, left: 4 }}>
            <CartesianGrid stroke="var(--tone-border)" strokeDasharray="3 4" vertical={false} />
            <XAxis
              dataKey="x"
              type="number"
              domain={["auto", "auto"]}
              tickFormatter={(value: number) => shortTick(value)}
              tick={{ fontSize: 11 }}
              label={{ value: xAxisLabel ?? "x", position: "insideBottom", offset: -12, fontSize: 11, fill: "var(--tone-muted)" }}
            />
            <YAxis
              scale={yLogarithmic ? "log" : "auto"}
              domain={yLogarithmic ? ["auto", "auto"] : ["auto", "auto"]}
              allowDataOverflow={false}
              tickFormatter={(value: number) => shortTick(value)}
              tick={{ fontSize: 11 }}
              width={58}
              label={{
                value: yAxisLabel ?? "value",
                angle: -90,
                position: "insideLeft",
                offset: 16,
                fontSize: 11,
                fill: "var(--tone-muted)",
              }}
            />
            <Tooltip
              isAnimationActive={false}
              content={({ active, payload, label }) => {
                if (!active || !payload || payload.length === 0) return null;
                return (
                  <div className="chart-tooltip">
                    <div className="mb-1 text-[11px] uppercase tracking-wide" style={{ color: "var(--tone-muted)" }}>
                      {xAxisLabel ?? "x"} = {shortTick(Number(label))}
                    </div>
                    {payload.map((entry, entryIndex) => (
                      <div key={`${String(entry.name)}-${entryIndex}`} className="row">
                        <span style={{ color: entry.color }}>{String(entry.name)}</span>
                        <span>{typeof entry.value === "number" ? shortTick(entry.value) : String(entry.value)}</span>
                      </div>
                    ))}
                  </div>
                );
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11.5, paddingTop: 4 }} iconType="plainline" />
            {usable.map((entry, index) => (
              <Line
                key={entry.label}
                type="linear"
                dataKey={entry.key}
                name={entry.label}
                stroke={PALETTE[index % PALETTE.length]}
                strokeWidth={1.7}
                dot={entry.points <= 90 ? { r: 2, strokeWidth: 0, fill: PALETTE[index % PALETTE.length] } : false}
                isAnimationActive={false}
                connectNulls={false}
              />
            ))}
            {yLogarithmic ? <ReferenceLine y={1} stroke="var(--tone-border)" strokeDasharray="2 3" /> : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="text-[11.5px] text-muted">
        {thinnedBy > 1
          ? `Every ${thinnedBy}-th sample is drawn so the canvas can show it (${usable
              .map((entry) => `${entry.label}: ${entry.points} of ${entry.total} points`)
              .join(", ")}); the computation itself used every point.`
          : usable.some((entry) => entry.finite < entry.total)
            ? `${usable
                .map((entry) => `${entry.label}: ${entry.finite} of ${entry.total} points are finite`)
                .join(", ")}. Non-finite samples are left out rather than connected across, so a gap in the line means a gap in the computation.`
            : `All ${usable[0]?.total ?? 0} computed points are drawn (${usable
                .map((entry) => entry.label)
                .join(", ")}). Nothing is smoothed or interpolated by the chart.`}
        {yLogarithmic ? " The vertical axis is logarithmic; a straight line there means geometric convergence." : ""}
      </figcaption>
    </figure>
  );
}

interface PreparedSeries {
  key: string;
  label: string;
  points: number;
  /** points recorded for this series */
  total: number;
  /** points that were finite as an (x, y) pair, i.e. drawable at all */
  finite: number;
}

function buildRows(series: ChartSeries[], yLogarithmic: boolean) {
  // x and y are filtered *as pairs*: dropping a NaN from y alone would slide every later value
  // onto the wrong x, which is precisely the kind of invented data this chart must never draw.
  const usable = series
    .map((entry, index) => {
      const pairs: [number, number][] = [];
      const count = Math.min(entry.x.length, entry.y.length);
      for (let position = 0; position < count; position += 1) {
        const x = entry.x[position];
        const y = entry.y[position];
        if (isFiniteNumber(x) && isFiniteNumber(y)) pairs.push([x, y]);
      }
      return {
        key: `s${index}`,
        label: entry.yUnit ? `${entry.label} [${entry.yUnit}]` : entry.label,
        pairs,
        total: count,
      };
    })
    .filter((entry) => entry.pairs.length > 0);

  const longest = Math.max(0, ...usable.map((entry) => entry.pairs.length));
  const step = longest > MAX_POINTS ? Math.ceil(longest / MAX_POINTS) : 1;

  const rows = new Map<number, Record<string, number>>();
  for (const entry of usable) {
    entry.pairs.forEach(([x, y], index) => {
      if (index % step !== 0) return;
      if (yLogarithmic && y <= 0) return;
      const bucket = rows.get(index) ?? {};
      bucket.x = x;
      bucket[entry.key] = y;
      rows.set(index, bucket);
    });
  }

  return {
    rows: [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([, row]) => row),
    thinnedBy: step,
    usable: usable.map<PreparedSeries>((entry) => ({
      key: entry.key,
      label: entry.label,
      points: Math.ceil(entry.pairs.length / step),
      total: entry.total,
      finite: entry.pairs.length,
    })),
  };
}

function shortTick(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value === 0) return "0";
  const magnitude = Math.floor(Math.log10(Math.abs(value)));
  if (magnitude < -3 || magnitude >= 6) return value.toExponential(1).replace(/\.0e/, "e");
  return Number(value.toFixed(Math.max(0, 4 - magnitude))).toString();
}

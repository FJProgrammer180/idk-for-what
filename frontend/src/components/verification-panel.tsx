"use client";

import { useState } from "react";
import { CheckCircle2, CircleSlash, FlaskConical, ShieldAlert, XCircle } from "lucide-react";
import { formatDateTime, formatNumber, isFiniteNumber } from "@/lib/format";

/** Numeric extras a check reported (measured value, threshold, residual norms). */
function extraMetrics(check: VerificationCheck): string[] {
  const skip = new Set(["name", "passed", "detail", "status"]);
  return Object.entries(check)
    .filter(([key, value]) => !skip.has(key) && (isFiniteNumber(value) || typeof value === "string"))
    .slice(0, 4)
    .map(([key, value]) => {
      const label = key === "value" ? "measured" : key.replace(/_/g, " ");
      return isFiniteNumber(value) ? `${label} ${formatNumber(value, 8)}` : `${label}: ${String(value)}`;
    });
}
import { verificationStatus } from "@/lib/status";
import { cn } from "@/lib/cn";
import { Badge } from "./ui";
import type { VerificationCheck, VerificationRecord } from "@/lib/types";

type CheckVerdict = "passed" | "failed" | "inconclusive";

const CHECK_TONE: Record<CheckVerdict, { tone: "success" | "danger" | "warning"; icon: React.ReactNode; verb: string }> = {
  passed: { tone: "success", icon: <CheckCircle2 size={14} />, verb: "agrees" },
  failed: { tone: "danger", icon: <XCircle size={14} />, verb: "disagrees" },
  inconclusive: { tone: "warning", icon: <CircleSlash size={14} />, verb: "no verdict" },
};

/**
 * A check carries `passed: boolean`; the difference between "the independent method disagrees"
 * and "no verdict was possible" is recorded in the sibling lists `failed_checks` and
 * `inconclusive_checks`, so the three-way label is assembled from all of them.
 */
function verdictFor(check: VerificationCheck, record: VerificationRecord): CheckVerdict {
  if (check.passed) return "passed";
  const inconclusive = new Set(record.inconclusive_checks ?? []);
  if (inconclusive.has(check.name)) return "inconclusive";
  const explicit = check.status;
  if (explicit === "inconclusive" || explicit === "passed") return explicit;
  return "failed";
}

/**
 * What the independent re-computation concluded, check by check.
 *
 * The wording matters: a passed check is an agreement between two algorithms on the same
 * inputs, not a proof of correctness, and an inconclusive check is stated as such instead of
 * being dropped from the list. The verifier never raises, so a missing or unavailable record is
 * shown as "not verified" with the reason rather than as a green tick.
 */
export function VerificationPanel({
  record,
  runStatus,
  className,
}: {
  record: VerificationRecord | null | undefined;
  runStatus?: string | null;
  className?: string;
}) {
  const [showVersions, setShowVersions] = useState(false);

  if (!record) {
    return (
      <div className={cn("rounded-xl border border-dashed border-border p-4 text-[13px] text-muted", className)}>
        <p className="mb-1 flex items-center gap-2 font-medium text-ink">
          <ShieldAlert size={15} /> No verification recorded
        </p>
        <p>
          Nobody re-computed this result with a different algorithm{runStatus ? ` (the run itself finished as "${runStatus}")` : ""}.
          Treat the numbers as unconfirmed and use <span className="mono">POST /runs/&lt;id&gt;/verify</span> or the button
          below to cross-check them.
        </p>
      </div>
    );
  }

  const meta = verificationStatus(record.status);
  const counts = record.checks.reduce(
    (accumulator, check) => {
      const verdict = verdictFor(check, record);
      accumulator[verdict] = (accumulator[verdict] ?? 0) + 1;
      return accumulator;
    },
    {} as Record<string, number>,
  );
  const strategy = typeof record.verifier?.strategy === "string" ? record.verifier.strategy : null;
  const versions = (record.verifier?.software_versions ?? {}) as Record<string, string>;

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Badge tone={meta.tone} dot>
            {meta.label}
          </Badge>
          <p className="max-w-2xl text-[12.5px] leading-relaxed text-muted">{meta.meaning}</p>
        </div>
        <p className="text-[11.5px] text-muted">
          {record.check_count} check{record.check_count === 1 ? "" : "s"} ·{" "}
          {counts.passed ?? 0} agree, {counts.failed ?? 0} disagree, {counts.inconclusive ?? 0} inconclusive
          {record.verified_at ? ` · ${formatDateTime(record.verified_at)}` : ""}
        </p>
      </div>

      {strategy ? (
        <p className="text-[12.5px]">
          <span className="label mr-2">Strategy</span>
          <span className="mono">{strategy}</span>
        </p>
      ) : null}

      {record.error ? (
        <p className="rounded-lg border border-rose-500/40 bg-rose-500/[0.07] px-3 py-2 text-[12.5px] text-rose-700 dark:text-rose-300">
          The verifier itself failed: {record.error}
        </p>
      ) : null}

      <ul className="divide-y divide-[color:var(--tone-border)] overflow-hidden rounded-xl border border-border">
        {record.checks.map((check, index) => {
          const verdict = verdictFor(check, record);
          const tone = CHECK_TONE[verdict];
          return (
            <li key={`${check.name}-${index}`} className="flex items-start gap-2.5 bg-surface px-3 py-2.5 text-[12.5px]">
              <span className={cn("mt-0.5", tone.tone === "success" ? "text-emerald-600 dark:text-emerald-400" : tone.tone === "danger" ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400")}>
                {tone.icon}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-medium">
                  {check.name.replace(/_/g, " ")} <span className="font-normal text-muted">— {tone.verb}</span>
                </p>
                {check.detail ? <p className="mt-0.5 break-words text-muted">{check.detail}</p> : null}
                {extraMetrics(check).length > 0 ? (
                  <p className="mono mt-0.5 text-[11.5px] text-muted">{extraMetrics(check).join(" · ")}</p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>

      {record.inconclusive_checks.length > 0 ? (
        <p className="text-[12px] text-muted">
          Undecided checks: {record.inconclusive_checks.join(", ")}. An inconclusive cross-check is
          reported as such — it means the reference computation could not be performed or did not
          apply, not that the result is wrong.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3 text-[11.5px] text-muted">
        <button type="button" className="inline-flex items-center gap-1.5 underline-offset-2 hover:underline" onClick={() => setShowVersions((value) => !value)}>
          <FlaskConical size={13} /> {showVersions ? "hide" : "show"} the software that produced these numbers
        </button>
        {showVersions ? (
          <span className="mono">
            {Object.entries(versions)
              .map(([name, version]) => `${name} ${version}`)
              .join(" · ") || "no version stamp was recorded"}
          </span>
        ) : null}
      </div>
    </div>
  );
}

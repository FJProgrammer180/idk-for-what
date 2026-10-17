"use client";

import { runStatus, valueNoun, verificationStatus } from "@/lib/status";
import { cn } from "@/lib/cn";
import { Badge } from "./ui";
import type { RunStatus, VerificationStatus } from "@/lib/types";

/**
 * A run badge that always carries its own explanation. `completed_unconverged` next to a bare
 * number is the exact failure mode this project refuses to ship, so the meaning is rendered as
 * text wherever there is room for it, not hidden in a tooltip.
 */
export function RunBadge({
  status,
  withMeaning = false,
  className,
}: {
  status: RunStatus | string | null | undefined;
  withMeaning?: boolean;
  className?: string;
}) {
  const meta = runStatus(status);
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-2", className)}>
      <Badge tone={meta.tone} dot>
        {meta.label}
      </Badge>
      {withMeaning ? <span className="text-[12.5px] text-muted">{meta.meaning}</span> : null}
    </span>
  );
}

export function VerificationBadge({
  status,
  withMeaning = false,
  className,
}: {
  status: VerificationStatus | string | null | undefined;
  withMeaning?: boolean;
  className?: string;
}) {
  const meta = verificationStatus(status);
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-2", className)}>
      <Badge tone={meta.tone} dot>
        {meta.label}
      </Badge>
      {withMeaning ? <span className="text-[12.5px] text-muted">{meta.meaning}</span> : null}
    </span>
  );
}

/**
 * The one-sentence honest summary of a finished run: what happened, what we may call the
 * number, and whether anybody checked it.
 */
export function OutcomeLine({
  status,
  verification,
  message,
}: {
  status: RunStatus | string | null | undefined;
  verification?: VerificationStatus | string | null;
  message?: string | null;
}) {
  const run = runStatus(status);
  const check = verification ? verificationStatus(verification) : null;
  return (
    <p className="text-[13px] leading-relaxed text-muted">
      <span className="font-medium text-ink">{run.label}</span> — {run.meaning}{" "}
      {status === "completed_unconverged" ? (
        <>
          {" "}
          The table below therefore reports the <span className="font-medium text-ink">{valueNoun(status)}</span>, not a
          converged solution.
        </>
      ) : null}
      {check ? (
        <>
          {" "}
          Independent check: <span className="font-medium text-ink">{check.label}</span> — {check.meaning}
        </>
      ) : null}
      {message ? <span className="mono block pt-1 text-[12px]">{message}</span> : null}
    </p>
  );
}

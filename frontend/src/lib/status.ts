/**
 * One place decides how a run's outcome is *described*.
 *
 * The project's honesty rule: a method that stopped on its iteration cap produced an iterate,
 * not a root; a run nobody re-computed independently is unverified, not correct. So the copy
 * below is deliberately unflattering in those cases and components must not restyle it.
 */

import type { RunStatus, VerificationStatus } from "./types";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export interface StatusMeta {
  label: string;
  tone: Tone;
  /** One sentence a user can read instead of the badge alone. */
  meaning: string;
}

const RUN_STATUS: Record<RunStatus, StatusMeta> = {
  queued: {
    label: "Queued",
    tone: "neutral",
    meaning: "Accepted by the worker, not started yet.",
  },
  running: {
    label: "Running",
    tone: "info",
    meaning: "The solver is executing now.",
  },
  verifying: {
    label: "Verifying",
    tone: "info",
    meaning: "The computation finished; an independent algorithm is being asked to reproduce it.",
  },
  completed: {
    label: "Converged",
    tone: "success",
    meaning: "The stopping criterion was met and the output was checked against an independent computation.",
  },
  completed_unconverged: {
    label: "Stopped early",
    tone: "warning",
    meaning: "The iteration budget ran out first. The value returned is an iterate, not a verified solution.",
  },
  completed_unverified: {
    label: "Not verified",
    tone: "warning",
    meaning: "The computation finished but the independent re-computation did not run or could not be compared.",
  },
  failed: {
    label: "Failed",
    tone: "danger",
    meaning: "No result. The inputs or the solver's preconditions were not satisfied.",
  },
  cancelled: {
    label: "Cancelled",
    tone: "neutral",
    meaning: "Stopped on request before it produced a result.",
  },
};

const VERIFICATION: Record<VerificationStatus, StatusMeta> = {
  passed: {
    label: "Verified",
    tone: "success",
    meaning: "An independent method reproduced the reported numbers within tolerance.",
  },
  failed: {
    label: "Verification failed",
    tone: "danger",
    meaning: "The independent recomputation disagrees with the reported result. Treat the run as wrong until reconciled.",
  },
  inconclusive: {
    label: "Inconclusive",
    tone: "warning",
    meaning: "The cross-check ran but could not reach a verdict (for instance no closed form exists for these inputs).",
  },
  skipped: {
    label: "Check skipped",
    tone: "neutral",
    meaning: "No independent check was applicable to this output.",
  },
  not_requested: {
    label: "Not verified",
    tone: "neutral",
    meaning: "Nobody asked for an independent check of this run.",
  },
  unavailable: {
    label: "Checker unavailable",
    tone: "warning",
    meaning: "The verification tooling could not run for this payload.",
  },
};

export function runStatus(status: string | null | undefined): StatusMeta {
  const known = RUN_STATUS[status as RunStatus];
  if (known) return known;
  return {
    label: status ? status.replace(/_/g, " ") : "Unknown",
    tone: "neutral",
    meaning: "The backend reported a status this client does not recognise.",
  };
}

export function verificationStatus(status: string | null | undefined): StatusMeta {
  const known = VERIFICATION[status as VerificationStatus];
  if (known) return known;
  return {
    label: status ? status.replace(/_/g, " ") : "Not verified",
    tone: "neutral",
    meaning: "No independent cross-check has been recorded for this run.",
  };
}

/** A run whose headline number must be shown with a caveat (or not trusted at all). */
export function needsCaveat(status: string | null | undefined): boolean {
  return status === "completed_unconverged" || status === "completed_unverified" || status === "failed";
}

export function isTrustworthy(status: string | null | undefined, verification: string | null | undefined): boolean {
  return status === "completed" && verification === "passed";
}

/** How the value itself should be named in the UI. Never "the root"/"the answer". */
export function valueNoun(status: string | null | undefined): string {
  switch (status) {
    case "completed_unconverged":
      return "last iterate";
    case "completed_unverified":
      return "unverified value";
    case "failed":
      return "no value";
    default:
      return "computed value";
  }
}

export const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-border bg-surface-2 text-muted",
  info: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  success: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  danger: "border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-300",
};

export const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-slate-400",
  info: "bg-sky-500",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
};

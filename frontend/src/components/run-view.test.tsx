import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { RunView } from "./run-view";
import type { RunPayload, VerificationRecord } from "@/lib/types";

/**
 * The inspection pane is where a wrong reading happens, so its wording is tested with the two
 * payloads that matter most: a run that stopped on its iteration budget, and a run whose
 * independent check disagreed.
 */

const unconverged: RunPayload = {
  method: "bisection",
  solver: "bisection",
  status: "completed_unconverged",
  convergence_achieved: false,
  message: "max_iterations reached after 5 evaluations; the interval is still 0.0625 wide — this is an iterate, NOT a verified root",
  error_estimate: 0.03125,
  tolerance: 1e-12,
  iteration_count: 5,
  iterations: [
    { index: 1, x: 1.5, f_x: 0.25, error: 0.5 },
    { index: 2, x: 1.375, f_x: -0.109375, error: 0.25 },
  ],
  result: { root: 1.40625, f_root: 0.01171875, bracket: [1.375, 1.4375], interval_width: 0.0625 },
  warnings: ["max_iterations_reached after 5 iterations"],
  metadata: { iterations_used: 5, stopping_criterion: "abs(b - a) / 2 <= tol" },
};

const verification: VerificationRecord = {
  status: "failed",
  check_count: 3,
  checks: [
    { name: "residual_evaluation", passed: false, detail: "f(root) = 0.0117 is not within 1e-8 of zero", value: 0.01171875, threshold: 1e-8 },
    { name: "output_schema", passed: true },
    { name: "finiteness", passed: true },
  ],
  failed_checks: ["residual_evaluation"],
  inconclusive_checks: [],
  verified_at: "2026-10-17T04:00:00Z",
  verifier: { strategy: "independent recomputation with a different algorithm", software_versions: { numpy: "2.1.3" } },
};

describe("RunView", () => {
  it("labels a stopped-early run as an iterate and never as converged", () => {
    render(<RunView payload={unconverged} verification={null} status="completed_unconverged" />);
    expect(screen.getByText("Stopped early")).toBeInTheDocument();
    expect(screen.getByText(/last iterate/i)).toBeInTheDocument();
    expect(screen.queryByText("Converged")).not.toBeInTheDocument();
    // the server's own sentence is quoted, not paraphrased
    expect(screen.getByText(/NOT a verified root/)).toBeInTheDocument();
  });

  it("reports that no verification exists instead of leaving it implied", () => {
    render(<RunView payload={unconverged} verification={null} status="completed_unconverged" />);
    expect(screen.getByText(/No verification recorded/i)).toBeInTheDocument();
  });

  it("shows a failing cross-check as a contradiction and names the check", () => {
    render(<RunView payload={unconverged} verification={verification} status="completed_unconverged" />);
    // the header badge and the panel agree rather than one of them softening the other
    expect(screen.getAllByText("Verification failed").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText(/residual evaluation/i)[0]).toBeInTheDocument();
    expect(screen.getByText(/f\(root\) = 0.0117/)).toBeInTheDocument();
    expect(screen.getByText(/measured 0.01171875 · threshold 1e-8/)).toBeInTheDocument();
  });

  it("renders the value with its error estimate rather than a bare number", () => {
    render(<RunView payload={unconverged} verification={verification} status="completed_unconverged" />);
    // the root is printed to the decimal place its own error estimate justifies - not more
    expect(screen.getByText("1.406 ± 3.13e-2")).toBeInTheDocument();
    expect(screen.queryByText(/1\.40625/)).not.toBeInTheDocument();
  });

  it("surfaces the recorded failure of a failed run", () => {
    const failed: RunPayload = { ...unconverged, status: "failed", result: {} };
    render(
      <RunView
        payload={failed}
        verification={null}
        status="failed"
        error={{ code: "invalid_input", message: "'a' and 'b' must have opposite signs", retryable: false, detail: null }}
      />,
    );
    expect(screen.getByText(/must have opposite signs/)).toBeInTheDocument();
    expect(screen.getByText(/not retryable as-is/i)).toBeInTheDocument();
  });

  it("links the stored artifacts so a report can be written from this exact run", () => {
    const onVerify = vi.fn();
    render(<RunView payload={unconverged} verification={verification} status="completed_unconverged" runId="run_abc" onVerify={onVerify} />);
    expect(screen.getByRole("link", { name: /plot\.png/ })).toHaveAttribute("href", "/api/v1/runs/run_abc/plot.png");
    expect(screen.getByText("run_abc")).toBeInTheDocument();
    screen.getByRole("button", { name: /verify again/i }).click();
    expect(onVerify).toHaveBeenCalled();
  });

  it("marks an ephemeral run as not stored", () => {
    render(<RunView payload={unconverged} verification={null} status="completed" runId={null} />);
    expect(screen.getByText("not stored")).toBeInTheDocument();
  });
});

"use client";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import DashboardPage from "./page";

// Mock session + resource to provide backend-shaped dashboard payload
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { display_name: "Test User", email: "t@e.com" },
    capabilities: { run_statuses: ["completed", "failed"] },
    provider: null,
    loading: false,
    error: null,
  }),
}));

const mockDashboard = {
  generated_from: "database queries against this account's rows",
  totals: { experiments: 2, runs: 3, reports: 1, projects: 1 },
  runs_by_status: { completed: 2, failed: 1 },
  runs_by_verification: { passed: 2 },
  method_usage: [
    { method: "bisection", runs: 3, kind: "numerical" },
    { method: "gaussian_elimination", runs: 1 },
  ],
  runtime: { sampled_runs: 3, mean_execution_time_ms: 12.5, max_execution_time_ms: 20 },
  recent_experiments: [
    { id: "exp_1", title: "sqrt2", kind: "numerical", method: "bisection", status: "draft", headline: 1.414, verification_status: "passed", updated_at: new Date().toISOString() },
  ],
  // Lightweight recent_runs as backend actually returns (no structured_output)
  recent_runs: [
    {
      run_id: "run_1",
      experiment_id: "exp_1",
      experiment_title: "sqrt2 experiment",
      status: "completed",
      execution_time_ms: 12.3,
      verification_status: "passed",
      created_at: new Date().toISOString(),
    },
    {
      run_id: "run_2",
      experiment_id: "exp_2",
      status: "failed",
      execution_time_ms: null,
      verification_status: null,
      created_at: new Date().toISOString(),
    },
  ],
  recent_sessions: [
    { session_id: "ses_1", title: "Test session", status: "completed", provider: "mock", model: "deterministic-mock", updated_at: new Date().toISOString() },
  ],
};

vi.mock("@/lib/resources", async () => {
  const actual = await vi.importActual<typeof import("@/lib/resources")>("@/lib/resources");
  return {
    ...actual,
    useResource: () => ({
      data: mockDashboard,
      totals: mockDashboard.totals,
      loading: false,
      error: null,
      reload: vi.fn(),
      setData: vi.fn(),
      refreshing: false,
    }),
  };
});

describe("DashboardPage - handles actual backend response shapes (F-03)", () => {
  it("renders without crashing when recent_runs are lightweight rows (no structured_output)", () => {
    render(<DashboardPage />);
    // Stat cards
    expect(screen.getByText("Experiments")).toBeInTheDocument();
    // Run outcomes + verification sections
    expect(screen.getByText("Run outcomes")).toBeInTheDocument();
    expect(screen.getByText("Independent verification")).toBeInTheDocument();
    // Method usage should show methods even when entry has extra `kind`
    expect(screen.getByText("bisection")).toBeInTheDocument();
    // Recent runs table should show experiment_title fallback instead of structured_output.method
    expect(screen.getByText("sqrt2 experiment")).toBeInTheDocument();
    // VerificationBadge should use verification_status flat field
    expect(screen.getAllByText(/Verified|Passed/i).length).toBeGreaterThan(0);
  });

  it("does not render [object Object] for method_usage or totals", () => {
    render(<DashboardPage />);
    const html = document.body.innerHTML;
    expect(html).not.toContain("[object Object]");
  });

  it("handles empty verification and missing finished_at gracefully", () => {
    render(<DashboardPage />);
    // run_2 has null verification_status -> should show Not verified/failed badge, not crash
    expect(screen.getAllByText(/Failed/i).length).toBeGreaterThanOrEqual(1);
  });
});

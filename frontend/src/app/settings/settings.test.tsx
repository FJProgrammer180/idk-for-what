"use client";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import SettingsPage from "./page";

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { email: "demo@physicslab.local", display_name: "Demo" },
    refresh: vi.fn(),
  }),
}));

const mockSettingsBackendShape = {
  profile: {
    id: "usr_1",
    email: "demo@physicslab.local",
    display_name: "Demo",
    role: "researcher",
    is_demo: true,
    created_at: new Date().toISOString(),
    last_login_at: new Date().toISOString(),
  },
  computation_limits: {
    max_iterations: 200,
    compute_timeout_seconds: 20,
    max_agent_steps: 8,
    max_agent_tool_calls: 12,
    max_upload_bytes: 10000000,
    max_expression_chars: 400,
    server_ceiling_max_iterations: 200,
    enforced_by: "backend",
  },
  data_retention: {
    retention_days: 0,
    policy: "0 = keep indefinitely; >0 = purge runs older than N days on request",
    // backend actually returns object, not number — this was the rendering bug (F-03)
    stored_rows: { experiments: 8, runs: 8, datasets: 0, reports: 0 } as unknown as number,
  },
  ai_provider: {
    provider: "mock",
    model: "deterministic-mock",
    is_mock: true,
    configured: true,
    note: "Deterministic rule planner",
    error: null,
    allowed_tools: [],
    supports_structured_tools: false,
    research_integration_configured: false,
  },
  theme_defaults: { palette: "near-black" },
  // backend returns object, not string — this was the second TS contract bug
  environment: {
    app_environment: "development",
    software_versions: { numpy: "2.1.3" },
    cors_origins: ["http://localhost:3000"],
  } as unknown as string,
  security: {
    execution: {},
    access_control: {},
    data: {},
    observability: {},
    secrets: {},
    honesty_guarantees: {},
    gaps: [],
  },
};

vi.mock("@/lib/resources", async () => {
  const actual = await vi.importActual<typeof import("@/lib/resources")>("@/lib/resources");
  return {
    ...actual,
    useResource: () => ({
      data: mockSettingsBackendShape,
      loading: false,
      error: null,
      reload: vi.fn(),
      setData: vi.fn(),
      refreshing: false,
    }),
  };
});

describe("SettingsPage - handles actual backend payload shapes (F-03)", () => {
  it("renders environment label without [object Object] when backend returns object", () => {
    render(<SettingsPage />);
    expect(screen.getByText("Settings")).toBeInTheDocument();
    // Should show the app_environment value, not the stringified object
    expect(screen.getByText("development")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("[object Object]");
  });

  it("renders stored_rows object as breakdown, not [object Object]", () => {
    render(<SettingsPage />);
    // Should contain the breakdown "8 experiments, 8 runs" rather than object string
    expect(screen.getByText(/8 experiments/)).toBeInTheDocument();
    expect(screen.getByText(/8 runs/)).toBeInTheDocument();
    expect(screen.getAllByText(/keep forever/).length).toBeGreaterThanOrEqual(1);
    expect(document.body.innerHTML).not.toContain("[object Object]");
  });

  it("preserves compute policy values from computation_limits", () => {
    render(<SettingsPage />);
    expect(screen.getByText(/max_iterations \(yours\)/)).toBeInTheDocument();
    expect(screen.getAllByText("200").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/Effective compute policy/)).toBeInTheDocument();
  });

  it("bottom-row purge controls are clickable at md widths (F-06)", () => {
    // Ensure the fix for 640–1023px does not hide controls behind overflow
    render(<SettingsPage />);
    const previewBtn = screen.getByRole("button", { name: /preview purge/i });
    const purgeBtn = screen.getByRole("button", { name: /purge now/i });
    expect(previewBtn).toBeEnabled();
    expect(purgeBtn).toBeEnabled();
    // Buttons must have relative positioning to stay above any chart/overlay at tablet widths
    expect(previewBtn.className).toContain("relative");
    // Container must allow wrapping, not clipping
    const html = document.body.innerHTML;
    expect(html).not.toContain("pointer-events-none");
  });
});

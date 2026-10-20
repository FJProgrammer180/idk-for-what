/**
 * Hand-written types for the PHYSICS LAB AI API.
 *
 * These were derived by printing the actual responses of every endpoint the UI calls (see
 * `docs/api.md`), not by guessing: e.g. a run row nests its payload under `structured_output`
 * and its cross-check under `verification`, a verification *check* reports `passed: boolean`
 * (not a status string), and the human-readable strategy lives under `verification.verifier`.
 *
 * Where a payload genuinely varies by method (an iteration row's columns, a result object's
 * keys) the type says so instead of pretending to know, and components must render a stated
 * fallback rather than a zero.
 */

export type RunStatus =
  | "queued"
  | "running"
  | "verifying"
  | "completed"
  | "completed_unconverged"
  | "completed_unverified"
  | "failed"
  | "cancelled";

export type AgentStatus = "queued" | "planning" | "running" | "verifying" | "completed" | "failed" | "cancelled";

export type VerificationStatus =
  | "passed"
  | "failed"
  | "inconclusive"
  | "skipped"
  | "not_requested"
  | "unavailable";

export type JobKind = "numerical" | "simulation";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_more: boolean;
  offset?: number;
}

// --- account ---------------------------------------------------------------

export interface Profile {
  id: string;
  email: string;
  display_name: string;
  role: string;
  is_demo: boolean;
  created_at: string;
  last_login_at: string | null;
  limits?: Partial<ComputationLimits> | null;
}

export interface AuthResponse {
  auth_mode: string;
  csrf_token: string;
  expires_in_seconds: number;
  user: Profile;
}

export interface ComputationLimits {
  max_iterations: number;
  compute_timeout_seconds: number;
  max_agent_steps: number;
  max_agent_tool_calls: number;
  max_expression_chars: number;
  max_upload_bytes: number;
  server_ceiling_max_iterations?: number;
  enforced_by?: string;
}

export interface DataRetention {
  policy: string | null;
  retention_days: number | null;
  stored_rows: number | Record<string, number>;
}

export interface AiProvider {
  provider: string;
  model: string;
  is_mock: boolean;
  configured: boolean;
  note: string;
  error: string | null;
  allowed_tools: string[];
  supports_structured_tools: boolean;
  research_integration_configured: boolean;
}

export interface SettingsPayload {
  profile: Profile;
  computation_limits: ComputationLimits;
  data_retention: DataRetention;
  ai_provider: AiProvider;
  theme_defaults: Record<string, unknown>;
  environment: string | Record<string, unknown>;
  security: SecurityPosture;
}

/** `PATCH /settings` (body is flat: display_name / retention_days / max_iterations). */
export interface SettingsPatch {
  status: "updated" | "noop";
  changed: string[];
  ignored: string[];
  rejected: unknown;
  profile: { display_name: string };
  computation_limits: { max_iterations: number; compute_timeout_seconds?: number; server_ceiling_max_iterations?: number };
  data_retention: { retention_days: number };
}

// --- capabilities ----------------------------------------------------------

export interface SecurityPosture {
  execution: Record<string, unknown>;
  access_control: Record<string, unknown>;
  data: Record<string, unknown>;
  observability: Record<string, unknown>;
  secrets: Record<string, unknown>;
  honesty_guarantees: Record<string, unknown>;
  gaps: string[];
}

export interface Capabilities {
  version: string;
  run_statuses: RunStatus[];
  agent_statuses: AgentStatus[];
  limits: ComputationLimits & { max_grid_points?: number };
  ai: AiProvider;
  numerical: {
    methods: MethodSpec[];
    problem_types: Record<string, string>;
    regression_models: Record<string, string>;
    expression_grammar: Record<string, unknown>;
    software_versions: Record<string, string>;
  };
  simulations: SimulationSpec[];
  tools: AgentTool[];
  security: SecurityPosture;
}

export interface MethodSpec {
  method: string;
  title: string;
  problem_type: string;
  requires: string[];
  optional: string[];
  /** A sentence for a numerical method, a list for a model - both shapes occur in the API. */
  assumptions: string | string[];
  order: string | number | null;
}

export interface SimulationParameterSpec {
  default: number | string | null;
  min?: number | null;
  max?: number | null;
  unit?: string | null;
  description?: string;
  derived?: boolean;
  enum?: (string | number)[];
}

export interface SimulationSpec {
  key: string;
  title: string;
  category?: string;
  description?: string;
  parameters: Record<string, SimulationParameterSpec>;
  state_variables?: string[];
  assumptions?: string[];
  limitations?: string[];
  governing_equations?: string[];
  analytical_validation_available?: boolean;
}

// --- projects, experiments, runs ------------------------------------------

export interface Project {
  id: string;
  name: string;
  description: string;
  is_archived: boolean;
  created_at: string;
  updated_at?: string;
  experiment_count?: number;
}

export interface ExperimentSummary {
  id: string;
  title: string;
  kind: JobKind;
  problem_type: string;
  method: string;
  status: string;
  project_id: string | null;
  notes: string;
  template_key: string | null;
  input_parameters: Record<string, unknown>;
  result_metadata: Record<string, unknown>;
  run_count: number;
  latest_run: RunRow | null;
  created_at: string;
  updated_at: string;
}

/** One recorded iteration. Column set is method-specific, hence the index signature. */
export interface IterationRow {
  index: number;
  error?: number | null;
  f_x?: number | null;
  [key: string]: number | string | null | undefined;
}

export interface VerificationCheck {
  name: string;
  passed: boolean;
  detail?: string;
  /** The verifier only sends `status` on a check it could not decide. */
  status?: "passed" | "failed" | "inconclusive";
  [key: string]: unknown;
}

export interface VerificationRecord {
  status: VerificationStatus;
  checks: VerificationCheck[];
  check_count: number;
  failed_checks: string[];
  inconclusive_checks: string[];
  verified_at: string | null;
  verifier: {
    strategy?: string;
    software_versions?: Record<string, string>;
    required_keys?: Record<string, string[]>;
    [key: string]: unknown;
  };
  error?: string | null;
}

/** What an ODE/quadrature/regression payload carries besides numbers. */
export interface ModelDisclosure {
  preset?: string | null;
  parameters?: Record<string, unknown>;
  initial_state?: Record<string, unknown>;
  integration_window?: { t_end?: number; source?: string } | null;
  assumptions?: string[];
  limitations?: string[];
  governing_equations?: string[];
}

export interface RunPayload {
  method?: string;
  job_kind?: JobKind;
  experiment_id?: string;
  /** `method:implementation`, e.g. `ode_ivp:DOP853`. */
  solver?: string;
  status?: RunStatus;
  convergence_achieved?: boolean;
  message?: string;
  error_estimate?: number | null;
  tolerance?: number | null;
  iteration_count?: number;
  iterations?: IterationRow[];
  iterations_truncated?: boolean;
  warnings?: string[];
  non_finite_fields?: string[];
  execution_time_ms?: number;
  inputs?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  software_versions?: Record<string, string>;
  /** Scalars for a numerical run; `time` plus one array per state for an ODE. */
  result?: Record<string, unknown>;
  /** For simulations: `{time: [...], theta: [...], omega: [...]}`. */
  series?: Record<string, number[]>;
  summary?: Record<string, unknown>;
  units?: Record<string, string>;
  grid?: Record<string, unknown>;
  model?: ModelDisclosure;
  validation?: {
    checks: { name: string; passed: boolean; detail?: string; [key: string]: unknown }[];
    overall: string;
    notes?: string[];
  };
  simulation?: string;
  parameter_aliases?: Record<string, string>;
  limits_applied?: LimitsApplied;
  [key: string]: unknown;
}

export interface LimitsApplied {
  max_iterations: number;
  requested: number | null;
  account_ceiling: number | null;
  server_ceiling: number | null;
  clamped: boolean;
  source: string;
  /** False for direct solvers, which have no iteration budget to bound. */
  applies?: boolean;
  note?: string;
}

export interface RunError {
  code: string;
  message: string;
  retryable: boolean;
  detail?: string | null;
}

/** The row shape both `/experiments/{id}/runs` and `/runs/{id}` return. */
export interface RunRow {
  run_id: string;
  experiment_id: string;
  experiment_title?: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  execution_time_ms: number | null;
  execution_config: { parameters?: Record<string, unknown>; [key: string]: unknown };
  structured_output: RunPayload | null;
  verification: VerificationRecord | null;
  runtime_metadata: Record<string, unknown>;
  software_versions: Record<string, string>;
  error: RunError | null;
  cancel_requested: boolean;
}

/** `POST /experiments/{id}/runs`: `wait:true` returns the finished row, otherwise a poll URL. */
export interface RunCreateResponse {
  run: RunRow;
  status: string;
  async: boolean;
  poll_url: string | null;
}

/** `POST /compute/run` and `POST /compute/simulate` (nothing is persisted for these). */
export interface EphemeralRun {
  mode: "ephemeral";
  note: string;
  inputs: Record<string, unknown>;
  limits_applied: LimitsApplied;
  result: RunPayload;
  verification: VerificationRecord;
  software_versions: Record<string, string>;
  plot_png_base64?: string | null;
}

export interface ChartData {
  run_id: string;
  status: RunStatus;
  method: string | null;
  axes: { x: string; y: string };
  series: { label: string; x: number[]; y: number[]; unit?: string }[];
  iteration_table: IterationRow[];
  units: Record<string, string>;
  note: string;
}

export interface Template {
  key: string;
  example_parameters?: Record<string, unknown>;
  title: string;
  kind: JobKind;
  method: string;
  problem_type: string;
  description: string;
  expected: string;
  assumptions: string[];
  tags: string[];
  parameters: Record<string, unknown>;
}

// --- data & reports --------------------------------------------------------

/** `POST /datasets` (and the stored half of `GET /datasets/{id}`). */
export interface DatasetInfo {
  dataset_id: string;
  original_filename: string;
  project_id: string | null;
  size_bytes: number;
  sha256: string;
  row_count: number;
  created_at: string;
  duplicate?: boolean;
  note?: string;
  schema_profile?: {
    row_count: number;
    column_count: number;
    columns: {
      name: string;
      dtype: string;
      numeric: boolean;
      missing: number;
      missing_fraction: number;
      unique: number;
      min?: number | null;
      max?: number | null;
      mean?: number | null;
      std?: number | null;
      median?: number | null;
      count?: number;
    }[];
    delimiter?: string;
    has_header?: boolean;
    notes?: string[];
  };
}

export interface DatasetRow {
  dataset_id: string;
  original_filename: string;
  row_count: number;
  column_count: number;
  size_bytes: number;
  sha256: string;
  created_at: string;
  has_analysis: boolean;
}

export interface DatasetDetail {
  dataset: DatasetInfo;
  profile: Record<string, unknown>;
  preview_rows: Record<string, unknown>[];
  export_url: string;
}

export interface DatasetAnalysis {
  dataset_id: string;
  status: string;
  result: {
    profile: Record<string, unknown>;
    regression: RegressionFit | null;
    rows_used?: number;
    columns?: string[];
    notes?: string[];
    message?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface RegressionFit {
  model: string;
  parameters: Record<string, number>;
  r_squared: number | null;
  residual_mean: number | null;
  residual_max_abs: number | null;
  n: number;
  [key: string]: unknown;
}

export type ReportRequestFormat = "markdown" | "pdf" | "both";
/** What the server echoes back: a request for "both" is reported as both artifacts. */
export type ReportFormat = "markdown" | "pdf" | "markdown+pdf";

export interface ReportCreateResponse {
  report_id: string;
  title: string;
  experiment_id: string;
  run_id: string;
  format: ReportFormat;
  sections: string[];
  word_count: number;
  warnings: string[];
  artifacts: Partial<Record<"markdown" | "pdf", string>>;
  download: Partial<Record<"markdown" | "pdf", string>>;
}

export interface ReportRow {
  report_id: string;
  title: string;
  experiment_id: string;
  run_id: string;
  format: ReportFormat;
  created_at: string;
  artifact_path: string | null;
  artifact_size_bytes: number | null;
  checksum_sha256: string | null;
  word_count_estimate: number;
  has_pdf: boolean;
  verification_snapshot: {
    run_status?: RunStatus;
    run_verification_status?: VerificationStatus;
    generated_at?: string;
    cited_sources?: number;
    pdf?: { path?: string; bytes?: number; pages?: number } | null;
    software_versions?: Record<string, string>;
  } | null;
}

export interface ReportDetail extends ReportRow {
  body_markdown: string;
  sections?: string[];
  warnings?: string[];
}

export interface SourceRecord {
  id: string;
  title: string;
  authors?: string;
  year?: number;
  venue?: string;
  url?: string | null;
  doi?: string | null;
  topic?: string;
  summary?: string;
  provenance?: string;
  verified_online?: boolean;
}

export interface SearchResponse {
  status: string;
  external_research_performed: boolean;
  reason: string;
  query: string;
  max_results: number;
  local_references: SourceRecord[];
  local_reference_provenance: string;
  guidance?: string;
}

// --- agent -----------------------------------------------------------------

export interface AgentTool {
  name: string;
  description: string;
  category: string;
  read_only: boolean;
  persist: boolean;
  input_schema: { additionalProperties?: boolean; [key: string]: unknown };
}

export interface AgentPlanStep {
  index: number;
  tool: string;
  description: string;
  status: string;
  detail?: string;
}

export interface AgentToolResult {
  tool: string;
  arguments: Record<string, unknown>;
  status: string;
  ok: boolean;
  output?: Record<string, unknown> | null;
  error?: string | null;
  duration_ms?: number;
}

export interface AgentEvent {
  sequence: number;
  event_type: string;
  status: string;
  created_at: string | null;
  payload: Record<string, unknown>;
}

export interface AgentSessionRow {
  session_id: string;
  title: string;
  status: AgentStatus | string;
  provider: string;
  model: string;
  project_id: string | null;
  plan: AgentPlanStep[];
  usage: Record<string, number>;
  created_at: string;
  updated_at?: string;
  event_count?: number;
  /** Present on create; the model refused or answered outside a session row. */
  note?: string;
  is_mock?: boolean;
}

export interface AgentSessionDetail extends Omit<AgentSessionRow, "created_at"> {
  created_at: string;
  events: AgentEvent[];
}

export interface AgentReply {
  session_id: string;
  status: string;
  answer_markdown: string;
  plan: AgentPlanStep[];
  tool_results: AgentToolResult[];
  usage: Record<string, number>;
  provider: AiProvider | string;
  error: { code?: string; message?: string } | string | null;
  events: AgentEvent[];
}

// --- dashboard -------------------------------------------------------------

/**
 * The backend's recent collections are intentionally lightweight (no payloads) —
 * the UI must accept both the full RunRow shape and the trimmed summary the
 * dashboard actually returns. See backend/app/api/v1/insights.py :: dashboard().
 */
export type DashboardRun = RunRow & {
  /** Lightweight fields the dashboard row carries instead of a full payload */
  experiment_title?: string;
  verification_status?: VerificationStatus | string | null;
  headline?: unknown;
};

export interface Dashboard {
  generated_from: string;
  totals: Record<string, number>;
  runs_by_status: Partial<Record<RunStatus, number>>;
  runs_by_verification: Partial<Record<VerificationStatus, number>>;
  method_usage: { method: string; runs: number; kind?: string }[];
  runtime: { sampled_runs?: number; mean_execution_time_ms?: number; max_execution_time_ms?: number };
  recent_experiments: (ExperimentSummary & { headline?: unknown; verification_status?: string | null })[];
  recent_runs: DashboardRun[];
  recent_sessions: (AgentSessionRow & { provider?: string; model?: string })[];
  recent_agent_activity?: (AgentEvent & { session_title?: string; summary?: Record<string, unknown> })[];
}
